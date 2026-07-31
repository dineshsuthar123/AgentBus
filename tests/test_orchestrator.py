import json

from agentbus.config import AgentBusConfig
from agentbus.runtime.intelligence import (
    PlannerIntelligenceContext,
    StaticPlannerIntelligenceSource,
)
from agentbus.runtime.orchestrator import MultiAgentOrchestrator


PLAN = {
    "goal": "Create calculator",
    "steps": [
        {
            "id": "step-1",
            "title": "Implement",
            "description": "Create files",
            "risk": "low",
        }
    ],
    "test_strategy": "Run pytest",
    "done_criteria": ["Tests pass"],
}


class FakePlanner:
    def __init__(self):
        self.context_pack = None

    def plan(self, user_task, file_list=None, context_pack=None):
        self.context_pack = context_pack
        return PLAN


class FakeCoder:
    def __init__(self):
        self.calls = []

    def execute(self, user_task, plan, reviewer_feedback=None):
        self.calls.append(
            {
                "user_task": user_task,
                "plan": plan,
                "reviewer_feedback": reviewer_feedback,
            }
        )
        return f"coded attempt {len(self.calls)}"


class FakeVerifier:
    def __init__(self, passed=True):
        self.calls = 0
        self.passed = passed

    def verify(self):
        self.calls += 1
        return {
            "command": ["python", "-m", "pytest"],
            "exit_code": 0 if self.passed else 1,
            "passed": self.passed,
            "output": f"{self.calls} passed",
        }


class FakeReviewer:
    def __init__(self, reviews):
        self.reviews = list(reviews)
        self.calls = []

    def review(self, user_task, plan, git_diff, test_output=None):
        self.calls.append(
            {
                "user_task": user_task,
                "plan": plan,
                "git_diff": git_diff,
                "test_output": test_output,
            }
        )
        return self.reviews.pop(0)


class FakeGitRepository:
    def __init__(self):
        self.commits = []
        self.created_branches = []

    def is_git_repo(self):
        return True

    def current_branch(self):
        return "main"

    def has_uncommitted_changes(self):
        return False

    def create_branch(self, branch_name):
        self.created_branches.append(branch_name)
        return f"Created branch: {branch_name}"

    def changed_files(self):
        return ["calculator.py", "tests/test_calculator.py"]

    def commit(self, message):
        self.commits.append(message)
        return "abc1234"

    def push_branch(self, branch_name=None):
        return f"Pushed branch: {branch_name or 'main'}"


def config(tmp_path):
    return AgentBusConfig(
        workspace_dir=str(tmp_path / "workspace"),
        runs_dir=str(tmp_path / "runs"),
    )


def log_events(tmp_path):
    log_file = next((tmp_path / "runs").glob("*.jsonl"))
    return [
        json.loads(line)["type"]
        for line in log_file.read_text(encoding="utf-8").splitlines()
    ]


def test_orchestrator_happy_path_uses_agents_and_logs(tmp_path):
    coder = FakeCoder()
    planner = FakePlanner()
    reviewer = FakeReviewer(
        [
            {
                "approved": True,
                "issues": [],
                "summary": "Approved",
                "required_fixes": [],
            }
        ]
    )
    orchestrator = MultiAgentOrchestrator(
        config=config(tmp_path),
        planner=planner,
        coder=coder,
        reviewer=reviewer,
        verifier=FakeVerifier(),
    )

    result = orchestrator.run("create calculator")

    assert result.approved is True
    assert result.retry_performed is False
    assert planner.context_pack is not None
    assert "Repo Context Pack" in planner.context_pack
    assert len(coder.calls) == 1
    assert "Approved by reviewer" in result.final_summary
    assert "planner_started" in log_events(tmp_path)
    assert "repo_scan_started" in log_events(tmp_path)
    assert "test_command_detected" in log_events(tmp_path)
    assert "context_pack_created" in log_events(tmp_path)
    assert "orchestration_finished" in log_events(tmp_path)


def test_orchestrator_validates_repository_intelligence_before_execution(
    tmp_path,
):
    planner = FakePlanner()
    coder = FakeCoder()
    intelligence = PlannerIntelligenceContext(
        risk_areas=("calculator.py",),
    )
    orchestrator = MultiAgentOrchestrator(
        config=config(tmp_path),
        planner=planner,
        coder=coder,
        reviewer=FakeReviewer(
            [
                {
                    "approved": True,
                    "issues": [],
                    "summary": "Approved",
                    "required_fixes": [],
                }
            ]
        ),
        verifier=FakeVerifier(),
        intelligence_source=StaticPlannerIntelligenceSource(intelligence),
    )

    result = orchestrator.run("create calculator")

    assert "Repository Intelligence Context" in planner.context_pack
    assert "calculator.py" in planner.context_pack
    assert result.plan["intelligence_context_hash"] == intelligence.context_hash
    assert result.plan["intelligence_scope_validated"] is True
    assert coder.calls[0]["plan"]["intelligence_scope_validated"] is True
    assert "planner_intelligence_context" in log_events(tmp_path)
    assert "planner_intelligence_validated" in log_events(tmp_path)


def test_orchestrator_does_not_trust_unvalidated_planner_metadata(tmp_path):
    unsafe_plan = {
        **PLAN,
        "targeted_files": ["unvalidated.py"],
        "intelligence_context_hash": "f" * 64,
        "intelligence_scope_validated": True,
        "steps": [
            {
                **PLAN["steps"][0],
                "targeted_symbols": ["symbol_unvalidated"],
            }
        ],
    }
    planner = FakePlanner()
    planner.plan = lambda *args, **kwargs: unsafe_plan
    orchestrator = MultiAgentOrchestrator(
        config=config(tmp_path),
        planner=planner,
        coder=FakeCoder(),
        reviewer=FakeReviewer(
            [
                {
                    "approved": True,
                    "issues": [],
                    "summary": "Approved",
                    "required_fixes": [],
                }
            ]
        ),
        verifier=FakeVerifier(),
    )

    result = orchestrator.run("create calculator")

    assert "intelligence_context_hash" not in result.plan
    assert "intelligence_scope_validated" not in result.plan
    assert "targeted_files" not in result.plan
    assert "targeted_symbols" not in result.plan["steps"][0]


def test_orchestrator_reviewer_rejection_triggers_one_retry(tmp_path):
    coder = FakeCoder()
    verifier = FakeVerifier()
    reviewer = FakeReviewer(
        [
            {
                "approved": False,
                "issues": [{"severity": "medium", "message": "Missing tests"}],
                "summary": "Needs tests",
                "required_fixes": ["Add tests"],
            },
            {
                "approved": True,
                "issues": [],
                "summary": "Fixed",
                "required_fixes": [],
            },
        ]
    )
    orchestrator = MultiAgentOrchestrator(
        config=config(tmp_path),
        planner=FakePlanner(),
        coder=coder,
        reviewer=reviewer,
        verifier=verifier,
    )

    result = orchestrator.run("create calculator")

    assert result.approved is True
    assert result.retry_performed is True
    assert len(coder.calls) == 2
    assert coder.calls[1]["reviewer_feedback"]["required_fixes"] == ["Add tests"]
    assert verifier.calls == 2
    assert "retry_started" in log_events(tmp_path)


def test_orchestrator_does_not_commit_if_verifier_fails(tmp_path):
    git_repo = FakeGitRepository()
    reviewer = FakeReviewer(
        [
            {
                "approved": True,
                "issues": [],
                "summary": "Approved",
                "required_fixes": [],
            }
        ]
    )
    orchestrator = MultiAgentOrchestrator(
        config=config(tmp_path),
        planner=FakePlanner(),
        coder=FakeCoder(),
        reviewer=reviewer,
        verifier=FakeVerifier(passed=False),
        git_repository=git_repo,
        commit_changes=True,
    )

    result = orchestrator.run("create calculator")

    assert result.approved is True
    assert result.commit_hash is None
    assert git_repo.commits == []


def test_orchestrator_commits_only_after_reviewer_approval(tmp_path):
    git_repo = FakeGitRepository()
    reviewer = FakeReviewer(
        [
            {
                "approved": True,
                "issues": [],
                "summary": "Approved",
                "required_fixes": [],
            }
        ]
    )
    orchestrator = MultiAgentOrchestrator(
        config=config(tmp_path),
        planner=FakePlanner(),
        coder=FakeCoder(),
        reviewer=reviewer,
        verifier=FakeVerifier(),
        git_repository=git_repo,
        create_branch=True,
        branch_name="agentbus/add-tests",
        commit_changes=True,
    )

    result = orchestrator.run("Add calculator tests")

    assert result.git_branch == "agentbus/add-tests"
    assert result.changed_files == ["calculator.py", "tests/test_calculator.py"]
    assert result.commit_hash == "abc1234"
    assert git_repo.created_branches == ["agentbus/add-tests"]
    assert git_repo.commits == ["feat: add calculator tests"]
