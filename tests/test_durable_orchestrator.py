import threading

import pytest

from agentbus.config import AgentBusConfig
from agentbus.execution.cancellation import CancellationToken
from agentbus.execution.cancellation_registry import CancellationRegistry
from agentbus.execution.engine import DurableExecutionEngine
from agentbus.execution.models import RunStatus, TaskStatus
from agentbus.execution.models import FailureCategory
from agentbus.execution.state_store import StateStore, StateStoreError
from agentbus.models.errors import ModelAuthenticationError
from agentbus.runtime.orchestrator import MultiAgentOrchestrator
from agentbus.tools.protocol import ToolResourceBudget


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


class FailingProviderCoder(FakeCoder):
    def execute(self, user_task, plan, reviewer_feedback=None):
        super().execute(user_task, plan, reviewer_feedback)
        raise ModelAuthenticationError(
            "Azure OpenAI authentication failed.",
            provider="azure",
            model="coder-deployment",
            request_id="safe-request-id",
            metadata={"api_key": "must-not-persist"},
        )


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

    def review_task(self, **kwargs):
        return {
            "approved": True,
            "issues": [],
            "summary": "Current task approved",
            "required_fixes": [],
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

    def worktree_snapshot(self):
        return {}

    def changed_since(self, snapshot):
        return self.changed_files()

    def full_diff(self, max_chars=30_000, paths=None):
        return "fake target repository diff"

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
    (tmp_path / "workspace").mkdir(exist_ok=True)
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
    assert snapshot.run.metadata["tool_runtime"]["resource_budget"] == (
        runner.config.tool_resource_budget.model_dump(mode="json")
    )

    report = runner.run_durable(run_id)

    assert report.status == RunStatus.SUCCEEDED
    assert [call["task_id"] for call in coder.calls] == ["step-1", "step-2"]


def test_durable_run_persists_custom_tool_budget_for_resume(tmp_path):
    budget = ToolResourceBudget(
        invocations_per_task=2,
        invocations_per_run=3,
    )
    settings = config(tmp_path).with_overrides(tool_resource_budget=budget)
    runner, store = orchestrator(tmp_path, config=settings)

    run_id = runner.create_durable_run("Create calculator")

    persisted = store.get_run(run_id)
    assert persisted.metadata["tool_runtime"]["resource_budget"] == (
        budget.model_dump(mode="json")
    )


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
    assert report.successful_tasks == ["step-1"]
    assert report.failed_tasks == []
    attempt = runner.state_store.list_attempts(run_id, "step-1")[0]
    assert attempt.status.value == "succeeded"
    assert report.changed_files == ["calculator.py", "tests/test_calculator.py"]
    assert report.side_effects_persisted is True
    store_artifacts = runner.state_store.load_snapshot(run_id).artifacts
    assert store_artifacts
    assert {artifact.identifier for artifact in store_artifacts} == {
        "calculator.py",
        "tests/test_calculator.py",
    }
    assert git_repository.commits == []
    assert git_repository.pushes == []
    assert pr_client.calls == []


def test_task_reviews_are_scoped_and_final_review_runs_last(tmp_path):
    timeline = []

    class TimelineCoder(FakeCoder):
        def execute(self, user_task, plan, reviewer_feedback=None):
            timeline.append(f"coder:{plan['steps'][0]['id']}")
            return super().execute(user_task, plan, reviewer_feedback)

    class TimelineVerifier(FakeVerifier):
        def verify(self, **kwargs):
            timeline.append("verify")
            if kwargs.get("invocation_key") == "final-run":
                assert kwargs["run_id"]
                assert kwargs["task_id"] == "step-2"
                assert kwargs["tool_runtime"].workspace == (
                    tmp_path / "workspace"
                ).resolve()
            return super().verify()

    class TimelineReviewer(FakeReviewer):
        def review_task(self, **kwargs):
            task_id = kwargs["task_spec"]["id"]
            timeline.append(f"task-review:{task_id}")
            assert len(kwargs["task_spec"].get("dependencies", [])) <= 1
            return super().review_task(**kwargs)

        def review(self, user_task, plan, git_diff, test_output=None):
            timeline.append("final-review")
            return super().review(user_task, plan, git_diff, test_output)

    runner, _ = orchestrator(
        tmp_path,
        coder=TimelineCoder(),
        verifier=TimelineVerifier(),
        reviewer=TimelineReviewer(),
    )

    report = runner.run_durable(runner.create_durable_run("Create calculator"))

    assert report.status == RunStatus.SUCCEEDED
    assert timeline == [
        "coder:step-1",
        "verify",
        "task-review:step-1",
        "coder:step-2",
        "verify",
        "task-review:step-2",
        "verify",
        "final-review",
    ]


def test_resume_reconciles_persisted_final_review_without_rerunning_it(
    tmp_path,
    monkeypatch,
):
    reviewer = FakeReviewer()
    verifier = FakeVerifier()
    one_step = {**PLAN, "steps": [PLAN["steps"][0]]}
    runner, store = orchestrator(
        tmp_path,
        planner=FakePlanner(one_step),
        reviewer=reviewer,
        verifier=verifier,
    )
    run_id = runner.create_durable_run("Create calculator")
    original_update = store.update_run_status
    crash_once = {"pending": True}

    def crash_before_final_status(*args, **kwargs):
        if kwargs.get("event_type") == "durable_run_succeeded" and crash_once["pending"]:
            crash_once["pending"] = False
            raise StateStoreError("simulated final status interruption")
        return original_update(*args, **kwargs)

    monkeypatch.setattr(store, "update_run_status", crash_before_final_status)
    with pytest.raises(StateStoreError, match="simulated final status interruption"):
        runner.run_durable(run_id)

    review_calls = reviewer.calls
    verifier_calls = verifier.calls
    report = runner.resume_durable(run_id)

    assert report.status == RunStatus.SUCCEEDED
    assert reviewer.calls == review_calls
    assert verifier.calls == verifier_calls
    assert report.successful_tasks == ["step-1"]


def test_durable_provider_failure_is_classified_and_prevents_git_finalization(
    tmp_path,
):
    git_repository = FakeGitRepository()
    pr_client = FakePRClient()
    coder = FailingProviderCoder()
    one_step = {**PLAN, "steps": [PLAN["steps"][0]]}
    runner, store = orchestrator(
        tmp_path,
        planner=FakePlanner(one_step),
        coder=coder,
        git_repository=git_repository,
        pr_client=pr_client,
        commit_changes=True,
        open_pr=True,
    )

    run_id = runner.create_durable_run("Create calculator")
    report = runner.run_durable(run_id)
    attempt = store.list_attempts(run_id, "step-1")[0]

    assert report.status == RunStatus.FAILED
    assert attempt.error_category == FailureCategory.POLICY_VIOLATION
    assert attempt.metadata["provider_failure"]["provider"] == "azure"
    assert attempt.metadata["provider_failure"]["model"] == "coder-deployment"
    assert "must-not-persist" not in str(attempt.metadata)
    assert len(coder.calls) == 1
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


def test_cancellation_during_final_review_preserves_successful_task_history(
    tmp_path,
):
    settings = config(tmp_path)
    store = StateStore(settings.state_database_path)
    registry = CancellationRegistry(store)
    token = CancellationToken()
    registry.register("final-review-cancel", token)
    review_started = threading.Event()
    cancellation_observed = threading.Event()
    release_review = threading.Event()
    git_repository = FakeGitRepository()

    class BlockingFinalReviewer(FakeReviewer):
        def review(self, user_task, plan, git_diff, test_output=None):
            with token.operation(
                "deterministic.generate_json",
                source="provider:deterministic",
                interruptible=True,
                provider="deterministic",
            ):
                review_started.set()
                assert token.wait(timeout_seconds=5)
                cancellation_observed.set()
                assert release_review.wait(timeout=5)
                token.checkpoint(
                    "provider:deterministic",
                    stage="final-review",
                    provider="deterministic",
                )
            raise AssertionError("Final reviewer must be interrupted")

    one_step = {**PLAN, "steps": [PLAN["steps"][0]]}
    runner = MultiAgentOrchestrator(
        config=settings,
        planner=FakePlanner(one_step),
        coder=FakeCoder(),
        verifier=FakeVerifier(),
        reviewer=BlockingFinalReviewer(),
        git_repository=git_repository,
        pr_client=FakePRClient(),
        state_store=store,
        commit_changes=True,
        cancellation=token,
        cancellation_registry=registry,
    )
    run_id = runner.create_durable_run(
        "Create calculator",
        run_id="final-review-cancel",
    )
    reports = []
    thread = threading.Thread(
        target=lambda: reports.append(runner.run_durable(run_id))
    )
    thread.start()
    assert review_started.wait(timeout=5)

    requested = DurableExecutionEngine(
        store,
        cancellation_registry=registry,
    ).request_cancellation(run_id, "stop final review")
    assert cancellation_observed.wait(timeout=5)

    assert requested.status == RunStatus.WAITING_FOR_REVIEW
    assert store.get_task(run_id, "step-1").status == TaskStatus.SUCCEEDED
    release_review.set()
    thread.join(timeout=5)

    assert thread.is_alive() is False
    assert reports[0].status == RunStatus.CANCELLED
    assert reports[0].successful_tasks == ["step-1"]
    assert store.list_attempts(run_id, "step-1")[0].status.value == "succeeded"
    assert git_repository.commits == []
    assert store.get_run(run_id).commit_identifier is None
