from datetime import datetime, timezone

from agentbus.replay import DriftCategory, compare_traces
from agentbus.trace import (
    Trace,
    TraceFailure,
    TraceLink,
    TraceLinkType,
    TraceOutput,
    TraceSpan,
    TraceSpanType,
    TraceStatus,
)


NOW = datetime(2026, 1, 2, tzinfo=timezone.utc)


def _span(
    trace_id: str,
    span_id: str,
    span_type: TraceSpanType,
    sequence: int,
    *,
    status=TraceStatus.SUCCEEDED,
    output_hash="1" * 64,
    attributes=None,
):
    return TraceSpan(
        trace_id=trace_id,
        span_id=span_id,
        parent_span_id=None if span_type == TraceSpanType.RUN else "root",
        run_id=f"run-{trace_id[-1]}",
        span_type=span_type,
        name=span_type.value,
        sequence=sequence,
        started_at=NOW,
        ended_at=NOW,
        status=status,
        output_references=[
            TraceOutput(
                reference_id=f"output-{span_id}",
                name="output",
                sha256=output_hash,
                byte_length=1,
            )
        ],
        attributes=attributes or {},
    )


def _trace(
    trace_id: str,
    spans,
    *,
    status=TraceStatus.SUCCEEDED,
    links=(),
):
    return Trace(
        trace_id=trace_id,
        run_id=f"run-{trace_id[-1]}",
        root_span_id="root",
        status=status,
        created_at=NOW,
        completed_at=NOW,
        spans=list(spans),
        links=list(links),
    )


def test_comparison_aligns_semantic_spans_despite_different_ids() -> None:
    left = _trace(
        "trace-1",
        [
            _span("trace-1", "root", TraceSpanType.RUN, 1),
            _span("trace-1", "provider-left", TraceSpanType.PROVIDER_RESPONSE, 2),
        ],
    )
    right = _trace(
        "trace-2",
        [
            _span("trace-2", "root", TraceSpanType.RUN, 1),
            _span("trace-2", "provider-right", TraceSpanType.PROVIDER_RESPONSE, 2),
        ],
    )

    comparison = compare_traces(left, right, clock=lambda: NOW)

    assert comparison.summary.changed_spans == 0
    assert comparison.summary.unchanged_spans == 2
    assert comparison.categories == []


def test_comparison_classifies_model_policy_tool_and_ordering_drift() -> None:
    left = _trace(
        "trace-1",
        [
            _span("trace-1", "root", TraceSpanType.RUN, 1),
            _span("trace-1", "provider-a", TraceSpanType.PROVIDER_RESPONSE, 2),
            _span(
                "trace-1",
                "policy-a",
                TraceSpanType.TOOL_POLICY,
                3,
                attributes={"result": {"outcome": "allow"}},
            ),
            _span("trace-1", "tool-a", TraceSpanType.TOOL_INVOCATION, 4),
        ],
    )
    right = _trace(
        "trace-2",
        [
            _span("trace-2", "root", TraceSpanType.RUN, 1),
            _span(
                "trace-2",
                "provider-b",
                TraceSpanType.PROVIDER_RESPONSE,
                4,
                output_hash="2" * 64,
            ),
            _span(
                "trace-2",
                "policy-b",
                TraceSpanType.TOOL_POLICY,
                3,
                attributes={"result": {"outcome": "deny"}},
            ),
            _span(
                "trace-2",
                "tool-b",
                TraceSpanType.TOOL_INVOCATION,
                2,
                output_hash="3" * 64,
            ),
        ],
    )

    comparison = compare_traces(left, right, clock=lambda: NOW)

    assert DriftCategory.MODEL in comparison.categories
    assert DriftCategory.POLICY in comparison.categories
    assert DriftCategory.TOOL in comparison.categories
    assert DriftCategory.ORDERING in comparison.categories


def test_comparison_marks_status_regression_and_expected_fork_span() -> None:
    left = _trace(
        "trace-1",
        [_span("trace-1", "root", TraceSpanType.RUN, 1)],
    )
    failed_root = _span(
        "trace-2",
        "root",
        TraceSpanType.RUN,
        1,
    ).model_copy(
        update={
            "status": TraceStatus.FAILED,
            "failure": TraceFailure(
                category="verification",
                message="failed",
            ),
        }
    )
    from agentbus.trace import TraceSpan as Span

    failed_root = Span.model_validate(failed_root.model_dump())
    added = _span("trace-2", "fork-extra", TraceSpanType.CUSTOM, 2)
    right = _trace(
        "trace-2",
        [failed_root, added],
        status=TraceStatus.FAILED,
        links=[
            TraceLink(
                link_type=TraceLinkType.FORKED_FROM,
                trace_id="trace-1",
            )
        ],
    )

    comparison = compare_traces(left, right, clock=lambda: NOW)

    assert DriftCategory.REGRESSION in comparison.categories
    extra = next(item for item in comparison.spans if "custom" in item.semantic_key)
    assert extra.categories == [DriftCategory.EXPECTED]
