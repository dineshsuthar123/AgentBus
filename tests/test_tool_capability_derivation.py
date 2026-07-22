from __future__ import annotations

from pathlib import Path

import pytest

from agentbus.tools.capabilities import (
    anticipated_tool_usage,
    derive_required_capabilities,
    require_expected_capabilities,
    requires_process_slot,
)
from agentbus.tools.descriptors import descriptor_map
from agentbus.tools.protocol import (
    ToolCapabilityEscalationError,
    ToolInvocation,
    ToolInvocationContext,
    ToolResourceBudget,
)


def test_filesystem_capabilities_are_derived_from_concrete_paths(
    tmp_path: Path,
) -> None:
    invocation, descriptor = _invocation(
        tmp_path,
        "filesystem.write",
        {"path": "src/module.py", "content": "value = 1\n"},
    )

    required = derive_required_capabilities(invocation, descriptor)

    assert all(
        capability.scope.affected_paths == ("src/module.py",)
        for capability in required
    )
    require_expected_capabilities(required, required)
    with pytest.raises(ToolCapabilityEscalationError, match="exactly match"):
        require_expected_capabilities(descriptor.capabilities, required)


def test_process_derivation_narrows_executable_and_working_directory(
    tmp_path: Path,
) -> None:
    source = tmp_path / "src"
    source.mkdir()
    invocation, descriptor = _invocation(
        tmp_path,
        "process.execute",
        {
            "executable": "python",
            "arguments": ["-V"],
            "working_directory": "src",
        },
    )

    required = derive_required_capabilities(invocation, descriptor)

    assert required[0].scope.executables == ("python",)
    assert required[0].scope.working_directories == (str(source.resolve()),)
    assert requires_process_slot(
        invocation.model_copy(update={"requested_capabilities": required})
    ) is True


def test_process_derivation_rejects_undeclared_executable(tmp_path: Path) -> None:
    invocation, descriptor = _invocation(
        tmp_path,
        "process.execute",
        {"executable": "custom-tool", "arguments": []},
    )

    with pytest.raises(ToolCapabilityEscalationError, match="exceed"):
        derive_required_capabilities(invocation, descriptor)


def test_anticipated_usage_reserves_mutation_capacity(tmp_path: Path) -> None:
    write, _ = _invocation(
        tmp_path,
        "filesystem.write",
        {"path": "module.py", "content": "three"},
    )
    patch, _ = _invocation(
        tmp_path,
        "filesystem.patch",
        {"path": "module.py", "expected": "a", "replacement": "b"},
        budget=ToolResourceBudget(maximum_file_bytes=1234),
    )

    assert anticipated_tool_usage(write).written_bytes == 5
    assert anticipated_tool_usage(write).artifact_bytes == 5
    assert anticipated_tool_usage(patch).written_bytes == 1234
    assert anticipated_tool_usage(patch).file_mutations == 1


def _invocation(
    root: Path,
    tool_name: str,
    arguments: dict,
    *,
    budget: ToolResourceBudget | None = None,
):
    descriptor = descriptor_map(workspace=root)[tool_name]
    invocation = ToolInvocation(
        invocation_id="inv-1",
        run_id="run-1",
        task_id="step-1",
        tool_name=tool_name,
        tool_version=descriptor.version,
        arguments=arguments,
        requested_capabilities=descriptor.capabilities,
        context=ToolInvocationContext(
            workspace_identity=str(root.resolve()),
            worktree_identity=str(root.resolve()),
            caller_role="coder",
            workspace_trusted=True,
            provider_consented=True,
        ),
        resource_budget=budget or ToolResourceBudget(),
    )
    return invocation, descriptor
