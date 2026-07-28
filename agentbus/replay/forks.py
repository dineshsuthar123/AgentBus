from __future__ import annotations

import hashlib
import uuid
from collections.abc import Callable
from datetime import datetime
from typing import Any

from pydantic import Field, field_validator, model_validator

from agentbus.replay.comparison import RunComparison, compare_traces
from agentbus.replay.engine import ReplayEngine
from agentbus.replay.errors import (
    ReplayConsentRequiredError,
    ReplayIncompatibleError,
)
from agentbus.replay.session import (
    ReplayRequest,
    ReplayResult,
    ReplaySessionStatus,
)
from agentbus.trace.models import (
    ReplayMode,
    Trace,
    TraceCheckpoint,
    TraceEvent,
    TraceInput,
    TraceLink,
    TraceLinkType,
    TraceReplayMetadata,
    TraceSpan,
    TraceModel,
    utc_now,
)
from agentbus.trace.redaction import canonical_json_bytes, sanitize_document
from agentbus.trace.spans import trace_id_for_run, trace_item_id
from agentbus.trace.storage import ContentAddressedStore

FORK_INPUT_MEDIA_TYPE = "application/vnd.agentbus.fork-inputs+json"
_ALLOWED_CHANGES = {
    "approval_decisions",
    "deterministic_provider_profile",
    "model_route",
    "policy_configuration",
    "resource_budgets",
    "retry_limit",
    "selected_source_patch",
    "task_text",
    "tool_response",
}
_APPROVAL_INVALIDATING_CHANGES = {
    "approval_decisions",
    "policy_configuration",
    "resource_budgets",
    "tool_response",
}
_LIVE_PROVIDERS = {"azure", "ollama"}


class ForkRequest(TraceModel):
    replay_id: str = Field(
        default_factory=lambda: f"replay-{uuid.uuid4().hex}",
        min_length=1,
        max_length=128,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]*$",
    )
    source_trace_id: str = Field(min_length=1, max_length=128)
    source_run_id: str = Field(min_length=1, max_length=128)
    mode: ReplayMode = ReplayMode.OFFLINE
    changed_inputs: dict[str, Any]
    live_provider_consent: bool = False

    @field_validator("changed_inputs")
    @classmethod
    def changes_are_allowed_and_safe(
        cls,
        value: dict[str, Any],
    ) -> dict[str, Any]:
        unknown = sorted(set(value) - _ALLOWED_CHANGES)
        if unknown:
            raise ValueError(
                "Unsupported fork input changes: " + ", ".join(unknown)
            )
        return sanitize_document(value).value

    @model_validator(mode="after")
    def changes_are_present(self) -> "ForkRequest":
        if not self.changed_inputs:
            raise ValueError("A fork must change at least one input.")
        return self


class ForkResult(TraceModel):
    source_trace_id: str
    fork_trace: Trace
    replay: ReplayResult
    comparison: RunComparison
    changed_input_names: list[str]
    changed_inputs_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    historical_approvals_invalidated: bool = False


class ForkManager:
    def __init__(
        self,
        store: ContentAddressedStore,
        replay_engine: ReplayEngine,
        *,
        clock: Callable[[], datetime] = utc_now,
        live_replay_executor: Callable[[Trace, ForkRequest], ReplayResult]
        | None = None,
    ):
        self.store = store
        self.replay_engine = replay_engine
        self.clock = clock
        self.live_replay_executor = live_replay_executor

    def fork(
        self,
        source: Trace,
        request: ForkRequest,
        *,
        session_created_at: datetime | None = None,
    ) -> ForkResult:
        if (
            request.source_trace_id != source.trace_id
            or request.source_run_id != source.run_id
        ):
            raise ReplayIncompatibleError(
                "Fork request does not identify the supplied source trace."
            )
        live_requested = _live_provider_requested(request.changed_inputs)
        if live_requested and not request.live_provider_consent:
            raise ReplayConsentRequiredError(
                "A live provider route requires explicit fork consent."
            )
        if live_requested:
            if self.live_replay_executor is None:
                raise ReplayIncompatibleError(
                    "Live fork execution is not configured; no provider was called."
                )
            replay = self.live_replay_executor(source, request)
        else:
            replay = self.replay_engine.replay(
                source,
                ReplayRequest(
                    replay_id=request.replay_id,
                    source_trace_id=source.trace_id,
                    source_run_id=source.run_id,
                    mode=request.mode,
                    fork=True,
                    changed_inputs=request.changed_inputs,
                ),
                session_created_at=session_created_at,
            )
        if replay.session.status != ReplaySessionStatus.SUCCEEDED:
            raise ReplayIncompatibleError(
                "Fork replay did not complete successfully: "
                f"{replay.session.status.value}."
            )
        changed_document = sanitize_document(request.changed_inputs)
        changed_hash = hashlib.sha256(
            changed_document.canonical_bytes
        ).hexdigest()
        metadata = self.store.put_json(
            changed_document.value,
            producing_span_id=source.root_span_id,
            media_type=FORK_INPUT_MEDIA_TYPE,
        )
        fork_trace = self._fork_trace(
            source,
            request,
            changed_hash=changed_hash,
            changed_reference=TraceInput(
                reference_id=f"{request.replay_id}-changed-inputs",
                name="fork changed inputs",
                sha256=metadata.sha256,
                media_type=metadata.media_type,
                byte_length=metadata.byte_size,
                redacted=metadata.redaction.applied,
                required_for_replay=True,
            ),
        )
        comparison = compare_traces(source, fork_trace, clock=self.clock)
        approval_invalidated = bool(
            set(request.changed_inputs) & _APPROVAL_INVALIDATING_CHANGES
        )
        return ForkResult(
            source_trace_id=source.trace_id,
            fork_trace=fork_trace,
            replay=replay,
            comparison=comparison,
            changed_input_names=sorted(request.changed_inputs),
            changed_inputs_sha256=changed_hash,
            historical_approvals_invalidated=approval_invalidated,
        )

    def _fork_trace(
        self,
        source: Trace,
        request: ForkRequest,
        *,
        changed_hash: str,
        changed_reference: TraceInput,
    ) -> Trace:
        run_digest = hashlib.sha256(
            f"{source.run_id}\0{request.replay_id}".encode("utf-8")
        ).hexdigest()
        run_id = f"fork-{run_digest[:32]}"
        trace_id = trace_id_for_run(run_id)
        span_ids = {
            span.span_id: trace_item_id(
                trace_id,
                span.sequence,
                span.span_type.value,
            )
            for span in source.spans
        }
        invalidates_approval = bool(
            set(request.changed_inputs) & _APPROVAL_INVALIDATING_CHANGES
        )
        started = self.clock()
        source_started = source.created_at
        spans = [
            _fork_span(
                span,
                trace_id=trace_id,
                run_id=run_id,
                span_ids=span_ids,
                started=started,
                source_started=source_started,
                changed_reference=(
                    changed_reference
                    if span.span_id == source.root_span_id
                    else None
                ),
                invalidate_approvals=invalidates_approval,
            )
            for span in source.spans
        ]
        event_ids = {
            event.event_id: trace_item_id(trace_id, event.sequence, "event")
            for event in source.events
        }
        events = [
            TraceEvent.model_validate(
                event.model_copy(
                    update={
                        "trace_id": trace_id,
                        "event_id": event_ids[event.event_id],
                        "run_id": run_id,
                        "span_id": (
                            span_ids[event.span_id]
                            if event.span_id is not None
                            else None
                        ),
                        "timestamp": started
                        + (event.timestamp - source_started),
                    }
                ).model_dump()
            )
            for event in source.events
        ]
        checkpoints = [
            TraceCheckpoint.model_validate(
                checkpoint.model_copy(
                    update={
                        "checkpoint_id": trace_item_id(
                            trace_id,
                            checkpoint.sequence,
                            "checkpoint",
                        ),
                        "trace_id": trace_id,
                        "run_id": run_id,
                        "span_id": span_ids[checkpoint.span_id],
                        "replayable": False,
                        "created_at": started
                        + (checkpoint.created_at - source_started),
                    }
                ).model_dump()
            )
            for checkpoint in source.checkpoints
        ]
        completed = started + (
            (source.completed_at or source.created_at) - source.created_at
        )
        return Trace(
            trace_id=trace_id,
            run_id=run_id,
            root_span_id=span_ids[source.root_span_id],
            status=source.status,
            created_at=started,
            completed_at=completed if source.completed_at is not None else None,
            spans=spans,
            events=events,
            checkpoints=checkpoints,
            links=[
                TraceLink(
                    link_type=TraceLinkType.FORKED_FROM,
                    trace_id=source.trace_id,
                    span_id=source.root_span_id,
                    attributes={"changed_inputs_sha256": changed_hash},
                )
            ],
            replay=TraceReplayMetadata(
                replay_id=request.replay_id,
                source_trace_id=source.trace_id,
                mode=request.mode,
                forked=True,
                substitutions_sha256=changed_hash,
                providerless=True,
                created_at=started,
                completed_at=completed,
            ),
            attributes={
                **source.attributes,
                "fork": {
                    "source_trace_id": source.trace_id,
                    "changed_input_names": sorted(request.changed_inputs),
                    "changed_inputs_sha256": changed_hash,
                    "historical_approvals_invalidated": invalidates_approval,
                },
            },
        )


def _fork_span(
    span: TraceSpan,
    *,
    trace_id: str,
    run_id: str,
    span_ids: dict[str, str],
    started: datetime,
    source_started: datetime,
    changed_reference: TraceInput | None,
    invalidate_approvals: bool,
) -> TraceSpan:
    inputs = list(span.input_references)
    if changed_reference is not None:
        inputs.append(changed_reference)
    links = [
        *span.links,
        TraceLink(
            link_type=TraceLinkType.FORKED_FROM,
            trace_id=span.trace_id,
            span_id=span.span_id,
        ),
    ]
    return TraceSpan.model_validate(
        span.model_copy(
            update={
                "trace_id": trace_id,
                "span_id": span_ids[span.span_id],
                "parent_span_id": (
                    span_ids[span.parent_span_id]
                    if span.parent_span_id is not None
                    else None
                ),
                "run_id": run_id,
                "started_at": started + (span.started_at - source_started),
                "ended_at": (
                    started + (span.ended_at - source_started)
                    if span.ended_at is not None
                    else None
                ),
                "input_references": inputs,
                "approval_references": (
                    [] if invalidate_approvals else span.approval_references
                ),
                "links": links,
                "attributes": {
                    **span.attributes,
                    "fork_source_span_id": span.span_id,
                    "fresh_approval_required": invalidate_approvals,
                },
            }
        ).model_dump()
    )


def _live_provider_requested(changes: dict[str, Any]) -> bool:
    route = changes.get("model_route")
    if isinstance(route, dict):
        return str(route.get("provider", "")).lower() in _LIVE_PROVIDERS
    return False


__all__ = [
    "FORK_INPUT_MEDIA_TYPE",
    "ForkManager",
    "ForkRequest",
    "ForkResult",
]
