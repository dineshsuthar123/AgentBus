from __future__ import annotations

import json
import os
import sqlite3
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from agentbus import __version__
from agentbus.config import AgentBusConfig
from agentbus.control.models import DaemonRegistryEntry, RunCreateRequest
from agentbus.control.registry import (
    DaemonRegistry,
    executable_identity,
    process_matches,
    process_start_identity,
)
from agentbus.control.replay_supervisor import BackgroundReplaySupervisor
from agentbus.control.services import ControlQueryService
from agentbus.control.supervisor import AgentBusRunBackend
from agentbus.control.version import CONTROL_PROTOCOL_VERSION
from agentbus.execution.cancellation import (
    CancellationOperation,
    CancellationState,
)
from agentbus.execution.engine import DurableExecutionEngine
from agentbus.execution.leases import (
    LeaseExpiredError,
    LeaseService,
    LeaseStatus,
)
from agentbus.execution.models import (
    AttemptStatus,
    FailureCategory,
    RunRecord,
    RunStatus,
    TaskExecutionResult,
    TaskSpec,
    TaskStatus,
)
from agentbus.execution.schema import SCHEMA_SQL, SCHEMA_VERSION
from agentbus.execution.state_store import StateStore
from agentbus.intelligence import (
    IndexOperationKind,
    IndexOperationLease,
    IndexOperationState,
    IndexState,
    IndexStore,
    RepositoryIndexer,
    repository_identity,
    workspace_identity,
)
from agentbus.product.daemon import start_daemon
from agentbus.product.migrations import MigrationCoordinator
from agentbus.replay.service import TraceReplayService
from agentbus.replay.session import (
    ReplayRequest,
    ReplaySession,
    ReplaySessionStatus,
)
from agentbus.runtime.orchestrator import MultiAgentOrchestrator
from agentbus.tools.protocol import (
    CapabilityScope,
    ToolCapability,
    ToolCapabilityName,
    ToolInvocation,
    ToolInvocationContext,
    ToolInvocationStatus,
    ToolPolicyDecision,
    ToolPolicyOutcome,
    ToolVersion,
    capability_fingerprint,
    sha256_json,
)
from agentbus.trace import ReplayMode, RuntimeTrace, TraceSpanType, TraceStatus
from agentbus.worktrees.errors import WorktreeRemovalUnsafeError
from agentbus.worktrees.manager import GitWorktreeManager
from agentbus.worktrees.models import WorktreeStatus


RESTART_BOUNDARIES = frozenset(
    {
        "idle",
        "planning",
        "task_execution",
        "approval",
        "provider_wait",
        "managed_tool",
        "index_build",
        "replay",
        "trace_write",
        "migration",
        "cleanup",
    }
)


class _CountingExecutor:
    def __init__(self, mutation_log: Path | None = None) -> None:
        self.calls: list[tuple[str, int]] = []
        self.mutation_log = mutation_log

    def execute(self, context) -> TaskExecutionResult:
        self.calls.append((context.task.task_id, context.attempt_number))
        if self.mutation_log is not None:
            previous = (
                self.mutation_log.read_text(encoding="utf-8")
                if self.mutation_log.exists()
                else ""
            )
            self.mutation_log.write_text(
                previous + f"{context.task.task_id}\n",
                encoding="utf-8",
            )
        return TaskExecutionResult(
            succeeded=True,
            summary=f"{context.task.task_id} complete",
            verifier_status="passed",
            reviewer_status="approved",
            changed_files=[f"{context.task.task_id}.txt"],
        )


class _ControlledClock:
    def __init__(self) -> None:
        self.seconds = 0.0

    def clock(self) -> datetime:
        return datetime(2030, 1, 1, tzinfo=UTC) + timedelta(
            seconds=self.seconds
        )

    def monotonic(self) -> float:
        return self.seconds


def _plan(*, count: int = 2, risk: str = "low") -> dict:
    return {
        "goal": "Restart-safe work",
        "steps": [
            {
                "id": f"step-{index + 1}",
                "title": f"Step {index + 1}",
                "description": f"Complete step {index + 1}",
                "risk": risk,
            }
            for index in range(count)
        ],
        "test_strategy": "offline",
        "done_criteria": ["all tasks complete"],
    }


def _create_run(
    store: StateStore,
    *,
    run_id: str = "restart-run",
    count: int = 2,
    risk: str = "low",
    workspace: Path | None = None,
    executor=None,
) -> DurableExecutionEngine:
    engine = DurableExecutionEngine(store, executor)
    engine.create_run(
        "Exercise restart reconciliation",
        _plan(count=count, risk=risk),
        model="deterministic",
        workspace=str(workspace or store.database_path.parent),
        run_id=run_id,
    )
    return engine


def _events(store: StateStore, run_id: str, event_type: str) -> list[dict]:
    return [
        event
        for event in store.list_events(run_id)
        if event["event_type"] == event_type
    ]


def _git(path: Path, *arguments: str) -> str:
    environment = {
        **os.environ,
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_CONFIG_SYSTEM": os.devnull,
        "GIT_ATTR_NOSYSTEM": "1",
    }
    result = subprocess.run(
        ["git", *arguments],
        cwd=path,
        check=False,
        capture_output=True,
        text=True,
        shell=False,
        env=environment,
    )
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


def _repository(path: Path) -> tuple[Path, str]:
    path.mkdir()
    _git(path, "init", "-q")
    (path / "baseline.txt").write_text("baseline\n", encoding="utf-8")
    _git(path, "add", "--", "baseline.txt")
    _git(
        path,
        "-c",
        "user.name=AgentBus Test",
        "-c",
        "user.email=agentbus@example.invalid",
        "commit",
        "-q",
        "-m",
        "baseline",
    )
    return path.resolve(), _git(path, "rev-parse", "HEAD")


def _daemon_entry(
    registry_path: Path,
    state_database: Path,
    *,
    daemon_id: str,
    start_identity: str | None = None,
) -> DaemonRegistryEntry:
    now = datetime.now(UTC)
    return DaemonRegistryEntry(
        daemon_id=daemon_id,
        pid=os.getpid(),
        executable=executable_identity(),
        process_start_identity=(
            process_start_identity()
            if start_identity is None
            else start_identity
        ),
        host="127.0.0.1",
        port=43123,
        agentbus_version=__version__,
        protocol_version=CONTROL_PROTOCOL_VERSION,
        started_at=now,
        heartbeat_at=now,
        state_database=str(state_database.resolve()),
        registry_path=str(registry_path.resolve()),
    )


def _tool_invocation(root: Path) -> ToolInvocation:
    return ToolInvocation(
        invocation_id="interrupted-tool",
        run_id="tool-run",
        task_id="step-1",
        tool_name="filesystem.write",
        tool_version=ToolVersion(major=1),
        arguments={"path": "must-not-exist.txt", "content": "not executed"},
        requested_capabilities=(
            ToolCapability(
                name=ToolCapabilityName.FILESYSTEM_WRITE,
                scope=CapabilityScope(roots=(str(root.resolve()),)),
            ),
        ),
        context=ToolInvocationContext(
            workspace_identity=str(root.resolve()),
            worktree_identity=str(root.resolve()),
            caller_role="coder",
            workspace_trusted=True,
            provider_consented=True,
        ),
        idempotency_key=None,
    )


def _tool_policy(invocation: ToolInvocation) -> ToolPolicyDecision:
    return ToolPolicyDecision(
        outcome=ToolPolicyOutcome.ALLOW_WITH_CONSTRAINTS,
        rule_id="allow.scoped_mutation",
        reason="Bounded local mutation.",
        invocation_id=invocation.invocation_id,
        invocation_revision=invocation.invocation_revision,
        capability_fingerprint=capability_fingerprint(
            invocation.requested_capabilities
        ),
        arguments_sha256=sha256_json(invocation.arguments),
        constraints=invocation.requested_capabilities,
    )


def test_idle_restart_cleans_stale_registry_and_preserves_owned_identity(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    config = AgentBusConfig(
        workspace_dir=str(workspace),
        state_db=str(tmp_path / "state.db"),
    )
    registry_path = tmp_path / "daemons.json"
    registry = DaemonRegistry(registry_path)
    stale = _daemon_entry(
        registry_path,
        config.state_database_path,
        daemon_id="stale-daemon",
        start_identity="stale-process-generation",
    )
    active = _daemon_entry(
        registry_path,
        config.state_database_path,
        daemon_id="active-daemon",
    )
    registry.register(stale)
    registry.register(active)

    def fail_spawn(*_args, **_kwargs):
        raise AssertionError("an owned daemon should be reused")

    monkeypatch.setattr(
        "agentbus.product.daemon.subprocess.Popen",
        fail_spawn,
    )

    result = start_daemon(config, registry_path=registry_path)

    assert result.started is False
    assert result.entry.daemon_id == "active-daemon"
    assert process_matches(result.entry) is True
    assert [entry.daemon_id for entry in registry.list()] == [
        "active-daemon"
    ]


def test_planning_restart_fails_provisional_run_without_provider_replay(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    database = tmp_path / "state.db"
    config = AgentBusConfig(
        workspace_dir=str(workspace),
        state_db=str(database),
        provider_name="deterministic",
    )
    request = RunCreateRequest(
        task="Plan without replaying after process loss",
        workspace=str(workspace),
        provider="deterministic",
        durable=True,
    )
    first = AgentBusRunBackend(config, StateStore(database))
    first.prepare_submission(request, "planning-run")
    pending = first.store.get_run("planning-run")
    first.shutdown()

    assert pending.status == RunStatus.PENDING
    assert pending.metadata["control_submission"]["state"] == "planning"
    assert first.store.list_tasks("planning-run") == []

    second = AgentBusRunBackend(
        config,
        StateStore(database),
        reconcile_interrupted=True,
    )
    failed = second.store.get_run("planning-run")
    second.shutdown()
    third = AgentBusRunBackend(
        config,
        StateStore(database),
        reconcile_interrupted=True,
    )
    third.shutdown()

    assert failed.status == RunStatus.FAILED
    assert failed.completed_at is not None
    assert "not rerun" in (failed.failure_reason or "")
    assert len(
        _events(
            StateStore(database),
            "planning-run",
            "durable_planning_restart_reconciled",
        )
    ) == 1


def test_completed_planning_atomically_replaces_provisional_graph(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    database = tmp_path / "state.db"
    config = AgentBusConfig(
        workspace_dir=str(workspace),
        state_db=str(database),
        provider_name="deterministic",
    )
    request = RunCreateRequest(
        task="Persist a complete plan",
        workspace=str(workspace),
        provider="deterministic",
        durable=True,
    )
    backend = AgentBusRunBackend(config, StateStore(database))
    backend.prepare_submission(request, "planned-run")

    DurableExecutionEngine(backend.store).create_run(
        request.task,
        _plan(count=1),
        model=config.resolve_model("coder"),
        workspace=str(workspace.resolve()),
        run_id="planned-run",
    )
    planned = backend.store.load_snapshot("planned-run")
    backend.shutdown()

    assert planned.run.metadata["control_submission"]["state"] == "planned"
    assert [task.task_id for task in planned.tasks] == ["step-1"]
    assert len(_events(StateStore(database), "planned-run", "durable_run_created")) == 1
    assert len(_events(StateStore(database), "planned-run", "task_graph_validated")) == 1


def test_task_execution_restart_preserves_history_and_fences_old_lease(
    tmp_path: Path,
) -> None:
    database = tmp_path / "state.db"
    mutation_log = tmp_path / "mutations.log"
    executor = _CountingExecutor(mutation_log)
    first_store = StateStore(database)
    first = _create_run(first_store, executor=executor)
    first.execute_next("restart-run")

    def interrupt_after_attempt(stage, context) -> None:
        assert stage == "after_attempt_started"
        assert context.task.task_id == "step-2"
        raise RuntimeError("simulated daemon loss")

    first.crash_hook = interrupt_after_attempt
    with pytest.raises(RuntimeError, match="simulated daemon loss"):
        first.execute_next("restart-run")

    restored = StateStore(database)
    report = DurableExecutionEngine(restored, executor).resume("restart-run")
    repeated = DurableExecutionEngine(
        StateStore(database),
        executor,
    ).resume("restart-run")
    attempts = restored.list_attempts("restart-run", "step-2")

    assert report.status == RunStatus.SUCCEEDED
    assert repeated.status == RunStatus.SUCCEEDED
    assert executor.calls == [("step-1", 1), ("step-2", 2)]
    assert mutation_log.read_text(encoding="utf-8").splitlines() == [
        "step-1",
        "step-2",
    ]
    assert [attempt.status for attempt in attempts] == [
        AttemptStatus.INTERRUPTED,
        AttemptStatus.SUCCEEDED,
    ]
    assert len(
        _events(restored, "restart-run", "durable_task_succeeded")
    ) == 2
    assert len(
        _events(restored, "restart-run", "durable_run_succeeded")
    ) == 1

    clock = _ControlledClock()
    leases = LeaseService(restored, lease_seconds=1, clock=clock.clock)
    old = leases.acquire_lease("restart-run", "step-2", "old-daemon")
    clock.seconds = 2
    replacement = leases.acquire_lease(
        "restart-run",
        "step-2",
        "new-daemon",
    )
    with pytest.raises(LeaseExpiredError):
        leases.validate_fencing_token(
            old.lease_id,
            "old-daemon",
            old.fencing_token,
        )
    assert replacement.fencing_token == old.fencing_token + 1
    leases.release_lease(
        replacement.lease_id,
        "new-daemon",
        replacement.fencing_token,
    )
    assert not any(
        row["status"] == LeaseStatus.ACTIVE.value
        for row in restored.list_worker_lease_rows("restart-run")
    )


def test_approval_restart_waits_without_duplicate_task_execution(
    tmp_path: Path,
) -> None:
    database = tmp_path / "state.db"
    executor = _CountingExecutor()
    first = _create_run(
        StateStore(database),
        count=1,
        risk="high",
        executor=executor,
    )

    waiting = first.run_until_blocked("restart-run")
    still_waiting = DurableExecutionEngine(
        StateStore(database),
        executor,
    ).resume("restart-run")

    assert waiting.status == RunStatus.WAITING_FOR_APPROVAL
    assert still_waiting.status == RunStatus.WAITING_FOR_APPROVAL
    assert executor.calls == []

    decision_engine = DurableExecutionEngine(StateStore(database))
    decision_engine.approve_task(
        "restart-run",
        "step-1",
        "Approved after daemon restart",
    )
    completed = DurableExecutionEngine(
        StateStore(database),
        executor,
    ).resume("restart-run")
    DurableExecutionEngine(StateStore(database), executor).resume("restart-run")

    assert completed.status == RunStatus.SUCCEEDED
    assert executor.calls == [("step-1", 1)]
    assert len(
        _events(
            StateStore(database),
            "restart-run",
            "durable_task_succeeded",
        )
    ) == 1


def test_provider_wait_restart_restores_cancellation_and_clears_operation(
    tmp_path: Path,
) -> None:
    database = tmp_path / "state.db"
    store = StateStore(database)
    engine = _create_run(store, count=1)
    store.update_run_status("restart-run", RunStatus.RUNNING)
    store.update_task_status("restart-run", "step-1", TaskStatus.READY)
    store.update_task_status("restart-run", "step-1", TaskStatus.RUNNING)
    attempt = store.create_attempt("restart-run", "step-1")
    now = datetime.now(UTC)
    store.persist_cancellation_state(
        "restart-run",
        CancellationState(
            requested=True,
            requested_at=now,
            reason="cancel during provider wait",
            propagated_at=now,
            propagation_sources=["cancellation-token"],
            provider_cancellation_requested_at=now,
            provider_names=["deterministic"],
            active_operations=[
                CancellationOperation(
                    operation_id="provider-wait-operation",
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

    report = DurableExecutionEngine(StateStore(database)).resume("restart-run")
    repeated = DurableExecutionEngine(
        StateStore(database)
    ).resume("restart-run")
    restored = StateStore(database)
    cancellation = restored.get_cancellation_state("restart-run")

    assert report.status == RunStatus.CANCELLED
    assert repeated.status == RunStatus.CANCELLED
    assert cancellation.active_operations == []
    assert cancellation.operations_completed_after_request == []
    assert cancellation.acknowledgement_source == "cancellation-recovery"
    interrupted = restored.get_attempt(attempt.attempt_id)
    assert interrupted.status == AttemptStatus.INTERRUPTED
    assert interrupted.error_category == FailureCategory.CANCELLED
    assert len(_events(restored, "restart-run", "run_cancelled")) == 1
    assert len(
        _events(restored, "restart-run", "cancellation_cleanup_completed")
    ) == 1
    assert engine.get_report("restart-run").status == RunStatus.CANCELLED


def test_managed_tool_restart_is_reconciled_before_durable_resume(
    tmp_path: Path,
) -> None:
    workspace, _head = _repository(tmp_path / "repository")
    database = tmp_path / "state.db"
    store = StateStore(database)
    _create_run(
        store,
        run_id="tool-run",
        count=1,
        workspace=workspace,
    )
    store.update_run_status("tool-run", RunStatus.FAILED)
    invocation = _tool_invocation(workspace)
    store.record_tool_invocation(invocation, process_slot=True)
    store.record_tool_policy_decision(
        "tool-run",
        _tool_policy(invocation),
    )
    store.mark_tool_invocation_started(
        "tool-run",
        invocation.invocation_id,
    )
    config = AgentBusConfig(
        workspace_dir=str(workspace),
        state_db=str(database),
        runs_dir=str(tmp_path / "runs"),
        provider_name="deterministic",
    )

    first = MultiAgentOrchestrator(
        config=config,
        state_store=StateStore(database),
    ).resume_durable("tool-run")
    second = MultiAgentOrchestrator(
        config=config,
        state_store=StateStore(database),
    ).resume_durable("tool-run")
    restored = StateStore(database)
    record = restored.get_tool_invocation(
        "tool-run",
        invocation.invocation_id,
    )

    assert first.status == RunStatus.FAILED
    assert second.status == RunStatus.FAILED
    assert record.status == ToolInvocationStatus.FAILED
    assert record.safe_result is not None
    assert record.safe_result.error is not None
    assert record.safe_result.error.code == "restart_interrupted"
    assert record.safe_result.error.retryable is False
    assert not (workspace / "must-not-exist.txt").exists()
    assert len(restored.list_tool_audits("tool-run")) == 1
    assert len(_events(restored, "tool-run", "tool_restart_reconciled")) == 1


def test_index_build_restart_reclaims_only_stale_owner_and_repairs(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "repository"
    workspace.mkdir()
    (workspace / "service.py").write_text(
        "def restart_safe():\n    return True\n",
        encoding="utf-8",
    )
    store = IndexStore(tmp_path / "repository-index.sqlite3")
    repository = repository_identity("restart-matrix/index")
    identity = workspace_identity(repository.repository_id, ("",))
    clock = _ControlledClock()
    abandoned = IndexOperationLease(
        store,
        repository,
        IndexOperationKind.BUILD,
        operation_id="indexop_" + ("1" * 32),
        owner_pid=101,
        stale_after=timedelta(seconds=10),
        heartbeat_interval_seconds=1,
        clock=clock.clock,
        monotonic=clock.monotonic,
    ).acquire()
    clock.seconds = 11

    repaired = RepositoryIndexer(
        workspace,
        repository,
        identity,
        store,
        operation_stale_after=timedelta(seconds=10),
        operation_heartbeat_seconds=1,
        operation_owner_pid=202,
        operation_clock=clock.clock,
        operation_monotonic=clock.monotonic,
    ).repair(operation_id="indexop_" + ("2" * 32))

    assert repaired.snapshot.state == IndexState.CURRENT
    assert repaired.operation is not None
    assert repaired.operation.operation_kind == IndexOperationKind.REPAIR
    assert repaired.operation.state == IndexOperationState.COMPLETED
    assert repaired.operation.operation_id != abandoned.operation_id
    assert [
        source.relative_path
        for source in store.list_files(repaired.snapshot.snapshot_id)
    ] == ["service.py"]


def test_replay_restart_fails_orphan_once_without_reexecution(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    database = tmp_path / "state.db"
    config = AgentBusConfig(
        workspace_dir=str(workspace),
        state_db=str(database),
    )
    store = StateStore(database)
    store.create_run(
        RunRecord(
            run_id="replay-source",
            original_task="Source trace",
            model="deterministic",
            workspace=str(workspace),
            graph_data={"version": 1, "tasks": []},
        )
    )
    runtime = RuntimeTrace.open(
        store,
        "replay-source",
        object_root=config.trace_store_path,
        workspace=workspace,
    )
    trace = runtime.finish(status=TraceStatus.SUCCEEDED)
    assert trace is not None
    service = TraceReplayService(config, state_store=store)
    request = ReplayRequest(
        source_trace_id=trace.trace_id,
        source_run_id=trace.run_id,
        mode=ReplayMode.OFFLINE,
    )
    prepared, pending = service.queue_replay(trace.trace_id, request)
    running = ReplaySession.model_validate(
        pending.model_copy(
            update={
                "status": ReplaySessionStatus.RUNNING,
                "started_at": pending.created_at,
            }
        ).model_dump()
    )
    store.record_replay_session(prepared, running)
    query = ControlQueryService(config, store)

    first = BackgroundReplaySupervisor(
        query,
        reconcile_interrupted=True,
    )
    first_reconciled = first.reconciled_replays
    first.shutdown()
    second = BackgroundReplaySupervisor(
        ControlQueryService(config, StateStore(database)),
        reconcile_interrupted=True,
    )
    second_reconciled = second.reconciled_replays
    second.shutdown()
    restored = StateStore(database)
    session = restored.get_replay_session(prepared.replay_id)

    assert len(first_reconciled) == 1
    assert second_reconciled == ()
    assert session.status == ReplaySessionStatus.FAILED
    assert session.failure_category == "DaemonRestartInterrupted"
    assert session.provider_calls == 0
    assert session.network_calls == 0
    assert restored.get_replay_result(prepared.replay_id) is None
    assert len(
        _events(restored, "replay-source", "replay_restart_reconciled")
    ) == 1


def test_trace_write_restart_interrupts_only_open_span(
    tmp_path: Path,
) -> None:
    database = tmp_path / "state.db"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    store = StateStore(database)
    store.create_run(
        RunRecord(
            run_id="trace-run",
            original_task="Trace restart",
            model="deterministic",
            workspace=str(workspace),
            graph_data={"version": 1, "tasks": []},
        )
    )
    first = RuntimeTrace.open(
        store,
        "trace-run",
        object_root=tmp_path / "objects",
        workspace=workspace,
    )
    completed = first.start_span(TraceSpanType.TASK, "completed")
    first.finish_span(completed)
    abandoned = first.start_span(TraceSpanType.TASK, "abandoned")

    second = RuntimeTrace.open(
        StateStore(database),
        "trace-run",
        object_root=tmp_path / "objects",
        workspace=workspace,
        reconcile=True,
    )
    third = RuntimeTrace.open(
        StateStore(database),
        "trace-run",
        object_root=tmp_path / "objects",
        workspace=workspace,
        reconcile=True,
    )
    snapshot = third.snapshot()
    assert snapshot is not None
    by_id = {span.span_id: span for span in snapshot.spans}

    assert by_id[completed.span_id].status == TraceStatus.SUCCEEDED
    assert by_id[abandoned.span_id].status == TraceStatus.INTERRUPTED
    assert len(
        [
            span
            for span in snapshot.spans
            if span.status == TraceStatus.INTERRUPTED
        ]
    ) == 1
    sequences = [
        *(span.sequence for span in snapshot.spans),
        *(event.sequence for event in snapshot.events),
    ]
    assert len(sequences) == len(set(sequences))
    assert second.finish(status=TraceStatus.SUCCEEDED) is not None


def test_migration_restart_recovers_journal_once(
    tmp_path: Path,
) -> None:
    config = AgentBusConfig(
        state_dir=str(tmp_path / "state"),
        state_db="state.db",
    )
    coordinator = MigrationCoordinator(config)
    coordinator.state_path.parent.mkdir(parents=True)
    v1_sql = SCHEMA_SQL.split(
        "CREATE TABLE IF NOT EXISTS worktrees",
        1,
    )[0]
    with sqlite3.connect(coordinator.state_path) as connection:
        connection.executescript(v1_sql)
        connection.execute(
            "INSERT INTO schema_metadata(key, value) VALUES "
            "('schema_version', '1')"
        )
        connection.commit()
    coordinator.journal_path.write_text(
        json.dumps({"status": "in_progress"}),
        encoding="utf-8",
    )

    first = MigrationCoordinator(config).apply()
    second = MigrationCoordinator(config).apply()
    journal = json.loads(
        coordinator.journal_path.read_text(encoding="utf-8")
    )

    assert first.recovered_interrupted_operation is True
    assert second.recovered_interrupted_operation is False
    assert len(first.backups) == 1
    assert second.backups == ()
    assert StateStore(coordinator.state_path).schema_version == SCHEMA_VERSION
    assert journal["status"] == "complete"


def test_cleanup_restart_requires_owned_clean_worktree_and_removes_once(
    tmp_path: Path,
) -> None:
    source, head = _repository(tmp_path / "repository")
    database = tmp_path / "state.db"
    store = StateStore(database)
    _create_run(store, count=1, workspace=source)
    root = tmp_path / "worktrees"
    first = GitWorktreeManager(source, root, store)
    worktree = first.create_task_worktree(
        "restart-run",
        "step-1",
        head,
        "old-daemon",
    )
    first.mark_cleanup_pending(worktree.worktree_id)

    second = GitWorktreeManager(
        source,
        root,
        StateStore(database),
    )
    recovered = second.recover(worktree.worktree_id)
    recovered_path = second.validate(recovered)
    removed = second.remove(worktree.worktree_id)
    third = GitWorktreeManager(
        source,
        root,
        StateStore(database),
    )

    assert recovered_path.is_absolute()
    assert removed.status == WorktreeStatus.REMOVED
    assert not recovered_path.exists()
    with pytest.raises(WorktreeRemovalUnsafeError):
        third.remove(worktree.worktree_id)
    assert _git(source, "rev-parse", "HEAD") == head
    assert _git(source, "status", "--porcelain=v1") == ""
    assert len(
        _events(
            StateStore(database),
            "restart-run",
            "worktree_removed",
        )
    ) == 1


def test_restart_matrix_covers_every_required_boundary() -> None:
    assert RESTART_BOUNDARIES == {
        "idle",
        "planning",
        "task_execution",
        "approval",
        "provider_wait",
        "managed_tool",
        "index_build",
        "replay",
        "trace_write",
        "migration",
        "cleanup",
    }
