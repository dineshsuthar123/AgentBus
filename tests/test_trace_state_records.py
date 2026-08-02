from datetime import datetime, timedelta, timezone

import pytest

from agentbus.execution.models import RunRecord
from agentbus.execution.state_store import (
    ComparisonRecordConflictError,
    ProvenanceRecordConflictError,
    ReplaySessionConflictError,
    StateStore,
)
from agentbus.replay.comparison import compare_traces
from agentbus.replay.session import (
    ReplayRequest,
    ReplayResult,
    ReplaySession,
    ReplaySessionStatus,
)
from agentbus.trace import (
    IntelligenceDriftCategory,
    ProvenanceBuilder,
    ReplayMode,
    ReplayabilityLevel,
    StateStoreTraceSink,
    TraceRecorder,
    TraceStatus,
)


class ControlledClock:
    def __init__(self) -> None:
        self.value = datetime(2026, 2, 3, tzinfo=timezone.utc)

    def __call__(self) -> datetime:
        current = self.value
        self.value += timedelta(milliseconds=1)
        return current


def _finalized_trace(store: StateStore, run_id: str):
    store.create_run(
        RunRecord(
            run_id=run_id,
            original_task=f"Trace {run_id}",
            model="deterministic",
            workspace="workspace",
        )
    )
    recorder = TraceRecorder(
        run_id,
        sink=StateStoreTraceSink(store),
        clock=ControlledClock(),
    )
    recorder.start_trace()
    return recorder.finish_trace()


def _manifest(trace):
    return ProvenanceBuilder(clock=ControlledClock()).build(
        trace,
        configuration={"provider": "deterministic"},
        policy_version="1",
        policy_document={"default": "deny"},
        task_graph={"version": 1, "tasks": []},
        replayability=ReplayabilityLevel.EXACTLY_REPLAYABLE,
    )


def _validated_session(session: ReplaySession, **updates) -> ReplaySession:
    return ReplaySession.model_validate(
        session.model_copy(update=updates).model_dump()
    )


def test_provenance_manifest_is_durable_idempotent_and_immutable(tmp_path) -> None:
    store = StateStore(tmp_path / "state.db")
    trace = _finalized_trace(store, "run-provenance")
    manifest = _manifest(trace)

    assert store.record_provenance_manifest(manifest) == manifest
    assert store.record_provenance_manifest(manifest) == manifest
    assert store.get_provenance_manifest(trace.trace_id) == manifest
    assert store.get_run_provenance_manifest(trace.run_id) == manifest

    changed = manifest.model_copy(update={"policy_version": "2"})
    with pytest.raises(ProvenanceRecordConflictError, match="immutable"):
        store.record_provenance_manifest(changed)


def test_replay_session_lifecycle_is_durable_bounded_and_path_safe(tmp_path) -> None:
    store = StateStore(tmp_path / "state.db")
    trace = _finalized_trace(store, "run-replay")
    request = ReplayRequest(
        replay_id="replay-1",
        source_trace_id=trace.trace_id,
        source_run_id=trace.run_id,
        mode=ReplayMode.OFFLINE,
        isolated_workspace=str(tmp_path / "private" / "replay"),
    )

    pending = store.create_replay_session(request)
    assert pending.status == ReplaySessionStatus.PENDING
    assert pending.isolated_workspace == "[ISOLATED_REPLAY_WORKSPACE]"
    assert (
        store.get_replay_request("replay-1").isolated_workspace
        == "[ISOLATED_REPLAY_WORKSPACE]"
    )

    started_at = pending.created_at + timedelta(seconds=1)
    running = _validated_session(
        pending,
        status=ReplaySessionStatus.RUNNING,
        started_at=started_at,
    )
    assert store.record_replay_session(request, running) == running

    drifted = _validated_session(
        running,
        intelligence_drift=[IntelligenceDriftCategory.GRAPH],
    )
    assert store.record_replay_session(request, drifted) == drifted
    with pytest.raises(ReplaySessionConflictError, match="cannot be rewritten"):
        store.record_replay_session(request, running)

    terminal = _validated_session(
        drifted,
        status=ReplaySessionStatus.SUCCEEDED,
        completed_at=started_at + timedelta(seconds=1),
    )
    result = ReplayResult(
        session=terminal,
        source_status=TraceStatus.SUCCEEDED,
        replayed_status=TraceStatus.SUCCEEDED,
        result_sha256="a" * 64,
    )
    store.record_replay_session(request, terminal, result=result)

    assert store.get_replay_session("replay-1") == terminal
    assert store.get_replay_result("replay-1") == result
    assert store.list_replay_sessions(
        status=ReplaySessionStatus.SUCCEEDED
    ) == [terminal]
    changed = _validated_session(
        terminal,
        failure_message="rewrite terminal history",
    )
    with pytest.raises(ReplaySessionConflictError, match="immutable"):
        store.record_replay_session(request, changed)
    with pytest.raises(Exception, match="between 1 and 1000"):
        store.list_replay_sessions(limit=1_001)


def test_trace_comparisons_are_durable_and_immutable(tmp_path) -> None:
    store = StateStore(tmp_path / "state.db")
    left = _finalized_trace(store, "run-left")
    right = _finalized_trace(store, "run-right")
    comparison = compare_traces(
        left,
        right,
        left_provenance=_manifest(left),
        right_provenance=_manifest(right),
        clock=ControlledClock(),
    )

    assert store.record_trace_comparison(comparison) == comparison
    assert store.record_trace_comparison(comparison) == comparison
    assert store.get_trace_comparison(comparison.comparison_id) == comparison
    assert store.list_trace_comparisons(trace_id=left.trace_id) == [
        comparison
    ]

    changed = comparison.model_copy(update={"right_status": "failed"})
    with pytest.raises(ComparisonRecordConflictError, match="immutable"):
        store.record_trace_comparison(changed)
