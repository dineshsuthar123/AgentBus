from datetime import datetime, timedelta, timezone

import pytest

from agentbus.execution.models import RunRecord
from agentbus.execution.state_store import (
    StateStore,
    TraceRecordConflictError,
)
from agentbus.trace import (
    StateStoreTraceSink,
    TraceEvent,
    TraceRecorder,
    TraceSpan,
    TraceSpanType,
    TraceStatus,
    trace_context,
)


class ControlledClock:
    def __init__(self) -> None:
        self.value = datetime(2026, 1, 2, tzinfo=timezone.utc)

    def __call__(self) -> datetime:
        current = self.value
        self.value += timedelta(milliseconds=1)
        return current


def _run(run_id: str = "run-1") -> RunRecord:
    return RunRecord(
        run_id=run_id,
        original_task="Trace the run",
        model="deterministic",
        workspace="workspace",
    )


def test_trace_sink_persists_complete_trace_across_store_restart(tmp_path) -> None:
    database = tmp_path / "state.db"
    store = StateStore(database)
    store.create_run(_run())
    recorder = TraceRecorder(
        "run-1",
        sink=StateStoreTraceSink(store),
        clock=ControlledClock(),
    )
    root = recorder.start_trace()

    with trace_context(root):
        with recorder.span(
            TraceSpanType.TASK,
            "step one",
            task_id="step-1",
        ):
            recorder.record_event("task.observed", attributes={"safe": True})
            recorder.checkpoint("after task")
    expected = recorder.finish_trace()

    restored = StateStore(database)
    actual = restored.get_run_trace("run-1")

    assert actual == expected
    assert restored.get_trace(actual.trace_id) == expected
    assert restored.next_trace_sequence(actual.trace_id) == 9
    assert recorder.recording_errors == ()


def test_terminal_trace_span_cannot_be_rewritten(tmp_path) -> None:
    store = StateStore(tmp_path / "state.db")
    store.create_run(_run())
    recorder = TraceRecorder(
        "run-1",
        sink=StateStoreTraceSink(store),
        clock=ControlledClock(),
    )
    root = recorder.start_trace()
    with trace_context(root):
        child = recorder.start_span(TraceSpanType.TASK, "task")
        terminal = recorder.finish_span(child.span_id)
    recorder.finish_trace()

    changed = TraceSpan.model_validate(
        terminal.model_copy(update={"attributes": {"changed": True}}).model_dump()
    )
    with pytest.raises(TraceRecordConflictError, match="immutable"):
        store.record_trace_span(changed)


def test_global_trace_sequence_collision_is_rejected_transactionally(
    tmp_path,
) -> None:
    store = StateStore(tmp_path / "state.db")
    store.create_run(_run())
    recorder = TraceRecorder(
        "run-1",
        sink=StateStoreTraceSink(store),
        clock=ControlledClock(),
    )
    root = recorder.start_trace()
    collision = TraceEvent(
        trace_id=recorder.trace_id,
        event_id="event-collision",
        run_id="run-1",
        span_id=root.span_id,
        event_type="collision",
        sequence=1,
        timestamp=datetime.now(timezone.utc),
    )

    with pytest.raises(TraceRecordConflictError, match="already used"):
        store.record_trace_event(collision)

    assert [event.sequence for event in store.list_trace_events(recorder.trace_id)] == [
        2
    ]


def test_trace_pages_are_bounded_and_resume_sequence_is_durable(tmp_path) -> None:
    store = StateStore(tmp_path / "state.db")
    store.create_run(_run())
    recorder = TraceRecorder(
        "run-1",
        sink=StateStoreTraceSink(store),
        clock=ControlledClock(),
    )
    recorder.start_trace()
    for index in range(5):
        recorder.record_event(f"event.{index}")

    page = store.list_trace_events(
        recorder.trace_id,
        after_sequence=3,
        limit=2,
    )

    assert [event.sequence for event in page] == [4, 5]
    with pytest.raises(Exception, match="between 1 and 5000"):
        store.list_trace_spans(recorder.trace_id, limit=5_001)
