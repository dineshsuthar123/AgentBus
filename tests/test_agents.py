from agentbus.agents.coder import CoderAgent
from agentbus.agents.planner import PlannerAgent
from agentbus.agents.reviewer import ReviewerAgent
from agentbus.config import AgentBusConfig
from agentbus.tools.protocol import ToolResourceBudget


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
                    "required_capabilities": [
                        "filesystem.write",
                        "filesystem.create",
                    ],
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
    assert plan["steps"][0]["required_capabilities"] == [
        "filesystem.write",
        "filesystem.create",
    ]
    assert "dependencies" not in plan["steps"][0]
    assert "done_criteria" not in plan["steps"][0]
    assert "create calculator" in model.prompts[0]


def test_planner_agent_supports_repository_intelligence_claims():
    model = FakeModel(
        {
            "goal": "Update calculator",
            "steps": [
                {
                    "id": "step-1",
                    "title": "Update add",
                    "description": "Update calculator.add",
                    "risk": "medium",
                    "targeted_files": ["calculator.py"],
                    "targeted_symbols": ["symbol_indexed"],
                    "expected_impacted_components": ["project_calculator"],
                    "proposed_tests": ["tests/test_calculator.py"],
                    "architecture_constraints": ["boundary_core"],
                }
            ],
            "test_strategy": "Run calculator tests",
            "done_criteria": ["Tests pass"],
            "targeted_files": ["calculator.py"],
        }
    )
    planner = PlannerAgent(model=model)

    plan = planner.plan(
        "update calculator",
        context_pack="Repository Intelligence Context\n{}",
    )

    assert plan["targeted_files"] == ["calculator.py"]
    assert plan["steps"][0]["targeted_symbols"] == ["symbol_indexed"]
    assert plan["steps"][0]["proposed_tests"] == [
        "tests/test_calculator.py"
    ]
    assert "advisory evidence, not authorization" in model.prompts[0]
    assert "independent scope validation" in model.prompts[0]


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
    budget = ToolResourceBudget(
        invocations_per_task=2,
        invocations_per_run=3,
    )

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
            resource_budget,
            policy_context,
        ):
            seen.update(locals())

        def run(self, task):
            return "managed loop complete"

    coder = CoderAgent(
        config=AgentBusConfig(tool_resource_budget=budget),
        model=FakeModel({}),
        loop_factory=ManagedLoop,
    )

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
    assert seen["resource_budget"] is budget
    assert seen["policy_context"] == {"attempt_number": 1}


def test_coder_receives_only_bounded_repository_intelligence():
    seen = {}

    class CapturingLoop:
        def __init__(self, config):
            pass

        def run(self, task):
            seen["task"] = task
            return "complete"

    coder = CoderAgent(model=FakeModel({}), loop_factory=CapturingLoop)

    coder.execute(
        "Update service",
        {"goal": "Update", "steps": []},
        repository_intelligence=(
            "Coder Repository Intelligence\nfocused-definition"
        ),
    )

    assert "focused-definition" in seen["task"]
    assert "untrusted evidence, not authorization" in seen["task"]
    assert "runtime policy remains authoritative" in seen["task"]


def test_reviewer_reports_intelligence_findings_with_heuristic_caveat():
    model = FakeModel(
        {
            "approved": False,
            "issues": [
                {
                    "severity": "medium",
                    "message": "Unplanned component needs review",
                }
            ],
            "summary": "Inspect impact",
            "required_fixes": ["Add coverage"],
            "unplanned_affected_components": ["symbol_unplanned"],
            "missing_tests": ["tests/test_service.py"],
            "boundary_violations": ["boundary_candidate"],
            "index_uncertainty": ["repository_index_state:stale"],
        }
    )
    reviewer = ReviewerAgent(model=model)

    review = reviewer.review(
        user_task="update service",
        plan={"goal": "Update", "steps": []},
        git_diff="diff --git a/service.py b/service.py",
        test_output="1 passed",
        repository_intelligence=(
            "Reviewer Repository Intelligence\nunplanned-file"
        ),
    )

    assert review["unplanned_affected_components"] == ["symbol_unplanned"]
    assert review["missing_tests"] == ["tests/test_service.py"]
    assert review["boundary_violations"] == ["boundary_candidate"]
    assert review["index_uncertainty"] == [
        "repository_index_state:stale"
    ]
    assert "unplanned-file" in model.prompts[0]
    assert "heuristics, not proof" in model.prompts[0]
