from agentbus.agents.coder import CoderAgent
from agentbus.agents.planner import PlannerAgent
from agentbus.agents.reviewer import ReviewerAgent


class FakeModel:
    def __init__(self, output):
        self.output = output
        self.prompts = []

    def generate_json(self, prompt):
        self.prompts.append(prompt)
        return self.output


def test_planner_agent_parses_valid_model_output():
    model = FakeModel(
        {
            "goal": "Create calculator functions",
            "steps": [
                {
                    "id": "step-1",
                    "title": "Add module",
                    "description": "Create calculator.py",
                    "risk": "low",
                }
            ],
            "test_strategy": "Run pytest",
            "done_criteria": ["Tests pass"],
        }
    )
    planner = PlannerAgent(model=model)

    plan = planner.plan("create calculator", file_list="No files found.")

    assert plan["goal"] == "Create calculator functions"
    assert plan["steps"][0]["risk"] == "low"
    assert "dependencies" not in plan["steps"][0]
    assert "done_criteria" not in plan["steps"][0]
    assert "create calculator" in model.prompts[0]


def test_reviewer_agent_parses_valid_model_output():
    model = FakeModel(
        {
            "approved": True,
            "issues": [],
            "summary": "Looks good",
            "required_fixes": [],
        }
    )
    reviewer = ReviewerAgent(model=model)

    review = reviewer.review(
        user_task="create calculator",
        plan={"goal": "calculator", "steps": []},
        git_diff="diff --git a/calculator.py b/calculator.py",
        test_output="1 passed",
    )

    assert review["approved"] is True
    assert review["summary"] == "Looks good"
    assert "1 passed" in model.prompts[0]


def test_coder_preserves_legacy_loop_factory_that_only_accepts_config():
    seen = {}

    class LegacyLoop:
        def __init__(self, config):
            seen["config"] = config

        def run(self, task):
            seen["task"] = task
            return "legacy loop complete"

    coder = CoderAgent(model=FakeModel({}), loop_factory=LegacyLoop)

    result = coder.execute(
        "Complete task",
        {"goal": "Complete", "steps": []},
    )

    assert result == "legacy loop complete"
    assert seen["config"] is coder.config
    assert "Complete task" in seen["task"]


def test_coder_propagates_managed_runtime_identity_to_modern_loop():
    seen = {}
    runtime = object()

    class ManagedLoop:
        def __init__(
            self,
            config,
            model,
            cancellation,
            tool_runtime,
            run_id,
            task_id,
            workspace_trusted,
            provider_consented,
            policy_context,
        ):
            seen.update(locals())

        def run(self, task):
            return "managed loop complete"

    coder = CoderAgent(model=FakeModel({}), loop_factory=ManagedLoop)

    result = coder.execute(
        "Complete task",
        {"goal": "Complete", "steps": []},
        tool_runtime=runtime,
        run_id="run-1",
        task_id="task-1",
        workspace_trusted=True,
        provider_consented=True,
        policy_context={"attempt_number": 1},
    )

    assert result == "managed loop complete"
    assert seen["tool_runtime"] is runtime
    assert seen["run_id"] == "run-1"
    assert seen["task_id"] == "task-1"
    assert seen["policy_context"] == {"attempt_number": 1}
