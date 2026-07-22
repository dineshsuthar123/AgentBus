from __future__ import annotations

from pathlib import Path
from typing import Any

from agentbus.tools.protocol import (
    CapabilityScope,
    ToolCapability,
    ToolCapabilityName,
    ToolDescriptor,
    ToolSafetyClassification,
    ToolVersion,
)


BUILTIN_TOOL_VERSION = ToolVersion(major=1)


def builtin_descriptors(
    *,
    workspace: str | Path,
    worktree: str | Path | None = None,
) -> tuple[ToolDescriptor, ...]:
    workspace_root = str(Path(workspace).expanduser().resolve())
    worktree_root = str(
        Path(worktree if worktree is not None else workspace).expanduser().resolve()
    )
    filesystem_scope = CapabilityScope(
        roots=(worktree_root,),
        working_directories=(worktree_root,),
    )
    process_scope = CapabilityScope(
        roots=(worktree_root,),
        executables=(
            "python",
            "python3",
            "pytest",
            "node",
            "npm",
            "git",
        ),
        working_directories=(worktree_root,),
        network_allowed=False,
    )
    git_scope = CapabilityScope(
        roots=(workspace_root, worktree_root),
        working_directories=(worktree_root,),
    )

    return (
        _descriptor(
            "repository.scan",
            "Inspect bounded repository metadata inside the assigned worktree.",
            (_capability(ToolCapabilityName.FILESYSTEM_READ, filesystem_scope),),
            _object_schema(),
            _object_schema(),
        ),
        _descriptor(
            "filesystem.read",
            "Read a bounded non-secret file inside the assigned worktree.",
            (_capability(ToolCapabilityName.FILESYSTEM_READ, filesystem_scope),),
            _path_schema(),
            _object_schema(),
        ),
        _descriptor(
            "filesystem.write",
            "Atomically write a bounded file inside the assigned worktree.",
            (
                _capability(ToolCapabilityName.FILESYSTEM_WRITE, filesystem_scope),
                _capability(ToolCapabilityName.FILESYSTEM_CREATE, filesystem_scope),
            ),
            _write_schema(),
            _object_schema(),
            safety=ToolSafetyClassification.SENSITIVE,
            idempotent=True,
        ),
        _descriptor(
            "filesystem.patch",
            "Apply an expected-content patch inside the assigned worktree.",
            (_capability(ToolCapabilityName.FILESYSTEM_WRITE, filesystem_scope),),
            _patch_schema(),
            _object_schema(),
            safety=ToolSafetyClassification.SENSITIVE,
            idempotent=True,
        ),
        _descriptor(
            "filesystem.delete",
            "Delete one explicitly authorized file inside the assigned worktree.",
            (_capability(ToolCapabilityName.FILESYSTEM_DELETE, filesystem_scope),),
            _path_schema(),
            _object_schema(),
            safety=ToolSafetyClassification.DANGEROUS,
            idempotent=True,
        ),
        _descriptor(
            "git.status",
            "Read bounded Git status for the assigned repository.",
            (_capability(ToolCapabilityName.GIT_READ, git_scope),),
            _object_schema(),
            _object_schema(),
        ),
        _descriptor(
            "git.diff",
            "Read a bounded Git diff for approved repository paths.",
            (_capability(ToolCapabilityName.GIT_READ, git_scope),),
            _object_schema(),
            _object_schema(),
        ),
        _descriptor(
            "git.log",
            "Read bounded Git history without invoking hooks.",
            (_capability(ToolCapabilityName.GIT_READ, git_scope),),
            _object_schema(),
            _object_schema(),
        ),
        _descriptor(
            "git.commit",
            "Stage approved paths and commit inside an owned worktree.",
            (
                _capability(ToolCapabilityName.GIT_WRITE, git_scope),
                _capability(ToolCapabilityName.GIT_COMMIT, git_scope),
            ),
            _commit_schema(),
            _object_schema(),
            safety=ToolSafetyClassification.RISKY,
            idempotent=False,
        ),
        _descriptor(
            "test.execute",
            "Execute configured tests through the controlled process supervisor.",
            (
                _capability(ToolCapabilityName.TEST_EXECUTE, process_scope),
                _capability(ToolCapabilityName.PROCESS_EXECUTE, process_scope),
            ),
            _command_schema(),
            _object_schema(),
            supports_cancellation=True,
        ),
        _descriptor(
            "process.execute",
            "Execute an allowlisted program without shell interpretation.",
            (_capability(ToolCapabilityName.PROCESS_EXECUTE, process_scope),),
            _command_schema(),
            _object_schema(),
            safety=ToolSafetyClassification.RISKY,
            supports_cancellation=True,
        ),
    )


def descriptor_map(
    *,
    workspace: str | Path,
    worktree: str | Path | None = None,
) -> dict[str, ToolDescriptor]:
    return {
        descriptor.name: descriptor
        for descriptor in builtin_descriptors(workspace=workspace, worktree=worktree)
    }


def _capability(
    name: ToolCapabilityName,
    scope: CapabilityScope,
) -> ToolCapability:
    return ToolCapability(name=name, scope=scope)


def _descriptor(
    name: str,
    description: str,
    capabilities: tuple[ToolCapability, ...],
    argument_schema: dict[str, Any],
    output_schema: dict[str, Any],
    *,
    safety: ToolSafetyClassification = ToolSafetyClassification.SAFE,
    idempotent: bool = True,
    supports_cancellation: bool = False,
) -> ToolDescriptor:
    return ToolDescriptor(
        name=name,
        version=BUILTIN_TOOL_VERSION,
        description=description,
        capabilities=capabilities,
        argument_schema=argument_schema,
        output_schema=output_schema,
        safety=safety,
        idempotent=idempotent,
        supports_cancellation=supports_cancellation,
    )


def _object_schema(*, required: tuple[str, ...] = ()) -> dict[str, Any]:
    schema: dict[str, Any] = {
        "type": "object",
        "additionalProperties": False,
        "properties": {},
    }
    if required:
        schema["required"] = list(required)
    return schema


def _path_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["path"],
        "properties": {"path": {"type": "string", "maxLength": 2048}},
    }


def _write_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["path", "content"],
        "properties": {
            "path": {"type": "string", "maxLength": 2048},
            "content": {"type": "string", "maxLength": 2_097_152},
        },
    }


def _patch_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["path", "expected", "replacement"],
        "properties": {
            "path": {"type": "string", "maxLength": 2048},
            "expected": {"type": "string", "maxLength": 2_097_152},
            "replacement": {"type": "string", "maxLength": 2_097_152},
        },
    }


def _commit_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["paths", "message"],
        "properties": {
            "paths": {
                "type": "array",
                "minItems": 1,
                "maxItems": 256,
                "items": {"type": "string", "maxLength": 2048},
            },
            "message": {"type": "string", "minLength": 1, "maxLength": 512},
        },
    }


def _command_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["executable", "arguments"],
        "properties": {
            "executable": {"type": "string", "maxLength": 1024},
            "arguments": {
                "type": "array",
                "maxItems": 256,
                "items": {"type": "string", "maxLength": 8192},
            },
        },
    }
