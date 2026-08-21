from agentbus.agents.coder import CoderAgent
from agentbus.agents.planner import PlannerAgent
from agentbus.agents.reviewer import ReviewerAgent
from agentbus.config import AgentBusConfig
from agentbus.execution.task_graph import TaskGraph
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
                    "execution_kind": "implementation",
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
    assert plan["steps"][0]["execution_kind"] == "implementation"
    assert "dependencies" not in plan["steps"][0]
    assert "done_criteria" not in plan["steps"][0]
    assert "create calculator" in model.prompts[0]
    assert "A durable step is an independently executable" in model.prompts[0]
    assert "BAD durable decomposition" in model.prompts[0]
    assert "GOOD atomic durable task" in model.prompts[0]


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
                    "execution_kind": "implementation",
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


def test_planner_contract_feedback_is_bounded_and_does_not_grant_capabilities():
    model = FakeModel(
        {
            "goal": "Fix calculator",
            "steps": [
                {
                    "id": "step-1",
                    "title": "Fix divide",
                    "description": "Fix and verify divide in one work unit.",
                    "risk": "low",
                    "execution_kind": "implementation",
                    "required_capabilities": [
                        "filesystem.read",
                        "filesystem.write",
                    ],
                }
            ],
            "test_strategy": "Run calculator tests",
            "done_criteria": ["Calculator tests pass"],
        }
    )
    planner = PlannerAgent(model=model)

    planner.plan(
        "fix divide",
        file_list="calculator.py",
        contract_feedback=[
            "step-1 [implementation_without_mutation]: declare a coherent work unit",
        ],
    )

    prompt = model.prompts[0]
    assert "previous durable plan was rejected" in prompt
    assert "Do not add capabilities unless" in prompt
    assert "implementation_without_mutation" in prompt


def test_atomic_calculator_fix_is_one_verifiable_implementation_task():
    model = FakeModel(
        {
            "goal": "Fix division by zero without changing normal division",
            "steps": [
                {
                    "id": "step-1",
                    "title": "Fix and verify divide",
                    "description": (
                        "Inspect the calculator and tests, update divide, run the "
                        "relevant tests, and inspect the resulting diff."
                    ),
                    "risk": "low",
                    "execution_kind": "implementation",
                    "required_capabilities": [
                        "filesystem.read",
                        "filesystem.write",
                        "test.execute",
                        "process.execute",
                        "git.read",
                    ],
                    "expected_outputs": ["calculator.py"],
                    "done_criteria": ["Both calculator tests pass"],
                }
            ],
            "test_strategy": "Run the existing calculator tests",
            "done_criteria": ["Both calculator tests pass"],
        }
    )
    planner = PlannerAgent(model=model)

    plan = planner.plan(
        "Fix divide() so division by zero raises ValueError with a clear message. "
        "Preserve normal division behavior. Make the existing tests pass. "
        "Do not modify unrelated files.",
        file_list="calculator.py\ntest_calculator.py",
    )
    graph = TaskGraph.from_planner_output(plan)

    assert len(plan["steps"]) == 1
    assert len(graph.tasks) == 1
    assert graph.tasks[0].execution_kind.value == "implementation"
    assert graph.tasks[0].done_criteria == ["Both calculator tests pass"]
    assert graph.tasks[0].metadata["required_capabilities"] == [
        "filesystem.read",
        "filesystem.write",
        "test.execute",
        "process.execute",
        "git.read",
    ]
    assert "standalone inspect" in model.prompts[0]
    assert "prefer one implementation step" in model.prompts[0]


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


def test_reviewer_receives_analysis_artifacts_and_not_applicable_verification():
    model = FakeModel(
        {
            "approved": True,
            "issues": [],
            "summary": "Analysis is complete",
            "required_fixes": [],
        }
    )
    reviewer = ReviewerAgent(model=model)
    artifact = {
        "task_id": "step-1",
        "identifier": "analysis:abc123",
        "summary": "calculator uses direct arithmetic",
        "truncated": False,
    }

    reviewer.review(
        user_task="inspect calculator",
        plan={"goal": "inspect", "steps": []},
        git_diff="",
        analysis_artifacts=[artifact],
    )
    reviewer.review_task(
        original_task="inspect calculator",
        task_spec={
            "id": "step-1",
            "execution_kind": "analysis",
            "done_criteria": ["analysis available"],
        },
        expected_outputs=[],
        artifacts=["analysis:abc123"],
        task_diff="No repository changes.",
        coder_summary=artifact["summary"],
        verifier_result={
            "passed": True,
            "status": "not_applicable",
            "skipped": True,
        },
    )

    assert "Persisted analysis artifacts:" in model.prompts[0]
    assert "calculator uses direct arithmetic" in model.prompts[0]
    assert 'execution_kind="analysis"' in model.prompts[1]
    assert "code verifier is not applicable" in model.prompts[1]
    assert '"status": "not_applicable"' in model.prompts[1]


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


def test_coder_scopes_overall_request_to_current_durable_task():
    seen = {}

    class CapturingLoop:
        def __init__(self, config):
            pass

        def run(self, task):
            seen["task"] = task
            return "current task complete"

    coder = CoderAgent(model=FakeModel({}), loop_factory=CapturingLoop)
    coder.execute(
        "Fix calculator, then update downstream documentation.",
        {
            "goal": "Fix calculator",
            "steps": [
                {
                    "id": "step-1",
                    "title": "Fix calculator",
                    "execution_kind": "implementation",
                    "required_capabilities": [
                        "filesystem.read",
                        "filesystem.write",
                    ],
                    "done_criteria": ["Calculator tests pass"],
                }
            ],
        },
    )

    prompt = seen["task"]
    assert "Execute ONLY the current durable task" in prompt
    assert "Overall request context:" in prompt
    assert "Current durable task plan:" in prompt
    assert "Original user task:" not in prompt
    assert "does not authorize work" in prompt
    assert "Do not perform downstream work" in prompt
    assert "required_capabilities are an upper bound" in prompt
    assert "Do not request capabilities beyond" in prompt
    assert "plan contract is insufficient" in prompt


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
