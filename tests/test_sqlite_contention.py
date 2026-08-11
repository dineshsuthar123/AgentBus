from __future__ import annotations

import sqlite3
import subprocess
import sys
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import Barrier
from typing import Any

import pytest

import agentbus._sqlite as sqlite_runtime
from agentbus.execution.models import (
    ApprovalOutcome,
    RunRecord,
    TaskSpec,
    TaskStatus,
)
from agentbus.execution.schema import SCHEMA_SQL, SCHEMA_VERSION
from agentbus.execution.state_store import (
    StateStore,
    StateStoreBusyError,
)
from agentbus.intelligence import (
    IndexBusyError,
    IndexSnapshot,
    IndexState,
    IndexStore,
    Project,
    ProjectKind,
    SourceFile,
    SourceLanguage,
    content_hash,
    file_id,
    project_id,
    repository_identity,
    snapshot_id,
    workspace_identity,
)
from agentbus.intelligence.fingerprints import (
    file_set_fingerprint,
    graph_fingerprint,
    parser_versions_fingerprint,
    project_map_fingerprint,
)
from agentbus.intelligence.migrations import apply_migrations, schema_version
from agentbus.intelligence.schema import LATEST_SCHEMA_VERSION
from agentbus.replay.session import ReplayRequest, ReplaySessionStatus
from agentbus.tools.protocol import (
    CapabilityScope,
    ToolCapability,
    ToolCapabilityName,
    ToolInvocation,
    ToolInvocationContext,
    ToolInvocationStatus,
    ToolPolicyDecision,
    ToolPolicyOutcome,
    ToolResourceUsage,
    ToolResult,
    ToolVersion,
    capability_fingerprint,
    sha256_json,
)
from agentbus.tools.records import build_tool_audit_record
from agentbus.trace import ReplayMode, StateStoreTraceSink, TraceRecorder


_STRESS_RUN_ID = "sqlite-stress"
_WORK_ITEMS_PER_CATEGORY = 3

_INTERRUPT_EVENT_SCRIPT = r"""
import os
import sqlite3
import sys

connection = sqlite3.connect(sys.argv[1], isolation_level=None)
connection.execute("BEGIN IMMEDIATE")
connection.execute(
    "INSERT INTO events(run_id, task_id, event_type, payload_json, created_at) "
    "VALUES (?, NULL, ?, '{}', '2026-01-01T00:00:00Z')",
    (sys.argv[2], sys.argv[3]),
)
if sys.argv[4] == "commit":
    connection.commit()
os._exit(23)
"""

_INTERRUPT_STATE_MIGRATION_SCRIPT = r"""
import os
import sqlite3
import sys

connection = sqlite3.connect(sys.argv[1], isolation_level=None)
connection.execute("BEGIN IMMEDIATE")
connection.execute("CREATE TABLE interrupted_state_probe(value TEXT)")
connection.execute(
    "UPDATE schema_metadata SET value = '2' WHERE key = 'schema_version'"
)
os._exit(24)
"""

_INTERRUPT_INDEX_MIGRATION_SCRIPT = r"""
import os
import sqlite3
import sys

connection = sqlite3.connect(sys.argv[1], isolation_level=None)
connection.execute("BEGIN IMMEDIATE")
connection.execute("CREATE TABLE interrupted_index_probe(value TEXT)")
connection.execute("PRAGMA user_version = 2")
os._exit(25)
"""


def test_runtime_records_and_index_snapshots_survive_concurrent_writers(
    tmp_path: Path,
) -> None:
    state = StateStore(tmp_path / "state.db")
    transition_tasks = [
        _task(f"transition-{index}")
        for index in range(_WORK_ITEMS_PER_CATEGORY)
    ]
    approval_tasks = [
        _task(f"approval-{index}") for index in range(_WORK_ITEMS_PER_CATEGORY)
    ]
    audit_tasks = [
        _task(f"audit-{index}") for index in range(_WORK_ITEMS_PER_CATEGORY)
    ]
    state.create_run_with_tasks(
        _run(_STRESS_RUN_ID),
        transition_tasks + approval_tasks + audit_tasks,
    )
    audits = [
        _prepare_tool_audit(state, tmp_path, index)
        for index in range(_WORK_ITEMS_PER_CATEGORY)
    ]
    active_traces = [
        _active_trace(state, f"trace-{index}")
        for index in range(_WORK_ITEMS_PER_CATEGORY)
    ]
    replay_requests = [
        _replay_request(state, tmp_path, index)
        for index in range(_WORK_ITEMS_PER_CATEGORY)
    ]

    operations: list[Callable[[], Any]] = []
    for index in range(_WORK_ITEMS_PER_CATEGORY):
        operations.extend(
            (
                lambda index=index: state.record_event(
                    _STRESS_RUN_ID,
                    f"stress.event.{index}",
                ),
                lambda index=index: state.update_task_status(
                    _STRESS_RUN_ID,
                    f"transition-{index}",
                    TaskStatus.READY,
                ),
                lambda index=index: state.record_approval(
                    _STRESS_RUN_ID,
                    f"approval-{index}",
                    ApprovalOutcome.APPROVED,
                    f"concurrent approval {index}",
                ),
                lambda index=index: active_traces[index].record_event(
                    f"stress.trace.{index}"
                ),
                lambda index=index: state.create_replay_session(
                    replay_requests[index]
                ),
                lambda index=index: state.record_tool_audit(audits[index]),
            )
        )

    results = _run_concurrently(operations)

    assert len(results) == len(operations)
    event_types = {
        event["event_type"] for event in state.list_events(_STRESS_RUN_ID)
    }
    assert {
        f"stress.event.{index}" for index in range(_WORK_ITEMS_PER_CATEGORY)
    } <= event_types
    assert all(
        state.get_task(_STRESS_RUN_ID, task.task_id).status == TaskStatus.READY
        for task in transition_tasks
    )
    assert all(
        state.latest_approval(_STRESS_RUN_ID, task.task_id).decision
        == ApprovalOutcome.APPROVED
        for task in approval_tasks
    )
    assert len(state.list_tool_audits(_STRESS_RUN_ID)) == _WORK_ITEMS_PER_CATEGORY
    assert all(
        state.get_replay_session(request.replay_id).status
        == ReplaySessionStatus.PENDING
        for request in replay_requests
    )
    assert all(
        any(
            event.event_type == f"stress.trace.{index}"
            for event in state.list_trace_events(recorder.trace_id)
        )
        for index, recorder in enumerate(active_traces)
    )
    _assert_database_ok(state.database_path)

    index = IndexStore(tmp_path / "index.db")
    bundles = [
        _index_bundle(item) for item in range(_WORK_ITEMS_PER_CATEGORY * 2)
    ]
    snapshots = _run_concurrently(
        [
            lambda bundle=bundle: index.publish_snapshot(
                bundle[0],
                bundle[1],
                bundle[2],
                projects=bundle[3],
                files=bundle[4],
            )
            for bundle in bundles
        ]
    )

    assert {snapshot.snapshot_id for snapshot in snapshots} == {
        bundle[2].snapshot_id for bundle in bundles
    }
    assert all(
        index.list_snapshots(bundle[0].repository_id) == (bundle[2],)
        for bundle in bundles
    )
    index.verify()
    _assert_database_ok(index.database_path)


def test_state_store_retries_a_controlled_sqlite_busy_once(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = StateStore(
        tmp_path / "state.db",
        busy_timeout_ms=1,
        transaction_retry_delays=(0.25,),
    )
    state.create_run(_run("retry-state"))
    blocker = _hold_writer_lock(state.database_path)
    observed_delays: list[float] = []

    def release_lock(delay: float) -> None:
        observed_delays.append(delay)
        blocker.commit()

    monkeypatch.setattr(sqlite_runtime, "_sleep", release_lock)
    try:
        event_id = state.record_event("retry-state", "retry.succeeded")
    finally:
        if blocker.in_transaction:
            blocker.rollback()
        blocker.close()

    assert event_id > 0
    assert observed_delays == [0.25]
    assert state.list_events("retry-state")[-1]["event_type"] == "retry.succeeded"


def test_index_store_retries_a_controlled_sqlite_busy_once(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    index = IndexStore(
        tmp_path / "index.db",
        busy_timeout_ms=1,
        transaction_retry_delays=(0.5,),
    )
    blocker = _hold_writer_lock(index.database_path)
    observed_delays: list[float] = []

    def release_lock(delay: float) -> None:
        observed_delays.append(delay)
        blocker.commit()

    monkeypatch.setattr(sqlite_runtime, "_sleep", release_lock)
    try:
        with index._write_transaction() as connection:
            connection.execute("CREATE TABLE retry_probe(value TEXT)")
    finally:
        if blocker.in_transaction:
            blocker.rollback()
        blocker.close()

    assert observed_delays == [0.5]
    with sqlite3.connect(index.database_path) as connection:
        assert connection.execute(
            "SELECT name FROM sqlite_master WHERE name = 'retry_probe'"
        ).fetchone() == ("retry_probe",)


def test_busy_retry_exhaustion_is_bounded_and_categorized(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = StateStore(
        tmp_path / "state.db",
        busy_timeout_ms=1,
        transaction_retry_delays=(0.0, 0.0),
    )
    state.create_run(_run("busy-state"))
    state_blocker = _hold_writer_lock(state.database_path)
    observed_delays: list[float] = []
    monkeypatch.setattr(
        sqlite_runtime,
        "_sleep",
        lambda delay: observed_delays.append(delay),
    )
    try:
        with pytest.raises(StateStoreBusyError, match="State database is busy"):
            state.record_event("busy-state", "must.not.persist")
    finally:
        state_blocker.rollback()
        state_blocker.close()

    assert observed_delays == [0.0, 0.0]
    assert not any(
        event["event_type"] == "must.not.persist"
        for event in state.list_events("busy-state")
    )

    index = IndexStore(
        tmp_path / "index.db",
        busy_timeout_ms=1,
        transaction_retry_delays=(0.0, 0.0),
    )
    index_blocker = _hold_writer_lock(index.database_path)
    observed_delays.clear()
    try:
        with pytest.raises(IndexBusyError, match="index is busy"):
            with index._write_transaction() as connection:
                connection.execute("CREATE TABLE must_not_persist(value TEXT)")
    finally:
        index_blocker.rollback()
        index_blocker.close()

    assert observed_delays == [0.0, 0.0]
    with sqlite3.connect(index.database_path) as connection:
        assert connection.execute(
            "SELECT name FROM sqlite_master WHERE name = 'must_not_persist'"
        ).fetchone() is None


def test_abrupt_process_exit_respects_transaction_boundary_and_wal_reopens(
    tmp_path: Path,
) -> None:
    database = tmp_path / "state.db"
    state = StateStore(database)
    state.create_run(_run("interrupt-run"))

    uncommitted = _run_crashing_process(
        _INTERRUPT_EVENT_SCRIPT,
        database,
        "interrupt-run",
        "process.uncommitted",
        "no-commit",
    )
    committed = _run_crashing_process(
        _INTERRUPT_EVENT_SCRIPT,
        database,
        "interrupt-run",
        "process.committed",
        "commit",
    )

    assert uncommitted.returncode == 23
    assert committed.returncode == 23
    reopened = StateStore(database)
    event_types = {
        event["event_type"] for event in reopened.list_events("interrupt-run")
    }
    assert "process.uncommitted" not in event_types
    assert "process.committed" in event_types
    assert _journal_mode(database) == "wal"
    reopened.record_event("interrupt-run", "process.recovered")
    _assert_database_ok(database)

    index_database = tmp_path / "index.db"
    IndexStore(index_database).verify()
    reopened_index = IndexStore(index_database)
    assert reopened_index.journal_mode == "wal"
    reopened_index.verify()
    _assert_database_ok(index_database)


def test_interrupted_state_migration_rolls_back_and_resumes(tmp_path: Path) -> None:
    database = tmp_path / "legacy-state.db"
    _create_state_v1_database(database)

    interrupted = _run_crashing_process(
        _INTERRUPT_STATE_MIGRATION_SCRIPT,
        database,
    )

    assert interrupted.returncode == 24
    with sqlite3.connect(database) as connection:
        assert connection.execute(
            "SELECT value FROM schema_metadata WHERE key = 'schema_version'"
        ).fetchone() == ("1",)
        assert connection.execute(
            "SELECT name FROM sqlite_master WHERE name = 'interrupted_state_probe'"
        ).fetchone() is None

    reopened = StateStore(database)
    assert reopened.schema_version == SCHEMA_VERSION
    _assert_database_ok(database)


def test_interrupted_index_migration_rolls_back_and_resumes(tmp_path: Path) -> None:
    database = tmp_path / "legacy-index.db"
    with sqlite3.connect(database, isolation_level=None) as connection:
        assert apply_migrations(connection, target_version=1) == 1

    interrupted = _run_crashing_process(
        _INTERRUPT_INDEX_MIGRATION_SCRIPT,
        database,
    )

    assert interrupted.returncode == 25
    with sqlite3.connect(database) as connection:
        assert schema_version(connection) == 1
        assert connection.execute(
            "SELECT name FROM sqlite_master WHERE name = 'interrupted_index_probe'"
        ).fetchone() is None

    reopened = IndexStore(database)
    assert reopened.schema_version == LATEST_SCHEMA_VERSION
    reopened.verify()


def _run(run_id: str) -> RunRecord:
    return RunRecord(
        run_id=run_id,
        original_task="Stress isolated SQLite persistence",
        model="deterministic",
        workspace="workspace",
        planner_output={"goal": "stress storage", "steps": []},
        graph_data={"version": 1, "tasks": []},
    )


def _task(task_id: str) -> TaskSpec:
    return TaskSpec(
        task_id=task_id,
        title=f"Exercise {task_id}",
        description="Exercise one isolated concurrent storage operation.",
    )


def _active_trace(store: StateStore, run_id: str) -> TraceRecorder:
    store.create_run(_run(run_id))
    recorder = TraceRecorder(run_id, sink=StateStoreTraceSink(store))
    recorder.start_trace()
    return recorder


def _replay_request(
    store: StateStore,
    tmp_path: Path,
    index: int,
) -> ReplayRequest:
    run_id = f"replay-source-{index}"
    recorder = _active_trace(store, run_id)
    trace = recorder.finish_trace()
    return ReplayRequest(
        replay_id=f"replay-{index}",
        source_trace_id=trace.trace_id,
        source_run_id=run_id,
        mode=ReplayMode.OFFLINE,
        isolated_workspace=str(tmp_path / "replays" / str(index)),
    )


def _prepare_tool_audit(
    store: StateStore,
    workspace: Path,
    index: int,
):
    task_id = f"audit-{index}"
    invocation = ToolInvocation(
        invocation_id=f"invocation-{index}",
        run_id=_STRESS_RUN_ID,
        task_id=task_id,
        tool_name="filesystem.write",
        tool_version=ToolVersion(major=1),
        arguments={"path": f"generated-{index}.txt", "content": "bounded"},
        requested_capabilities=(
            ToolCapability(
                name=ToolCapabilityName.FILESYSTEM_WRITE,
                scope=CapabilityScope(roots=(str(workspace.resolve()),)),
            ),
        ),
        context=ToolInvocationContext(
            workspace_identity=str(workspace.resolve()),
            worktree_identity=str(workspace.resolve()),
            caller_role="coder",
            workspace_trusted=True,
            provider_consented=True,
        ),
        idempotency_key=None,
    )
    decision = ToolPolicyDecision(
        outcome=ToolPolicyOutcome.ALLOW_WITH_CONSTRAINTS,
        rule_id="allow.scoped_mutation",
        reason="Bounded contention fixture",
        invocation_id=invocation.invocation_id,
        invocation_revision=invocation.invocation_revision,
        capability_fingerprint=capability_fingerprint(
            invocation.requested_capabilities
        ),
        arguments_sha256=sha256_json(invocation.arguments),
        constraints=invocation.requested_capabilities,
    )
    store.record_tool_invocation(invocation)
    store.record_tool_policy_decision(_STRESS_RUN_ID, decision)
    started = store.mark_tool_invocation_started(
        _STRESS_RUN_ID,
        invocation.invocation_id,
    )
    result = ToolResult(
        invocation_id=invocation.invocation_id,
        invocation_revision=invocation.invocation_revision,
        status=ToolInvocationStatus.SUCCEEDED,
        structured_output={"written": True},
        resource_usage=ToolResourceUsage(file_mutations=1, written_bytes=7),
        policy_decision=decision,
    )
    completed = store.complete_tool_invocation(_STRESS_RUN_ID, result)
    return build_tool_audit_record(
        invocation,
        result,
        audit_id=f"audit-record-{index}",
        started_at=started.started_at,
        completed_at=completed.completed_at,
        affected_resource_hashes={f"generated-{index}.txt": "a" * 64},
    )


def _index_bundle(index: int):
    repository = repository_identity(f"example/sqlite-{index}")
    workspace = workspace_identity(repository.repository_id, [""])
    name = f"sqlite-{index}"
    project_identity = project_id(
        repository.repository_id,
        "",
        ProjectKind.PYTHON,
        name=name,
    )
    project = Project(
        project_id=project_identity,
        repository_id=repository.repository_id,
        name=name,
        kind=ProjectKind.PYTHON,
        root="",
        source_roots=("src",),
        test_roots=("tests",),
        generated_roots=(),
        manifest_paths=("pyproject.toml",),
    )
    source = f"VALUE = {index}\n".encode()
    source_file = SourceFile(
        file_id=file_id(repository.repository_id, "src/value.py"),
        repository_id=repository.repository_id,
        project_id=project.project_id,
        relative_path="src/value.py",
        language=SourceLanguage.PYTHON,
        content_hash=content_hash(source),
        size_bytes=len(source),
        parser_name="python-ast",
        parser_version="1",
    )
    parser_versions = {"python-ast": "1"}
    project_hash = project_map_fingerprint((project,))
    graph_hash = graph_fingerprint(())
    source_fingerprint = file_set_fingerprint((source_file,))
    snapshot = IndexSnapshot(
        snapshot_id=snapshot_id(
            repository.repository_id,
            content_hash(f"{source_fingerprint}:{index}"),
            parser_versions_fingerprint(parser_versions),
            project_hash,
            graph_hash,
        ),
        repository_id=repository.repository_id,
        workspace_id=workspace.workspace_id,
        state=IndexState.CURRENT,
        created_at=datetime(2026, 1, 1, tzinfo=timezone.utc)
        + timedelta(seconds=index),
        completed_at=datetime(2026, 1, 1, tzinfo=timezone.utc)
        + timedelta(seconds=index + 1),
        file_count=1,
        project_map_hash=project_hash,
        graph_hash=graph_hash,
        parser_versions=parser_versions,
        source_fingerprint=source_fingerprint,
    )
    return repository, workspace, snapshot, (project,), (source_file,)


def _run_concurrently(operations: list[Callable[[], Any]]) -> list[Any]:
    barrier = Barrier(len(operations))

    def invoke(operation: Callable[[], Any]) -> Any:
        barrier.wait(timeout=15)
        return operation()

    with ThreadPoolExecutor(
        max_workers=len(operations),
        thread_name_prefix="sqlite-stress",
    ) as executor:
        futures = [executor.submit(invoke, operation) for operation in operations]
        return [future.result(timeout=30) for future in futures]


def _hold_writer_lock(database: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(database, isolation_level=None)
    connection.execute("BEGIN IMMEDIATE")
    return connection


def _run_crashing_process(
    script: str,
    database: Path,
    *arguments: str,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-c", script, str(database), *arguments],
        check=False,
        capture_output=True,
        text=True,
        timeout=15,
    )


def _create_state_v1_database(database: Path) -> None:
    v1_sql = SCHEMA_SQL.split("CREATE TABLE IF NOT EXISTS worktrees", 1)[0]
    with sqlite3.connect(database) as connection:
        connection.executescript(v1_sql)
        connection.execute(
            "INSERT INTO schema_metadata(key, value) VALUES (?, ?)",
            ("schema_version", "1"),
        )


def _journal_mode(database: Path) -> str:
    with sqlite3.connect(database) as connection:
        row = connection.execute("PRAGMA journal_mode").fetchone()
    return str(row[0]).casefold() if row else ""


def _assert_database_ok(database: Path) -> None:
    with sqlite3.connect(database) as connection:
        assert connection.execute("PRAGMA quick_check").fetchone() == ("ok",)
