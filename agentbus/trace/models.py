from __future__ import annotations

import json
from datetime import datetime, timezone
from enum import Enum
from typing import Annotated, Any

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

from agentbus.security.redaction import redact_text, sanitize_json
from agentbus.trace.version import TRACE_SCHEMA_NAME, TRACE_SCHEMA_VERSION

MAX_SAFE_TEXT_CHARS = 20_000
MAX_TRACE_ITEMS = 100_000
MAX_REFERENCES_PER_ITEM = 256

TraceIdentifier = Annotated[
    str,
    Field(
        min_length=1,
        max_length=128,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]*$",
    ),
]
Sha256Digest = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
SafeLabel = Annotated[str, Field(min_length=1, max_length=256)]


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class TraceModel(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)


class TraceSpanType(str, Enum):
    RUN = "run"
    PLANNING = "planning"
    TASK = "task"
    PROVIDER_REQUEST = "provider_request"
    PROVIDER_RESPONSE = "provider_response"
    MODEL_PARSE = "model_parse"
    TOOL_POLICY = "tool_policy"
    TOOL_INVOCATION = "tool_invocation"
    APPROVAL_WAIT = "approval_wait"
    VERIFIER = "verifier"
    REVIEWER = "reviewer"
    GIT_MUTATION = "git_mutation"
    INTEGRATION = "integration"
    CANCELLATION = "cancellation"
    CLEANUP = "cleanup"
    CUSTOM = "custom"


class TraceStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    INTERRUPTED = "interrupted"
    SKIPPED = "skipped"


class TraceLinkType(str, Enum):
    FOLLOWS_FROM = "follows_from"
    REPLAY_OF = "replay_of"
    FORKED_FROM = "forked_from"
    RELATED = "related"


class ReplayMode(str, Enum):
    STRICT = "strict"
    OFFLINE = "offline"
    VERIFY = "verify"
    SIMULATE = "simulate"


class TraceValueReference(TraceModel):
    reference_id: TraceIdentifier
    name: SafeLabel
    sha256: Sha256Digest
    media_type: str = Field(default="application/json", min_length=1, max_length=200)
    byte_length: int = Field(ge=0)
    redacted: bool = True

    @field_validator("name", "media_type")
    @classmethod
    def redact_bounded_text(cls, value: str) -> str:
        return _safe_text(value, max_chars=256 if len(value) <= 256 else 256)


class TraceInput(TraceValueReference):
    required_for_replay: bool = True


class TraceOutput(TraceValueReference):
    replayable: bool = True


class TraceArtifactReference(TraceModel):
    artifact_id: TraceIdentifier
    artifact_type: SafeLabel
    identifier: str = Field(min_length=1, max_length=2_048)
    sha256: Sha256Digest | None = None
    byte_length: int | None = Field(default=None, ge=0)
    media_type: str | None = Field(default=None, max_length=200)

    @field_validator("artifact_type", "identifier", "media_type")
    @classmethod
    def sanitize_artifact_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _safe_text(value, max_chars=2_048)


class TraceResourceUsage(TraceModel):
    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    cached_tokens: int | None = Field(default=None, ge=0)
    wall_time_ms: int | None = Field(default=None, ge=0)
    cpu_time_ms: int | None = Field(default=None, ge=0)
    peak_memory_bytes: int | None = Field(default=None, ge=0)
    stdout_bytes: int | None = Field(default=None, ge=0)
    stderr_bytes: int | None = Field(default=None, ge=0)
    custom: dict[str, Any] = Field(default_factory=dict)

    @field_validator("custom")
    @classmethod
    def sanitize_custom_usage(cls, value: dict[str, Any]) -> dict[str, Any]:
        return _safe_json(value, "trace resource usage")


class TraceFailure(TraceModel):
    category: SafeLabel
    message: str = Field(min_length=1, max_length=MAX_SAFE_TEXT_CHARS)
    retryable: bool = False
    details: dict[str, Any] = Field(default_factory=dict)

    @field_validator("category", "message")
    @classmethod
    def sanitize_failure_text(cls, value: str) -> str:
        return _safe_text(value)

    @field_validator("details")
    @classmethod
    def sanitize_failure_details(cls, value: dict[str, Any]) -> dict[str, Any]:
        return _safe_json(value, "trace failure details")


class TraceLink(TraceModel):
    link_type: TraceLinkType
    trace_id: TraceIdentifier
    span_id: TraceIdentifier | None = None
    attributes: dict[str, Any] = Field(default_factory=dict)

    @field_validator("attributes")
    @classmethod
    def sanitize_link_attributes(cls, value: dict[str, Any]) -> dict[str, Any]:
        return _safe_json(value, "trace link attributes")


class TraceReplayMetadata(TraceModel):
    replay_id: TraceIdentifier
    source_trace_id: TraceIdentifier
    mode: ReplayMode
    checkpoint_id: TraceIdentifier | None = None
    forked: bool = False
    substitutions_sha256: Sha256Digest | None = None
    providerless: bool = True
    created_at: datetime = Field(default_factory=utc_now)
    completed_at: datetime | None = None

    @field_validator("created_at", "completed_at")
    @classmethod
    def timestamps_are_utc(cls, value: datetime | None) -> datetime | None:
        return _utc_datetime(value)

    @model_validator(mode="after")
    def completion_is_ordered(self) -> "TraceReplayMetadata":
        _validate_interval(self.created_at, self.completed_at, "replay")
        return self


class TraceSpan(TraceModel):
    trace_id: TraceIdentifier
    span_id: TraceIdentifier
    parent_span_id: TraceIdentifier | None = None
    run_id: TraceIdentifier
    task_id: TraceIdentifier | None = None
    worker_id: TraceIdentifier | None = None
    invocation_id: TraceIdentifier | None = None
    span_type: TraceSpanType
    name: SafeLabel
    sequence: int = Field(ge=1)
    started_at: datetime = Field(default_factory=utc_now)
    ended_at: datetime | None = None
    status: TraceStatus = TraceStatus.RUNNING
    input_references: list[TraceInput] = Field(
        default_factory=list,
        max_length=MAX_REFERENCES_PER_ITEM,
    )
    output_references: list[TraceOutput] = Field(
        default_factory=list,
        max_length=MAX_REFERENCES_PER_ITEM,
    )
    policy_decision_references: list[TraceIdentifier] = Field(
        default_factory=list,
        max_length=MAX_REFERENCES_PER_ITEM,
    )
    approval_references: list[TraceIdentifier] = Field(
        default_factory=list,
        max_length=MAX_REFERENCES_PER_ITEM,
    )
    artifact_references: list[TraceArtifactReference] = Field(
        default_factory=list,
        max_length=MAX_REFERENCES_PER_ITEM,
    )
    links: list[TraceLink] = Field(
        default_factory=list,
        max_length=MAX_REFERENCES_PER_ITEM,
    )
    cancellation_state: dict[str, Any] = Field(default_factory=dict)
    resource_usage: TraceResourceUsage = Field(default_factory=TraceResourceUsage)
    failure: TraceFailure | None = None
    attributes: dict[str, Any] = Field(default_factory=dict)

    @field_validator("name")
    @classmethod
    def sanitize_name(cls, value: str) -> str:
        return _safe_text(value, max_chars=256)

    @field_validator("started_at", "ended_at")
    @classmethod
    def timestamps_are_utc(cls, value: datetime | None) -> datetime | None:
        return _utc_datetime(value)

    @field_validator("cancellation_state", "attributes")
    @classmethod
    def sanitize_attributes(cls, value: dict[str, Any]) -> dict[str, Any]:
        return _safe_json(value, "trace span attributes")

    @model_validator(mode="after")
    def terminal_state_is_consistent(self) -> "TraceSpan":
        _validate_interval(self.started_at, self.ended_at, "span")
        terminal = self.status not in {TraceStatus.PENDING, TraceStatus.RUNNING}
        if terminal and self.ended_at is None:
            raise ValueError("terminal trace spans must include ended_at")
        if self.status in {TraceStatus.PENDING, TraceStatus.RUNNING} and self.ended_at:
            raise ValueError("non-terminal trace spans cannot include ended_at")
        if self.status == TraceStatus.FAILED and self.failure is None:
            raise ValueError("failed trace spans must include safe failure information")
        if self.status != TraceStatus.FAILED and self.failure is not None:
            raise ValueError("only failed trace spans may include failure information")
        return self


class TraceEvent(TraceModel):
    trace_id: TraceIdentifier
    event_id: TraceIdentifier
    run_id: TraceIdentifier
    span_id: TraceIdentifier | None = None
    task_id: TraceIdentifier | None = None
    event_type: SafeLabel
    sequence: int = Field(ge=1)
    timestamp: datetime = Field(default_factory=utc_now)
    references: list[TraceValueReference] = Field(
        default_factory=list,
        max_length=MAX_REFERENCES_PER_ITEM,
    )
    attributes: dict[str, Any] = Field(default_factory=dict)

    @field_validator("event_type")
    @classmethod
    def sanitize_event_type(cls, value: str) -> str:
        return _safe_text(value, max_chars=256)

    @field_validator("timestamp")
    @classmethod
    def timestamp_is_utc(cls, value: datetime) -> datetime:
        return _utc_datetime(value)

    @field_validator("attributes")
    @classmethod
    def sanitize_event_attributes(cls, value: dict[str, Any]) -> dict[str, Any]:
        return _safe_json(value, "trace event attributes")


class TraceCheckpoint(TraceModel):
    checkpoint_id: TraceIdentifier
    trace_id: TraceIdentifier
    run_id: TraceIdentifier
    span_id: TraceIdentifier
    sequence: int = Field(ge=1)
    label: SafeLabel
    state_references: list[TraceInput] = Field(
        default_factory=list,
        max_length=MAX_REFERENCES_PER_ITEM,
    )
    replayable: bool = True
    created_at: datetime = Field(default_factory=utc_now)

    @field_validator("label")
    @classmethod
    def sanitize_label(cls, value: str) -> str:
        return _safe_text(value, max_chars=256)

    @field_validator("created_at")
    @classmethod
    def timestamp_is_utc(cls, value: datetime) -> datetime:
        return _utc_datetime(value)


class Trace(TraceModel):
    schema_name: str = TRACE_SCHEMA_NAME
    schema_version: int = TRACE_SCHEMA_VERSION
    trace_id: TraceIdentifier
    run_id: TraceIdentifier
    root_span_id: TraceIdentifier
    status: TraceStatus = TraceStatus.RUNNING
    created_at: datetime = Field(default_factory=utc_now)
    completed_at: datetime | None = None
    spans: list[TraceSpan] = Field(min_length=1, max_length=MAX_TRACE_ITEMS)
    events: list[TraceEvent] = Field(default_factory=list, max_length=MAX_TRACE_ITEMS)
    checkpoints: list[TraceCheckpoint] = Field(
        default_factory=list,
        max_length=MAX_TRACE_ITEMS,
    )
    links: list[TraceLink] = Field(
        default_factory=list,
        max_length=MAX_REFERENCES_PER_ITEM,
    )
    replay: TraceReplayMetadata | None = None
    attributes: dict[str, Any] = Field(default_factory=dict)

    @field_validator("created_at", "completed_at")
    @classmethod
    def timestamps_are_utc(cls, value: datetime | None) -> datetime | None:
        return _utc_datetime(value)

    @field_validator("attributes")
    @classmethod
    def sanitize_attributes(cls, value: dict[str, Any]) -> dict[str, Any]:
        return _safe_json(value, "trace attributes")

    @model_validator(mode="after")
    def validate_trace_graph(self) -> "Trace":
        if self.schema_name != TRACE_SCHEMA_NAME:
            raise ValueError(f"unsupported trace schema name: {self.schema_name!r}")
        if self.schema_version != TRACE_SCHEMA_VERSION:
            raise ValueError(
                f"unsupported trace schema version: {self.schema_version}"
            )
        _validate_interval(self.created_at, self.completed_at, "trace")
        terminal = self.status not in {TraceStatus.PENDING, TraceStatus.RUNNING}
        if terminal and self.completed_at is None:
            raise ValueError("terminal traces must include completed_at")
        if not terminal and self.completed_at is not None:
            raise ValueError("non-terminal traces cannot include completed_at")

        span_by_id = _unique_by_id(self.spans, "span_id", "span")
        root = span_by_id.get(self.root_span_id)
        if root is None:
            raise ValueError("root_span_id does not identify a trace span")
        if root.parent_span_id is not None or root.span_type != TraceSpanType.RUN:
            raise ValueError("the root span must be a parentless run span")

        sequences: dict[int, str] = {}
        for span in self.spans:
            _require_identity(self, span.trace_id, span.run_id, "span")
            _claim_sequence(sequences, span.sequence, f"span '{span.span_id}'")
            if span.parent_span_id is not None:
                if span.parent_span_id not in span_by_id:
                    raise ValueError(
                        f"span '{span.span_id}' references an unknown parent"
                    )
                if span.parent_span_id == span.span_id:
                    raise ValueError("trace spans cannot parent themselves")
        _validate_acyclic_spans(span_by_id)

        event_ids: set[str] = set()
        for event in self.events:
            _require_identity(self, event.trace_id, event.run_id, "event")
            if event.event_id in event_ids:
                raise ValueError(f"duplicate event ID: {event.event_id}")
            event_ids.add(event.event_id)
            _claim_sequence(sequences, event.sequence, f"event '{event.event_id}'")
            if event.span_id is not None and event.span_id not in span_by_id:
                raise ValueError(
                    f"event '{event.event_id}' references an unknown span"
                )

        checkpoint_ids: set[str] = set()
        for checkpoint in self.checkpoints:
            _require_identity(
                self,
                checkpoint.trace_id,
                checkpoint.run_id,
                "checkpoint",
            )
            if checkpoint.checkpoint_id in checkpoint_ids:
                raise ValueError(
                    f"duplicate checkpoint ID: {checkpoint.checkpoint_id}"
                )
            checkpoint_ids.add(checkpoint.checkpoint_id)
            _claim_sequence(
                sequences,
                checkpoint.sequence,
                f"checkpoint '{checkpoint.checkpoint_id}'",
            )
            if checkpoint.span_id not in span_by_id:
                raise ValueError(
                    f"checkpoint '{checkpoint.checkpoint_id}' references an unknown span"
                )
        if self.replay is not None and self.replay.forked:
            fork_links = [
                link
                for link in self.links
                if link.link_type == TraceLinkType.FORKED_FROM
            ]
            fork_attributes = self.attributes.get("fork")
            if (
                self.replay.source_trace_id == self.trace_id
                or len(fork_links) != 1
                or fork_links[0].trace_id != self.replay.source_trace_id
                or not isinstance(fork_attributes, dict)
                or fork_attributes.get("source_trace_id")
                != self.replay.source_trace_id
            ):
                raise ValueError("fork ancestry is inconsistent")
            for span in self.spans:
                source_span_id = span.attributes.get("fork_source_span_id")
                if not isinstance(source_span_id, str) or not any(
                    link.link_type == TraceLinkType.FORKED_FROM
                    and link.trace_id == self.replay.source_trace_id
                    and link.span_id == source_span_id
                    for link in span.links
                ):
                    raise ValueError("fork ancestry is inconsistent")
        return self


def _safe_text(value: str, *, max_chars: int = MAX_SAFE_TEXT_CHARS) -> str:
    sanitized = redact_text(value, max_chars=max_chars)
    if sanitized is None:
        raise ValueError("trace text cannot be null")
    return sanitized


def _safe_json(value: dict[str, Any], description: str) -> dict[str, Any]:
    sanitized = sanitize_json(value, max_chars=MAX_SAFE_TEXT_CHARS)
    try:
        json.dumps(sanitized, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{description} must be finite JSON") from exc
    return sanitized


def _utc_datetime(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("trace timestamps must be timezone-aware")
    return value.astimezone(timezone.utc)


def _validate_interval(
    started_at: datetime,
    ended_at: datetime | None,
    description: str,
) -> None:
    if ended_at is not None and ended_at < started_at:
        raise ValueError(f"{description} completion precedes its start")


def _unique_by_id(
    values: list[TraceSpan],
    attribute: str,
    description: str,
) -> dict[str, TraceSpan]:
    indexed: dict[str, TraceSpan] = {}
    for value in values:
        identifier = str(getattr(value, attribute))
        if identifier in indexed:
            raise ValueError(f"duplicate {description} ID: {identifier}")
        indexed[identifier] = value
    return indexed


def _claim_sequence(sequences: dict[int, str], sequence: int, owner: str) -> None:
    previous = sequences.get(sequence)
    if previous is not None:
        raise ValueError(
            f"deterministic sequence {sequence} is shared by {previous} and {owner}"
        )
    sequences[sequence] = owner


def _require_identity(
    trace: Trace,
    trace_id: str,
    run_id: str,
    description: str,
) -> None:
    if trace_id != trace.trace_id or run_id != trace.run_id:
        raise ValueError(f"{description} does not belong to this trace and run")


def _validate_acyclic_spans(spans: dict[str, TraceSpan]) -> None:
    for span_id in spans:
        visited: set[str] = set()
        current: str | None = span_id
        while current is not None:
            if current in visited:
                raise ValueError("trace span hierarchy contains a cycle")
            visited.add(current)
            current = spans[current].parent_span_id


__all__ = [
    "MAX_REFERENCES_PER_ITEM",
    "MAX_SAFE_TEXT_CHARS",
    "MAX_TRACE_ITEMS",
    "ReplayMode",
    "Trace",
    "TraceArtifactReference",
    "TraceCheckpoint",
    "TraceEvent",
    "TraceFailure",
    "TraceInput",
    "TraceLink",
    "TraceLinkType",
    "TraceOutput",
    "TraceReplayMetadata",
    "TraceResourceUsage",
    "TraceSpan",
    "TraceSpanType",
    "TraceStatus",
    "TraceValueReference",
    "utc_now",
]
