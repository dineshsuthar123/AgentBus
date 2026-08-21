import pytest

from agentbus.execution.models import TaskStatus
from agentbus.execution.task_graph import (
    PlanContractValidationError,
    TaskGraph,
    TaskGraphValidationError,
)


def planner_output(steps):
    return {
        "goal": "Build feature",
        "steps": steps,
        "test_strategy": "Run pytest",
        "done_criteria": ["Tests pass"],
    }


def step(task_id, *, dependencies=None):
    value = {
        "id": task_id,
        "title": task_id,
        "description": f"Implement {task_id}",
        "risk": "low",
        "execution_kind": "implementation",
        "required_capabilities": ["filesystem.write"],
    }
    if dependencies is not None:
        value["dependencies"] = dependencies
    return value


def test_sequential_graph_created_from_existing_planner_steps():
    graph = TaskGraph.from_planner_output(
        planner_output([step("step-1"), step("step-2"), step("step-3")])
    )

    assert graph.tasks[0].dependency_ids == []
    assert graph.tasks[1].dependency_ids == ["step-1"]
    assert graph.tasks[2].dependency_ids == ["step-2"]


def test_explicit_dependencies_are_preserved():
    graph = TaskGraph.from_planner_output(
        planner_output(
            [
                step("setup", dependencies=[]),
                step("code", dependencies=["setup"]),
                step("docs", dependencies=["setup"]),
                step("finish", dependencies=["code", "docs"]),
            ]
        )
    )

    assert graph.get("finish").dependency_ids == ["code", "docs"]


def test_duplicate_task_ids_are_rejected():
    with pytest.raises(TaskGraphValidationError, match="Duplicate task IDs: step-1"):
        TaskGraph.from_planner_output(
            planner_output([step("step-1"), step("step-1")])
        )


def test_missing_dependencies_are_rejected():
    with pytest.raises(TaskGraphValidationError, match="missing task 'unknown'"):
        TaskGraph.from_planner_output(
            planner_output([step("step-1", dependencies=["unknown"])])
        )


def test_dependency_cycles_are_rejected_with_path():
    with pytest.raises(TaskGraphValidationError, match="Dependency cycle detected"):
        TaskGraph.from_planner_output(
            planner_output(
                [
                    step("a", dependencies=["b"]),
                    step("b", dependencies=["a"]),
                ]
            )
        )


def test_ready_tasks_require_successful_dependencies():
    graph = TaskGraph.from_planner_output(
        planner_output([step("a"), step("b"), step("c")])
    )

    assert [task.task_id for task in graph.ready_tasks({})] == ["a"]
    statuses = {
        "a": TaskStatus.SUCCEEDED,
        "b": TaskStatus.PENDING,
        "c": TaskStatus.PENDING,
    }
    assert [task.task_id for task in graph.ready_tasks(statuses)] == ["b"]


def test_failed_dependency_identifies_blocked_task():
    graph = TaskGraph.from_planner_output(
        planner_output([step("a"), step("b"), step("c")])
    )
    statuses = {
        "a": TaskStatus.FAILED,
        "b": TaskStatus.PENDING,
        "c": TaskStatus.PENDING,
    }

    assert [task.task_id for task in graph.blocked_tasks(statuses)] == ["b"]


def test_graph_serialization_round_trip():
    graph = TaskGraph.from_planner_output(
        planner_output(
            [step("a", dependencies=[]), step("b", dependencies=["a"])]
        )
    )

    restored = TaskGraph.from_dict(graph.to_dict())

    assert restored.to_dict() == graph.to_dict()


def test_planner_capability_requirements_persist_in_task_metadata():
    planned = step("write", dependencies=[])
    planned["required_capabilities"] = [
        "filesystem.write",
        "filesystem.create",
    ]

    graph = TaskGraph.from_planner_output(planner_output([planned]))
    restored = TaskGraph.from_dict(graph.to_dict())

    assert restored.tasks[0].metadata["required_capabilities"] == [
        "filesystem.write",
        "filesystem.create",
    ]
    assert restored.tasks[0].metadata["execution_kind"] == "implementation"


def test_read_only_implementation_step_is_rejected_before_persistence():
    planned = step("inspect", dependencies=[])
    planned["required_capabilities"] = ["filesystem.read"]

    with pytest.raises(PlanContractValidationError) as captured:
        TaskGraph.from_planner_output(planner_output([planned]))

    assert {
        (issue.task_id, issue.code) for issue in captured.value.issues
    } == {("inspect", "implementation_without_mutation")}


def test_analysis_step_cannot_mutate_or_authorize_a_downstream_task():
    analysis = step("inspect", dependencies=[])
    analysis.update(
        {
            "execution_kind": "analysis",
            "required_capabilities": ["filesystem.read", "filesystem.write"],
        }
    )
    implementation = step("implement", dependencies=["inspect"])

    with pytest.raises(PlanContractValidationError) as captured:
        TaskGraph.from_planner_output(
            planner_output([analysis, implementation])
        )

    assert {
        issue.code for issue in captured.value.issues
    } == {"analysis_with_mutation", "analysis_prerequisite_unsupported"}


def test_terminal_analysis_task_has_explicit_read_only_contract():
    analysis = step("inspect", dependencies=[])
    analysis.update(
        {
            "execution_kind": "analysis",
            "required_capabilities": ["filesystem.read", "git.read"],
        }
    )

    graph = TaskGraph.from_planner_output(planner_output([analysis]))

    assert graph.tasks[0].execution_kind.value == "analysis"
    assert graph.tasks[0].metadata["required_capabilities"] == [
        "filesystem.read",
        "git.read",
    ]


def test_analysis_task_rejects_indirect_side_effect_capabilities():
    analysis = step("inspect", dependencies=[])
    analysis.update(
        {
            "execution_kind": "analysis",
            "required_capabilities": [
                "filesystem.read",
                "process.execute",
            ],
        }
    )

    with pytest.raises(PlanContractValidationError) as captured:
        TaskGraph.from_planner_output(planner_output([analysis]))

    assert {
        issue.code for issue in captured.value.issues
    } == {"analysis_with_side_effect_capability"}


def test_genuine_multi_step_implementation_slices_remain_supported():
    storage = step("storage", dependencies=[])
    storage["expected_outputs"] = ["storage.py", "test_storage.py"]
    webhook = step("webhook", dependencies=["storage"])
    webhook["expected_outputs"] = ["webhook.py", "test_webhook.py"]

    graph = TaskGraph.from_planner_output(
        planner_output([storage, webhook])
    )

    assert [task.task_id for task in graph.tasks] == ["storage", "webhook"]
    assert graph.get("webhook").dependency_ids == ["storage"]


def test_repository_intelligence_claims_persist_in_task_metadata():
    planned = step("write", dependencies=[])
    planned.update(
        {
            "targeted_files": ["src/service.py"],
            "targeted_symbols": ["symbol_service"],
            "expected_impacted_components": ["project_service"],
            "proposed_tests": ["tests/test_service.py"],
            "architecture_constraints": ["boundary_service"],
        }
    )
    output = planner_output([planned])
    output.update(
        {
            "intelligence_snapshot_id": "snapshot_123",
            "intelligence_context_hash": "a" * 64,
            "intelligence_warnings": ["index.stale"],
            "intelligence_scope_validated": True,
        }
    )

    task = TaskGraph.from_planner_output(output).tasks[0]

    assert task.metadata["targeted_files"] == ["src/service.py"]
    assert task.metadata["targeted_symbols"] == ["symbol_service"]
    assert task.metadata["expected_impacted_components"] == [
        "project_service"
    ]
    assert task.metadata["proposed_tests"] == ["tests/test_service.py"]
    assert task.metadata["architecture_constraints"] == ["boundary_service"]
    assert task.metadata["intelligence_snapshot_id"] == "snapshot_123"
    assert task.metadata["intelligence_context_hash"] == "a" * 64
    assert task.metadata["intelligence_warnings"] == ["index.stale"]
    assert task.metadata["intelligence_scope_validated"] is True
