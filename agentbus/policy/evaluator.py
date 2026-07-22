from __future__ import annotations

from agentbus.policy.defaults import (
    DEFAULT_TOOL_POLICY,
    ToolPolicyConfiguration,
)
from agentbus.policy.models import PolicyRuleMatch
from agentbus.policy.rules import (
    PolicyFacts,
    executable_is_shell,
    executable_is_standard,
    git_arguments_are_destructive,
    path_has_unsafe_syntax,
    path_is_protected,
    path_is_within_assigned_roots,
    path_requires_approval,
)
from agentbus.tools.protocol import (
    ToolCapabilityName,
    ToolPolicyOutcome,
    ToolSafetyClassification,
)


class DefaultPolicyEvaluator:
    def __init__(
        self,
        configuration: ToolPolicyConfiguration = DEFAULT_TOOL_POLICY,
    ) -> None:
        self.configuration = configuration

    def evaluate(self, facts: PolicyFacts) -> tuple[ToolPolicyOutcome, PolicyRuleMatch]:
        invocation = facts.invocation

        if any(path_has_unsafe_syntax(path) for path in facts.affected_paths):
            return self._deny(
                "deny.unsafe_path_syntax",
                "The invocation contains traversal, device, UNC, NUL, or alternate-data-stream path syntax.",
            )
        if any(
            not path_is_within_assigned_roots(path, invocation)
            for path in facts.affected_paths
        ):
            return self._deny(
                "deny.outside_assigned_roots",
                "The invocation affects a path outside its assigned workspace or worktree.",
            )
        if any(
            path_is_protected(path, self.configuration)
            for path in facts.affected_paths
        ):
            return self._deny(
                "deny.protected_file",
                "Credential, secret, daemon registry, or control-state files are protected.",
            )
        if invocation.arguments.get("shell") is True or executable_is_shell(
            facts.executable
        ):
            return self._deny(
                "deny.shell_execution",
                "Shell interpreters and shell=True are not available to managed tools.",
            )
        if git_arguments_are_destructive(facts):
            return self._deny(
                "deny.destructive_git",
                "Destructive, remote, or global Git operations are not allowed.",
            )
        if facts.requests_network and not bool(
            invocation.context.policy_context.get("network_allowed", False)
        ):
            return self._deny(
                "deny.unrestricted_network",
                "Network access is disabled unless explicitly enabled by run policy.",
            )
        if invocation.context.caller_role == "reviewer" and (
            facts.mutating or facts.executes_processes
        ):
            return self._deny(
                "deny.reviewer_mutation",
                "Reviewer tool access is read-only by default.",
            )
        if not invocation.context.workspace_trusted and (
            facts.mutating or facts.executes_processes
        ):
            return self._deny(
                "deny.untrusted_workspace_execution",
                "Workspace Trust is required for mutation and process execution.",
            )
        if len(facts.affected_paths) > self.configuration.automatic_path_limit:
            return self._approval(
                "approval.large_path_set",
                "The invocation affects more paths than the automatic policy limit.",
            )
        if any(
            path_requires_approval(path, self.configuration)
            for path in facts.affected_paths
        ):
            return self._approval(
                "approval.sensitive_project_file",
                "CI, deployment, infrastructure, or security-policy changes require approval.",
            )
        if ToolCapabilityName.FILESYSTEM_DELETE in facts.capability_names:
            return self._approval(
                "approval.file_delete",
                "File deletion requires exact capability-scoped approval.",
            )
        if ToolCapabilityName.PACKAGE_INSTALL in facts.capability_names:
            return self._approval(
                "approval.package_install",
                "Package installation requires explicit approval.",
            )
        if facts.requests_network:
            return self._approval(
                "approval.network_process",
                "Network-enabled tool execution requires destination-scoped approval.",
            )
        if facts.executes_processes:
            if not executable_is_standard(facts.executable, self.configuration):
                return self._approval(
                    "approval.nonstandard_executable",
                    "The requested executable is outside the standard allowlist.",
                )
            if invocation.resource_budget.wall_clock_seconds > (
                self.configuration.standard_wall_clock_seconds
            ):
                return self._approval(
                    "approval.extended_process_budget",
                    "The process wall-clock budget exceeds the automatic limit.",
                )
            return (
                ToolPolicyOutcome.ALLOW_WITH_CONSTRAINTS,
                PolicyRuleMatch(
                    rule_id="allow.constrained_process",
                    reason="The allowlisted executable is confined to its assigned worktree without network access.",
                    constraints=invocation.requested_capabilities,
                    safe_metadata={"executable": facts.executable or "[missing]"},
                ),
            )
        if facts.read_only:
            return (
                ToolPolicyOutcome.ALLOW,
                PolicyRuleMatch(
                    rule_id="allow.read_only",
                    reason="The invocation is a bounded read-only operation inside assigned roots.",
                ),
            )
        if facts.mutating and invocation.context.provider_consented:
            if facts.descriptor.safety in {
                ToolSafetyClassification.SAFE,
                ToolSafetyClassification.SENSITIVE,
            }:
                return (
                    ToolPolicyOutcome.ALLOW_WITH_CONSTRAINTS,
                    PolicyRuleMatch(
                        rule_id="allow.scoped_mutation",
                        reason="The mutation is contained to the trusted assigned worktree.",
                        constraints=invocation.requested_capabilities,
                    ),
                )
        return self._approval(
            "approval.default_risk",
            "The invocation is not covered by an automatic allow rule.",
        )

    @staticmethod
    def _deny(rule_id: str, reason: str) -> tuple[ToolPolicyOutcome, PolicyRuleMatch]:
        return ToolPolicyOutcome.DENY, PolicyRuleMatch(rule_id=rule_id, reason=reason)

    @staticmethod
    def _approval(
        rule_id: str,
        reason: str,
    ) -> tuple[ToolPolicyOutcome, PolicyRuleMatch]:
        return (
            ToolPolicyOutcome.REQUIRE_APPROVAL,
            PolicyRuleMatch(rule_id=rule_id, reason=reason),
        )
