from __future__ import annotations

import threading
from collections.abc import Callable, Iterable
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from dataclasses import dataclass
from enum import Enum
from typing import Generic, Protocol, TypeVar

from pydantic import Field, field_validator, model_validator

from agentbus.intelligence.errors import QueryLimitError
from agentbus.intelligence.models import IntelligenceModel, _relative_path
from agentbus.intelligence.parsers.base import CancellationSignal


_Item = TypeVar("_Item")
_Result = TypeVar("_Result")


class IndexProgressPhase(str, Enum):
    DISCOVERY = "discovery"
    INDEXING = "indexing"
    INVALIDATION = "invalidation"
    PERSISTENCE = "persistence"
    COMPLETED = "completed"
    PAUSED = "paused"
    FAILED = "failed"


class IndexProgressEvent(IntelligenceModel):
    operation_id: str = Field(
        pattern=r"^indexop_[a-f0-9]{32,64}$"
    )
    sequence: int = Field(ge=1, le=1_000_000)
    phase: IndexProgressPhase
    completed_items: int = Field(ge=0, le=1_000_000)
    total_items: int = Field(ge=0, le=1_000_000)
    relative_path: str | None = None
    message: str = Field(min_length=1, max_length=512)
    dropped_events: int = Field(default=0, ge=0, le=1_000_000)

    @field_validator("relative_path")
    @classmethod
    def validate_relative_path(cls, value: str | None) -> str | None:
        return _relative_path(value) if value else None

    @model_validator(mode="after")
    def validate_progress(self) -> IndexProgressEvent:
        if self.completed_items > self.total_items:
            raise ValueError("completed progress cannot exceed total progress")
        return self


class IndexProgressSink(Protocol):
    def __call__(self, event: IndexProgressEvent) -> None:
        """Receive one bounded, source-free progress event."""


@dataclass(frozen=True)
class IndexSchedulerLimits:
    maximum_workers: int = 4
    maximum_in_flight: int = 8
    maximum_items: int = 100_000

    def __post_init__(self) -> None:
        _bounded(self.maximum_workers, "maximum_workers", 1, 32)
        _bounded(self.maximum_in_flight, "maximum_in_flight", 1, 128)
        _bounded(self.maximum_items, "maximum_items", 1, 1_000_000)
        if self.maximum_in_flight < self.maximum_workers:
            raise ValueError(
                "maximum_in_flight cannot be less than maximum_workers"
            )


@dataclass(frozen=True)
class ScheduledItem(Generic[_Item, _Result]):
    index: int
    item: _Item
    result: _Result


@dataclass(frozen=True)
class ScheduledBatch(Generic[_Item, _Result]):
    completed: tuple[ScheduledItem[_Item, _Result], ...]
    pending: tuple[_Item, ...]
    cancelled: bool
    maximum_active_workers: int

    @property
    def results(self) -> tuple[_Result, ...]:
        return tuple(item.result for item in self.completed)


class IndexProgressReporter:
    def __init__(
        self,
        operation_id: str,
        sink: IndexProgressSink | None = None,
        *,
        maximum_events: int = 1_000,
    ) -> None:
        if maximum_events < 2 or maximum_events > 100_000:
            raise ValueError(
                "maximum_events must be between 2 and 100000"
            )
        IndexProgressEvent(
            operation_id=operation_id,
            sequence=1,
            phase=IndexProgressPhase.DISCOVERY,
            completed_items=0,
            total_items=0,
            message="Progress reporter initialized.",
        )
        self.operation_id = operation_id
        self.sink = sink
        self.maximum_events = maximum_events
        self._lock = threading.Lock()
        self._events: list[IndexProgressEvent] = []
        self._dropped_events = 0

    @property
    def events(self) -> tuple[IndexProgressEvent, ...]:
        with self._lock:
            return tuple(self._events)

    @property
    def dropped_events(self) -> int:
        with self._lock:
            return self._dropped_events

    def emit(
        self,
        phase: IndexProgressPhase,
        *,
        completed_items: int,
        total_items: int,
        message: str,
        relative_path: str | None = None,
        terminal: bool = False,
    ) -> IndexProgressEvent | None:
        with self._lock:
            reserve = 0 if terminal else 1
            if len(self._events) >= self.maximum_events - reserve:
                self._dropped_events += 1
                return None
            event = IndexProgressEvent(
                operation_id=self.operation_id,
                sequence=len(self._events) + 1,
                phase=phase,
                completed_items=completed_items,
                total_items=total_items,
                relative_path=relative_path,
                message=message,
                dropped_events=self._dropped_events,
            )
            self._events.append(event)
        sink = self.sink
        if sink is not None:
            try:
                sink(event)
            except Exception:
                with self._lock:
                    self._dropped_events += 1
                    if self.sink is sink:
                        self.sink = None
        return event


class BoundedIndexScheduler:
    def __init__(
        self,
        *,
        limits: IndexSchedulerLimits | None = None,
    ) -> None:
        self.limits = limits or IndexSchedulerLimits()

    def run(
        self,
        items: Iterable[_Item],
        worker: Callable[[_Item], _Result],
        *,
        cancellation: CancellationSignal | None = None,
        reporter: IndexProgressReporter | None = None,
        phase: IndexProgressPhase = IndexProgressPhase.INDEXING,
        item_path: Callable[[_Item], str | None] | None = None,
    ) -> ScheduledBatch[_Item, _Result]:
        work = tuple(items)
        if len(work) > self.limits.maximum_items:
            raise QueryLimitError(
                "Repository indexing work exceeds the scheduler item limit."
            )
        if reporter is not None:
            reporter.emit(
                phase,
                completed_items=0,
                total_items=len(work),
                message="Bounded repository indexing work started.",
            )
        if not work or _cancelled(cancellation):
            return ScheduledBatch(
                completed=(),
                pending=work,
                cancelled=bool(work),
                maximum_active_workers=0,
            )

        active_workers = 0
        maximum_active_workers = 0
        active_lock = threading.Lock()

        def guarded_worker(item: _Item) -> _Result:
            nonlocal active_workers, maximum_active_workers
            with active_lock:
                active_workers += 1
                maximum_active_workers = max(
                    maximum_active_workers,
                    active_workers,
                )
            try:
                return worker(item)
            finally:
                with active_lock:
                    active_workers -= 1

        completed: dict[int, ScheduledItem[_Item, _Result]] = {}
        futures: dict[Future[_Result], int] = {}
        next_index = 0
        next_progress_index = 0
        cancelled = False

        with ThreadPoolExecutor(
            max_workers=self.limits.maximum_workers,
            thread_name_prefix="agentbus-index",
        ) as executor:

            def submit_available() -> None:
                nonlocal next_index
                while (
                    next_index < len(work)
                    and len(futures) < self.limits.maximum_in_flight
                    and not _cancelled(cancellation)
                ):
                    index = next_index
                    next_index += 1
                    futures[executor.submit(guarded_worker, work[index])] = (
                        index
                    )

            submit_available()
            while futures:
                done, _ = wait(
                    tuple(futures),
                    return_when=FIRST_COMPLETED,
                )
                for future in sorted(done, key=lambda item: futures[item]):
                    index = futures.pop(future)
                    if future.cancelled():
                        continue
                    completed[index] = ScheduledItem(
                        index=index,
                        item=work[index],
                        result=future.result(),
                    )
                while next_progress_index in completed:
                    current = completed[next_progress_index]
                    if reporter is not None:
                        reporter.emit(
                            phase,
                            completed_items=next_progress_index + 1,
                            total_items=len(work),
                            relative_path=(
                                item_path(current.item)
                                if item_path is not None
                                else None
                            ),
                            message="Repository indexing item completed.",
                        )
                    next_progress_index += 1
                if _cancelled(cancellation):
                    cancelled = True
                    for future in futures:
                        future.cancel()
                else:
                    submit_available()

        completed_items = tuple(
            completed[index] for index in sorted(completed)
        )
        completed_indices = set(completed)
        pending = tuple(
            item
            for index, item in enumerate(work)
            if index not in completed_indices
        )
        return ScheduledBatch(
            completed=completed_items,
            pending=pending,
            cancelled=cancelled or bool(pending),
            maximum_active_workers=maximum_active_workers,
        )


def _cancelled(cancellation: CancellationSignal | None) -> bool:
    return bool(cancellation is not None and cancellation.is_set())


def _bounded(
    value: int,
    name: str,
    minimum: int,
    maximum: int,
) -> None:
    if value < minimum or value > maximum:
        raise ValueError(f"{name} must be between {minimum} and {maximum}")
