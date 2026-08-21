from __future__ import annotations

import threading
from pathlib import Path

import pytest

from agentbus.execution.cancellation import CancellationToken
from agentbus.execution.cancellation_registry import CancellationRegistry
from agentbus.execution.engine import DurableExecutionEngine
from agentbus.execution.models import (
    AttemptStatus,
    FailureCategory,
    RunStatus,
    TaskExecutionResult,
    TaskStatus,
)
from agentbus.execution.state_store import StateStore


CANCELLATION_BOUNDARIES = (
    "planning",
    "provider-boundary",
    "parsing",
    "task-scheduling",
    "lease-acquisition",
    "worktree-creation",
    "managed-filesystem-mutation",
    "test-execution",
    "verifier",
    "reviewer",
    "approval-wait",
    "integration",
    "repository-indexing",
    "local-mcp-invocation",
    "replay",
    "trace-archive-generation",
    "cleanup",
)


def plan() -> dict[str, object]:
    return {
        "goal": "Exercise durable cancellation invariants",
        "steps": [
            {
                "id": f"step-{index}",
                "title": f"Step {index}",
                "description": f"Complete step {index}",
                "risk": "low",
                "maximum_attempts": 2,
            }
            for index in range(1, 4)
        ],
        "test_strategy": "offline",
        "done_criteria": ["Cancellation remains durable"],
    }


class BarrierExecutor:
    def __init__(
        self,
        token: CancellationToken,
        boundary: str,
        started: threading.Barrier,
        release: threading.Barrier,
    ) -> None:
        self.token = token
        self.boundary = boundary
        self.started = started
        self.release = release
        self.calls: list[str] = []

    def execute(self, context) -> TaskExecutionResult:
        task_id = context.task.task_id
        self.calls.append(task_id)
        if task_id == "step-1":
            return TaskExecutionResult(
                succeeded=True,
                summary="predecessor completed",
            )
        if task_id != "step-2":
            raise AssertionError("Scheduling continued after cancellation")
        with self.token.operation(
            f"race.{self.boundary}",
            source="race-matrix",
            interruptible=True,
            task_id=task_id,
        ):
            self.started.wait(timeout=5)
            self.release.wait(timeout=5)
            self.token.checkpoint(
                "race-matrix",
                stage=self.boundary,
            )
        raise AssertionError("Cancellation checkpoint did not interrupt")


@pytest.mark.parametrize("boundary", CANCELLATION_BOUNDARIES)
@pytest.mark.parametrize("iteration", range(2))
def test_durable_cancellation_boundary_matrix(
    tmp_path: Path,
    boundary: str,
    iteration: int,
) -> None:
    del iteration
    database = tmp_path / "state.db"
    store = StateStore(database)
    registry = CancellationRegistry(store)
    token = registry.prepare("run-1")
    started = threading.Barrier(2)
    release = threading.Barrier(2)
    executor = BarrierExecutor(token, boundary, started, release)
    engine = DurableExecutionEngine(
        store,
        executor,
        cancellation_registry=registry,
    )
    engine.create_run(
        f"Cancel during {boundary}",
        plan(),
        model="deterministic",
        workspace=str(tmp_path.resolve()),
        run_id="run-1",
    )
    reports = []
    thread = threading.Thread(
        target=lambda: reports.append(engine.run_until_blocked("run-1"))
    )
    thread.start()
    try:
        started.wait(timeout=5)
        requested = engine.request_cancellation(
            "run-1",
            f"cancel at {boundary}",
        )
        release.wait(timeout=5)
        thread.join(timeout=5)
    finally:
        if thread.is_alive():
            token.request("race matrix cleanup")
            try:
                release.abort()
            except threading.BrokenBarrierError:
                pass
            thread.join(timeout=5)

    assert thread.is_alive() is False
    assert requested.status == RunStatus.RUNNING
    assert reports[0].status == RunStatus.CANCELLED
    assert executor.calls == ["step-1", "step-2"]
    assert store.get_task("run-1", "step-1").status == TaskStatus.SUCCEEDED
    assert store.get_task("run-1", "step-2").status == TaskStatus.CANCELLED
    assert store.get_task("run-1", "step-3").status == TaskStatus.CANCELLED
    first_attempts = store.list_attempts("run-1", "step-1")
    cancelled_attempts = store.list_attempts("run-1", "step-2")
    assert len(first_attempts) == 1
    assert first_attempts[0].status == AttemptStatus.SUCCEEDED
    assert len(cancelled_attempts) == 1
    assert cancelled_attempts[0].status == AttemptStatus.INTERRUPTED
    assert cancelled_attempts[0].error_category == FailureCategory.CANCELLED
    attempt_snapshot = [
        attempt.model_dump(mode="json")
        for attempt in first_attempts + cancelled_attempts
    ]
    events_before_resume = store.list_events("run-1")
    _assert_single_terminal_lifecycle(events_before_resume)
    state = store.get_cancellation_state("run-1")
    assert state.active_operations == []
    assert state.cleanup_completed_at is not None
    assert "step-3" in state.tasks_prevented_from_starting
    assert store.list_worker_lease_rows("run-1") == []

    resumed = DurableExecutionEngine(
        StateStore(database),
        executor,
    ).resume("run-1")

    assert resumed.status == RunStatus.CANCELLED
    assert executor.calls == ["step-1", "step-2"]
    persisted = StateStore(database)
    attempts_after_resume = (
        persisted.list_attempts("run-1", "step-1")
        + persisted.list_attempts("run-1", "step-2")
    )
    assert [
        attempt.model_dump(mode="json") for attempt in attempts_after_resume
    ] == attempt_snapshot
    _assert_single_terminal_lifecycle(persisted.list_events("run-1"))


def _assert_single_terminal_lifecycle(events: list[dict[str, object]]) -> None:
    event_types = [str(event["event_type"]) for event in events]
    terminal = [
        event
        for event in event_types
        if event in {"run_cancelled", "run_failed", "run_succeeded", "run_rejected"}
    ]
    assert terminal == ["run_cancelled"]
    assert event_types.count("scheduling_stopped") == 1
    assert event_types.count("cancellation_cleanup_completed") == 1
    assert event_types.count("task_retry_scheduled") == 0
