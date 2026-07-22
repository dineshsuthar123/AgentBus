from __future__ import annotations

from agentbus.policy.defaults import DEFAULT_TOOL_POLICY, ToolPolicyConfiguration
from agentbus.policy.evaluator import DefaultPolicyEvaluator
from agentbus.policy.errors import ToolApprovalBindingError
from agentbus.policy.models import ToolApprovalGrant
from agentbus.policy.rules import derive_policy_facts
from agentbus.tools.protocol import (
    ToolDescriptor,
    ToolInvocation,
    ToolPolicyDecision,
    ToolPolicyOutcome,
    capability_fingerprint,
    validate_invocation_against_descriptor,
)


class ToolPolicyEngine:
    def __init__(
        self,
        configuration: ToolPolicyConfiguration = DEFAULT_TOOL_POLICY,
    ) -> None:
        self.configuration = configuration
        self.evaluator = DefaultPolicyEvaluator(configuration)

    def evaluate(
        self,
        invocation: ToolInvocation,
        descriptor: ToolDescriptor,
        *,
        approval: ToolApprovalGrant | None = None,
    ) -> ToolPolicyDecision:
        validate_invocation_against_descriptor(invocation, descriptor)
        facts = derive_policy_facts(descriptor, invocation)
        outcome, match = self.evaluator.evaluate(facts)
        decision = ToolPolicyDecision(
            outcome=outcome,
            rule_id=match.rule_id,
            reason=match.reason,
            invocation_id=invocation.invocation_id,
            invocation_revision=invocation.invocation_revision,
            capability_fingerprint=capability_fingerprint(
                invocation.requested_capabilities
            ),
            constraints=match.constraints,
            safe_metadata=match.safe_metadata,
        )
        if decision.outcome != ToolPolicyOutcome.REQUIRE_APPROVAL or approval is None:
            return decision

        from agentbus.policy.approvals import validate_tool_approval

        try:
            validate_tool_approval(approval, invocation, descriptor)
        except ToolApprovalBindingError as exc:
            return ToolPolicyDecision(
                outcome=ToolPolicyOutcome.DENY,
                rule_id="deny.invalid_approval",
                reason=str(exc),
                invocation_id=invocation.invocation_id,
                invocation_revision=invocation.invocation_revision,
                capability_fingerprint=decision.capability_fingerprint,
                safe_metadata={"approval_id": approval.approval_id},
            )
        return ToolPolicyDecision(
            outcome=ToolPolicyOutcome.ALLOW_WITH_CONSTRAINTS,
            rule_id="allow.approved_invocation",
            reason=(
                "An exact capability-, argument-, resource-, revision-, and "
                "worktree-scoped approval was validated."
            ),
            invocation_id=invocation.invocation_id,
            invocation_revision=invocation.invocation_revision,
            capability_fingerprint=decision.capability_fingerprint,
            constraints=invocation.requested_capabilities,
            safe_metadata={
                "approval_id": approval.approval_id,
                "original_policy_rule": decision.rule_id,
            },
        )
