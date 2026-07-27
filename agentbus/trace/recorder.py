from __future__ import annotations

import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import datetime
from typing import Any, Protocol

from agentbus.security.redaction import redact_text
from agentbus.trace.context import (
    TraceContext,
    current_trace_context,
    trace_context,
)
from agentbus.trace.errors import TraceRecordingError
from agentbus.trace.events import TraceEventType
from agentbus.trace.models import (
    Trace,
    TraceArtifactReference,
    TraceCheckpoint,
    TraceEvent,
    TraceFailure,
    TraceInput,
    TraceLink,
    TraceOutput,
    TraceReplayMetadata,
    TraceResourceUsage,
    TraceSpan,
    TraceSpanType,
    TraceStatus,
    utc_now,
)
from agentbus.trace.spans import (
    DeterministicSequence,
    trace_id_for_run,
    trace_item_id,
)


class TraceSink(Protocol):
    """Persistence boundary kept independent from execution truth."""

    def write_span(self, span: TraceSpan) -> None:
        ...

    def write_event(self, event: TraceEvent) -> None:
        ...

    def write_checkpoint(self, checkpoint: TraceCheckpoint) -> None:
        ...

    def write_trace(self, trace: Trace) -> None:
        ...


class TraceRecorder:
    """Build a causal trace while treating persistence as an observer."""

    def __init__(
        self,
        run_id: str,
        *,
        trace_id: str | None = None,
        sink: TraceSink | None = None,
        clock: Callable[[], datetime] = utc_now,
        next_sequence: int = 1,
        critical_sink: bool = False,
    ):
        self.run_id = run_id
        self.trace_id = trace_id or trace_id_for_run(run_id)
        self.sink = sink
        self.clock = clock
        self.critical_sink = critical_sink
        self._sequence = DeterministicSequence(next_value=next_sequence)
        self._spans: dict[str, TraceSpan] = {}
        self._events: list[TraceEvent] = []
        self._checkpoints: list[TraceCheckpoint] = []
        self._recording_errors: list[str] = []
        self._root_span_id: str | None = None
        self._created_at: datetime | None = None
        self._completed_at: datetime | None = None
        self._status = TraceStatus.PENDING
        self._links: list[TraceLink] = []
        self._replay: TraceReplayMetadata | None = None
        self._trace_attributes: dict[str, Any] = {}
        self._lock = threading.RLock()

    @classmethod
    def resume(
        cls,
        trace: Trace,
        *,
        sink: TraceSink | None = None,
        clock: Callable[[], datetime] = utc_now,
        next_sequence: int | None = None,
        critical_sink: bool = False,
    ) -> "TraceRecorder":
        """Restore one active trace without rewriting persisted history."""
        if trace.status != TraceStatus.RUNNING:
            raise TraceRecordingError(
                f"Only running traces can resume; trace is {trace.status.value}."
            )
        maximum_sequence = max(
            [
                *(span.sequence for span in trace.spans),
                *(event.sequence for event in trace.events),
                *(checkpoint.sequence for checkpoint in trace.checkpoints),
            ],
            default=0,
        )
        durable_next = maximum_sequence + 1
        if next_sequence is not None and next_sequence < durable_next:
            raise TraceRecordingError(
                "The supplied resume sequence would overwrite trace history."
            )
        recorder = cls(
            trace.run_id,
            trace_id=trace.trace_id,
            sink=sink,
            clock=clock,
            next_sequence=next_sequence or durable_next,
            critical_sink=critical_sink,
        )
        recorder._spans = {span.span_id: span for span in trace.spans}
        recorder._events = list(trace.events)
        recorder._checkpoints = list(trace.checkpoints)
        recorder._root_span_id = trace.root_span_id
        recorder._created_at = trace.created_at
        recorder._completed_at = None
        recorder._status = TraceStatus.RUNNING
        recorder._links = list(trace.links)
        recorder._replay = trace.replay
        recorder._trace_attributes = dict(trace.attributes)
        return recorder

    @property
    def root_span_id(self) -> str | None:
        with self._lock:
            return self._root_span_id

    @property
    def recording_errors(self) -> tuple[str, ...]:
        with self._lock:
            return tuple(self._recording_errors)

    def recording_failed(self, item_type: str, error: BaseException) -> None:
        """Record an optional integration failure without changing run truth."""
        safe_error = (
            redact_text(str(error), max_chars=1_000) or type(error).__name__
        )
        with self._lock:
            self._recording_errors.append(f"{item_type}: {safe_error}")

    def start_trace(
        self,
        *,
        name: str = "AgentBus run",
        attributes: dict[str, Any] | None = None,
    ) -> TraceContext:
        with self._lock:
            if self._root_span_id is not None:
                raise TraceRecordingError("The execution trace has already started.")
            sequence = self._sequence.claim()
            timestamp = self.clock()
            span_id = trace_item_id(self.trace_id, sequence, "run")
            span = TraceSpan(
                trace_id=self.trace_id,
                span_id=span_id,
                run_id=self.run_id,
                span_type=TraceSpanType.RUN,
                name=name,
                sequence=sequence,
                started_at=timestamp,
                status=TraceStatus.RUNNING,
                attributes=attributes or {},
            )
            self._root_span_id = span_id
            self._created_at = timestamp
            self._status = TraceStatus.RUNNING
            self._spans[span_id] = span
            self._emit_span(span)
            context = TraceContext(
                trace_id=self.trace_id,
                run_id=self.run_id,
                span_id=span_id,
            )
        self.record_event(
            TraceEventType.RUN_STARTED,
            span_id=span_id,
            attributes={"root_span_id": span_id},
        )
        return context

    def start_span(
        self,
        span_type: TraceSpanType,
        name: str,
        *,
        parent_span_id: str | None = None,
        task_id: str | None = None,
        worker_id: str | None = None,
        invocation_id: str | None = None,
        input_references: list[TraceInput] | None = None,
        attributes: dict[str, Any] | None = None,
    ) -> TraceContext:
        with self._lock:
            self._require_running_trace()
            inherited = current_trace_context()
            if inherited is not None:
                self._require_context_identity(inherited)
            parent_id = (
                parent_span_id
                or (inherited.span_id if inherited is not None else None)
                or self._root_span_id
            )
            if parent_id not in self._spans:
                raise TraceRecordingError(
                    f"Parent trace span '{parent_id}' does not exist."
                )
            parent = self._spans[parent_id]
            if parent.status != TraceStatus.RUNNING:
                raise TraceRecordingError(
                    f"Parent trace span '{parent_id}' is already terminal."
                )
            sequence = self._sequence.claim()
            span_id = trace_item_id(self.trace_id, sequence, span_type.value)
            span = TraceSpan(
                trace_id=self.trace_id,
                span_id=span_id,
                parent_span_id=parent_id,
                run_id=self.run_id,
                task_id=task_id or (inherited.task_id if inherited else None),
                worker_id=worker_id or (inherited.worker_id if inherited else None),
                invocation_id=(
                    invocation_id
                    or (inherited.invocation_id if inherited else None)
                ),
                span_type=span_type,
                name=name,
                sequence=sequence,
                started_at=self.clock(),
                status=TraceStatus.RUNNING,
                input_references=input_references or [],
                attributes=attributes or {},
            )
            self._spans[span_id] = span
            self._emit_span(span)
            context = TraceContext(
                trace_id=self.trace_id,
                run_id=self.run_id,
                span_id=span_id,
                task_id=span.task_id,
                worker_id=span.worker_id,
                invocation_id=span.invocation_id,
            )
        self.record_event(
            TraceEventType.SPAN_STARTED,
            span_id=span_id,
            task_id=span.task_id,
            attributes={"span_type": span_type.value},
        )
        return context

    def finish_span(
        self,
        span_id: str,
        *,
        status: TraceStatus = TraceStatus.SUCCEEDED,
        failure: TraceFailure | None = None,
        output_references: list[TraceOutput] | None = None,
        policy_decision_references: list[str] | None = None,
        approval_references: list[str] | None = None,
        artifact_references: list[TraceArtifactReference] | None = None,
        cancellation_state: dict[str, Any] | None = None,
        resource_usage: TraceResourceUsage | None = None,
        attributes: dict[str, Any] | None = None,
    ) -> TraceSpan:
        if status in {TraceStatus.PENDING, TraceStatus.RUNNING}:
            raise TraceRecordingError("A finished span must use a terminal status.")
        with self._lock:
            span = self._spans.get(span_id)
            if span is None:
                raise TraceRecordingError(f"Trace span '{span_id}' does not exist.")
            if span_id == self._root_span_id:
                raise TraceRecordingError(
                    "Finish the root span through finish_trace()."
                )
            if span.status != TraceStatus.RUNNING:
                raise TraceRecordingError(
                    f"Trace span '{span_id}' already completed as {span.status.value}."
                )
            merged_attributes = {**span.attributes, **(attributes or {})}
            completed_at = max(self.clock(), span.started_at)
            updated = span.model_copy(
                update={
                    "ended_at": completed_at,
                    "status": status,
                    "failure": failure,
                    "output_references": (
                        span.output_references
                        if output_references is None
                        else output_references
                    ),
                    "policy_decision_references": (
                        span.policy_decision_references
                        if policy_decision_references is None
                        else policy_decision_references
                    ),
                    "approval_references": (
                        span.approval_references
                        if approval_references is None
                        else approval_references
                    ),
                    "artifact_references": (
                        span.artifact_references
                        if artifact_references is None
                        else artifact_references
                    ),
                    "cancellation_state": (
                        span.cancellation_state
                        if cancellation_state is None
                        else cancellation_state
                    ),
                    "resource_usage": resource_usage or span.resource_usage,
                    "attributes": merged_attributes,
                }
            )
            updated = TraceSpan.model_validate(updated.model_dump())
            self._spans[span_id] = updated
            self._emit_span(updated)
        self.record_event(
            TraceEventType.SPAN_COMPLETED,
            span_id=span_id,
            task_id=updated.task_id,
            attributes={"span_type": updated.span_type.value, "status": status.value},
        )
        return updated

    @contextmanager
    def span(
        self,
        span_type: TraceSpanType,
        name: str,
        **kwargs: Any,
    ) -> Iterator[TraceContext]:
        context = self.start_span(span_type, name, **kwargs)
        try:
            with trace_context(context):
                yield context
        except Exception as exc:
            failure = TraceFailure(
                category=type(exc).__name__,
                message=redact_text(str(exc)) or "Trace span failed.",
                retryable=False,
            )
            self.finish_span(
                context.span_id,
                status=TraceStatus.FAILED,
                failure=failure,
            )
            raise
        else:
            self.finish_span(context.span_id)

    def record_event(
        self,
        event_type: TraceEventType | str,
        *,
        span_id: str | None = None,
        task_id: str | None = None,
        attributes: dict[str, Any] | None = None,
    ) -> TraceEvent:
        with self._lock:
            self._require_running_trace()
            inherited = current_trace_context()
            if inherited is not None:
                self._require_context_identity(inherited)
            target_span = (
                span_id
                or (inherited.span_id if inherited is not None else None)
                or self._root_span_id
            )
            if target_span not in self._spans:
                raise TraceRecordingError(
                    f"Event target span '{target_span}' does not exist."
                )
            sequence = self._sequence.claim()
            value = event_type.value if isinstance(event_type, TraceEventType) else event_type
            event = TraceEvent(
                trace_id=self.trace_id,
                event_id=trace_item_id(self.trace_id, sequence, "event"),
                run_id=self.run_id,
                span_id=target_span,
                task_id=task_id or (inherited.task_id if inherited else None),
                event_type=value,
                sequence=sequence,
                timestamp=self.clock(),
                attributes=attributes or {},
            )
            self._events.append(event)
            self._emit_event(event)
            return event

    def checkpoint(
        self,
        label: str,
        *,
        span_id: str | None = None,
        state_references: list[TraceInput] | None = None,
        state_reference_factory: Callable[[str], list[TraceInput]] | None = None,
        replayable: bool = True,
    ) -> TraceCheckpoint:
        if state_references is not None and state_reference_factory is not None:
            raise TraceRecordingError(
                "Checkpoint references and a reference factory are mutually exclusive."
            )
        with self._lock:
            self._require_running_trace()
            inherited = current_trace_context()
            if inherited is not None:
                self._require_context_identity(inherited)
            target_span = (
                span_id
                or (inherited.span_id if inherited is not None else None)
                or self._root_span_id
            )
            if target_span not in self._spans:
                raise TraceRecordingError(
                    f"Checkpoint target span '{target_span}' does not exist."
                )
            sequence = self._sequence.claim()
            checkpoint_id = trace_item_id(
                self.trace_id,
                sequence,
                "checkpoint",
            )
            references = (
                state_reference_factory(checkpoint_id)
                if state_reference_factory is not None
                else state_references or []
            )
            checkpoint = TraceCheckpoint(
                checkpoint_id=checkpoint_id,
                trace_id=self.trace_id,
                run_id=self.run_id,
                span_id=target_span,
                sequence=sequence,
                label=label,
                state_references=references,
                replayable=replayable,
                created_at=self.clock(),
            )
            self._checkpoints.append(checkpoint)
            self._emit_checkpoint(checkpoint)
            return checkpoint

    def finish_trace(
        self,
        *,
        status: TraceStatus = TraceStatus.SUCCEEDED,
        failure: TraceFailure | None = None,
        attributes: dict[str, Any] | None = None,
    ) -> Trace:
        if status in {TraceStatus.PENDING, TraceStatus.RUNNING}:
            raise TraceRecordingError("A finished trace must use a terminal status.")
        with self._lock:
            self._require_running_trace()
            active = [
                span.span_id
                for span in self._spans.values()
                if span.span_id != self._root_span_id
                and span.status == TraceStatus.RUNNING
            ]
            if active:
                raise TraceRecordingError(
                    "Cannot finish a trace with active spans: " + ", ".join(active[:10])
                )
            assert self._root_span_id is not None
            root = self._spans[self._root_span_id]
            completed_at = max(self.clock(), root.started_at)
            root = root.model_copy(
                update={
                    "ended_at": completed_at,
                    "status": status,
                    "failure": failure,
                    "attributes": {**root.attributes, **(attributes or {})},
                }
            )
            root = TraceSpan.model_validate(root.model_dump())
            self._spans[root.span_id] = root
            self._completed_at = completed_at
            self._status = status
            self._emit_span(root)
            self._record_terminal_event(status)
            trace = self.snapshot()
            self._emit_trace(trace)
            return trace

    def snapshot(self) -> Trace:
        with self._lock:
            if self._root_span_id is None or self._created_at is None:
                raise TraceRecordingError("The execution trace has not started.")
            return Trace(
                trace_id=self.trace_id,
                run_id=self.run_id,
                root_span_id=self._root_span_id,
                status=self._status,
                created_at=self._created_at,
                completed_at=self._completed_at,
                spans=sorted(self._spans.values(), key=lambda item: item.sequence),
                events=sorted(self._events, key=lambda item: item.sequence),
                checkpoints=sorted(
                    self._checkpoints,
                    key=lambda item: item.sequence,
                ),
                links=self._links,
                replay=self._replay,
                attributes={
                    **self._trace_attributes,
                    "recording_degraded": bool(self._recording_errors),
                    "recording_error_count": len(self._recording_errors),
                },
            )

    def reconcile_interrupted_spans(
        self,
        *,
        reason: str = "daemon_restart",
    ) -> list[TraceSpan]:
        """Close abandoned child spans while leaving completed history immutable."""
        with self._lock:
            self._require_running_trace()
            active_ids = [
                span.span_id
                for span in sorted(
                    self._spans.values(),
                    key=lambda item: item.sequence,
                    reverse=True,
                )
                if span.span_id != self._root_span_id
                and span.status == TraceStatus.RUNNING
            ]
        interrupted = [
            self.finish_span(
                span_id,
                status=TraceStatus.INTERRUPTED,
                attributes={"interruption_reason": reason},
            )
            for span_id in active_ids
        ]
        if interrupted:
            self.record_event(
                TraceEventType.TRACE_RECONCILED,
                attributes={"interrupted_span_count": len(interrupted)},
            )
        return interrupted

    def _record_terminal_event(self, status: TraceStatus) -> None:
        assert self._root_span_id is not None
        sequence = self._sequence.claim()
        event = TraceEvent(
            trace_id=self.trace_id,
            event_id=trace_item_id(self.trace_id, sequence, "event"),
            run_id=self.run_id,
            span_id=self._root_span_id,
            event_type=TraceEventType.RUN_COMPLETED.value,
            sequence=sequence,
            timestamp=self._completed_at or self.clock(),
            attributes={"status": status.value},
        )
        self._events.append(event)
        self._emit_event(event)

    def _require_running_trace(self) -> None:
        if self._root_span_id is None or self._status != TraceStatus.RUNNING:
            raise TraceRecordingError("The execution trace is not running.")

    def _require_context_identity(self, context: TraceContext) -> None:
        if context.trace_id != self.trace_id or context.run_id != self.run_id:
            raise TraceRecordingError(
                "The active trace context belongs to a different execution."
            )

    def _emit_span(self, span: TraceSpan) -> None:
        if self.sink is not None:
            self._write_sink("span", lambda: self.sink.write_span(span))

    def _emit_event(self, event: TraceEvent) -> None:
        if self.sink is not None:
            self._write_sink("event", lambda: self.sink.write_event(event))

    def _emit_checkpoint(self, checkpoint: TraceCheckpoint) -> None:
        if self.sink is not None:
            self._write_sink(
                "checkpoint",
                lambda: self.sink.write_checkpoint(checkpoint),
            )

    def _emit_trace(self, trace: Trace) -> None:
        if self.sink is None:
            return
        write_trace = getattr(self.sink, "write_trace", None)
        if write_trace is not None:
            self._write_sink("trace", lambda: write_trace(trace))

    def _write_sink(self, item_type: str, write: Callable[[], None]) -> None:
        try:
            write()
        except Exception as exc:
            self.recording_failed(item_type, exc)
            safe_error = redact_text(str(exc), max_chars=1_000) or "unknown error"
            if self.critical_sink:
                raise TraceRecordingError(
                    f"Critical trace {item_type} persistence failed: {safe_error}"
                ) from exc


__all__ = ["TraceRecorder", "TraceSink"]
