from pathlib import Path

import pytest

from agentbus.config import AgentBusConfig
from agentbus.execution.models import RunRecord
from agentbus.execution.state_store import StateStore
from agentbus.replay import (
    ForkRequest,
    ReplayRequest,
    ReplaySessionStatus,
)
from agentbus.replay.service import TraceReplayService
from agentbus.trace import (
    ReplayMode,
    RuntimeTrace,
    TraceSpanType,
    TraceStatus,
)
from agentbus.trace.sealing import seal_run_provenance


def _service(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    config = AgentBusConfig(
        workspace_dir=str(workspace),
        state_db=str(tmp_path / "state.db"),
    )
    store = StateStore(config.state_database_path)
    return config, store, TraceReplayService(config, state_store=store)


def _record_run(
    config: AgentBusConfig,
    store: StateStore,
    run_id: str,
):
    store.create_run(
        RunRecord(
            run_id=run_id,
            original_task=f"Replay {run_id}",
            model="deterministic",
            workspace=str(config.workspace_path),
            graph_data={"version": 1, "tasks": []},
        )
    )
    runtime = RuntimeTrace.open(
        store,
        run_id,
        object_root=config.trace_store_path,
        workspace=config.workspace_path,
    )
    with runtime.scope(runtime.root_context):
        runtime.call(
            TraceSpanType.VERIFIER,
            "final verifier",
            lambda: {"passed": True, "exit_code": 0},
            capture="json",
        )
        runtime.call(
            TraceSpanType.REVIEWER,
            "final reviewer",
            lambda: {"approved": True, "issues": []},
            capture="json",
        )
    trace = runtime.finish(status=TraceStatus.SUCCEEDED)
    assert trace is not None
    manifest = seal_run_provenance(
        trace,
        state_store=store,
        object_store=runtime.object_store,
        configuration={"provider": "deterministic"},
        task_graph={"version": 1, "tasks": []},
        final_repository_tree_sha256="c" * 64,
    )
    return trace, manifest


def test_service_verifies_replays_and_persists_terminal_session(
    tmp_path,
) -> None:
    config, store, service = _service(tmp_path)
    trace, manifest = _record_run(config, store, "run-service")
    request = ReplayRequest(
        replay_id="replay-service",
        source_trace_id=trace.trace_id,
        source_run_id=trace.run_id,
        mode=ReplayMode.OFFLINE,
    )

    verification = service.verify(trace.run_id)
    replayability = service.replayability(trace.trace_id)
    prepared, pending = service.queue_replay(trace.run_id, request)
    result = service.replay(trace.run_id, prepared)

    assert verification.valid is True
    assert verification.provenance_root == manifest.integrity_root
    assert verification.object_count == 2
    assert verification.protocol_drift == []
    assert replayability.replayable_offline is True
    assert result.session.status == ReplaySessionStatus.SUCCEEDED
    assert result.session.created_at == pending.created_at
    assert result.session.provider_calls == 0
    assert result.session.network_calls == 0
    assert store.get_replay_result(request.replay_id) == result


def test_service_partial_replay_uses_owned_isolation_and_persists_it(
    tmp_path,
) -> None:
    config, store, service = _service(tmp_path)
    trace, _ = _record_run(config, store, "run-partial")
    verifier = next(
        span
        for span in trace.spans
        if span.span_type == TraceSpanType.VERIFIER
    )
    request = ReplayRequest(
        replay_id="replay-partial",
        source_trace_id=trace.trace_id,
        source_run_id=trace.run_id,
        mode=ReplayMode.OFFLINE,
        from_span_id=verifier.span_id,
    )

    prepared, pending = service.queue_replay(trace.run_id, request)
    result = service.replay(trace.run_id, prepared)

    assert result.session.status == ReplaySessionStatus.SUCCEEDED
    assert result.session.created_at == pending.created_at
    assert request.isolated_workspace is None
    assert prepared.isolated_workspace is not None
    assert (
        result.session.isolated_workspace
        == "[ISOLATED_REPLAY_WORKSPACE]"
    )
    persisted = store.get_replay_session(request.replay_id)
    assert persisted == result.session
    replay_root = (
        config.workspace_path.parent
        / ".agentbus-replays"
        / config.workspace_path.name
        / request.replay_id
    )
    assert (replay_root / "state.db").is_file()
    assert config.workspace_path not in replay_root.parents


def test_service_persists_unexpected_replay_failure(
    tmp_path,
    monkeypatch,
) -> None:
    config, store, service = _service(tmp_path)
    trace, _ = _record_run(config, store, "run-unexpected")
    request = ReplayRequest(
        replay_id="replay-unexpected",
        source_trace_id=trace.trace_id,
        source_run_id=trace.run_id,
        mode=ReplayMode.OFFLINE,
    )

    class BrokenEngine:
        def replay(self, *_args, **_kwargs):
            raise RuntimeError("unexpected private failure")

    monkeypatch.setattr(service, "_engine", lambda *_args: BrokenEngine())

    with pytest.raises(RuntimeError, match="unexpected private failure"):
        service.replay(trace.run_id, request)

    persisted = store.get_replay_session(request.replay_id)
    assert persisted.status == ReplaySessionStatus.FAILED
    assert persisted.failure_category == "RuntimeError"
    assert persisted.completed_at is not None


def test_service_exports_imports_and_replays_archive_without_execution_on_import(
    tmp_path,
) -> None:
    config, store, service = _service(tmp_path)
    trace, _ = _record_run(config, store, "run-archive")
    archive = tmp_path / "run.agentbus-trace"

    exported = service.export_trace(trace.run_id, archive)
    imported = service.import_archive(archive)

    assert exported.trace_id == trace.trace_id
    assert imported.trace == trace
    assert imported.objects_imported is True
    assert store.list_replay_sessions() == []

    replayed = service.replay_archive(archive)
    assert replayed.replay.session.status == ReplaySessionStatus.SUCCEEDED
    assert replayed.replay.session.provider_calls == 0
    assert replayed.replay.session.network_calls == 0


def test_service_comparison_is_idempotent_and_fork_stays_offline(
    tmp_path,
) -> None:
    config, store, service = _service(tmp_path)
    left, _ = _record_run(config, store, "run-left")
    right, _ = _record_run(config, store, "run-right")

    first = service.compare(left.run_id, right.trace_id)
    second = service.compare(left.trace_id, right.run_id)
    fork_request = ForkRequest(
        replay_id="replay-fork",
        source_trace_id=left.trace_id,
        source_run_id=left.run_id,
        changed_inputs={"task_text": "Changed offline task"},
    )
    pending = store.create_replay_session(
        ReplayRequest(
            replay_id=fork_request.replay_id,
            source_trace_id=fork_request.source_trace_id,
            source_run_id=fork_request.source_run_id,
            mode=fork_request.mode,
            fork=True,
            changed_inputs=fork_request.changed_inputs,
        )
    )
    fork = service.fork(left.run_id, fork_request)

    assert first == second
    assert store.get_trace_comparison(first.comparison_id) == first
    assert fork.replay.session.status == ReplaySessionStatus.SUCCEEDED
    assert fork.replay.session.created_at == pending.created_at
    assert fork.replay.session.provider_calls == 0
    assert fork.replay.session.network_calls == 0
    assert fork.fork_trace.trace_id != left.trace_id
    assert store.get_replay_result("replay-fork") == fork.replay
