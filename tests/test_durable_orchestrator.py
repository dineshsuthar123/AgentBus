import pytest

from agentbus.config import AgentBusConfig
from agentbus.execution.models import RunStatus, TaskStatus
from agentbus.execution.state_store import StateStore, StateStoreError
from agentbus.runtime.orchestrator import MultiAgentOrchestrator


PLAN = {
    "goal": "Create calculator",
    "steps": [
        {
            "id": "step-1",
            "title": "Implement",
            "description": "Create calculator",
            "risk": "low",
        },
        {
            "id": "step-2",
            "title": "Test",
            "description": "Test calculator",
            "risk": "low",
        },
    ],
    "test_strategy": "Run pytest",
    "done_criteria": ["Tests pass"],
}


class FakePlanner:
    def __init__(self, plan=None):
        self.output = plan or PLAN
        self.context_pack = None

    def plan(self, user_task, file_list=None, context_pack=None):
        self.context_pack = context_pack
        return self.output


class FakeCoder:
    def __init__(self):
        self.calls = []

    def execute(self, user_task, plan, reviewer_feedback=None):
        self.calls.append(
            {
                "task_id": plan["steps"][0]["id"],
                "reviewer_feedback": reviewer_feedback,
            }
        )
        return f"implemented {plan['steps'][0]['id']}"


class FakeVerifier:
    def __init__(self, passed=True):
        self.passed = passed
        self.calls = 0

    def verify(self):
        self.calls += 1
        return {
            "command": ["python", "-m", "pytest"],
            "exit_code": 0 if self.passed else 1,
            "passed": self.passed,
            "output": "offline verifier output",
            "reason": "fake",
        }


class FakeReviewer:
    def __init__(self, approved=True):
        self.approved = approved
        self.calls = 0

    def review(self, user_task, plan, git_diff, test_output=None):
        self.calls += 1
        return {
            "approved": self.approved,
            "issues": [] if self.approved else [{"message": "Needs correction"}],
            "summary": "Approved" if self.approved else "Rejected",
            "required_fixes": [] if self.approved else ["Fix implementation"],
        }


class FakeGitRepository:
    def __init__(self):
        self.commits = []
        self.pushes = []
        self.created_branches = []
        self.head = "before1"
        self.dirty = True

    def is_git_repo(self):
        return True

    def current_branch(self):
        return "main"

    def head_commit(self, short=True):
        return self.head

    def has_uncommitted_changes(self):
        return False

    def create_branch(self, branch_name):
        self.created_branches.append(branch_name)
        return f"Created branch: {branch_name}"

    def changed_files(self):
        if not self.dirty:
            return []
        return ["calculator.py", "tests/test_calculator.py"]

    def commit(self, message):
        self.commits.append(message)
        self.head = "abc1234"
        self.dirty = False
        return self.head

    def push_branch(self, branch_name=None):
        self.pushes.append(branch_name)
        return f"Pushed branch: {branch_name}"


class FakePRClient:
    def __init__(self):
        self.calls = []

    def create_pr(self, **kwargs):
        self.calls.append(kwargs)
        return "https://github.com/acme/repo/pull/1"


class AmbiguousPRClient:
    def __init__(self):
        self.calls = 0

    def create_pr(self, **kwargs):
        self.calls += 1
        raise RuntimeError("connection ended after request")


def config(tmp_path):
    return AgentBusConfig(
        workspace_dir=str(tmp_path / "workspace"),
        runs_dir=str(tmp_path / "runs"),
        state_dir=str(tmp_path / "state"),
    )


def orchestrator(tmp_path, **kwargs):
    settings = config(tmp_path)
    store = StateStore(settings.state_database_path)
    defaults = {
        "config": settings,
        "planner": FakePlanner(),
        "coder": FakeCoder(),
        "verifier": FakeVerifier(),
        "reviewer": FakeReviewer(),
        "git_repository": FakeGitRepository(),
        "pr_client": FakePRClient(),
        "state_store": store,
    }
    defaults.update(kwargs)
    return MultiAgentOrchestrator(**defaults), store


def test_durable_mode_persists_validated_planner_graph_before_execution(tmp_path):
    planner = FakePlanner()
    coder = FakeCoder()
    runner, store = orchestrator(tmp_path, planner=planner, coder=coder)

    run_id = runner.create_durable_run("Create calculator")
    snapshot = store.load_snapshot(run_id)

    assert snapshot.run.status == RunStatus.PENDING
    assert [task.task_id for task in snapshot.tasks] == ["step-1", "step-2"]
    assert snapshot.tasks[1].spec.dependency_ids == ["step-1"]
    assert all(task.status == TaskStatus.PENDING for task in snapshot.tasks)
    assert coder.calls == []
    assert "Repo Context Pack" in planner.context_pack

    report = runner.run_durable(run_id)

    assert report.status == RunStatus.SUCCEEDED
    assert [call["task_id"] for call in coder.calls] == ["step-1", "step-2"]


def test_durable_verifier_failure_prevents_commit(tmp_path):
    git_repository = FakeGitRepository()
    one_step = {**PLAN, "steps": [PLAN["steps"][0]]}
    runner, _ = orchestrator(
        tmp_path,
        planner=FakePlanner(one_step),
        verifier=FakeVerifier(passed=False),
        git_repository=git_repository,
        commit_changes=True,
    )

    run_id = runner.create_durable_run("Create calculator")
    report = runner.run_durable(run_id)

    assert report.status == RunStatus.FAILED
    assert report.verifier_status == "failed"
    assert git_repository.commits == []


def test_durable_reviewer_rejection_prevents_commit_and_pr(tmp_path):
    git_repository = FakeGitRepository()
    pr_client = FakePRClient()
    one_step = {**PLAN, "steps": [PLAN["steps"][0]]}
    runner, _ = orchestrator(
        tmp_path,
        planner=FakePlanner(one_step),
        reviewer=FakeReviewer(approved=False),
        git_repository=git_repository,
        pr_client=pr_client,
        commit_changes=True,
        open_pr=True,
    )

    run_id = runner.create_durable_run("Create calculator")
    report = runner.run_durable(run_id)

    assert report.status == RunStatus.FAILED
    assert report.reviewer_status == "rejected"
    assert git_repository.commits == []
    assert git_repository.pushes == []
    assert pr_client.calls == []


def test_successful_durable_run_allows_opt_in_commit_and_pr(tmp_path):
    git_repository = FakeGitRepository()
    pr_client = FakePRClient()
    one_step = {**PLAN, "steps": [PLAN["steps"][0]]}
    runner, store = orchestrator(
        tmp_path,
        planner=FakePlanner(one_step),
        git_repository=git_repository,
        pr_client=pr_client,
        create_branch=True,
        branch_name="agentbus/calculator",
        commit_changes=True,
        open_pr=True,
    )

    run_id = runner.create_durable_run("Create calculator")
    report = runner.run_durable(run_id)

    assert report.status == RunStatus.SUCCEEDED
    assert report.commit_identifier == "abc1234"
    assert report.pr_url == "https://github.com/acme/repo/pull/1"
    assert git_repository.created_branches == ["agentbus/calculator"]
    assert git_repository.commits == ["feat: create calculator"]
    assert git_repository.pushes == ["agentbus/calculator"]
    assert store.get_run(run_id).commit_identifier == "abc1234"


def test_ambiguous_pr_outcome_is_not_retried_automatically(tmp_path):
    git_repository = FakeGitRepository()
    pr_client = AmbiguousPRClient()
    one_step = {**PLAN, "steps": [PLAN["steps"][0]]}
    runner, _ = orchestrator(
        tmp_path,
        planner=FakePlanner(one_step),
        git_repository=git_repository,
        pr_client=pr_client,
        commit_changes=True,
        open_pr=True,
    )

    run_id = runner.create_durable_run("Create calculator")
    first = runner.run_durable(run_id)
    second = runner.run_durable(run_id, resume=True)

    assert first.status == RunStatus.SUCCEEDED
    assert second.status == RunStatus.SUCCEEDED
    assert pr_client.calls == 1
    assert "unknown outcome" in second.finalization_error


def test_commit_completed_before_state_crash_is_reconciled_without_duplicate(
    tmp_path, monkeypatch
):
    git_repository = FakeGitRepository()
    one_step = {**PLAN, "steps": [PLAN["steps"][0]]}
    runner, store = orchestrator(
        tmp_path,
        planner=FakePlanner(one_step),
        git_repository=git_repository,
        commit_changes=True,
    )
    run_id = runner.create_durable_run("Create calculator")
    original_update = store.update_run_details
    crash_once = {"pending": True}

    def crash_after_commit(*args, **kwargs):
        if kwargs.get("event_type") == "commit_created" and crash_once["pending"]:
            crash_once["pending"] = False
            raise StateStoreError("simulated state write interruption")
        return original_update(*args, **kwargs)

    monkeypatch.setattr(store, "update_run_details", crash_after_commit)
    with pytest.raises(StateStoreError, match="simulated state write interruption"):
        runner.run_durable(run_id)

    report = runner.run_durable(run_id, resume=True)

    assert report.status == RunStatus.SUCCEEDED
    assert report.commit_identifier == "abc1234"
    assert git_repository.commits == ["feat: create calculator"]
