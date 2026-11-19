from datetime import timedelta

from agentbus.policy.approvals import build_tool_approval_request, decide_tool_approval
from agentbus.policy.engine import ToolPolicyEngine
from agentbus.policy.models import ToolApprovalDisposition
from agentbus.repair.live.contracts import TargetPlan
from agentbus.repair.service import RepairError
from agentbus.tools.protocol import (
    CapabilityScope, ToolCapability, ToolCapabilityName, ToolDescriptor, ToolInvocation,
    ToolInvocationContext, ToolPolicyOutcome, ToolSafetyClassification, ToolVersion,
    ToolResourceBudget,
)


def invocation_for(plan: TargetPlan, workspace: str, destination: str):
    capabilities = (ToolCapability(name=ToolCapabilityName.PROCESS_NETWORK,
                                   scope=CapabilityScope(network_allowed=True, network_destinations=(destination,))),)
    descriptor = ToolDescriptor(name="customer.repair", version=ToolVersion(major=1),
                                description="Exact application-owned conditional purchase-access correction plan.",
                                capabilities=capabilities, argument_schema={"type": "object", "additionalProperties": False,
                                    "properties": {"plan_digest": {"type": "string"}, "maximum_writes": {"type": "integer"}},
                                    "required": ["plan_digest", "maximum_writes"]}, output_schema={"type": "object"},
                                safety=ToolSafetyClassification.DANGEROUS, maximum_timeout_seconds=600)
    invocation = ToolInvocation(invocation_id=plan.plan_id, run_id=plan.plan_id, task_id="customer-repair",
                                tool_name=descriptor.name, tool_version=descriptor.version, requested_capabilities=capabilities,
                                arguments={"plan_digest": plan.digest, "maximum_writes": plan.maximum_writes},
                                context=ToolInvocationContext(workspace_identity=workspace, worktree_identity=workspace,
                                    caller_role="operator", workspace_trusted=True, provider_consented=False,
                                    policy_context={"network_allowed": True}), idempotency_key=plan.digest,
                                requested_at=plan.expires_at - timedelta(minutes=10), timeout_seconds=600,
                                resource_budget=ToolResourceBudget(wall_clock_seconds=600))
    return invocation, descriptor


def approve(plan, workspace, destination, store):
    from agentbus.execution.state_store import ToolApprovalNotFoundError
    try:
        existing = store.get_tool_approval(plan.plan_id, "repair-" + plan.plan_id)
        if existing.disposition == "approved":
            validate(plan, workspace, destination, store)
            return existing
    except ToolApprovalNotFoundError:
        pass
    invocation, descriptor = invocation_for(plan, workspace, destination)
    engine = ToolPolicyEngine()
    decision = engine.evaluate(invocation, descriptor)
    if decision.outcome != ToolPolicyOutcome.REQUIRE_APPROVAL:
        raise RepairError("Runtime policy did not authorize an exact customer-repair approval.", code="repair_policy_denied", status=403)
    request = build_tool_approval_request(invocation, descriptor, decision, approval_id="repair-" + plan.plan_id, expires_at=plan.expires_at)
    request = request.model_copy(update={"created_at": invocation.requested_at})
    store.record_tool_invocation(invocation)
    store.record_tool_policy_decision(plan.plan_id, decision, approval_id=request.approval_id)
    store.record_tool_approval_request(request, invocation, descriptor)
    grant = decide_tool_approval(request, invocation, disposition=ToolApprovalDisposition.APPROVED)
    store.record_tool_approval_grant(grant, invocation, descriptor)
    return grant


def validate(plan, workspace, destination, store):
    from agentbus.policy.models import ToolApprovalGrant
    record = store.get_tool_approval(plan.plan_id, "repair-" + plan.plan_id)
    if record.disposition != "approved":
        raise RepairError("The exact runtime approval is missing or rejected.", status=403)
    grant = ToolApprovalGrant(approval_id=record.approval_id, request=record.request,
                              disposition=record.disposition, binding_sha256=record.binding_sha256,
                              decided_at=record.decided_at, reason=record.reason)
    invocation, descriptor = invocation_for(plan, workspace, destination)
    decision = ToolPolicyEngine().evaluate(invocation, descriptor, approval=grant)
    if decision.outcome != ToolPolicyOutcome.ALLOW_WITH_CONSTRAINTS:
        raise RepairError("The exact runtime approval changed or expired. Review a new plan.", code="repair_approval_invalid", status=403)
    return decision
