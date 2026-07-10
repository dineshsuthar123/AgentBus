from __future__ import annotations

from enum import Enum

from agentbus.execution.models import AttemptStatus, RunStatus, TaskStatus


class InvalidStateTransition(RuntimeError):
    """Raised when persisted execution state would violate the lifecycle policy."""


RUN_TRANSITIONS: dict[RunStatus, set[RunStatus]] = {
    RunStatus.PENDING: {RunStatus.RUNNING, RunStatus.FAILED, RunStatus.CANCELLED},
    RunStatus.RUNNING: {
        RunStatus.WAITING_FOR_APPROVAL,
        RunStatus.SUCCEEDED,
        RunStatus.FAILED,
        RunStatus.CANCELLED,
    },
    RunStatus.WAITING_FOR_APPROVAL: {
        RunStatus.RUNNING,
        RunStatus.FAILED,
        RunStatus.CANCELLED,
    },
    RunStatus.SUCCEEDED: set(),
    RunStatus.FAILED: set(),
    RunStatus.CANCELLED: set(),
}


TASK_TRANSITIONS: dict[TaskStatus, set[TaskStatus]] = {
    TaskStatus.PENDING: {
        TaskStatus.READY,
        TaskStatus.BLOCKED,
        TaskStatus.CANCELLED,
    },
    TaskStatus.READY: {
        TaskStatus.RUNNING,
        TaskStatus.WAITING_FOR_APPROVAL,
        TaskStatus.BLOCKED,
        TaskStatus.CANCELLED,
    },
    TaskStatus.RUNNING: {
        TaskStatus.SUCCEEDED,
        TaskStatus.RETRYABLE,
        TaskStatus.FAILED,
        TaskStatus.CANCELLED,
    },
    TaskStatus.WAITING_FOR_APPROVAL: {
        TaskStatus.READY,
        TaskStatus.REJECTED,
        TaskStatus.BLOCKED,
        TaskStatus.CANCELLED,
    },
    TaskStatus.RETRYABLE: {
        TaskStatus.READY,
        TaskStatus.FAILED,
        TaskStatus.BLOCKED,
        TaskStatus.CANCELLED,
    },
    TaskStatus.SUCCEEDED: set(),
    TaskStatus.FAILED: set(),
    TaskStatus.REJECTED: set(),
    TaskStatus.BLOCKED: set(),
    TaskStatus.CANCELLED: set(),
}


ATTEMPT_TRANSITIONS: dict[AttemptStatus, set[AttemptStatus]] = {
    AttemptStatus.RUNNING: {
        AttemptStatus.SUCCEEDED,
        AttemptStatus.FAILED,
        AttemptStatus.INTERRUPTED,
    },
    AttemptStatus.SUCCEEDED: set(),
    AttemptStatus.FAILED: set(),
    AttemptStatus.INTERRUPTED: set(),
}


def validate_run_transition(current: RunStatus, target: RunStatus) -> None:
    _validate("run", current, target, RUN_TRANSITIONS)


def validate_task_transition(current: TaskStatus, target: TaskStatus) -> None:
    _validate("task", current, target, TASK_TRANSITIONS)


def validate_attempt_transition(current: AttemptStatus, target: AttemptStatus) -> None:
    _validate("attempt", current, target, ATTEMPT_TRANSITIONS)


def _validate(
    entity: str,
    current: Enum,
    target: Enum,
    transitions: dict[Enum, set[Enum]],
) -> None:
    if target not in transitions[current]:
        raise InvalidStateTransition(
            f"Invalid {entity} transition: {current.value} -> {target.value}."
        )
