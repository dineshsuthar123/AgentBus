from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from agentbus.execution.cancellation import CancellationRequested, CancellationToken
from agentbus.sandbox.platform import ExecutableCatalog
from agentbus.tools import ManagedToolContextError, builtin_tool_registry
from agentbus.tools.protocol import (
    ToolInvocation,
    ToolInvocationContext,
    ToolResourceBudget,
)


def test_builtin_registry_lazily_exposes_all_managed_tools(tmp_path: Path) -> None:
    catalog = ExecutableCatalog({"python": sys.executable})

    registry = builtin_tool_registry(
        workspace=tmp_path,
        executable_catalog=catalog,
    )

    assert len(registry) == 18
    assert registry.descriptor(
        "process.execute"
    ).capabilities[0].scope.executables == ("python",)
    assert registry.resolve("filesystem.read").descriptor.name == "filesystem.read"


def test_repository_scan_filters_protected_paths(tmp_path: Path) -> None:
    (tmp_path / "README.md").write_text("safe", encoding="utf-8")
    (tmp_path / ".env").write_text("TOKEN=private", encoding="utf-8")
    registry = _registry(tmp_path)
    tool = registry.resolve("repository.scan")

    output = tool.execute(_invocation(registry, tmp_path, "repository.scan", {}))

    assert "README.md" in output.structured_output["files"]
    assert ".env" not in output.structured_output["files"]
    assert ".env" in output.structured_output["skipped_paths"]


def test_filesystem_adapters_create_read_and_patch_with_attribution(
    tmp_path: Path,
) -> None:
    registry = _registry(tmp_path)
    create = registry.resolve("filesystem.create").execute(
        _invocation(
            registry,
            tmp_path,
            "filesystem.create",
            {"path": "src/module.py", "content": "value = 1\n"},
            invocation_id="inv-create",
        )
    )

    assert create.structured_output["task_id"] == "step-1"
    assert create.structured_output["invocation_id"] == "inv-create"
    assert create.resource_usage.file_mutations == 1
    assert create.artifacts[0].relative_path == "src/module.py"
    assert create.artifacts[0].media_type == "text/plain; charset=utf-8"
    assert create.artifacts[0].safe_metadata["encoding"] == "utf-8"

    read = registry.resolve("filesystem.read").execute(
        _invocation(
            registry,
            tmp_path,
            "filesystem.read",
            {"path": "src/module.py"},
            invocation_id="inv-read",
        )
    )
    assert read.structured_output["content"] == "value = 1\n"
    digest = read.structured_output["sha256"]

    patch = registry.resolve("filesystem.patch").execute(
        _invocation(
            registry,
            tmp_path,
            "filesystem.patch",
            {
                "path": "src/module.py",
                "expected": "value = 1",
                "replacement": "value = 2",
                "expected_sha256": digest,
            },
            invocation_id="inv-patch",
        )
    )
    assert patch.structured_output["before_sha256"] == digest
    assert (tmp_path / "src" / "module.py").read_text(encoding="utf-8") == (
        "value = 2\n"
    )


def test_adapter_rejects_different_invocation_context(tmp_path: Path) -> None:
    other = tmp_path / "other"
    other.mkdir()
    registry = _registry(tmp_path)
    invocation = _invocation(
        registry,
        tmp_path,
        "filesystem.read",
        {"path": "README.md"},
    ).model_copy(
        update={
            "context": ToolInvocationContext(
                workspace_identity=str(other.resolve()),
                worktree_identity=str(other.resolve()),
                caller_role="coder",
                workspace_trusted=True,
                provider_consented=True,
            )
        }
    )

    with pytest.raises(ManagedToolContextError, match="pinned runtime"):
        registry.resolve("filesystem.read").execute(invocation)


def test_git_read_adapter_is_scoped_to_initialized_repository(tmp_path: Path) -> None:
    _git(tmp_path, "init")
    (tmp_path / "module.py").write_text("value = 1\n", encoding="utf-8")
    registry = _registry(tmp_path)

    output = registry.resolve("git.status").execute(
        _invocation(registry, tmp_path, "git.status", {})
    )

    assert "module.py" in output.structured_output["text"]
    assert output.structured_output["truncated"] is False


def test_process_adapter_streams_bounded_output_and_honors_cancellation(
    tmp_path: Path,
) -> None:
    chunks = []
    catalog = ExecutableCatalog({"python": sys.executable})
    registry = builtin_tool_registry(
        workspace=tmp_path,
        executable_catalog=catalog,
        output_callback=lambda invocation, chunk: chunks.append(
            (invocation.invocation_id, chunk)
        ),
    )
    invocation = _invocation(
        registry,
        tmp_path,
        "process.execute",
        {"executable": "python", "arguments": ["-c", "print('managed')"]},
    )

    output = registry.resolve("process.execute").execute(invocation)

    assert output.exit_code == 0
    assert output.structured_output["passed"] is True
    assert output.stdout.splitlines() == ["managed"]
    assert chunks[0][0] == invocation.invocation_id
    assert chunks[0][1].text.splitlines() == ["managed"]

    cancellation = CancellationToken()
    cancellation.request("operator cancelled")
    with pytest.raises(CancellationRequested):
        registry.resolve("process.execute").execute(
            invocation.model_copy(update={"invocation_id": "inv-cancelled"}),
            cancellation=cancellation,
        )


def _registry(root: Path):
    return builtin_tool_registry(
        workspace=root,
        executable_catalog=ExecutableCatalog({"python": sys.executable}),
    )


def _invocation(
    registry,
    root: Path,
    tool_name: str,
    arguments: dict,
    *,
    invocation_id: str = "inv-1",
) -> ToolInvocation:
    descriptor = registry.descriptor(tool_name)
    return ToolInvocation(
        invocation_id=invocation_id,
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
        resource_budget=ToolResourceBudget(wall_clock_seconds=10),
    )


def _git(root: Path, *arguments: str) -> None:
    subprocess.run(
        ["git", *arguments],
        cwd=root,
        check=True,
        shell=False,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
