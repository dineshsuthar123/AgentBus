from __future__ import annotations

import threading
from datetime import datetime, timezone

from agentbus.config import AgentBusConfig
from agentbus.control.models import RunCreateRequest
from agentbus.control.supervisor import AgentBusRunBackend
from agentbus.execution.cancellation import (
    CancellationOperation,
    CancellationRequested,
    CancellationState,
    CancellationToken,
)
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
from agentbus.runtime.verifier import Verifier


def plan(count: int = 2) -> dict:
    return {
        "goal": "Exercise cooperative cancellation",
        "steps": [
            {
                "id": f"step-{index + 1}",
                "title": f"Step {index + 1}",
                "description": f"Complete step {index + 1}",
                "risk": "low",
                "maximum_attempts": 3,
            }
            for index in range(count)
        ],
        "test_strategy": "offline",
        "done_criteria": ["Cancellation is safe"],
    }


def create_run(
    engine: DurableExecutionEngine,
    *,
    count: int = 2,
    run_id: str = "run-1",
) -> None:
    engine.create_run(
        "Exercise cancellation",
        plan(count),
        model="deterministic",
        workspace="workspace",
        run_id=run_id,
    )


def test_active_attempt_is_not_rewritten_until_executor_acknowledges(
    tmp_path,
):
    store = StateStore(tmp_path / "state.db")
    registry = CancellationRegistry(store)
    token = registry.prepare("run-1")
    second_started = threading.Event()
    cancellation_observed = threading.Event()
    release = threading.Event()
    calls: list[str] = []

    class Executor:
        def execute(self, context):
            calls.append(context.task.task_id)
            if context.task.task_id == "step-1":
                return TaskExecutionResult(
                    succeeded=True,
                    summary="first task complete",
                )
            with token.operation(
                "deterministic.generate_json",
                source="provider:deterministic",
                interruptible=True,
                provider="deterministic",
                task_id=context.task.task_id,
            ):
                second_started.set()
                assert token.wait(timeout_seconds=5)
                cancellation_observed.set()
                assert release.wait(timeout=5)
                token.checkpoint(
                    "provider:deterministic",
                    stage="latency-wait",
                    provider="deterministic",
                )
            raise AssertionError("Cancellation checkpoint must interrupt execution")

    engine = DurableExecutionEngine(
        store,
        Executor(),
        cancellation_registry=registry,
    )
    create_run(engine)
    reports = []
    thread = threading.Thread(
        target=lambda: reports.append(engine.run_until_blocked("run-1"))
    )
    thread.start()
    assert second_started.wait(timeout=5)

    requested = DurableExecutionEngine(
        store,
        cancellation_registry=registry,
    ).request_cancellation("run-1", "Stop active provider")
    assert cancellation_observed.wait(timeout=5)

    assert requested.status == RunStatus.RUNNING
    assert store.get_task("run-1", "step-1").status == TaskStatus.SUCCEEDED
    assert store.get_task("run-1", "step-2").status == TaskStatus.RUNNING
    assert store.list_attempts("run-1", "step-2")[-1].status == AttemptStatus.RUNNING

    release.set()
    thread.join(timeout=5)

    assert thread.is_alive() is False
    assert reports[0].status == RunStatus.CANCELLED
    assert calls == ["step-1", "step-2"]
    assert store.get_task("run-1", "step-1").status == TaskStatus.SUCCEEDED
    assert store.get_task("run-1", "step-2").status == TaskStatus.CANCELLED
    cancelled_attempt = store.list_attempts("run-1", "step-2")[-1]
    assert cancelled_attempt.status == AttemptStatus.INTERRUPTED
    assert cancelled_attempt.error_category == FailureCategory.CANCELLED
    assert not any(
        event["event_type"] == "task_retry_scheduled"
        for event in store.list_events("run-1")
    )
    event_types = [
        event["event_type"]
        for event in store.list_events("run-1")
        if event["event_type"]
        in {
            "cancellation_requested",
            "cancellation_propagated",
            "provider_cancellation_requested",
            "provider_cancellation_acknowledged",
            "scheduling_stopped",
            "run_cancelled",
            "cancellation_cleanup_completed",
        }
    ]
    assert event_types == [
        "cancellation_requested",
        "cancellation_propagated",
        "provider_cancellation_requested",
        "provider_cancellation_acknowledged",
        "scheduling_stopped",
        "run_cancelled",
        "cancellation_cleanup_completed",
    ]

    resumed = DurableExecutionEngine(
        StateStore(tmp_path / "state.db"),
        Executor(),
    ).resume("run-1")
    assert resumed.status == RunStatus.CANCELLED
    assert calls == ["step-1", "step-2"]


def test_restart_clears_stale_operations_without_claiming_they_completed(
    tmp_path,
):
    database = tmp_path / "state.db"
    store = StateStore(database)
    engine = DurableExecutionEngine(store)
    create_run(engine, count=1, run_id="recovery-run")
    store.update_run_status("recovery-run", RunStatus.RUNNING)
    store.update_task_status("recovery-run", "step-1", TaskStatus.READY)
    store.update_task_status("recovery-run", "step-1", TaskStatus.RUNNING)
    attempt = store.create_attempt("recovery-run", "step-1")
    now = datetime.now(timezone.utc)
    store.persist_cancellation_state(
        "recovery-run",
        CancellationState(
            requested=True,
            requested_at=now,
            reason="cancel before process loss",
            propagated_at=now,
            propagation_sources=["cancellation-token"],
            provider_cancellation_requested_at=now,
            provider_names=["deterministic"],
            active_operations=[
                CancellationOperation(
                    operation_id="old-process-operation",
                    name="deterministic.generate_json",
                    source="provider:deterministic",
                    interruptible=True,
                    provider="deterministic",
                    task_id="step-1",
                    started_at=now,
                )
            ],
            revision=3,
        ),
    )

    report = DurableExecutionEngine(StateStore(database)).resume("recovery-run")

    assert report.status == RunStatus.CANCELLED
    restored = StateStore(database).get_cancellation_state("recovery-run")
    assert restored.active_operations == []
    assert restored.operations_completed_after_request == []
    assert restored.acknowledgement_source == "cancellation-recovery"
    restored_attempt = StateStore(database).get_attempt(attempt.attempt_id)
    assert restored_attempt.status == AttemptStatus.INTERRUPTED
    assert restored_attempt.error_category == FailureCategory.CANCELLED


def test_verifier_reports_non_interruptible_completion_after_request(tmp_path):
    token = CancellationToken()
    command_started = threading.Event()
    release_command = threading.Event()
    errors: list[Exception] = []

    class BlockingCommands:
        def run_command_result(self, command, environment_overrides=None):
            command_started.set()
            assert release_command.wait(timeout=5)
            return {
                "command": command,
                "exit_code": 0,
                "passed": True,
                "output": "verification completed",
            }

    verifier = Verifier(
        config=AgentBusConfig(workspace_dir=str(tmp_path)),
        command=["offline-verifier"],
        command_tools=BlockingCommands(),
        cancellation=token,
    )
    thread = threading.Thread(
        target=lambda: _capture_exception(errors, verifier.verify)
    )
    thread.start()
    assert command_started.wait(timeout=5)
    operation = token.wait_for_active_operation(
        source="verifier",
        timeout_seconds=2,
    )
    assert operation is not None
    assert operation.interruptible is False

    token.request("stop during verification")
    requested = token.snapshot()
    assert requested.active_non_interruptible_operations == ["verifier.command"]
    release_command.set()
    thread.join(timeout=5)

    assert thread.is_alive() is False
    assert len(errors) == 1
    assert isinstance(errors[0], CancellationRequested)
    state = token.snapshot()
    assert state.operations_completed_after_request == ["verifier.command"]
    assert state.acknowledgement_source == "verifier"
    assert state.acknowledgement_stage == "after-command"


def test_cancellation_before_planning_still_creates_terminal_run(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    config = AgentBusConfig(
        workspace_dir=str(workspace),
        runs_dir=str(tmp_path / "runs"),
        state_dir=str(tmp_path / "state"),
        provider_name="deterministic",
    )
    store = StateStore(tmp_path / "state.db")
    backend = AgentBusRunBackend(config, store)
    run_id = "cancel-before-planner"
    backend.prepare(run_id)
    backend.cancellations.get(run_id).request("cancel immediately")
    request = RunCreateRequest(
        task="Create a deterministic result",
        workspace=str(workspace),
        provider="deterministic",
        durable=True,
    )

    backend.execute_new(request, run_id)

    run = store.get_run(run_id)
    assert run.status == RunStatus.CANCELLED
    assert run.metadata["cancelled_before_planning_completed"] is True
    assert store.get_task(run_id, "planning").status == TaskStatus.CANCELLED
    state = store.get_cancellation_state(run_id)
    assert state.cleanup_completed_at is not None
    assert state.resume_eligible is False
    assert not any(
        event["event_type"] == "planner_output"
        for event in store.list_events(run_id)
    )


def _capture_exception(errors: list[Exception], callable_object) -> None:
    try:
        callable_object()
    except Exception as exc:
        errors.append(exc)
