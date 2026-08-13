from __future__ import annotations

import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from agentbus._failure_injection import (
    FailureInjectionPoint,
    FailureProbe,
    failure_due,
)
from agentbus.execution.models import utc_now
from agentbus.security.redaction import redact_text


class CancellationOperation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    operation_id: str
    name: str
    source: str
    interruptible: bool
    provider: str | None = None
    task_id: str | None = None
    started_at: datetime


class CancellationState(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    requested: bool = False
    requested_at: datetime | None = None
    reason: str | None = None
    propagated_at: datetime | None = None
    propagation_sources: list[str] = Field(default_factory=list)
    provider_cancellation_requested_at: datetime | None = None
    provider_names: list[str] = Field(default_factory=list)
    acknowledged: bool = False
    acknowledged_at: datetime | None = None
    acknowledgement_source: str | None = None
    acknowledgement_stage: str | None = None
    provider_cancellation_acknowledged_at: datetime | None = None
    provider_acknowledgement_source: str | None = None
    active_operations: list[CancellationOperation] = Field(default_factory=list)
    operations_completed_after_request: list[str] = Field(default_factory=list)
    tasks_prevented_from_starting: list[str] = Field(default_factory=list)
    tasks_completed_after_request: list[str] = Field(default_factory=list)
    scheduling_stopped_at: datetime | None = None
    cleanup_completed_at: datetime | None = None
    resume_eligible: bool = True
    terminal_reason: str | None = None
    revision: int = Field(default=0, ge=0)

    @property
    def active_non_interruptible_operations(self) -> list[str]:
        return [
            operation.name
            for operation in self.active_operations
            if not operation.interruptible
        ]


class CancellationRequested(RuntimeError):
    def __init__(self, state: CancellationState, *, source: str, stage: str | None):
        self.state = state
        self.source = source
        self.stage = stage
        reason = state.reason or "Cancellation requested."
        super().__init__(reason)


CancellationListener = Callable[[CancellationState], None]


class CancellationToken:
    """Thread-safe cooperative cancellation and lifecycle observation."""

    def __init__(
        self,
        initial: CancellationState | None = None,
        *,
        clock: Callable[[], datetime] = utc_now,
        failure_probe: FailureProbe | None = None,
    ):
        state = initial or CancellationState()
        self._clock = clock
        self._failure_probe = failure_probe
        self._lock = threading.RLock()
        self._condition = threading.Condition(self._lock)
        self._event = threading.Event()
        self._listeners: list[CancellationListener] = []
        self._state = state
        self._operations = {
            operation.operation_id: operation
            for operation in state.active_operations
        }
        self._operation_counter = len(self._operations)
        if state.requested:
            self._event.set()

    @property
    def is_requested(self) -> bool:
        return self._event.is_set()

    def is_set(self) -> bool:
        """Match ``threading.Event`` for compatibility with existing callers."""
        return self.is_requested

    def snapshot(self) -> CancellationState:
        with self._lock:
            return self._state.model_copy(deep=True)

    def add_listener(
        self,
        listener: CancellationListener,
        *,
        emit_current: bool = False,
    ) -> None:
        with self._lock:
            self._listeners.append(listener)
            state = self._state
        if emit_current:
            listener(state.model_copy(deep=True))

    def request(self, reason: str | None = None) -> bool:
        with self._lock:
            if self._state.requested:
                return False
            now = self._clock()
            providers = sorted(
                {
                    operation.provider
                    for operation in self._operations.values()
                    if operation.provider
                }
            )
            propagation_sources = sorted(
                set(self._state.propagation_sources) | {"cancellation-token"}
            )
            self._state = self._state.model_copy(
                update={
                    "requested": True,
                    "requested_at": now,
                    "reason": redact_text(reason, max_chars=2_000),
                    "propagated_at": now,
                    "propagation_sources": propagation_sources[:32],
                    "provider_cancellation_requested_at": (
                        now if providers else None
                    ),
                    "provider_names": providers,
                    "revision": self._state.revision + 1,
                }
            )
            self._event.set()
            self._condition.notify_all()
            state, listeners = self._notification()
        self._notify(listeners, state)
        return True

    def mark_propagated(self, source: str) -> bool:
        clean_source = _bounded_label(source, "propagation source")
        with self._lock:
            sources = list(self._state.propagation_sources)
            if clean_source in sources:
                return False
            sources.append(clean_source)
            self._state = self._state.model_copy(
                update={
                    "propagated_at": self._state.propagated_at or self._clock(),
                    "propagation_sources": sources[:32],
                    "revision": self._state.revision + 1,
                }
            )
            state, listeners = self._notification()
        self._notify(listeners, state)
        return True

    def acknowledge(
        self,
        source: str,
        *,
        stage: str | None = None,
        provider: str | None = None,
    ) -> bool:
        clean_source = _bounded_label(source, "acknowledgement source")
        clean_stage = _bounded_optional(stage)
        clean_provider = _bounded_optional(provider)
        with self._lock:
            if not self._state.requested:
                return False
            now = self._clock()
            updates: dict[str, Any] = {}
            changed = False
            if not self._state.acknowledged:
                updates.update(
                    {
                        "acknowledged": True,
                        "acknowledged_at": now,
                        "acknowledgement_source": clean_source,
                        "acknowledgement_stage": clean_stage,
                    }
                )
                changed = True
            if clean_provider and not self._state.provider_cancellation_acknowledged_at:
                providers = sorted(
                    set(self._state.provider_names) | {clean_provider}
                )
                updates.update(
                    {
                        "provider_cancellation_acknowledged_at": now,
                        "provider_acknowledgement_source": clean_source,
                        "provider_names": providers,
                    }
                )
                changed = True
            if not changed:
                return False
            updates["revision"] = self._state.revision + 1
            self._state = self._state.model_copy(update=updates)
            self._condition.notify_all()
            state, listeners = self._notification()
        self._notify(listeners, state)
        return True

    def checkpoint(
        self,
        source: str,
        *,
        stage: str | None = None,
        provider: str | None = None,
        acknowledge: bool = True,
    ) -> None:
        if not self.is_requested and failure_due(
            self._failure_probe,
            FailureInjectionPoint.CANCELLATION,
            scope="checkpoint",
        ):
            self.request("Controlled cancellation failure injection.")
        if not self.is_requested:
            return
        if acknowledge:
            self.acknowledge(source, stage=stage, provider=provider)
        raise CancellationRequested(
            self.snapshot(),
            source=source,
            stage=stage,
        )

    def wait(self, timeout_seconds: float | None = None) -> bool:
        return self._event.wait(timeout_seconds)

    def wait_for_active_operation(
        self,
        *,
        source: str | None = None,
        timeout_seconds: float = 5.0,
    ) -> CancellationOperation | None:
        with self._condition:
            def find() -> CancellationOperation | None:
                return next(
                    (
                        operation
                        for operation in self._operations.values()
                        if source is None or operation.source == source
                    ),
                    None,
                )

            operation = find()
            if operation is not None:
                return operation
            self._condition.wait_for(
                lambda: find() is not None,
                timeout=timeout_seconds,
            )
            return find()

    @contextmanager
    def operation(
        self,
        name: str,
        *,
        source: str,
        interruptible: bool,
        provider: str | None = None,
        task_id: str | None = None,
    ) -> Iterator[CancellationOperation]:
        clean_name = _bounded_label(name, "operation name")
        clean_source = _bounded_label(source, "operation source")
        clean_provider = _bounded_optional(provider)
        clean_task_id = _bounded_optional(task_id)
        self.checkpoint(
            clean_source,
            stage=f"before:{clean_name}",
            provider=clean_provider,
        )
        with self._lock:
            self._operation_counter += 1
            operation = CancellationOperation(
                operation_id=f"operation-{self._operation_counter}",
                name=clean_name,
                source=clean_source,
                interruptible=bool(interruptible),
                provider=clean_provider,
                task_id=clean_task_id,
                started_at=self._clock(),
            )
            self._operations[operation.operation_id] = operation
            self._state = self._state.model_copy(
                update={
                    "active_operations": list(self._operations.values()),
                    "revision": self._state.revision + 1,
                }
            )
            self._condition.notify_all()
            state, listeners = self._notification()
        self._notify(listeners, state)
        completed_normally = False
        try:
            yield operation
            completed_normally = True
        finally:
            with self._lock:
                self._operations.pop(operation.operation_id, None)
                completed = list(
                    self._state.operations_completed_after_request
                )
                requested_during_operation = bool(
                    completed_normally
                    and self._state.requested_at
                    and self._state.requested_at >= operation.started_at
                )
                if requested_during_operation and operation.name not in completed:
                    completed.append(operation.name)
                self._state = self._state.model_copy(
                    update={
                        "active_operations": list(self._operations.values()),
                        "operations_completed_after_request": completed[:64],
                        "revision": self._state.revision + 1,
                    }
                )
                self._condition.notify_all()
                state, listeners = self._notification()
            self._notify(listeners, state)

    def mark_scheduling_stopped(
        self,
        tasks_prevented_from_starting: list[str] | tuple[str, ...],
    ) -> bool:
        with self._lock:
            if self._state.scheduling_stopped_at is not None:
                return False
            tasks = sorted(
                {
                    _bounded_label(task_id, "task id")
                    for task_id in tasks_prevented_from_starting
                }
            )[:256]
            self._state = self._state.model_copy(
                update={
                    "scheduling_stopped_at": self._clock(),
                    "tasks_prevented_from_starting": tasks,
                    "revision": self._state.revision + 1,
                }
            )
            state, listeners = self._notification()
        self._notify(listeners, state)
        return True

    def record_task_completed_after_request(self, task_id: str) -> bool:
        clean_task_id = _bounded_label(task_id, "task id")
        with self._lock:
            if not self._state.requested:
                return False
            completed = list(self._state.tasks_completed_after_request)
            if clean_task_id in completed:
                return False
            completed.append(clean_task_id)
            self._state = self._state.model_copy(
                update={
                    "tasks_completed_after_request": completed[:256],
                    "revision": self._state.revision + 1,
                }
            )
            state, listeners = self._notification()
        self._notify(listeners, state)
        return True

    def complete_cleanup(
        self,
        *,
        terminal_reason: str,
        resume_eligible: bool,
    ) -> bool:
        with self._lock:
            if self._state.cleanup_completed_at is not None:
                return False
            self._state = self._state.model_copy(
                update={
                    "cleanup_completed_at": self._clock(),
                    "terminal_reason": redact_text(
                        terminal_reason,
                        max_chars=2_000,
                    ),
                    "resume_eligible": bool(resume_eligible),
                    "revision": self._state.revision + 1,
                }
            )
            state, listeners = self._notification()
        self._notify(listeners, state)
        return True

    def abandon_active_operations(self, source: str) -> list[CancellationOperation]:
        """Clear operation markers whose owning process no longer exists."""
        clean_source = _bounded_label(source, "recovery source")
        with self._lock:
            abandoned = list(self._operations.values())
            if not abandoned:
                return []
            self._operations.clear()
            updates: dict[str, Any] = {
                "active_operations": [],
                "revision": self._state.revision + 1,
            }
            if self._state.requested and not self._state.acknowledged:
                now = self._clock()
                updates.update(
                    {
                        "acknowledged": True,
                        "acknowledged_at": now,
                        "acknowledgement_source": clean_source,
                        "acknowledgement_stage": "process-recovery",
                    }
                )
            self._state = self._state.model_copy(update=updates)
            self._condition.notify_all()
            state, listeners = self._notification()
        self._notify(listeners, state)
        return abandoned

    def _notification(
        self,
    ) -> tuple[CancellationState, tuple[CancellationListener, ...]]:
        return self._state.model_copy(deep=True), tuple(self._listeners)

    @staticmethod
    def _notify(
        listeners: tuple[CancellationListener, ...],
        state: CancellationState,
    ) -> None:
        for listener in listeners:
            listener(state.model_copy(deep=True))


def _bounded_label(value: str, description: str) -> str:
    clean = redact_text(str(value), max_chars=256)
    if not clean:
        raise ValueError(f"{description} must not be empty")
    return clean


def _bounded_optional(value: str | None) -> str | None:
    return redact_text(value, max_chars=256) if value is not None else None
