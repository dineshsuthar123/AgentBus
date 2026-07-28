from __future__ import annotations

from datetime import datetime
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
from agentbus.tools.capabilities import derive_required_capabilities
from agentbus.tools.protocol import (
    ToolCapability,
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
    current_decision: ToolPolicyDecision | None = None
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
    private_roots = _invocation_private_roots(invocation)
    safe_descriptor = ToolDescriptor.model_validate(
        _sanitize_protocol_model(
            store,
            descriptor,
            private_roots=private_roots,
        )
    )
    safe_invocation = ToolInvocation.model_validate(
        _sanitize_protocol_model(
            store,
            invocation,
            private_roots=private_roots,
        )
    )
    safe_decision = _sanitize_policy_decision(
        store,
        safe_invocation,
        policy_decision,
        private_roots=private_roots,
    )
    safe_result = None
    if result is not None:
        result_payload = _sanitize_protocol_model(
            store,
            result,
            private_roots=private_roots,
        )
        result_payload["policy_decision"] = safe_decision.model_dump(mode="json")
        safe_result = ToolResult.model_validate(result_payload)
    safe_approval = (
        _sanitize_approval(
            store,
            approval,
            invocation=safe_invocation,
            decision=safe_decision,
            private_roots=private_roots,
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


def sanitize_replay_policy_decision(
    store: ContentAddressedStore,
    *,
    invocation: ToolInvocation,
    policy_decision: ToolPolicyDecision,
) -> ToolPolicyDecision:
    private_roots = _invocation_private_roots(invocation)
    safe_invocation = ToolInvocation.model_validate(
        _sanitize_protocol_model(
            store,
            invocation,
            private_roots=private_roots,
        )
    )
    return _sanitize_policy_decision(
        store,
        safe_invocation,
        policy_decision,
        private_roots=private_roots,
    )


def _sanitize_policy_decision(
    store: ContentAddressedStore,
    safe_invocation: ToolInvocation,
    policy_decision: ToolPolicyDecision,
    *,
    private_roots: tuple[str, ...],
) -> ToolPolicyDecision:
    decision_payload = _sanitize_protocol_model(
        store,
        policy_decision,
        private_roots=private_roots,
    )
    decision_payload.update(
        {
            "capability_fingerprint": capability_fingerprint(
                safe_invocation.requested_capabilities
            ),
            "arguments_sha256": sha256_json(safe_invocation.arguments),
        }
    )
    return ToolPolicyDecision.model_validate(decision_payload)


def _sanitize_protocol_model(
    store: ContentAddressedStore,
    value: Any,
    *,
    private_roots: tuple[str, ...] = (),
) -> dict[str, Any]:
    sanitized = sanitize_document(
        safe_protocol_dict(value),
        private_roots=(*store.private_roots, *private_roots),
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
    private_roots: tuple[str, ...],
) -> ToolApprovalGrant:
    request_payload = _sanitize_protocol_model(
        store,
        approval.request,
        private_roots=private_roots,
    )
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
    approval_payload = _sanitize_protocol_model(
        store,
        approval,
        private_roots=private_roots,
    )
    approval_payload.update(
        {
            "request": request.model_dump(mode="json"),
            "binding_sha256": approval_binding_sha256(request, invocation),
        }
    )
    return ToolApprovalGrant.model_validate(approval_payload)


def _invocation_private_roots(
    invocation: ToolInvocation,
) -> tuple[str, ...]:
    return (
        invocation.context.workspace_identity,
        invocation.context.worktree_identity,
    )


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
        private_roots = _descriptor_private_roots(current_descriptor)
        policy_workspace = _policy_workspace_identity(
            current_descriptor,
            fallback=isolated_workspace,
        )
        replay_invocation = envelope.invocation.model_copy(
            update={
                "tool_version": current_descriptor.version,
                "protocol_version": current_descriptor.protocol_version,
                "context": ToolInvocationContext(
                    workspace_identity=policy_workspace,
                    worktree_identity=policy_workspace,
                    caller_role=envelope.invocation.context.caller_role,
                    workspace_trusted=True,
                    provider_consented=(
                        envelope.invocation.context.provider_consented
                    ),
                    policy_context=envelope.invocation.context.policy_context,
                ),
            }
        )
        historical_capabilities = {
            item.model_dump_json()
            for item in envelope.invocation.requested_capabilities
        }
        capability_drift = False
        expanded_capabilities = False
        try:
            current_required = derive_required_capabilities(
                replay_invocation,
                current_descriptor,
            )
            safe_current_required = _sanitize_capabilities(
                current_required,
                private_roots=private_roots,
            )
            current_capabilities = {
                item.model_dump_json() for item in safe_current_required
            }
            capability_drift = historical_capabilities != current_capabilities
            expanded_capabilities = bool(
                current_capabilities - historical_capabilities
            )
            replay_invocation = replay_invocation.model_copy(
                update={"requested_capabilities": current_required}
            )
            current = self.policy_engine.evaluate(
                replay_invocation,
                current_descriptor,
            )
            current = _sanitize_current_policy_decision(
                current,
                replay_invocation,
                safe_current_required,
                private_roots=private_roots,
                evaluated_at=historical.evaluated_at,
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
            current_decision=current,
            descriptor_drift=descriptor_drift,
            capability_drift=capability_drift,
            policy_drift=policy_drift,
            approval_compatible_for_substitution=approval_compatible,
            fresh_authorization_required=fresh_authorization,
            reasons=reasons,
        )


def _descriptor_private_roots(
    descriptor: ToolDescriptor,
) -> tuple[str, ...]:
    candidates = {
        value
        for capability in descriptor.capabilities
        for value in (
            *capability.scope.roots,
            *capability.scope.working_directories,
        )
    }
    return tuple(
        sorted(
            (
                value
                for value in candidates
                if Path(value).expanduser().is_absolute()
            ),
            key=len,
            reverse=True,
        )
    )


def _policy_workspace_identity(
    descriptor: ToolDescriptor,
    *,
    fallback: str | Path,
) -> str:
    for capability in descriptor.capabilities:
        for value in (
            *capability.scope.working_directories,
            *capability.scope.roots,
        ):
            if Path(value).expanduser().is_absolute():
                return value
    return str(fallback)


def _sanitize_capabilities(
    capabilities: tuple[ToolCapability, ...],
    *,
    private_roots: tuple[str, ...],
) -> tuple[ToolCapability, ...]:
    return tuple(
        ToolCapability.model_validate(
            sanitize_document(
                capability.model_dump(mode="json"),
                private_roots=private_roots,
            ).value
        )
        for capability in capabilities
    )


def _sanitize_current_policy_decision(
    decision: ToolPolicyDecision,
    invocation: ToolInvocation,
    safe_capabilities: tuple[ToolCapability, ...],
    *,
    private_roots: tuple[str, ...],
    evaluated_at: datetime,
) -> ToolPolicyDecision:
    payload = sanitize_document(
        safe_protocol_dict(decision),
        private_roots=private_roots,
    ).value
    safe_arguments = sanitize_document(
        invocation.arguments,
        private_roots=private_roots,
    ).value
    payload.update(
        {
            "capability_fingerprint": capability_fingerprint(
                safe_capabilities
            ),
            "arguments_sha256": sha256_json(safe_arguments),
            "evaluated_at": evaluated_at,
        }
    )
    return ToolPolicyDecision.model_validate(payload)


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
    capabilities = {
        capability.name for capability in envelope.invocation.requested_capabilities
    }
    external = bool(capabilities & _EXTERNAL_CAPABILITIES)
    mutating = bool(capabilities & _MUTATING_CAPABILITIES)
    if mode in {ReplayMode.OFFLINE, ReplayMode.SIMULATE} and mutating:
        return (
            ToolReplayStrategy.SIMULATE_MUTATION,
            False,
            ["Mutation is simulated in providerless replay mode."],
        )
    if policy_drift and current.outcome == ToolPolicyOutcome.REQUIRE_APPROVAL:
        return (
            ToolReplayStrategy.REJECT,
            True,
            ["Current policy newly requires approval for this behavior."],
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
    "sanitize_replay_policy_decision",
]
