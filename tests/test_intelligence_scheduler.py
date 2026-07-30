from __future__ import annotations

from threading import Barrier, Event

import pytest
from pydantic import ValidationError

from agentbus.execution.cancellation import CancellationToken
from agentbus.intelligence import (
    BoundedIndexScheduler,
    IndexProgressEvent,
    IndexProgressPhase,
    IndexProgressReporter,
    IndexSchedulerLimits,
)


_OPERATION_ID = "indexop_" + ("c" * 32)


def test_scheduler_executes_with_bounded_parallelism() -> None:
    scheduler = BoundedIndexScheduler(
        limits=IndexSchedulerLimits(
            maximum_workers=2,
            maximum_in_flight=2,
        )
    )
    both_started = Barrier(2)

    def work(value: int) -> int:
        if value < 2:
            both_started.wait(timeout=5)
        return value * value

    batch = scheduler.run(range(4), work)

    assert batch.results == (0, 1, 4, 9)
    assert batch.pending == ()
    assert batch.cancelled is False
    assert batch.maximum_active_workers == 2


def test_scheduler_preserves_progress_order_after_out_of_order_work() -> None:
    scheduler = BoundedIndexScheduler(
        limits=IndexSchedulerLimits(
            maximum_workers=2,
            maximum_in_flight=2,
        )
    )
    second_completed = Event()
    reporter = IndexProgressReporter(_OPERATION_ID)

    def work(value: int) -> int:
        if value == 0:
            assert second_completed.wait(timeout=5)
        else:
            second_completed.set()
        return value

    batch = scheduler.run(
        (0, 1),
        work,
        reporter=reporter,
        item_path=lambda value: f"src/{value}.py",
    )

    assert batch.results == (0, 1)
    assert [
        event.relative_path
        for event in reporter.events
        if event.relative_path is not None
    ] == ["src/0.py", "src/1.py"]
    assert [
        event.completed_items for event in reporter.events
    ] == [0, 1, 2]


def test_scheduler_stops_submitting_after_cancellation() -> None:
    scheduler = BoundedIndexScheduler(
        limits=IndexSchedulerLimits(
            maximum_workers=1,
            maximum_in_flight=1,
        )
    )
    cancellation = CancellationToken()
    calls: list[int] = []

    def work(value: int) -> int:
        calls.append(value)
        cancellation.request("test")
        return value

    batch = scheduler.run(
        range(5),
        work,
        cancellation=cancellation,
    )

    assert calls == [0]
    assert batch.results == (0,)
    assert batch.pending == (1, 2, 3, 4)
    assert batch.cancelled is True


def test_progress_reporter_caps_events_and_reserves_terminal_event() -> None:
    reporter = IndexProgressReporter(
        _OPERATION_ID,
        maximum_events=4,
    )
    scheduler = BoundedIndexScheduler(
        limits=IndexSchedulerLimits(
            maximum_workers=1,
            maximum_in_flight=1,
        )
    )

    batch = scheduler.run(range(10), lambda value: value, reporter=reporter)
    reporter.emit(
        IndexProgressPhase.COMPLETED,
        completed_items=10,
        total_items=10,
        message="Repository indexing completed.",
        terminal=True,
    )

    assert batch.results == tuple(range(10))
    assert len(reporter.events) == 4
    assert reporter.events[-1].phase == IndexProgressPhase.COMPLETED
    assert reporter.events[-1].dropped_events == 8
    assert reporter.dropped_events == 8


def test_scheduler_propagates_unexpected_worker_failure() -> None:
    scheduler = BoundedIndexScheduler(
        limits=IndexSchedulerLimits(
            maximum_workers=1,
            maximum_in_flight=1,
        )
    )

    with pytest.raises(RuntimeError, match="injected"):
        scheduler.run((1,), lambda _value: _raise_injected())


@pytest.mark.parametrize(
    "path",
    ("../outside.py", "/absolute.py", "C:/outside.py"),
)
def test_progress_events_reject_unsafe_paths(path: str) -> None:
    with pytest.raises(ValidationError):
        IndexProgressEvent(
            operation_id=_OPERATION_ID,
            sequence=1,
            phase=IndexProgressPhase.INDEXING,
            completed_items=1,
            total_items=1,
            relative_path=path,
            message="Unsafe path.",
        )


def _raise_injected() -> int:
    raise RuntimeError("injected worker failure")
