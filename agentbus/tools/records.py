from __future__ import annotations

import hashlib
from datetime import datetime

from pydantic import Field

from agentbus.tools.protocol import (
    ToolCancellationSnapshot,
    ToolCapability,
    ToolErrorCategory,
    ToolInvocation,
    ToolInvocationStatus,
    ToolPolicyDecision,
    ToolProtocolModel,
    ToolResourceBudget,
    ToolResourceUsage,
    ToolResult,
    ToolVersion,
    capability_fingerprint,
    sha256_json,
)


class ToolInvocationRecord(ToolProtocolModel):
    invocation_sequence: int = Field(ge=1)
    invocation_id: str = Field(min_length=1, max_length=128)
    invocation_revision: int = Field(ge=1)
    run_id: str = Field(min_length=1, max_length=128)
    task_id: str = Field(min_length=1, max_length=128)
    tool_name: str = Field(min_length=1, max_length=128)
    tool_version: ToolVersion
    protocol_version: str = Field(min_length=1, max_length=32)
    caller_role: str = Field(min_length=1, max_length=64)
    workspace_identity: str = Field(min_length=1, max_length=2_048)
    worktree_identity: str = Field(min_length=1, max_length=2_048)
    capabilities: tuple[ToolCapability, ...]
    capability_fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")
    arguments_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    invocation_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    operation_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    idempotency_key_sha256: str | None = Field(
        default=None,
        pattern=r"^[a-f0-9]{64}$",
    )
    status: ToolInvocationStatus
    resource_budget: ToolResourceBudget
    anticipated_usage: ToolResourceUsage = Field(default_factory=ToolResourceUsage)
    resource_usage: ToolResourceUsage = Field(default_factory=ToolResourceUsage)
    process_slot: bool = False
    policy_decision: ToolPolicyDecision | None = None
    approval_id: str | None = Field(default=None, max_length=128)
    safe_result: ToolResult | None = None
    cancellation: ToolCancellationSnapshot = Field(
        default_factory=ToolCancellationSnapshot
    )
    error_category: ToolErrorCategory | None = None
    error_message: str | None = Field(default=None, max_length=2_048)
    requested_at: datetime
    started_at: datetime | None = None
    completed_at: datetime | None = None
    updated_at: datetime


def invocation_identity_sha256(invocation: ToolInvocation) -> str:
    payload = invocation.model_dump(mode="json")
    payload.pop("requested_at", None)
    return sha256_json(payload)


def invocation_operation_sha256(invocation: ToolInvocation) -> str:
    payload = invocation.model_dump(mode="json")
    payload.pop("invocation_id", None)
    payload.pop("requested_at", None)
    return sha256_json(payload)


def invocation_idempotency_sha256(invocation: ToolInvocation) -> str | None:
    if invocation.idempotency_key is None:
        return None
    encoded = (
        "agentbus-tool-idempotency-v1\0" + invocation.idempotency_key
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def invocation_record_values(
    invocation: ToolInvocation,
    *,
    anticipated_usage: ToolResourceUsage | None = None,
    process_slot: bool = False,
    updated_at: datetime,
) -> dict[str, object]:
    return {
        "invocation_id": invocation.invocation_id,
        "invocation_revision": invocation.invocation_revision,
        "run_id": invocation.run_id,
        "task_id": invocation.task_id,
        "tool_name": invocation.tool_name,
        "tool_version": invocation.tool_version,
        "protocol_version": invocation.protocol_version,
        "caller_role": invocation.context.caller_role,
        "workspace_identity": invocation.context.workspace_identity,
        "worktree_identity": invocation.context.worktree_identity,
        "capabilities": invocation.requested_capabilities,
        "capability_fingerprint": capability_fingerprint(
            invocation.requested_capabilities
        ),
        "arguments_sha256": sha256_json(invocation.arguments),
        "invocation_sha256": invocation_identity_sha256(invocation),
        "operation_sha256": invocation_operation_sha256(invocation),
        "idempotency_key_sha256": invocation_idempotency_sha256(invocation),
        "status": ToolInvocationStatus.REQUESTED,
        "resource_budget": invocation.resource_budget,
        "anticipated_usage": anticipated_usage or ToolResourceUsage(),
        "resource_usage": ToolResourceUsage(),
        "process_slot": process_slot,
        "cancellation": ToolCancellationSnapshot(
            revision=invocation.cancellation_revision
        ),
        "requested_at": invocation.requested_at,
        "updated_at": updated_at,
    }
