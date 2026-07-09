import json

from agentbus.config import AgentBusConfig
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
    def __init__(self):
        self.calls = 0

    def verify(self):
        self.calls += 1
        return {
            "command": ["python", "-m", "pytest"],
            "exit_code": 0,
            "passed": True,
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
