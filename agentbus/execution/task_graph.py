from __future__ import annotations

from typing import Any, Iterable

from agentbus.execution.models import (
    RiskLevel,
    TaskDependency,
    TaskSpec,
    TaskStatus,
)


class TaskGraphValidationError(ValueError):
    """Raised when planner output cannot form a safe deterministic graph."""


FAILED_DEPENDENCY_STATUSES = {
    TaskStatus.FAILED,
    TaskStatus.REJECTED,
    TaskStatus.BLOCKED,
    TaskStatus.CANCELLED,
    TaskStatus.INTEGRATION_CONFLICT,
}


class TaskGraph:
    VERSION = 1

    def __init__(self, tasks: Iterable[TaskSpec]):
        self.tasks = list(tasks)
        self._by_id: dict[str, TaskSpec] = {}
        self._validate()

    @classmethod
    def from_planner_output(cls, planner_output: dict[str, Any]) -> "TaskGraph":
        raw_steps = planner_output.get("steps")
        if not isinstance(raw_steps, list) or not raw_steps:
            raise TaskGraphValidationError(
                "Planner output must contain at least one step in 'steps'."
            )

        explicit_dependencies = any(
            isinstance(step, dict) and "dependencies" in step for step in raw_steps
        )
        overall_done = planner_output.get("done_criteria", [])
        tasks: list[TaskSpec] = []

        for index, raw_step in enumerate(raw_steps):
            if not isinstance(raw_step, dict):
                raise TaskGraphValidationError(
                    f"Planner step at index {index} must be an object."
                )

            task_id = str(raw_step.get("id") or f"step-{index + 1}").strip()
            if not task_id:
                raise TaskGraphValidationError(
                    f"Planner step at index {index} has an empty task ID."
                )

            if explicit_dependencies:
                raw_dependencies = raw_step.get("dependencies", [])
            else:
                raw_dependencies = [] if index == 0 else [tasks[index - 1].task_id]

            dependencies = cls._parse_dependencies(task_id, raw_dependencies)
            try:
                metadata = {
                    **dict(raw_step.get("metadata", {})),
                    "planner_index": index,
                    "required_capabilities": list(
                        raw_step.get("required_capabilities") or []
                    ),
                }
                for field_name in (
                    "targeted_files",
                    "targeted_symbols",
                    "expected_impacted_components",
                    "proposed_tests",
                    "architecture_constraints",
                ):
                    if raw_step.get(field_name) is not None:
                        metadata[field_name] = list(raw_step[field_name])
                for field_name in (
                    "intelligence_snapshot_id",
                    "intelligence_context_hash",
                    "intelligence_warnings",
                    "intelligence_scope_validated",
                ):
                    if planner_output.get(field_name) is not None:
                        value = planner_output[field_name]
                        metadata[field_name] = (
                            list(value)
                            if field_name == "intelligence_warnings"
                            else value
                        )
                task = TaskSpec(
                    task_id=task_id,
                    title=str(raw_step.get("title") or task_id),
                    description=str(
                        raw_step.get("description") or raw_step.get("title") or task_id
                    ),
                    dependencies=dependencies,
                    assigned_role=str(raw_step.get("assigned_role") or "coder"),
                    risk=RiskLevel(raw_step.get("risk", RiskLevel.LOW.value)),
                    maximum_attempts=int(raw_step.get("maximum_attempts", 2)),
                    expected_outputs=list(raw_step.get("expected_outputs", [])),
                    done_criteria=list(raw_step.get("done_criteria", overall_done)),
                    metadata=metadata,
                )
            except (TypeError, ValueError) as exc:
                raise TaskGraphValidationError(
                    f"Planner step '{task_id}' is invalid: {exc}"
                ) from exc
            tasks.append(task)

        return cls(tasks)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "TaskGraph":
        if data.get("version") != cls.VERSION:
            raise TaskGraphValidationError(
                f"Unsupported task graph version: {data.get('version')!r}."
            )
        raw_tasks = data.get("tasks")
        if not isinstance(raw_tasks, list):
            raise TaskGraphValidationError("Serialized task graph is missing 'tasks'.")
        try:
            return cls(TaskSpec.model_validate(task) for task in raw_tasks)
        except (TypeError, ValueError) as exc:
            if isinstance(exc, TaskGraphValidationError):
                raise
            raise TaskGraphValidationError(f"Serialized task graph is invalid: {exc}") from exc

    @staticmethod
    def _parse_dependencies(
        task_id: str, raw_dependencies: Any
    ) -> list[TaskDependency]:
        if not isinstance(raw_dependencies, list):
            raise TaskGraphValidationError(
                f"Task '{task_id}' dependencies must be a list."
            )
        dependencies: list[TaskDependency] = []
        for dependency in raw_dependencies:
            if isinstance(dependency, str):
                dependencies.append(TaskDependency(task_id=dependency))
            elif isinstance(dependency, dict):
                try:
                    dependencies.append(TaskDependency.model_validate(dependency))
                except ValueError as exc:
                    raise TaskGraphValidationError(
                        f"Task '{task_id}' has an invalid dependency: {exc}"
                    ) from exc
            else:
                raise TaskGraphValidationError(
                    f"Task '{task_id}' dependency values must be IDs or objects."
                )
        return dependencies

    def _validate(self) -> None:
        if not self.tasks:
            raise TaskGraphValidationError("Task graph must contain at least one task.")

        duplicates: list[str] = []
        for task in self.tasks:
            if task.task_id in self._by_id:
                duplicates.append(task.task_id)
            self._by_id[task.task_id] = task
        if duplicates:
            duplicate_list = ", ".join(sorted(set(duplicates)))
            raise TaskGraphValidationError(f"Duplicate task IDs: {duplicate_list}.")

        for task in self.tasks:
            for dependency_id in task.dependency_ids:
                if dependency_id not in self._by_id:
                    raise TaskGraphValidationError(
                        f"Task '{task.task_id}' depends on missing task "
                        f"'{dependency_id}'."
                    )
                if dependency_id == task.task_id:
                    raise TaskGraphValidationError(
                        f"Task '{task.task_id}' cannot depend on itself."
                    )

        self._validate_acyclic()

    def _validate_acyclic(self) -> None:
        visiting: set[str] = set()
        visited: set[str] = set()
        path: list[str] = []

        def visit(task_id: str) -> None:
            if task_id in visited:
                return
            if task_id in visiting:
                start = path.index(task_id)
                cycle = path[start:] + [task_id]
                raise TaskGraphValidationError(
                    f"Dependency cycle detected: {' -> '.join(cycle)}."
                )
            visiting.add(task_id)
            path.append(task_id)
            for dependency_id in self._by_id[task_id].dependency_ids:
                visit(dependency_id)
            path.pop()
            visiting.remove(task_id)
            visited.add(task_id)

        for task in self.tasks:
            visit(task.task_id)

    def get(self, task_id: str) -> TaskSpec:
        try:
            return self._by_id[task_id]
        except KeyError as exc:
            raise KeyError(f"Unknown task ID: {task_id}") from exc

    def ready_tasks(self, statuses: dict[str, TaskStatus]) -> list[TaskSpec]:
        candidates = {TaskStatus.PENDING, TaskStatus.READY, TaskStatus.RETRYABLE}
        return [
            task
            for task in self.tasks
            if statuses.get(task.task_id, TaskStatus.PENDING) in candidates
            and all(
                statuses.get(dependency_id) == TaskStatus.SUCCEEDED
                for dependency_id in task.dependency_ids
            )
        ]

    def blocked_tasks(self, statuses: dict[str, TaskStatus]) -> list[TaskSpec]:
        return [
            task
            for task in self.tasks
            if statuses.get(task.task_id, TaskStatus.PENDING)
            not in {
                TaskStatus.SUCCEEDED,
                TaskStatus.FAILED,
                TaskStatus.REJECTED,
                TaskStatus.BLOCKED,
                TaskStatus.CANCELLED,
                TaskStatus.INTEGRATION_CONFLICT,
            }
            and any(
                statuses.get(dependency_id) in FAILED_DEPENDENCY_STATUSES
                for dependency_id in task.dependency_ids
            )
        ]

    def is_complete(self, statuses: dict[str, TaskStatus]) -> bool:
        terminal = {
            TaskStatus.SUCCEEDED,
            TaskStatus.FAILED,
            TaskStatus.REJECTED,
            TaskStatus.BLOCKED,
            TaskStatus.CANCELLED,
        }
        return all(statuses.get(task.task_id) in terminal for task in self.tasks)

    def all_succeeded(self, statuses: dict[str, TaskStatus]) -> bool:
        return all(
            statuses.get(task.task_id) == TaskStatus.SUCCEEDED for task in self.tasks
        )

    def topological_levels(self) -> list[list[TaskSpec]]:
        remaining = {task.task_id: set(task.dependency_ids) for task in self.tasks}
        levels: list[list[TaskSpec]] = []
        completed: set[str] = set()
        while remaining:
            ready_ids = sorted(
                task_id
                for task_id, dependencies in remaining.items()
                if dependencies <= completed
            )
            if not ready_ids:
                raise TaskGraphValidationError("Task graph cannot be topologically ordered.")
            levels.append([self._by_id[task_id] for task_id in ready_ids])
            completed.update(ready_ids)
            for task_id in ready_ids:
                remaining.pop(task_id)
        return levels

    def topological_order(self) -> list[TaskSpec]:
        return [task for level in self.topological_levels() for task in level]

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.VERSION,
            "tasks": [task.model_dump(mode="json") for task in self.tasks],
        }
