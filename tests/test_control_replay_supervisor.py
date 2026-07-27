from __future__ import annotations

import threading
from pathlib import Path

import pytest

from agentbus.config import AgentBusConfig
from agentbus.control.errors import ControlPlaneUnavailableError
from agentbus.control.models import ReplayCreateRequest
from agentbus.control.replay_supervisor import BackgroundReplaySupervisor
from agentbus.control.services import ControlQueryService
from agentbus.execution.models import RunRecord
from agentbus.execution.state_store import StateStore
from agentbus.replay.service import TraceReplayService
from agentbus.trace import RuntimeTrace, TraceSpanType, TraceStatus
from agentbus.trace.sealing import seal_run_provenance


def _query_with_trace(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    config = AgentBusConfig(
        workspace_dir=str(workspace),
        state_db=str(tmp_path / "state.db"),
    )
    store = StateStore(config.state_database_path)
    store.create_run(
        RunRecord(
            run_id="run-control-replay",
            original_task="Replay through control",
            model="deterministic",
            workspace=str(workspace),
            graph_data={"version": 1, "tasks": []},
        )
    )
    runtime = RuntimeTrace.open(
        store,
        "run-control-replay",
        object_root=config.trace_store_path,
        workspace=workspace,
    )
    with runtime.scope(runtime.root_context):
        runtime.call(
            TraceSpanType.VERIFIER,
            "control replay verifier",
            lambda: {"passed": True},
            capture="json",
        )
        runtime.call(
            TraceSpanType.REVIEWER,
            "control replay reviewer",
            lambda: {"approved": True},
            capture="json",
        )
    trace = runtime.finish(status=TraceStatus.SUCCEEDED)
    assert trace is not None
    seal_run_provenance(
        trace,
        state_store=store,
        object_store=runtime.object_store,
        configuration={"provider": "deterministic"},
        task_graph={"version": 1, "tasks": []},
        final_repository_tree_sha256="d" * 64,
    )
    return config, store, ControlQueryService(config, store), trace


def test_background_replay_runs_real_providerless_engine(tmp_path: Path) -> None:
    _config, _store, query, trace = _query_with_trace(tmp_path)
    supervisor = BackgroundReplaySupervisor(query)
    try:
        accepted = supervisor.submit(
            trace.run_id,
            ReplayCreateRequest(mode="offline"),
        )
        terminal = supervisor.wait(accepted.replay_id, timeout=5)
        listed = query.replays(limit=1)
        cancelled = supervisor.cancel(accepted.replay_id)
    finally:
        supervisor.shutdown()

    assert accepted.status == "pending"
    assert terminal.status == "succeeded"
    assert terminal.provider_calls == 0
    assert terminal.network_calls == 0
    assert terminal.isolated is False
    assert listed.replays[0].replay_id == accepted.replay_id
    assert cancelled.cancellation_requested is False


def test_background_replay_runs_offline_fork(tmp_path: Path) -> None:
    _config, _store, query, trace = _query_with_trace(tmp_path)
    supervisor = BackgroundReplaySupervisor(query)
    try:
        accepted = supervisor.submit(
            trace.run_id,
            ReplayCreateRequest(
                mode="offline",
                fork=True,
                changed_inputs={"task_text": "Changed offline task"},
            ),
        )
        terminal = supervisor.wait(accepted.replay_id, timeout=5)
    finally:
        supervisor.shutdown()

    assert terminal.status == "succeeded"
    assert terminal.fork is True
    assert terminal.changed_input_names == ["task_text"]
    assert terminal.provider_calls == 0
    assert terminal.network_calls == 0


def test_background_replay_cancellation_is_cooperative_and_capacity_is_bounded(
    tmp_path: Path,
) -> None:
    config, store, query, trace = _query_with_trace(tmp_path)
    started = threading.Event()
    completed = threading.Event()
    gate = threading.Event()

    class GatedReplayService(TraceReplayService):
        def replay(self, identifier, request):
            started.set()
            while not self.cancelled():
                gate.wait(0.01)
            try:
                return super().replay(identifier, request)
            finally:
                completed.set()

    def service_factory(cancelled):
        return GatedReplayService(
            config,
            state_store=store,
            cancelled=cancelled,
        )

    supervisor = BackgroundReplaySupervisor(
        query,
        max_background_replays=1,
        service_factory=service_factory,
    )
    try:
        accepted = supervisor.submit(
            trace.run_id,
            ReplayCreateRequest(mode="offline"),
        )
        assert started.wait(5)
        with pytest.raises(ControlPlaneUnavailableError, match="capacity"):
            supervisor.submit(
                trace.run_id,
                ReplayCreateRequest(mode="offline"),
            )

        response = supervisor.cancel(accepted.replay_id)
        assert completed.wait(5)
        terminal = supervisor.wait(accepted.replay_id, timeout=5)
    finally:
        supervisor.shutdown()

    assert response.cancellation_requested is True
    assert terminal.status == "cancelled"
    assert terminal.provider_calls == 0
    assert terminal.network_calls == 0
    assert supervisor.has_active_replays() is False
