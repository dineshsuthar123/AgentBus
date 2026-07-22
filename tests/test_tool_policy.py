from __future__ import annotations

from pathlib import Path

import pytest

from agentbus.policy import ToolPolicyEngine
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
