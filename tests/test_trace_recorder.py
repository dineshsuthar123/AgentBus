from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import pytest

from agentbus.trace import (
    TraceEventType,
    TraceFailure,
    TraceRecorder,
    TraceRecordingError,
    TraceSpanType,
    TraceStatus,
    trace_context,
)


class ControlledClock:
    def __init__(self) -> None:
        self.current = datetime(2026, 1, 2, tzinfo=timezone.utc)

    def __call__(self) -> datetime:
        value = self.current
        self.current += timedelta(milliseconds=1)
        return value


class BrokenSink:
    def write_span(self, span) -> None:
        raise RuntimeError("authorization=secret-value")

    def write_event(self, event) -> None:
        raise RuntimeError("authorization=secret-value")

    def write_checkpoint(self, checkpoint) -> None:
        raise RuntimeError("authorization=secret-value")


def test_recorder_builds_causal_trace_with_deterministic_order() -> None:
    recorder = TraceRecorder("run-1", clock=ControlledClock())
    root = recorder.start_trace()

    with trace_context(root):
        with recorder.span(
            TraceSpanType.TASK,
            "step one",
            task_id="step-1",
        ) as task:
            recorder.record_event("task.observed", attributes={"count": 1})
            checkpoint = recorder.checkpoint("task complete")

    trace = recorder.finish_trace()

    assert trace.status == TraceStatus.SUCCEEDED
    assert [item.sequence for item in trace.spans] == [1, 3]
    assert checkpoint.span_id == task.span_id
    all_sequences = [
        *(span.sequence for span in trace.spans),
        *(event.sequence for event in trace.events),
        *(item.sequence for item in trace.checkpoints),
    ]
    assert sorted(all_sequences) == list(range(1, len(all_sequences) + 1))
    assert trace.spans[1].parent_span_id == trace.root_span_id


def test_span_context_records_safe_failure_and_reraises() -> None:
    recorder = TraceRecorder("run-1", clock=ControlledClock())
    root = recorder.start_trace()

    with trace_context(root):
        with pytest.raises(ValueError, match="token"):
            with recorder.span(TraceSpanType.VERIFIER, "verify"):
                raise ValueError("token=private-value")

    trace = recorder.finish_trace(
        status=TraceStatus.FAILED,
        failure=TraceFailure(
            category="verification",
            message="verification failed",
        ),
    )

    failed = next(span for span in trace.spans if span.span_type == TraceSpanType.VERIFIER)
    assert failed.status == TraceStatus.FAILED
    assert "[REDACTED]" in failed.failure.message


def test_duplicate_terminal_span_is_rejected() -> None:
    recorder = TraceRecorder("run-1", clock=ControlledClock())
    root = recorder.start_trace()
    with trace_context(root):
        child = recorder.start_span(TraceSpanType.TASK, "task")
        recorder.finish_span(child.span_id)

    with pytest.raises(TraceRecordingError, match="already completed"):
        recorder.finish_span(child.span_id)


def test_optional_sink_failure_does_not_become_execution_truth() -> None:
    recorder = TraceRecorder(
        "run-1",
        sink=BrokenSink(),
        clock=ControlledClock(),
    )

    recorder.start_trace()
    trace = recorder.finish_trace()

    assert trace.status == TraceStatus.SUCCEEDED
    assert trace.attributes["recording_degraded"] is True
    assert recorder.recording_errors
    assert "secret-value" not in repr(recorder.recording_errors)


def test_critical_sink_failure_is_reported() -> None:
    recorder = TraceRecorder(
        "run-1",
        sink=BrokenSink(),
        critical_sink=True,
    )

    with pytest.raises(TraceRecordingError, match="Critical trace span"):
        recorder.start_trace()


def test_sequence_claims_remain_unique_under_concurrency() -> None:
    recorder = TraceRecorder("run-1", clock=ControlledClock())
    recorder.start_trace()

    with ThreadPoolExecutor(max_workers=8) as executor:
        futures = [
            executor.submit(
                recorder.record_event,
                TraceEventType.RECORDING_DEGRADED,
                attributes={"worker": index},
            )
            for index in range(100)
        ]
        for future in futures:
            future.result()

    trace = recorder.finish_trace()
    sequences = [event.sequence for event in trace.events]
    assert len(sequences) == len(set(sequences))
    assert sequences == sorted(sequences)
