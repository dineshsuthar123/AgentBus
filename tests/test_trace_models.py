from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from agentbus.trace import (
    TRACE_SCHEMA_VERSION,
    Trace,
    TraceCheckpoint,
    TraceEvent,
    TraceFailure,
    TraceSpan,
    TraceSpanType,
    TraceStatus,
)


NOW = datetime(2026, 1, 2, 3, 4, tzinfo=timezone.utc)


def _span(
    span_id: str,
    sequence: int,
    *,
    parent_span_id: str | None = None,
    span_type: TraceSpanType = TraceSpanType.TASK,
) -> TraceSpan:
    return TraceSpan(
        trace_id="trace-1",
        span_id=span_id,
        parent_span_id=parent_span_id,
        run_id="run-1",
        span_type=span_type,
        name=span_id,
        sequence=sequence,
        started_at=NOW,
        ended_at=NOW + timedelta(seconds=1),
        status=TraceStatus.SUCCEEDED,
    )


def _trace(*items: TraceSpan) -> Trace:
    root = _span("root", 1, span_type=TraceSpanType.RUN)
    return Trace(
        trace_id="trace-1",
        run_id="run-1",
        root_span_id="root",
        status=TraceStatus.SUCCEEDED,
        created_at=NOW,
        completed_at=NOW + timedelta(seconds=2),
        spans=[root, *items],
    )


def test_trace_contract_is_versioned_and_hierarchical() -> None:
    trace = _trace(_span("task-1", 2, parent_span_id="root"))

    assert trace.schema_version == TRACE_SCHEMA_VERSION
    assert trace.spans[1].parent_span_id == trace.root_span_id
    assert trace.model_dump(mode="json")["spans"][1]["span_type"] == "task"


def test_trace_sanitizes_sensitive_attributes_and_failure_text() -> None:
    password_value = "synthetic-" + "password-value"
    failed = TraceSpan(
        trace_id="trace-1",
        span_id="task-1",
        parent_span_id="root",
        run_id="run-1",
        span_type=TraceSpanType.TASK,
        name="task",
        sequence=2,
        started_at=NOW,
        ended_at=NOW,
        status=TraceStatus.FAILED,
        attributes={"api_key": "real-key", "nested": {"token": "real-token"}},
        failure=TraceFailure(
            category="provider",
            message="authorization: Bearer abc.def",
            details={"password": password_value},
        ),
    )

    trace = _trace(failed)

    assert trace.spans[1].attributes == {
        "api_key": "[REDACTED]",
        "nested": {"token": "[REDACTED]"},
    }
    assert "[REDACTED]" in trace.spans[1].failure.message
    assert trace.spans[1].failure.details["password"] == "[REDACTED]"


@pytest.mark.parametrize(
    ("span", "message"),
    [
        (
            lambda: TraceSpan(
                trace_id="trace-1",
                span_id="span-1",
                run_id="run-1",
                span_type=TraceSpanType.TASK,
                name="task",
                sequence=1,
                started_at=NOW.replace(tzinfo=None),
            ),
            "timezone-aware",
        ),
        (
            lambda: TraceSpan(
                trace_id="trace-1",
                span_id="span-1",
                run_id="run-1",
                span_type=TraceSpanType.TASK,
                name="task",
                sequence=1,
                started_at=NOW,
                ended_at=NOW,
                status=TraceStatus.FAILED,
            ),
            "safe failure",
        ),
    ],
)
def test_trace_span_rejects_ambiguous_state(span, message: str) -> None:
    with pytest.raises(ValidationError, match=message):
        span()


def test_trace_rejects_duplicate_global_sequence() -> None:
    event = TraceEvent(
        trace_id="trace-1",
        event_id="event-1",
        run_id="run-1",
        span_id="root",
        event_type="run.updated",
        sequence=1,
        timestamp=NOW,
    )

    with pytest.raises(ValidationError, match="deterministic sequence 1"):
        Trace(
            trace_id="trace-1",
            run_id="run-1",
            root_span_id="root",
            spans=[_span("root", 1, span_type=TraceSpanType.RUN)],
            events=[event],
        )


def test_trace_rejects_cycles_and_cross_trace_members() -> None:
    root = _span("root", 1, span_type=TraceSpanType.RUN)
    child = _span("child", 2, parent_span_id="root")
    grandchild = _span("grandchild", 3, parent_span_id="child")
    root = root.model_copy(update={"parent_span_id": "grandchild"})

    with pytest.raises(ValidationError, match="root span must be a parentless"):
        Trace(
            trace_id="trace-1",
            run_id="run-1",
            root_span_id="root",
            spans=[root, child, grandchild],
        )

    foreign = child.model_copy(update={"trace_id": "trace-2"})
    with pytest.raises(ValidationError, match="does not belong"):
        Trace(
            trace_id="trace-1",
            run_id="run-1",
            root_span_id="root",
            spans=[_span("root", 1, span_type=TraceSpanType.RUN), foreign],
        )


def test_trace_checkpoint_must_reference_a_known_span() -> None:
    checkpoint = TraceCheckpoint(
        checkpoint_id="checkpoint-1",
        trace_id="trace-1",
        run_id="run-1",
        span_id="missing",
        sequence=2,
        label="before verification",
        created_at=NOW,
    )

    with pytest.raises(ValidationError, match="references an unknown span"):
        Trace(
            trace_id="trace-1",
            run_id="run-1",
            root_span_id="root",
            spans=[_span("root", 1, span_type=TraceSpanType.RUN)],
            checkpoints=[checkpoint],
        )
