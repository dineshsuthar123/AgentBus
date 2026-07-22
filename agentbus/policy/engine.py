from __future__ import annotations

from agentbus.policy.defaults import DEFAULT_TOOL_POLICY, ToolPolicyConfiguration
from agentbus.policy.evaluator import DefaultPolicyEvaluator
from agentbus.policy.rules import derive_policy_facts
from agentbus.tools.protocol import (
    ToolDescriptor,
    ToolInvocation,
    ToolPolicyDecision,
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
    ) -> ToolPolicyDecision:
        validate_invocation_against_descriptor(invocation, descriptor)
        facts = derive_policy_facts(descriptor, invocation)
        outcome, match = self.evaluator.evaluate(facts)
        return ToolPolicyDecision(
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
