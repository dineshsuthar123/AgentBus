from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from agentbus.policy.errors import ToolApprovalBindingError
from agentbus.policy.models import ToolApprovalDisposition, ToolApprovalGrant
from agentbus.policy.rules import derive_policy_facts
from agentbus.security.redaction import redact_text
from agentbus.tools.protocol import (
    ToolApprovalRequest,
    ToolDescriptor,
    ToolInvocation,
    ToolPolicyDecision,
    ToolPolicyOutcome,
    capability_fingerprint,
    idempotency_key_sha256,
    require_capabilities_unchanged,
    require_invocation_revision,
    sha256_json,
)


def build_tool_approval_request(
    invocation: ToolInvocation,
    descriptor: ToolDescriptor,
    decision: ToolPolicyDecision,
    *,
    approval_id: str | None = None,
    expires_at: datetime | None = None,
) -> ToolApprovalRequest:
    if decision.outcome != ToolPolicyOutcome.REQUIRE_APPROVAL:
        raise ValueError("approval requests require a require_approval decision")
    if decision.invocation_id != invocation.invocation_id or (
        decision.invocation_revision != invocation.invocation_revision
    ):
        raise ToolApprovalBindingError(
            "Policy decision does not match the invocation revision."
        )
    facts = derive_policy_facts(descriptor, invocation)
    return ToolApprovalRequest(
        approval_id=approval_id or f"tool-approval-{uuid4().hex}",
        invocation_id=invocation.invocation_id,
        invocation_revision=invocation.invocation_revision,
        run_id=invocation.run_id,
        task_id=invocation.task_id,
        tool_name=invocation.tool_name,
        tool_version=invocation.tool_version,
        protocol_version=invocation.protocol_version,
        requested_capabilities=invocation.requested_capabilities,
        capability_fingerprint=capability_fingerprint(
            invocation.requested_capabilities
        ),
        arguments_sha256=sha256_json(invocation.arguments),
        workspace_identity=invocation.context.workspace_identity,
        worktree_identity=invocation.context.worktree_identity,
        affected_paths=facts.affected_paths,
        executable=facts.executable,
        arguments_summary=_arguments_summary(facts.arguments),
        working_directory=facts.working_directory,
        network_destination=(
            facts.network_destinations[0] if facts.network_destinations else None
        ),
        resource_budget=invocation.resource_budget,
        cancellation_revision=invocation.cancellation_revision,
        idempotency_key_sha256=idempotency_key_sha256(
            invocation.idempotency_key
        ),
        policy_rule=decision.rule_id,
        reason=decision.reason,
        proposed_constraints=decision.constraints,
        expires_at=expires_at,
    )


def decide_tool_approval(
    request: ToolApprovalRequest,
    invocation: ToolInvocation,
    *,
    disposition: ToolApprovalDisposition,
    reason: str | None = None,
    decided_at: datetime | None = None,
) -> ToolApprovalGrant:
    _validate_request_fields(request, invocation)
    return decide_persisted_tool_approval(
        request,
        disposition=disposition,
        reason=reason,
        decided_at=decided_at,
    )


def decide_persisted_tool_approval(
    request: ToolApprovalRequest,
    *,
    disposition: ToolApprovalDisposition,
    reason: str | None = None,
    decided_at: datetime | None = None,
) -> ToolApprovalGrant:
    """Decide an immutable persisted request without reconstructing raw inputs."""
    return ToolApprovalGrant(
        approval_id=request.approval_id,
        request=request,
        disposition=disposition,
        binding_sha256=approval_binding_sha256(request),
        reason=redact_text(reason, max_chars=2_048),
        decided_at=decided_at or datetime.now(timezone.utc),
    )


def validate_tool_approval(
    grant: ToolApprovalGrant,
    invocation: ToolInvocation,
    descriptor: ToolDescriptor,
    *,
    now: datetime | None = None,
) -> None:
    request = grant.request
    if grant.disposition != ToolApprovalDisposition.APPROVED:
        raise ToolApprovalBindingError("The tool approval was rejected.")
    if request.tool_name != descriptor.name or request.tool_version != descriptor.version:
        raise ToolApprovalBindingError(
            "The tool approval does not match the resolved descriptor."
        )
    if request.protocol_version != descriptor.protocol_version:
        raise ToolApprovalBindingError(
            "The tool protocol version changed after approval."
        )
    _validate_request_fields(request, invocation)
    expected = approval_binding_sha256(request, invocation)
    if grant.binding_sha256 != expected:
        raise ToolApprovalBindingError(
            "The tool approval binding does not match the current invocation."
        )
    current = now or datetime.now(timezone.utc)
    if request.expires_at is not None and current >= request.expires_at:
        raise ToolApprovalBindingError("The tool approval has expired.")


def approval_binding_sha256(
    request: ToolApprovalRequest,
    invocation: ToolInvocation | None = None,
) -> str:
    if invocation is not None:
        _validate_request_fields(request, invocation)
    return sha256_json(
        {
            "binding_version": 2,
            "request": request.model_dump(mode="json"),
        }
    )


def _validate_request_fields(
    request: ToolApprovalRequest,
    invocation: ToolInvocation,
) -> None:
    comparisons = {
        "invocation id": (request.invocation_id, invocation.invocation_id),
        "run id": (request.run_id, invocation.run_id),
        "task id": (request.task_id, invocation.task_id),
        "tool name": (request.tool_name, invocation.tool_name),
        "tool version": (request.tool_version, invocation.tool_version),
        "protocol version": (request.protocol_version, invocation.protocol_version),
        "workspace": (
            request.workspace_identity,
            invocation.context.workspace_identity,
        ),
        "worktree": (
            request.worktree_identity,
            invocation.context.worktree_identity,
        ),
        "resource budget": (request.resource_budget, invocation.resource_budget),
        "arguments": (request.arguments_sha256, sha256_json(invocation.arguments)),
        "cancellation revision": (
            request.cancellation_revision,
            invocation.cancellation_revision,
        ),
        "idempotency key": (
            request.idempotency_key_sha256,
            idempotency_key_sha256(invocation.idempotency_key),
        ),
    }
    for field, (approved, current) in comparisons.items():
        if approved != current:
            raise ToolApprovalBindingError(
                f"Tool approval {field} changed after authorization."
            )
    try:
        require_invocation_revision(
            approved_revision=request.invocation_revision,
            current_revision=invocation.invocation_revision,
        )
        require_capabilities_unchanged(
            request.requested_capabilities,
            invocation.requested_capabilities,
        )
    except ValueError as exc:
        raise ToolApprovalBindingError(str(exc)) from exc
    if request.capability_fingerprint != capability_fingerprint(
        invocation.requested_capabilities
    ):
        raise ToolApprovalBindingError(
            "Tool approval capability fingerprint changed after authorization."
        )


def _arguments_summary(arguments: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(
        redact_text(argument, max_chars=256) or ""
        for argument in arguments[:32]
    )
