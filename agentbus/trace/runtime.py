from __future__ import annotations

import hashlib
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any, TypeVar

from agentbus.security.redaction import redact_text
from agentbus.trace.context import TraceContext, trace_context
from agentbus.trace.events import TraceEventType
from agentbus.trace.models import (
    Trace,
    TraceArtifactReference,
    TraceFailure,
    TraceInput,
    TraceOutput,
    TraceResourceUsage,
    TraceSpan,
    TraceSpanType,
    TraceStatus,
)
from agentbus.trace.persistence import StateStoreTraceSink
from agentbus.trace.recorder import TraceRecorder
from agentbus.trace.storage import ContentAddressedStore

_Result = TypeVar("_Result")


class RuntimeTrace:
    """Failure-isolated trace facade used by the production runtime."""

    def __init__(
        self,
        recorder: TraceRecorder | None,
        object_store: ContentAddressedStore | None,
        *,
        terminal_trace: Trace | None = None,
    ) -> None:
        self.recorder = recorder
        self.object_store = object_store
        self._terminal_trace = terminal_trace

    @classmethod
    def open(
        cls,
        state_store,
        run_id: str,
        *,
        object_root: str | Path,
        workspace: str | Path,
        root_attributes: dict[str, Any] | None = None,
        reconcile: bool = True,
    ) -> "RuntimeTrace":
        object_store: ContentAddressedStore | None
        object_error: Exception | None = None
        try:
            object_store = ContentAddressedStore(
                object_root,
                private_roots=(workspace, Path.home()),
            )
        except Exception as exc:
            object_store = None
            object_error = exc

        existing = state_store.find_run_trace(run_id)
        sink = StateStoreTraceSink(state_store)
        if existing is None:
            recorder = TraceRecorder(run_id, sink=sink)
            recorder.start_trace(
                name="AgentBus durable run",
                attributes=root_attributes or {},
            )
        elif existing.status == TraceStatus.RUNNING:
            recorder = TraceRecorder.resume(
                existing,
                sink=sink,
                next_sequence=state_store.next_trace_sequence(existing.trace_id),
            )
            if reconcile:
                recorder.reconcile_interrupted_spans()
        else:
            return cls(
                None,
                object_store,
                terminal_trace=existing,
            )
        runtime = cls(recorder, object_store)
        if object_error is not None:
            runtime.recording_failed("object_store", object_error)
        return runtime

    @property
    def active(self) -> bool:
        return self.recorder is not None

    @property
    def trace_id(self) -> str | None:
        if self.recorder is not None:
            return self.recorder.trace_id
        if self._terminal_trace is not None:
            return self._terminal_trace.trace_id
        return None

    @property
    def root_context(self) -> TraceContext | None:
        if self.recorder is None or self.recorder.root_span_id is None:
            return None
        return TraceContext(
            trace_id=self.recorder.trace_id,
            run_id=self.recorder.run_id,
            span_id=self.recorder.root_span_id,
        )

    def snapshot(self) -> Trace | None:
        if self.recorder is not None:
            try:
                return self.recorder.snapshot()
            except Exception as exc:
                self.recording_failed("snapshot", exc)
                return None
        return self._terminal_trace

    def start_span(
        self,
        span_type: TraceSpanType,
        name: str,
        **kwargs: Any,
    ) -> TraceContext | None:
        if self.recorder is None:
            return None
        try:
            return self.recorder.start_span(span_type, name, **kwargs)
        except Exception as exc:
            self.recording_failed("span_start", exc)
            return None

    def finish_span(
        self,
        context: TraceContext | None,
        *,
        status: TraceStatus = TraceStatus.SUCCEEDED,
        failure: TraceFailure | None = None,
        input_references: list[TraceInput] | None = None,
        output_references: list[TraceOutput] | None = None,
        policy_decision_references: list[str] | None = None,
        approval_references: list[str] | None = None,
        artifact_references: list[TraceArtifactReference] | None = None,
        cancellation_state: dict[str, Any] | None = None,
        resource_usage: TraceResourceUsage | None = None,
        attributes: dict[str, Any] | None = None,
    ) -> TraceSpan | None:
        if self.recorder is None or context is None:
            return None
        try:
            return self.recorder.finish_span(
                context.span_id,
                status=status,
                failure=failure,
                input_references=input_references,
                output_references=output_references,
                policy_decision_references=policy_decision_references,
                approval_references=approval_references,
                artifact_references=artifact_references,
                cancellation_state=cancellation_state,
                resource_usage=resource_usage,
                attributes=attributes,
            )
        except Exception as exc:
            self.recording_failed("span_finish", exc)
            return None

    def fail_span(
        self,
        context: TraceContext | None,
        error: BaseException,
        *,
        retryable: bool = False,
        attributes: dict[str, Any] | None = None,
    ) -> TraceSpan | None:
        return self.finish_span(
            context,
            status=TraceStatus.FAILED,
            failure=_safe_failure(error, retryable=retryable),
            attributes=attributes,
        )

    @contextmanager
    def scope(
        self,
        context: TraceContext | None,
    ) -> Iterator[TraceContext | None]:
        if context is None:
            yield None
            return
        with trace_context(context):
            yield context

    def call(
        self,
        span_type: TraceSpanType,
        name: str,
        function: Callable[[], _Result],
        *,
        task_id: str | None = None,
        worker_id: str | None = None,
        attributes: dict[str, Any] | None = None,
        capture: str | None = None,
    ) -> _Result:
        context = self.start_span(
            span_type,
            name,
            task_id=task_id,
            worker_id=worker_id,
            attributes=attributes,
        )
        try:
            with self.scope(context):
                result = function()
        except BaseException as exc:
            self.fail_span(context, exc)
            raise
        output_references: list[TraceOutput] = []
        if capture == "json":
            reference = self.capture_json_output(context, name, result)
            if reference is not None:
                output_references.append(reference)
        elif capture == "text":
            reference = self.capture_text_output(context, name, str(result))
            if reference is not None:
                output_references.append(reference)
        self.finish_span(context, output_references=output_references)
        return result

    def capture_json_input(
        self,
        context: TraceContext | None,
        name: str,
        value: Any,
        *,
        required_for_replay: bool = True,
    ) -> TraceInput | None:
        metadata = self._put_json(context, name, value)
        if metadata is None or self.object_store is None:
            return None
        return self.object_store.reference_input(
            metadata,
            reference_id=_reference_id(context, name, "input"),
            name=name,
            required_for_replay=required_for_replay,
        )

    def capture_json_output(
        self,
        context: TraceContext | None,
        name: str,
        value: Any,
        *,
        replayable: bool = True,
    ) -> TraceOutput | None:
        metadata = self._put_json(context, name, value)
        if metadata is None or self.object_store is None:
            return None
        return self.object_store.reference_output(
            metadata,
            reference_id=_reference_id(context, name, "output"),
            name=name,
            replayable=replayable,
        )

    def capture_text_output(
        self,
        context: TraceContext | None,
        name: str,
        value: str,
        *,
        replayable: bool = True,
    ) -> TraceOutput | None:
        if self.object_store is None or context is None:
            return None
        try:
            metadata = self.object_store.put_text(
                value,
                producing_span_id=context.span_id,
            )
            return self.object_store.reference_output(
                metadata,
                reference_id=_reference_id(context, name, "output"),
                name=name,
                replayable=replayable,
            )
        except Exception as exc:
            self.recording_failed("text_capture", exc)
            return None

    def checkpoint(
        self,
        label: str,
        state: Any,
        *,
        context: TraceContext | None = None,
        replayable: bool = True,
    ):
        if self.recorder is None:
            return None
        target = context or self.root_context

        def references(checkpoint_id: str) -> list[TraceInput]:
            reference = self.capture_json_input(
                target,
                f"checkpoint.{label}",
                state,
            )
            return [reference] if reference is not None else []

        try:
            return self.recorder.checkpoint(
                label,
                span_id=target.span_id if target is not None else None,
                state_reference_factory=references,
                replayable=replayable,
            )
        except Exception as exc:
            self.recording_failed("checkpoint", exc)
            return None

    def record_event(
        self,
        event_type: str,
        *,
        context: TraceContext | None = None,
        task_id: str | None = None,
        attributes: dict[str, Any] | None = None,
    ):
        if self.recorder is None:
            return None
        try:
            return self.recorder.record_event(
                event_type,
                span_id=context.span_id if context is not None else None,
                task_id=task_id,
                attributes=attributes,
            )
        except Exception as exc:
            self.recording_failed("event", exc)
            return None

    def finish(
        self,
        *,
        status: TraceStatus,
        failure: TraceFailure | None = None,
        attributes: dict[str, Any] | None = None,
    ) -> Trace | None:
        if self.recorder is None:
            return self._terminal_trace
        try:
            trace = self.recorder.finish_trace(
                status=status,
                failure=failure,
                attributes=attributes,
            )
        except Exception as exc:
            self.recording_failed("trace_finish", exc)
            return self.snapshot()
        self._terminal_trace = trace
        self.recorder = None
        return trace

    def recording_failed(self, item_type: str, error: BaseException) -> None:
        if self.recorder is None:
            return
        self.recorder.recording_failed(item_type, error)
        try:
            self.recorder.record_event(
                TraceEventType.RECORDING_DEGRADED,
                attributes={
                    "item_type": item_type,
                    "error_category": type(error).__name__,
                },
            )
        except Exception:
            pass

    def _put_json(
        self,
        context: TraceContext | None,
        name: str,
        value: Any,
    ):
        if self.object_store is None or context is None:
            return None
        try:
            return self.object_store.put_json(
                value,
                producing_span_id=context.span_id,
                media_type=f"application/vnd.agentbus.{_media_label(name)}+json",
            )
        except Exception as exc:
            self.recording_failed("json_capture", exc)
            return None


def _safe_failure(
    error: BaseException,
    *,
    retryable: bool,
) -> TraceFailure:
    return TraceFailure(
        category=type(error).__name__,
        message=redact_text(str(error), max_chars=2_000) or "Operation failed.",
        retryable=retryable,
    )


def _reference_id(
    context: TraceContext | None,
    name: str,
    direction: str,
) -> str:
    span_id = context.span_id if context is not None else "missing"
    digest = hashlib.sha256(
        f"{span_id}:{direction}:{name}".encode("utf-8")
    ).hexdigest()
    return f"ref-{digest[:32]}"


def _media_label(value: str) -> str:
    normalized = "".join(
        character if character.isalnum() else "-"
        for character in value.lower()
    ).strip("-")
    return normalized[:64] or "capture"


__all__ = ["RuntimeTrace"]
