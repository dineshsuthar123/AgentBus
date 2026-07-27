from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import Field, model_validator

from agentbus.policy import (
    ToolApprovalGrant,
    ToolPolicyEngine,
    approval_binding_sha256,
)
from agentbus.replay.errors import ReplayIncompatibleError
from agentbus.replay.session import ToolReplayStrategy
from agentbus.trace.models import ReplayMode, TraceModel, TraceOutput
from agentbus.trace.redaction import sanitize_document
from agentbus.trace.storage import ContentAddressedStore
from agentbus.tools.protocol import (
    ToolCapabilityName,
    ToolApprovalRequest,
    ToolDescriptor,
    ToolInvocation,
    ToolInvocationContext,
    ToolPolicyDecision,
    ToolPolicyOutcome,
    ToolResult,
    ToolSafetyClassification,
    capability_fingerprint,
    idempotency_key_sha256,
    safe_protocol_dict,
    sha256_json,
)

TOOL_ENVELOPE_MEDIA_TYPE = "application/vnd.agentbus.tool-envelope+json"
TOOL_ENVELOPE_VERSION = 1
_MUTATING_CAPABILITIES = {
    ToolCapabilityName.FILESYSTEM_WRITE,
    ToolCapabilityName.FILESYSTEM_CREATE,
    ToolCapabilityName.FILESYSTEM_DELETE,
    ToolCapabilityName.FILESYSTEM_RENAME,
    ToolCapabilityName.GIT_WRITE,
    ToolCapabilityName.GIT_COMMIT,
    ToolCapabilityName.GIT_BRANCH,
    ToolCapabilityName.GIT_WORKTREE,
    ToolCapabilityName.PACKAGE_INSTALL,
}
_EXTERNAL_CAPABILITIES = {
    ToolCapabilityName.PROCESS_NETWORK,
    ToolCapabilityName.MCP_CONNECT,
    ToolCapabilityName.MCP_INVOKE,
}


class CapturedToolEnvelope(TraceModel):
    envelope_version: int = TOOL_ENVELOPE_VERSION
    descriptor: ToolDescriptor
    invocation: ToolInvocation
    policy_decision: ToolPolicyDecision
    result: ToolResult | None = None
    approval: ToolApprovalGrant | None = None

    @model_validator(mode="after")
    def bindings_match(self) -> "CapturedToolEnvelope":
        if self.envelope_version != TOOL_ENVELOPE_VERSION:
            raise ValueError(
                f"unsupported tool envelope version: {self.envelope_version}"
            )
        if self.descriptor.name != self.invocation.tool_name:
            raise ValueError("tool envelope descriptor does not match invocation")
        if (
            self.policy_decision.invocation_id != self.invocation.invocation_id
            or self.policy_decision.invocation_revision
            != self.invocation.invocation_revision
            or self.policy_decision.capability_fingerprint
            != capability_fingerprint(self.invocation.requested_capabilities)
        ):
            raise ValueError("tool envelope policy binding does not match invocation")
        if self.result is not None and (
            self.result.invocation_id != self.invocation.invocation_id
            or self.result.invocation_revision
            != self.invocation.invocation_revision
        ):
            raise ValueError("tool envelope result does not match invocation")
        return self


class ToolReplayAssessment(TraceModel):
    invocation_id: str = Field(min_length=1, max_length=128)
    strategy: ToolReplayStrategy
    historical_outcome: ToolPolicyOutcome
    current_outcome: ToolPolicyOutcome | None = None
    descriptor_drift: bool = False
    capability_drift: bool = False
    policy_drift: bool = False
    approval_compatible_for_substitution: bool = False
    fresh_authorization_required: bool = False
    reasons: list[str] = Field(min_length=1, max_length=128)


def capture_tool_envelope(
    store: ContentAddressedStore,
    *,
    descriptor: ToolDescriptor,
    invocation: ToolInvocation,
    policy_decision: ToolPolicyDecision,
    producing_span_id: str,
    reference_id: str,
    result: ToolResult | None = None,
    approval: ToolApprovalGrant | None = None,
) -> TraceOutput:
    safe_descriptor = ToolDescriptor.model_validate(
        _sanitize_protocol_model(store, descriptor)
    )
    safe_invocation = ToolInvocation.model_validate(
        _sanitize_protocol_model(store, invocation)
    )
    decision_payload = _sanitize_protocol_model(store, policy_decision)
    decision_payload.update(
        {
            "capability_fingerprint": capability_fingerprint(
                safe_invocation.requested_capabilities
            ),
            "arguments_sha256": sha256_json(safe_invocation.arguments),
        }
    )
    safe_decision = ToolPolicyDecision.model_validate(decision_payload)
    safe_result = None
    if result is not None:
        result_payload = _sanitize_protocol_model(store, result)
        result_payload["policy_decision"] = safe_decision.model_dump(mode="json")
        safe_result = ToolResult.model_validate(result_payload)
    safe_approval = (
        _sanitize_approval(
            store,
            approval,
            invocation=safe_invocation,
            decision=safe_decision,
        )
        if approval is not None
        else None
    )
    envelope = CapturedToolEnvelope(
        descriptor=safe_descriptor,
        invocation=safe_invocation,
        policy_decision=safe_decision,
        result=safe_result,
        approval=safe_approval,
    )
    metadata = store.put_json(
        envelope.model_dump(mode="json"),
        producing_span_id=producing_span_id,
        media_type=TOOL_ENVELOPE_MEDIA_TYPE,
    )
    return store.reference_output(
        metadata,
        reference_id=reference_id,
        name=f"tool.invocation.{invocation.tool_name}",
        replayable=True,
    )


def _sanitize_protocol_model(
    store: ContentAddressedStore,
    value: Any,
) -> dict[str, Any]:
    sanitized = sanitize_document(
        safe_protocol_dict(value),
        private_roots=store.private_roots,
    ).value
    if not isinstance(sanitized, dict):
        raise ValueError("sanitized tool protocol value must remain an object")
    return sanitized


def _sanitize_approval(
    store: ContentAddressedStore,
    approval: ToolApprovalGrant,
    *,
    invocation: ToolInvocation,
    decision: ToolPolicyDecision,
) -> ToolApprovalGrant:
    request_payload = _sanitize_protocol_model(store, approval.request)
    request_payload.update(
        {
            "requested_capabilities": [
                item.model_dump(mode="json")
                for item in invocation.requested_capabilities
            ],
            "capability_fingerprint": capability_fingerprint(
                invocation.requested_capabilities
            ),
            "arguments_sha256": sha256_json(invocation.arguments),
            "workspace_identity": invocation.context.workspace_identity,
            "worktree_identity": invocation.context.worktree_identity,
            "idempotency_key_sha256": idempotency_key_sha256(
                invocation.idempotency_key
            ),
            "proposed_constraints": [
                item.model_dump(mode="json") for item in decision.constraints
            ],
        }
    )
    request = ToolApprovalRequest.model_validate(request_payload)
    approval_payload = _sanitize_protocol_model(store, approval)
    approval_payload.update(
        {
            "request": request.model_dump(mode="json"),
            "binding_sha256": approval_binding_sha256(request, invocation),
        }
    )
    return ToolApprovalGrant.model_validate(approval_payload)


def load_tool_envelope(
    store: ContentAddressedStore,
    sha256: str,
) -> CapturedToolEnvelope:
    metadata = store.get_metadata(sha256)
    if metadata.media_type != TOOL_ENVELOPE_MEDIA_TYPE:
        raise ReplayIncompatibleError(
            "Captured tool reference has an incompatible media type."
        )
    try:
        return CapturedToolEnvelope.model_validate(store.get_json(sha256))
    except Exception as exc:
        raise ReplayIncompatibleError(
            "Captured tool envelope is invalid or incompatible."
        ) from exc


class ToolReplayPlanner:
    """Reevaluate historical tool behavior under the current descriptor/policy."""

    def __init__(self, policy_engine: ToolPolicyEngine | Any | None = None):
        self.policy_engine = policy_engine or ToolPolicyEngine()

    def assess(
        self,
        envelope: CapturedToolEnvelope,
        current_descriptor: ToolDescriptor,
        *,
        mode: ReplayMode,
        isolated_workspace: str | Path = "[ISOLATED_REPLAY_WORKSPACE]",
    ) -> ToolReplayAssessment:
        historical = envelope.policy_decision
        descriptor_drift = (
            envelope.descriptor.version != current_descriptor.version
            or envelope.descriptor.protocol_version
            != current_descriptor.protocol_version
            or envelope.descriptor.argument_schema
            != current_descriptor.argument_schema
            or envelope.descriptor.output_schema
            != current_descriptor.output_schema
        )
        historical_capabilities = {
            item.model_dump_json()
            for item in envelope.invocation.requested_capabilities
        }
        current_capabilities = {
            item.model_dump_json() for item in current_descriptor.capabilities
        }
        capability_drift = historical_capabilities != current_capabilities
        expanded_capabilities = bool(
            current_capabilities - historical_capabilities
        )
        replay_invocation = envelope.invocation.model_copy(
            update={
                "tool_version": current_descriptor.version,
                "protocol_version": current_descriptor.protocol_version,
                "context": ToolInvocationContext(
                    workspace_identity=str(isolated_workspace),
                    worktree_identity=str(isolated_workspace),
                    caller_role=envelope.invocation.context.caller_role,
                    workspace_trusted=True,
                    provider_consented=False,
                    policy_context=envelope.invocation.context.policy_context,
                ),
            }
        )
        try:
            current = self.policy_engine.evaluate(
                replay_invocation,
                current_descriptor,
            )
        except Exception as exc:
            return ToolReplayAssessment(
                invocation_id=envelope.invocation.invocation_id,
                strategy=ToolReplayStrategy.REJECT,
                historical_outcome=historical.outcome,
                descriptor_drift=True,
                capability_drift=capability_drift,
                policy_drift=True,
                fresh_authorization_required=expanded_capabilities,
                reasons=[
                    "Current descriptor or policy rejected historical invocation validation.",
                    f"Safe error category: {type(exc).__name__}.",
                ],
            )
        policy_drift = (
            current.outcome != historical.outcome
            or current.rule_id != historical.rule_id
            or capability_fingerprint(current.constraints)
            != capability_fingerprint(historical.constraints)
        )
        approval_compatible = _approval_compatible(envelope)
        strategy, fresh_authorization, reasons = _strategy(
            envelope,
            current_descriptor,
            current,
            mode=mode,
            descriptor_drift=descriptor_drift,
            capability_drift=capability_drift,
            expanded_capabilities=expanded_capabilities,
            policy_drift=policy_drift,
            approval_compatible=approval_compatible,
        )
        return ToolReplayAssessment(
            invocation_id=envelope.invocation.invocation_id,
            strategy=strategy,
            historical_outcome=historical.outcome,
            current_outcome=current.outcome,
            descriptor_drift=descriptor_drift,
            capability_drift=capability_drift,
            policy_drift=policy_drift,
            approval_compatible_for_substitution=approval_compatible,
            fresh_authorization_required=fresh_authorization,
            reasons=reasons,
        )


def _strategy(
    envelope: CapturedToolEnvelope,
    descriptor: ToolDescriptor,
    current: ToolPolicyDecision,
    *,
    mode: ReplayMode,
    descriptor_drift: bool,
    capability_drift: bool,
    expanded_capabilities: bool,
    policy_drift: bool,
    approval_compatible: bool,
) -> tuple[ToolReplayStrategy, bool, list[str]]:
    if current.outcome == ToolPolicyOutcome.DENY:
        return (
            ToolReplayStrategy.REJECT,
            False,
            ["Current policy denies the historical tool behavior."],
        )
    if expanded_capabilities:
        return (
            ToolReplayStrategy.REJECT,
            True,
            ["Current descriptor expands capabilities and requires fresh authorization."],
        )
    if policy_drift and current.outcome == ToolPolicyOutcome.REQUIRE_APPROVAL:
        return (
            ToolReplayStrategy.REJECT,
            True,
            ["Current policy newly requires approval for this behavior."],
        )
    capabilities = {
        capability.name for capability in envelope.invocation.requested_capabilities
    }
    external = bool(capabilities & _EXTERNAL_CAPABILITIES)
    mutating = bool(capabilities & _MUTATING_CAPABILITIES)
    if mode == ReplayMode.SIMULATE and mutating:
        return (
            ToolReplayStrategy.SIMULATE_MUTATION,
            False,
            ["Mutation is simulated in replay mode."],
        )
    if external:
        if envelope.result is not None:
            return (
                ToolReplayStrategy.REUSE_CAPTURED,
                False,
                ["Captured external tool envelope is reused without a network call."],
            )
        return (
            ToolReplayStrategy.REJECT,
            False,
            ["External tool behavior has no captured result."],
        )
    if mutating:
        if (
            descriptor.safety == ToolSafetyClassification.SAFE
            and descriptor.idempotent
            and not descriptor_drift
            and not capability_drift
        ):
            return (
                ToolReplayStrategy.RERUN_SANDBOX,
                current.outcome == ToolPolicyOutcome.REQUIRE_APPROVAL,
                ["Deterministic mutation may rerun only in an isolated sandbox."],
            )
        return (
            ToolReplayStrategy.SIMULATE_MUTATION,
            False,
            ["Mutation is simulated because its current contract is not exactly stable."],
        )
    if envelope.result is not None:
        return (
            ToolReplayStrategy.REUSE_CAPTURED,
            False,
            ["Captured pure-read result may be reused deterministically."],
        )
    if current.outcome == ToolPolicyOutcome.REQUIRE_APPROVAL:
        return (
            ToolReplayStrategy.REJECT,
            True,
            [
                "Rerunning this historical invocation requires fresh scoped authorization."
            ],
        )
    return (
        ToolReplayStrategy.RERUN_SANDBOX,
        False,
        ["Safe deterministic tool may rerun in an isolated sandbox."],
    )


def _approval_compatible(envelope: CapturedToolEnvelope) -> bool:
    approval = envelope.approval
    if approval is None:
        return False
    request = approval.request
    return bool(
        request.invocation_id == envelope.invocation.invocation_id
        and request.invocation_revision == envelope.invocation.invocation_revision
        and request.tool_name == envelope.invocation.tool_name
        and request.tool_version == envelope.descriptor.version
        and request.protocol_version == envelope.descriptor.protocol_version
        and request.capability_fingerprint
        == capability_fingerprint(envelope.invocation.requested_capabilities)
        and request.arguments_sha256 == envelope.policy_decision.arguments_sha256
    )


__all__ = [
    "TOOL_ENVELOPE_MEDIA_TYPE",
    "TOOL_ENVELOPE_VERSION",
    "CapturedToolEnvelope",
    "ToolReplayAssessment",
    "ToolReplayPlanner",
    "capture_tool_envelope",
    "load_tool_envelope",
]
