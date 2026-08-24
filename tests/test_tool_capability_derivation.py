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
    ToolCapabilityName,
    ToolCapabilityEscalationError,
    ToolInvocation,
    ToolInvocationContext,
    ToolResourceBudget,
    capability_set_contains,
)


_DERIVATION_CASES = (
    ("repository-scan", "repository.scan", {}, None, {"filesystem.read"}),
    (
        "read-existing",
        "filesystem.read",
        {"path": "existing.txt"},
        "existing",
        {"filesystem.read"},
    ),
    (
        "stat-existing",
        "filesystem.stat",
        {"path": "existing.txt"},
        "existing",
        {"filesystem.read"},
    ),
    ("list-root", "filesystem.list", {}, None, {"filesystem.read"}),
    (
        "create-missing",
        "filesystem.create",
        {"path": "missing.txt", "content": "new\n"},
        None,
        {"filesystem.create"},
    ),
    (
        "create-existing-rejection",
        "filesystem.create",
        {"path": "existing.txt", "content": "new\n"},
        "existing",
        {"filesystem.create"},
    ),
    (
        "write-existing",
        "filesystem.write",
        {"path": "existing.txt", "content": "updated\n"},
        "existing",
        {"filesystem.write"},
    ),
    (
        "write-missing",
        "filesystem.write",
        {"path": "missing.txt", "content": "new\n"},
        None,
        {"filesystem.create", "filesystem.write"},
    ),
    (
        "patch-existing",
        "filesystem.patch",
        {"path": "existing.txt", "expected": "old", "replacement": "new"},
        "existing",
        {"filesystem.write"},
    ),
    (
        "patch-missing-rejection",
        "filesystem.patch",
        {"path": "missing.txt", "expected": "old", "replacement": "new"},
        None,
        {"filesystem.write"},
    ),
    (
        "rename-existing",
        "filesystem.rename",
        {"source": "existing.txt", "destination": "renamed.txt"},
        "existing",
        {"filesystem.rename"},
    ),
    (
        "delete-existing",
        "filesystem.delete",
        {"path": "existing.txt", "expected_sha256": "0" * 64},
        "existing",
        {"filesystem.delete"},
    ),
    ("git-status", "git.status", {}, None, {"git.read"}),
    ("git-diff", "git.diff", {"paths": ["existing.txt"]}, None, {"git.read"}),
    ("git-show", "git.show", {"revision": "HEAD"}, None, {"git.read"}),
    ("git-log", "git.log", {}, None, {"git.read"}),
    ("git-branches", "git.branches", {}, None, {"git.read"}),
    (
        "git-stage",
        "git.stage",
        {"paths": ["existing.txt"]},
        None,
        {"git.write"},
    ),
    (
        "git-commit",
        "git.commit",
        {"paths": ["existing.txt"], "message": "test: bounded commit"},
        None,
        {"git.commit", "git.write"},
    ),
    (
        "test-execute",
        "test.execute",
        {"executable": "python", "arguments": ["-m", "pytest"]},
        None,
        {"process.execute", "test.execute"},
    ),
    (
        "process-execute",
        "process.execute",
        {"executable": "python", "arguments": ["-V"]},
        None,
        {"process.execute"},
    ),
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


def test_existing_write_requires_write_without_create(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "module.py").write_text(
        "value = 0\n",
        encoding="utf-8",
    )
    invocation, descriptor = _invocation(
        tmp_path,
        "filesystem.write",
        {"path": "src/module.py", "content": "value = 1\n"},
    )

    required = derive_required_capabilities(invocation, descriptor)

    assert [capability.name for capability in required] == [
        ToolCapabilityName.FILESYSTEM_WRITE
    ]


def test_new_path_write_requires_explicit_create(tmp_path: Path) -> None:
    invocation, descriptor = _invocation(
        tmp_path,
        "filesystem.write",
        {"path": "src/new_module.py", "content": "value = 1\n"},
    )

    required = derive_required_capabilities(invocation, descriptor)

    assert {capability.name for capability in required} == {
        ToolCapabilityName.FILESYSTEM_WRITE,
        ToolCapabilityName.FILESYSTEM_CREATE,
    }


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


@pytest.mark.parametrize(
    ("_case", "tool_name", "arguments", "setup", "expected_names"),
    _DERIVATION_CASES,
    ids=[case[0] for case in _DERIVATION_CASES],
)
def test_builtin_capability_derivation_is_closed_under_descriptor_maximum(
    tmp_path: Path,
    _case: str,
    tool_name: str,
    arguments: dict,
    setup: str | None,
    expected_names: set[str],
) -> None:
    if setup == "existing":
        (tmp_path / "existing.txt").write_text("old\n", encoding="utf-8")
    descriptor = descriptor_map(
        workspace=tmp_path,
        process_executables=("python",),
    )[tool_name]
    invocation = _invocation_for_descriptor(tmp_path, descriptor, arguments)

    derived = derive_required_capabilities(invocation, descriptor)

    assert {capability.name.value for capability in derived} == expected_names
    assert capability_set_contains(descriptor.capabilities, derived)


def test_missing_write_fails_closed_when_descriptor_omits_create(
    tmp_path: Path,
) -> None:
    descriptor = descriptor_map(workspace=tmp_path)["filesystem.write"]
    inconsistent = descriptor.model_copy(
        update={
            "capabilities": tuple(
                capability
                for capability in descriptor.capabilities
                if capability.name == ToolCapabilityName.FILESYSTEM_WRITE
            )
        }
    )
    invocation = _invocation_for_descriptor(
        tmp_path,
        inconsistent,
        {"path": "missing.py", "content": "value = 1\n"},
    )

    with pytest.raises(ToolCapabilityEscalationError, match="descriptor"):
        derive_required_capabilities(invocation, inconsistent)

    assert (tmp_path / "missing.py").exists() is False


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


def _invocation_for_descriptor(
    root: Path,
    descriptor,
    arguments: dict,
) -> ToolInvocation:
    return ToolInvocation(
        invocation_id="inv-closure",
        run_id="run-closure",
        task_id="step-1",
        tool_name=descriptor.name,
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
        resource_budget=ToolResourceBudget(),
    )
