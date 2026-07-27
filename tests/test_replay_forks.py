from datetime import datetime, timezone
from pathlib import Path

import pytest

from agentbus.replay import (
    DriftCategory,
    ForkManager,
    ForkRequest,
    ReplayConsentRequiredError,
    ReplayEngine,
    ReplayIncompatibleError,
)
from agentbus.trace import (
    ContentAddressedStore,
    Trace,
    TraceLinkType,
    TraceOutput,
    TraceSpan,
    TraceSpanType,
    TraceStatus,
)


NOW = datetime(2026, 1, 2, tzinfo=timezone.utc)


def _source(tmp_path: Path):
    store = ContentAddressedStore(tmp_path / "objects")
    metadata = store.put_json(
        {"status": "complete"},
        producing_span_id="root",
    )
    output = store.reference_output(
        metadata,
        reference_id="run-output",
        name="run state",
    )
    root = TraceSpan(
        trace_id="trace-1",
        span_id="root",
        run_id="run-1",
        span_type=TraceSpanType.RUN,
        name="run",
        sequence=1,
        started_at=NOW,
        ended_at=NOW,
        status=TraceStatus.SUCCEEDED,
        output_references=[output],
        approval_references=["approval-1"],
    )
    trace = Trace(
        trace_id="trace-1",
        run_id="run-1",
        root_span_id="root",
        status=TraceStatus.SUCCEEDED,
        created_at=NOW,
        completed_at=NOW,
        spans=[root],
    )
    return store, trace


def test_fork_creates_new_linked_trace_and_automatic_comparison(
    tmp_path: Path,
) -> None:
    store, source = _source(tmp_path)
    manager = ForkManager(
        store,
        ReplayEngine(store),
        clock=lambda: NOW,
    )
    request = ForkRequest(
        replay_id="replay-1",
        source_trace_id=source.trace_id,
        source_run_id=source.run_id,
        changed_inputs={"retry_limit": 3},
    )

    result = manager.fork(source, request)

    assert result.fork_trace.trace_id != source.trace_id
    assert result.fork_trace.run_id != source.run_id
    assert source.trace_id == "trace-1"
    assert result.fork_trace.links[0].link_type == TraceLinkType.FORKED_FROM
    assert result.fork_trace.links[0].trace_id == source.trace_id
    assert result.fork_trace.replay.forked is True
    assert result.comparison.left_trace_id == source.trace_id
    assert DriftCategory.EXPECTED in result.comparison.categories
    assert result.changed_input_names == ["retry_limit"]


def test_fork_invalidates_historical_approvals_on_policy_change(
    tmp_path: Path,
) -> None:
    store, source = _source(tmp_path)
    result = ForkManager(
        store,
        ReplayEngine(store),
        clock=lambda: NOW,
    ).fork(
        source,
        ForkRequest(
            replay_id="replay-1",
            source_trace_id=source.trace_id,
            source_run_id=source.run_id,
            changed_inputs={"policy_configuration": {"default": "deny"}},
        ),
    )

    assert result.historical_approvals_invalidated is True
    assert result.fork_trace.spans[0].approval_references == []
    assert result.fork_trace.spans[0].attributes["fresh_approval_required"] is True


def test_fork_never_silently_selects_live_provider(tmp_path: Path) -> None:
    store, source = _source(tmp_path)
    manager = ForkManager(store, ReplayEngine(store))
    base = {
        "replay_id": "replay-1",
        "source_trace_id": source.trace_id,
        "source_run_id": source.run_id,
        "changed_inputs": {"model_route": {"provider": "azure"}},
    }

    with pytest.raises(ReplayConsentRequiredError, match="explicit"):
        manager.fork(source, ForkRequest(**base))

    with pytest.raises(ReplayIncompatibleError, match="no provider was called"):
        manager.fork(
            source,
            ForkRequest(**base, live_provider_consent=True),
        )


def test_fork_rejects_unknown_change_surface() -> None:
    with pytest.raises(ValueError, match="Unsupported"):
        ForkRequest(
            source_trace_id="trace-1",
            source_run_id="run-1",
            changed_inputs={"arbitrary_command": "do not run"},
        )
