import pytest

from agentbus.execution.models import TaskStatus
from agentbus.execution.task_graph import TaskGraph, TaskGraphValidationError


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
