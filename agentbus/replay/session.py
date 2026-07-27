from __future__ import annotations

import uuid
from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import Field, field_validator

from agentbus.security.redaction import redact_text
from agentbus.trace.models import (
    ReplayMode,
    Sha256Digest,
    TraceIdentifier,
    TraceModel,
    TraceStatus,
    utc_now,
)


class ReplaySessionStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    INCOMPATIBLE = "incompatible"
    AWAITING_INPUT = "awaiting_input"


class ToolReplayStrategy(str, Enum):
    REUSE_CAPTURED = "reuse_captured"
    RERUN_SANDBOX = "rerun_sandbox"
    SIMULATE_MUTATION = "simulate_mutation"
    REJECT = "reject"


class ReplaySpanAction(str, Enum):
    REPLAYED = "replayed"
    SUBSTITUTED = "substituted"
    REUSED = "reused"
    RERUN = "rerun"
    SIMULATED = "simulated"
    OBSERVED = "observed"
    REJECTED = "rejected"


class ReplayRequest(TraceModel):
    replay_id: TraceIdentifier = Field(
        default_factory=lambda: f"replay-{uuid.uuid4().hex}"
    )
    source_trace_id: TraceIdentifier
    source_run_id: TraceIdentifier
    mode: ReplayMode
    from_span_id: TraceIdentifier | None = None
    from_checkpoint_id: TraceIdentifier | None = None
    fork: bool = False
    changed_inputs: dict[str, Any] = Field(default_factory=dict)
    tool_strategies: dict[str, ToolReplayStrategy] = Field(default_factory=dict)
    live_provider_consent: bool = False
    isolated_workspace: str | None = Field(default=None, max_length=4_096)

    @field_validator("changed_inputs")
    @classmethod
    def changed_inputs_are_safe(cls, value: dict[str, Any]) -> dict[str, Any]:
        from agentbus.trace.redaction import sanitize_document

        return sanitize_document(value).value


class ReplaySpanResult(TraceModel):
    span_id: TraceIdentifier
    action: ReplaySpanAction
    succeeded: bool
    summary: str = Field(min_length=1, max_length=4_000)
    output_sha256: Sha256Digest | None = None
    drift: list[str] = Field(default_factory=list, max_length=256)

    @field_validator("summary", "drift")
    @classmethod
    def text_is_safe(cls, value):
        if isinstance(value, list):
            return [
                redact_text(item, max_chars=1_000) or "unspecified"
                for item in value
            ]
        return redact_text(value, max_chars=4_000) or "unspecified"


class ReplaySession(TraceModel):
    replay_id: TraceIdentifier
    source_trace_id: TraceIdentifier
    source_run_id: TraceIdentifier
    mode: ReplayMode
    status: ReplaySessionStatus = ReplaySessionStatus.PENDING
    created_at: datetime = Field(default_factory=utc_now)
    started_at: datetime | None = None
    completed_at: datetime | None = None
    from_span_id: TraceIdentifier | None = None
    from_checkpoint_id: TraceIdentifier | None = None
    fork: bool = False
    changed_input_names: list[str] = Field(default_factory=list, max_length=1_024)
    isolated_workspace: str | None = Field(default=None, max_length=4_096)
    span_results: list[ReplaySpanResult] = Field(default_factory=list)
    substitutions: list[str] = Field(default_factory=list, max_length=4_096)
    missing_inputs: list[Sha256Digest] = Field(default_factory=list)
    policy_drift: list[str] = Field(default_factory=list, max_length=1_024)
    failure_category: str | None = Field(default=None, max_length=256)
    failure_message: str | None = Field(default=None, max_length=4_000)
    provider_calls: int = Field(default=0, ge=0)
    network_calls: int = Field(default=0, ge=0)

    @field_validator("failure_category", "failure_message")
    @classmethod
    def failure_is_safe(cls, value: str | None) -> str | None:
        return redact_text(value, max_chars=4_000)


class ReplayResult(TraceModel):
    session: ReplaySession
    source_status: TraceStatus
    replayed_status: TraceStatus
    result_sha256: Sha256Digest
    verifier_result: dict[str, Any] | None = None
    reviewer_result: dict[str, Any] | None = None

    @field_validator("verifier_result", "reviewer_result")
    @classmethod
    def component_results_are_safe(
        cls,
        value: dict[str, Any] | None,
    ) -> dict[str, Any] | None:
        if value is None:
            return None
        from agentbus.trace.redaction import sanitize_document

        return sanitize_document(value).value


__all__ = [
    "ReplayRequest",
    "ReplayResult",
    "ReplaySession",
    "ReplaySessionStatus",
    "ReplaySpanAction",
    "ReplaySpanResult",
    "ToolReplayStrategy",
]
