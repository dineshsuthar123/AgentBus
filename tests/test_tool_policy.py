from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from agentbus.policy import (
    ToolApprovalBindingError,
    ToolApprovalDisposition,
    ToolPolicyEngine,
    build_tool_approval_request,
    decide_tool_approval,
    validate_tool_approval,
)
from agentbus.tools.descriptors import descriptor_map
from agentbus.tools.protocol import (
    CapabilityScope,
    ToolCapability,
    ToolCapabilityName,
    ToolInvocation,
    ToolInvocationContext,
    ToolPolicyOutcome,
)


def test_policy_allows_bounded_read_inside_worktree(tmp_path: Path) -> None:
    invocation, descriptor = _invocation(tmp_path, "filesystem.read", path="src/app.py")

    decision = ToolPolicyEngine().evaluate(invocation, descriptor)

    assert decision.outcome == ToolPolicyOutcome.ALLOW
    assert decision.rule_id == "allow.read_only"
    assert decision.safe_metadata == {}


def test_policy_denies_traversal_and_protected_files(tmp_path: Path) -> None:
    engine = ToolPolicyEngine()
    traversal, descriptor = _invocation(
        tmp_path,
        "filesystem.read",
        path="../outside.txt",
    )
    protected, descriptor = _invocation(
        tmp_path,
        "filesystem.read",
        path=".env",
    )

    assert engine.evaluate(traversal, descriptor).rule_id == "deny.unsafe_path_syntax"
    assert engine.evaluate(protected, descriptor).rule_id == "deny.protected_file"


def test_policy_denies_absolute_path_outside_assigned_roots(tmp_path: Path) -> None:
    invocation, descriptor = _invocation(
        tmp_path,
        "filesystem.read",
        path=str(tmp_path.parent / "outside.txt"),
    )

    decision = ToolPolicyEngine().evaluate(invocation, descriptor)

    assert decision.outcome == ToolPolicyOutcome.DENY
    assert decision.rule_id == "deny.outside_assigned_roots"


def test_policy_requires_exact_approval_for_delete_and_ci_changes(
    tmp_path: Path,
) -> None:
    delete, delete_descriptor = _invocation(
        tmp_path,
        "filesystem.delete",
        path="src/obsolete.py",
    )
    write, write_descriptor = _invocation(
        tmp_path,
        "filesystem.write",
        path=".github/workflows/ci.yml",
        content="name: CI\n",
    )

    assert ToolPolicyEngine().evaluate(delete, delete_descriptor).rule_id == (
        "approval.file_delete"
    )
    assert ToolPolicyEngine().evaluate(write, write_descriptor).rule_id == (
        "approval.sensitive_project_file"
    )


def test_policy_keeps_reviewer_read_only(tmp_path: Path) -> None:
    invocation, descriptor = _invocation(
        tmp_path,
        "filesystem.write",
        caller_role="reviewer",
        path="src/app.py",
        content="changed",
    )

    decision = ToolPolicyEngine().evaluate(invocation, descriptor)

    assert decision.outcome == ToolPolicyOutcome.DENY
    assert decision.rule_id == "deny.reviewer_mutation"


def test_policy_allows_standard_process_only_with_constraints(tmp_path: Path) -> None:
    invocation, descriptor = _invocation(
        tmp_path,
        "process.execute",
        executable="python",
        arguments=["-c", "print('safe; shell syntax is data')"],
    )

    decision = ToolPolicyEngine().evaluate(invocation, descriptor)

    assert decision.outcome == ToolPolicyOutcome.ALLOW_WITH_CONSTRAINTS
    assert decision.rule_id == "allow.constrained_process"
    assert decision.constraints == invocation.requested_capabilities


@pytest.mark.parametrize("executable", ["powershell", "cmd.exe", "bash"])
def test_policy_denies_shell_interpreters(tmp_path: Path, executable: str) -> None:
    invocation, descriptor = _invocation(
        tmp_path,
        "process.execute",
        executable=executable,
        arguments=["echo", "unsafe"],
    )

    decision = ToolPolicyEngine().evaluate(invocation, descriptor)

    assert decision.outcome == ToolPolicyOutcome.DENY
    assert decision.rule_id == "deny.shell_execution"


def test_policy_denies_processes_in_untrusted_workspace(tmp_path: Path) -> None:
    invocation, descriptor = _invocation(
        tmp_path,
        "process.execute",
        trusted=False,
        executable="python",
        arguments=["-V"],
    )

    decision = ToolPolicyEngine().evaluate(invocation, descriptor)

    assert decision.rule_id == "deny.untrusted_workspace_execution"


def test_exact_approval_allows_original_delete_invocation(tmp_path: Path) -> None:
    invocation, descriptor = _invocation(
        tmp_path,
        "filesystem.delete",
        path="src/obsolete.py",
    )
    engine = ToolPolicyEngine()
    required = engine.evaluate(invocation, descriptor)
    request = build_tool_approval_request(
        invocation,
        descriptor,
        required,
        approval_id="approval-1",
    )
    grant = decide_tool_approval(
        request,
        invocation,
        disposition=ToolApprovalDisposition.APPROVED,
    )

    decision = engine.evaluate(invocation, descriptor, approval=grant)

    assert decision.outcome == ToolPolicyOutcome.ALLOW_WITH_CONSTRAINTS
    assert decision.rule_id == "allow.approved_invocation"
    assert decision.safe_metadata["approval_id"] == "approval-1"


@pytest.mark.parametrize(
    "update",
    [
        {"task_id": "step-2"},
        {"invocation_revision": 2},
        {"arguments": {"path": "src/different.py"}},
    ],
)
def test_approval_cannot_be_reused_for_changed_invocation(
    tmp_path: Path,
    update: dict,
) -> None:
    invocation, descriptor = _invocation(
        tmp_path,
        "filesystem.delete",
        path="src/obsolete.py",
    )
    engine = ToolPolicyEngine()
    required = engine.evaluate(invocation, descriptor)
    request = build_tool_approval_request(invocation, descriptor, required)
    grant = decide_tool_approval(
        request,
        invocation,
        disposition=ToolApprovalDisposition.APPROVED,
    )
    changed = invocation.model_copy(update=update)

    decision = engine.evaluate(changed, descriptor, approval=grant)

    assert decision.outcome == ToolPolicyOutcome.DENY
    assert decision.rule_id == "deny.invalid_approval"


def test_approval_rejects_capability_expansion_and_expiry(tmp_path: Path) -> None:
    invocation, descriptor = _invocation(
        tmp_path,
        "filesystem.delete",
        path="src/obsolete.py",
    )
    engine = ToolPolicyEngine()
    required = engine.evaluate(invocation, descriptor)
    expires_at = datetime.now(timezone.utc) + timedelta(minutes=1)
    request = build_tool_approval_request(
        invocation,
        descriptor,
        required,
        expires_at=expires_at,
    )
    grant = decide_tool_approval(
        request,
        invocation,
        disposition=ToolApprovalDisposition.APPROVED,
    )
    expanded = invocation.model_copy(
        update={
            "requested_capabilities": invocation.requested_capabilities
            + (ToolCapability(name=ToolCapabilityName.PROCESS_NETWORK),)
        }
    )

    with pytest.raises(ToolApprovalBindingError, match="capabilities changed"):
        validate_tool_approval(grant, expanded, descriptor)
    with pytest.raises(ToolApprovalBindingError, match="expired"):
        validate_tool_approval(
            grant,
            invocation,
            descriptor,
            now=expires_at + timedelta(seconds=1),
        )


def test_rejected_approval_never_authorizes_invocation(tmp_path: Path) -> None:
    invocation, descriptor = _invocation(
        tmp_path,
        "filesystem.delete",
        path="src/obsolete.py",
    )
    engine = ToolPolicyEngine()
    required = engine.evaluate(invocation, descriptor)
    request = build_tool_approval_request(invocation, descriptor, required)
    grant = decide_tool_approval(
        request,
        invocation,
        disposition=ToolApprovalDisposition.REJECTED,
        reason="Not needed.",
    )

    decision = engine.evaluate(invocation, descriptor, approval=grant)

    assert decision.outcome == ToolPolicyOutcome.DENY
    assert decision.rule_id == "deny.invalid_approval"


def _invocation(
    root: Path,
    tool_name: str,
    *,
    caller_role: str = "coder",
    trusted: bool = True,
    **arguments,
):
    descriptor = descriptor_map(workspace=root)[tool_name]
    requested = tuple(
        _scope_capability(capability, root, arguments)
        for capability in descriptor.capabilities
    )
    invocation = ToolInvocation(
        invocation_id="inv-1",
        run_id="run-1",
        task_id="step-1",
        tool_name=tool_name,
        tool_version=descriptor.version,
        arguments=arguments,
        requested_capabilities=requested,
        context=ToolInvocationContext(
            workspace_identity=str(root.resolve()),
            worktree_identity=str(root.resolve()),
            caller_role=caller_role,
            workspace_trusted=trusted,
            provider_consented=True,
        ),
    )
    return invocation, descriptor


def _scope_capability(
    capability: ToolCapability,
    root: Path,
    arguments: dict,
) -> ToolCapability:
    affected_paths = tuple(
        value
        for key, value in arguments.items()
        if key in {"path", "source", "destination"} and isinstance(value, str)
    )
    scope = capability.scope.model_copy(update={"affected_paths": affected_paths})
    return ToolCapability(name=capability.name, scope=CapabilityScope(**scope.model_dump()))
