from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from pydantic import Field

from agentbus.tools.protocol import (
    ToolArtifact,
    ToolApprovalRequest,
    ToolAuditRecord,
    ToolCancellationSnapshot,
    ToolCapability,
    ToolError,
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
    idempotency_key_sha256,
    sha256_json,
    safe_protocol_dict,
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


class ToolApprovalRecord(ToolProtocolModel):
    approval_sequence: int = Field(ge=1)
    approval_id: str = Field(min_length=1, max_length=128)
    request: ToolApprovalRequest
    request_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    binding_sha256: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    disposition: str | None = Field(default=None, pattern=r"^(approved|rejected)$")
    reason: str | None = Field(default=None, max_length=2_048)
    created_at: datetime
    decided_at: datetime | None = None


class ToolAuditEntry(ToolProtocolModel):
    audit_sequence: int = Field(ge=1)
    record: ToolAuditRecord
    record_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")


TERMINAL_TOOL_STATUSES = frozenset(
    {
        ToolInvocationStatus.SUCCEEDED,
        ToolInvocationStatus.FAILED,
        ToolInvocationStatus.DENIED,
        ToolInvocationStatus.CANCELLED,
        ToolInvocationStatus.TIMED_OUT,
    }
)


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
    return idempotency_key_sha256(invocation.idempotency_key)


def policy_decision_sha256(decision: ToolPolicyDecision) -> str:
    payload = decision.model_dump(mode="json")
    payload.pop("evaluated_at", None)
    return sha256_json(payload)


def approval_request_scope_sha256(request: ToolApprovalRequest) -> str:
    payload = request.model_dump(mode="json")
    payload.pop("created_at", None)
    return sha256_json(payload)


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


def safe_persisted_tool_result(result: ToolResult) -> ToolResult:
    structured_output = result.structured_output
    stdout_bytes = result.stdout.encode("utf-8")
    stderr_bytes = result.stderr.encode("utf-8")
    diagnostics: dict[str, Any] = {
        "persisted_replay_summary": True,
        "structured_output_sha256": sha256_json(structured_output),
        "structured_output_key_count": len(structured_output),
        "stdout_sha256": hashlib.sha256(stdout_bytes).hexdigest(),
        "stdout_retained_bytes": len(stdout_bytes),
        "stderr_sha256": hashlib.sha256(stderr_bytes).hexdigest(),
        "stderr_retained_bytes": len(stderr_bytes),
        "diagnostic_metadata_sha256": sha256_json(
            result.safe_diagnostic_metadata
        ),
        "diagnostic_metadata_key_count": len(result.safe_diagnostic_metadata),
    }
    return ToolResult(
        invocation_id=result.invocation_id,
        invocation_revision=result.invocation_revision,
        status=result.status,
        structured_output={
            "persisted_summary": True,
            "sha256": diagnostics["structured_output_sha256"],
            "key_count": diagnostics["structured_output_key_count"],
        },
        stdout="",
        stderr="",
        stdout_truncated=result.stdout_truncated or bool(result.stdout),
        stderr_truncated=result.stderr_truncated or bool(result.stderr),
        artifacts=tuple(
            ToolArtifact.model_validate(safe_protocol_dict(artifact))
            for artifact in result.artifacts
        ),
        error=_safe_error(result.error),
        exit_code=result.exit_code,
        duration_seconds=result.duration_seconds,
        timed_out=result.timed_out,
        cancellation=ToolCancellationSnapshot.model_validate(
            safe_protocol_dict(result.cancellation)
        ),
        resource_usage=ToolResourceUsage.model_validate(
            safe_protocol_dict(result.resource_usage)
        ),
        policy_decision=safe_policy_decision(result.policy_decision),
        approval_id=result.approval_id,
        safe_diagnostic_metadata=diagnostics,
    )


def safe_policy_decision(decision: ToolPolicyDecision) -> ToolPolicyDecision:
    return ToolPolicyDecision.model_validate(safe_protocol_dict(decision))


def safe_tool_approval_request(
    request: ToolApprovalRequest,
) -> ToolApprovalRequest:
    return ToolApprovalRequest.model_validate(safe_protocol_dict(request))


def build_tool_audit_record(
    invocation: ToolInvocation,
    result: ToolResult,
    *,
    started_at: datetime | None,
    completed_at: datetime | None,
    affected_resource_hashes: dict[str, str] | None = None,
    audit_id: str | None = None,
    created_at: datetime | None = None,
) -> ToolAuditRecord:
    if result.invocation_id != invocation.invocation_id or (
        result.invocation_revision != invocation.invocation_revision
    ):
        raise ValueError("tool audit result does not match its invocation")
    if (
        result.policy_decision.invocation_id != invocation.invocation_id
        or result.policy_decision.invocation_revision
        != invocation.invocation_revision
        or result.policy_decision.capability_fingerprint
        != capability_fingerprint(invocation.requested_capabilities)
        or result.policy_decision.arguments_sha256
        != sha256_json(invocation.arguments)
    ):
        raise ValueError("tool audit policy does not match invocation arguments")
    return ToolAuditRecord(
        audit_id=audit_id or f"tool-audit-{uuid4().hex}",
        invocation_id=invocation.invocation_id,
        invocation_revision=invocation.invocation_revision,
        run_id=invocation.run_id,
        task_id=invocation.task_id,
        tool_name=invocation.tool_name,
        tool_version=invocation.tool_version,
        protocol_version=invocation.protocol_version,
        caller_role=invocation.context.caller_role,
        capabilities=invocation.requested_capabilities,
        policy_decision=safe_policy_decision(result.policy_decision),
        approval_id=result.approval_id,
        arguments_sha256=sha256_json(invocation.arguments),
        affected_resource_hashes=affected_resource_hashes or {},
        started_at=started_at,
        completed_at=completed_at,
        cancellation=ToolCancellationSnapshot.model_validate(
            safe_protocol_dict(result.cancellation)
        ),
        timed_out=result.timed_out,
        resource_usage=ToolResourceUsage.model_validate(
            safe_protocol_dict(result.resource_usage)
        ),
        artifacts=tuple(
            ToolArtifact.model_validate(safe_protocol_dict(artifact))
            for artifact in result.artifacts
        ),
        outcome=result.status,
        error_category=(result.error.category if result.error is not None else None),
        created_at=created_at or datetime.now(timezone.utc),
    )


def safe_tool_audit_record(record: ToolAuditRecord) -> ToolAuditRecord:
    payload = safe_protocol_dict(record)
    payload["affected_resource_hashes"] = dict(record.affected_resource_hashes)
    return ToolAuditRecord.model_validate(payload)


def tool_audit_scope_sha256(record: ToolAuditRecord) -> str:
    payload = record.model_dump(mode="json")
    payload.pop("audit_id", None)
    payload.pop("created_at", None)
    return sha256_json(payload)


def _safe_error(error: ToolError | None) -> ToolError | None:
    if error is None:
        return None
    return ToolError.model_validate(safe_protocol_dict(error))
