from __future__ import annotations

from pathlib import Path
from collections.abc import Iterable
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
    process_executables: Iterable[str] | None = None,
) -> tuple[ToolDescriptor, ...]:
    workspace_root = str(Path(workspace).expanduser().resolve())
    worktree_root = str(
        Path(worktree if worktree is not None else workspace).expanduser().resolve()
    )
    filesystem_scope = CapabilityScope(
        roots=(worktree_root,),
        working_directories=(worktree_root,),
    )
    executable_scope = _process_executable_scope(
        process_executables
    )
    process_scope = CapabilityScope(
        roots=(worktree_root,),
        executables=executable_scope,
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
            _repository_scan_output_schema(),
        ),
        _descriptor(
            "filesystem.read",
            "Read a bounded non-secret file inside the assigned worktree.",
            (_capability(ToolCapabilityName.FILESYSTEM_READ, filesystem_scope),),
            _read_schema(),
            _file_read_output_schema(),
        ),
        _descriptor(
            "filesystem.stat",
            "Inspect bounded metadata for a path inside the assigned worktree.",
            (_capability(ToolCapabilityName.FILESYSTEM_READ, filesystem_scope),),
            _path_schema(),
            _file_stat_output_schema(),
        ),
        _descriptor(
            "filesystem.list",
            "List a bounded directory inside the assigned worktree.",
            (_capability(ToolCapabilityName.FILESYSTEM_READ, filesystem_scope),),
            _list_schema(),
            _file_list_output_schema(),
        ),
        _descriptor(
            "filesystem.create",
            "Create a bounded file without replacing an existing path.",
            (_capability(ToolCapabilityName.FILESYSTEM_CREATE, filesystem_scope),),
            _create_schema(),
            _mutation_output_schema(),
            safety=ToolSafetyClassification.SENSITIVE,
            idempotent=True,
        ),
        _descriptor(
            "filesystem.write",
            "Atomically write a bounded file inside the assigned worktree.",
            (
                _capability(ToolCapabilityName.FILESYSTEM_WRITE, filesystem_scope),
                _capability(ToolCapabilityName.FILESYSTEM_CREATE, filesystem_scope),
            ),
            _write_schema(),
            _mutation_output_schema(),
            safety=ToolSafetyClassification.SENSITIVE,
            idempotent=True,
        ),
        _descriptor(
            "filesystem.patch",
            "Apply an expected-content patch inside the assigned worktree.",
            (_capability(ToolCapabilityName.FILESYSTEM_WRITE, filesystem_scope),),
            _patch_schema(),
            _mutation_output_schema(),
            safety=ToolSafetyClassification.SENSITIVE,
            idempotent=True,
        ),
        _descriptor(
            "filesystem.rename",
            "Rename one file without overwriting another worktree path.",
            (_capability(ToolCapabilityName.FILESYSTEM_RENAME, filesystem_scope),),
            _rename_schema(),
            _mutation_output_schema(),
            safety=ToolSafetyClassification.RISKY,
            idempotent=False,
        ),
        _descriptor(
            "filesystem.delete",
            "Delete one explicitly authorized file inside the assigned worktree.",
            (_capability(ToolCapabilityName.FILESYSTEM_DELETE, filesystem_scope),),
            _delete_schema(),
            _mutation_output_schema(),
            safety=ToolSafetyClassification.DANGEROUS,
            idempotent=True,
        ),
        _descriptor(
            "git.status",
            "Read bounded Git status for the assigned repository.",
            (_git_capability(ToolCapabilityName.GIT_READ, git_scope, "status"),),
            _bounded_output_arguments_schema(),
            _text_output_schema(),
        ),
        _descriptor(
            "git.diff",
            "Read a bounded Git diff for approved repository paths.",
            (_git_capability(ToolCapabilityName.GIT_READ, git_scope, "diff"),),
            _git_diff_schema(),
            _text_output_schema(),
        ),
        _descriptor(
            "git.show",
            "Read one bounded commit without protected file content.",
            (_git_capability(ToolCapabilityName.GIT_READ, git_scope, "show"),),
            _git_show_schema(),
            _text_output_schema(),
        ),
        _descriptor(
            "git.log",
            "Read bounded Git history without invoking hooks.",
            (_git_capability(ToolCapabilityName.GIT_READ, git_scope, "log"),),
            _git_log_schema(),
            _text_output_schema(),
        ),
        _descriptor(
            "git.branches",
            "Inspect a bounded set of local branches.",
            (_git_capability(ToolCapabilityName.GIT_READ, git_scope, "branches"),),
            _git_branches_schema(),
            _text_output_schema(),
        ),
        _descriptor(
            "git.stage",
            "Stage only explicit policy-eligible paths in an owned worktree.",
            (_git_capability(ToolCapabilityName.GIT_WRITE, git_scope, "stage"),),
            _path_collection_schema(),
            _git_stage_output_schema(),
            safety=ToolSafetyClassification.RISKY,
            idempotent=True,
        ),
        _descriptor(
            "git.commit",
            "Stage approved paths and commit inside an owned worktree.",
            (
                _git_capability(ToolCapabilityName.GIT_WRITE, git_scope, "commit"),
                _git_capability(ToolCapabilityName.GIT_COMMIT, git_scope, "commit"),
            ),
            _commit_schema(),
            _git_commit_output_schema(),
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
            _process_output_schema(),
            supports_cancellation=True,
        ),
        _descriptor(
            "process.execute",
            "Execute an allowlisted program without shell interpretation.",
            (_capability(ToolCapabilityName.PROCESS_EXECUTE, process_scope),),
            _command_schema(),
            _process_output_schema(),
            safety=ToolSafetyClassification.RISKY,
            supports_cancellation=True,
        ),
    )


def descriptor_map(
    *,
    workspace: str | Path,
    worktree: str | Path | None = None,
    process_executables: Iterable[str] | None = None,
) -> dict[str, ToolDescriptor]:
    return {
        descriptor.name: descriptor
        for descriptor in builtin_descriptors(
            workspace=workspace,
            worktree=worktree,
            process_executables=process_executables,
        )
    }


def _capability(
    name: ToolCapabilityName,
    scope: CapabilityScope,
) -> ToolCapability:
    return ToolCapability(name=name, scope=scope)


def _git_capability(
    name: ToolCapabilityName,
    scope: CapabilityScope,
    operation: str,
) -> ToolCapability:
    return ToolCapability(
        name=name,
        scope=scope.model_copy(update={"git_operations": (operation,)}),
    )


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
        "properties": {
            "path": {"type": "string", "minLength": 1, "maxLength": 2048}
        },
    }


def _read_schema() -> dict[str, Any]:
    schema = _path_schema()
    schema["properties"]["maximum_bytes"] = {
        "type": "integer",
        "minimum": 1,
        "maximum": 268_435_456,
    }
    return schema


def _create_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["path", "content"],
        "properties": {
            "path": {"type": "string", "minLength": 1, "maxLength": 2048},
            "content": {"type": "string", "maxLength": 2_097_152},
        },
    }


def _write_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["path", "content"],
        "properties": {
            "path": {"type": "string", "minLength": 1, "maxLength": 2048},
            "content": {"type": "string", "maxLength": 2_097_152},
            "expected_sha256": _sha256_schema(nullable=True),
        },
    }


def _patch_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["path", "expected", "replacement"],
        "properties": {
            "path": {"type": "string", "minLength": 1, "maxLength": 2048},
            "expected": {"type": "string", "maxLength": 2_097_152},
            "replacement": {"type": "string", "maxLength": 2_097_152},
            "expected_sha256": _sha256_schema(nullable=True),
            "expected_occurrences": {
                "type": "integer",
                "minimum": 1,
                "maximum": 100_000,
            },
        },
    }


def _delete_schema() -> dict[str, Any]:
    schema = _path_schema()
    schema["required"].append("expected_sha256")
    schema["properties"]["expected_sha256"] = _sha256_schema()
    return schema


def _rename_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["source", "destination"],
        "properties": {
            "source": {"type": "string", "minLength": 1, "maxLength": 2048},
            "destination": {
                "type": "string",
                "minLength": 1,
                "maxLength": 2048,
            },
            "expected_sha256": _sha256_schema(nullable=True),
        },
    }


def _list_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "path": {"type": "string", "minLength": 1, "maxLength": 2048},
            "recursive": {"type": "boolean"},
            "recurse_generated": {"type": "boolean"},
            "maximum_entries": {
                "type": "integer",
                "minimum": 1,
                "maximum": 10_000,
            },
        },
    }


def _bounded_output_arguments_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "max_chars": {"type": "integer", "minimum": 1, "maximum": 4_194_304}
        },
    }


def _git_diff_schema() -> dict[str, Any]:
    schema = _bounded_output_arguments_schema()
    schema["properties"]["paths"] = _paths_schema()
    return schema


def _git_show_schema() -> dict[str, Any]:
    schema = _bounded_output_arguments_schema()
    schema["properties"].update(
        {
            "revision": {"type": "string", "minLength": 1, "maxLength": 255},
            "path": {"type": "string", "minLength": 1, "maxLength": 2048},
        }
    )
    return schema


def _git_log_schema() -> dict[str, Any]:
    schema = _bounded_output_arguments_schema()
    schema["properties"]["maximum_entries"] = {
        "type": "integer",
        "minimum": 1,
        "maximum": 100,
    }
    return schema


def _git_branches_schema() -> dict[str, Any]:
    schema = _bounded_output_arguments_schema()
    schema["properties"]["maximum_entries"] = {
        "type": "integer",
        "minimum": 1,
        "maximum": 1_000,
    }
    return schema


def _path_collection_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["paths"],
        "properties": {"paths": _paths_schema()},
    }


def _commit_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["paths", "message"],
        "properties": {
            "paths": _paths_schema(),
            "message": {
                "type": "string",
                "minLength": 1,
                "maxLength": 512,
                "pattern": "^[^\\r\\n\\u0000]+$",
            },
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
            "working_directory": {
                "type": "string",
                "minLength": 1,
                "maxLength": 2048,
            },
        },
    }


def _paths_schema() -> dict[str, Any]:
    return {
        "type": "array",
        "minItems": 1,
        "maxItems": 256,
        "uniqueItems": True,
        "items": {"type": "string", "minLength": 1, "maxLength": 2048},
    }


def _sha256_schema(*, nullable: bool = False) -> dict[str, Any]:
    return {
        "type": ["string", "null"] if nullable else "string",
        "pattern": "^[a-f0-9]{64}$",
    }


def _text_output_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["text", "truncated"],
        "properties": {
            "text": {"type": "string", "maxLength": 4_194_304},
            "truncated": {"type": "boolean"},
        },
    }


def _repository_scan_output_schema() -> dict[str, Any]:
    list_schema = {
        "type": "array",
        "maxItems": 10_000,
        "items": {"type": "string", "maxLength": 2048},
    }
    return {
        "type": "object",
        "additionalProperties": False,
        "required": [
            "workspace",
            "files",
            "directories",
            "detected_languages",
            "detected_frameworks",
            "package_managers",
            "test_files",
            "config_files",
            "entrypoints",
            "important_files",
            "ignored_dirs",
            "skipped_paths",
            "truncated",
        ],
        "properties": {
            "workspace": {"type": "string", "maxLength": 2048},
            "files": list_schema,
            "directories": list_schema,
            "detected_languages": list_schema,
            "detected_frameworks": list_schema,
            "package_managers": list_schema,
            "test_files": list_schema,
            "config_files": list_schema,
            "entrypoints": list_schema,
            "important_files": list_schema,
            "ignored_dirs": list_schema,
            "skipped_paths": list_schema,
            "truncated": {"type": "boolean"},
        },
    }


def _process_output_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": [
            "executable",
            "working_directory",
            "pid",
            "passed",
            "timed_out",
            "cancelled",
            "termination_reason",
            "stdout_truncated",
            "stderr_truncated",
        ],
        "properties": {
            "executable": {"type": "string", "maxLength": 64},
            "working_directory": {"type": "string", "maxLength": 2048},
            "pid": {"type": ["integer", "null"], "minimum": 0},
            "passed": {"type": "boolean"},
            "timed_out": {"type": "boolean"},
            "cancelled": {"type": "boolean"},
            "termination_reason": {
                "type": ["string", "null"],
                "maxLength": 256,
            },
            "stdout_truncated": {"type": "boolean"},
            "stderr_truncated": {"type": "boolean"},
        },
    }


def _file_read_output_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": [
            "relative_path",
            "content",
            "content_kind",
            "size_bytes",
            "bytes_read",
            "sha256",
            "truncated",
            "redacted",
            "classification",
        ],
        "properties": {
            "relative_path": {"type": "string", "maxLength": 2048},
            "content": {"type": ["string", "null"], "maxLength": 2_097_152},
            "content_kind": {"enum": ["text", "binary"]},
            "size_bytes": {"type": "integer", "minimum": 0},
            "bytes_read": {"type": "integer", "minimum": 0},
            "sha256": _sha256_schema(nullable=True),
            "truncated": {"type": "boolean"},
            "redacted": {"type": "boolean"},
            "classification": _classification_schema(),
        },
    }


def _file_stat_output_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": [
            "relative_path",
            "is_file",
            "is_directory",
            "is_link",
            "size_bytes",
            "modified_ns",
            "classification",
        ],
        "properties": {
            "relative_path": {"type": "string", "maxLength": 2048},
            "is_file": {"type": "boolean"},
            "is_directory": {"type": "boolean"},
            "is_link": {"type": "boolean"},
            "size_bytes": {"type": "integer", "minimum": 0},
            "modified_ns": {"type": "integer", "minimum": 0},
            "content_kind": {"enum": ["text", "binary", None]},
            "classification": _classification_schema(),
        },
    }


def _file_list_output_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["directory", "entries", "skipped_paths", "truncated"],
        "properties": {
            "directory": {"type": "string", "maxLength": 2048},
            "entries": {
                "type": "array",
                "maxItems": 10_000,
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": [
                        "relative_path",
                        "is_file",
                        "is_directory",
                        "is_link",
                        "size_bytes",
                        "generated",
                    ],
                    "properties": {
                        "relative_path": {"type": "string", "maxLength": 2048},
                        "is_file": {"type": "boolean"},
                        "is_directory": {"type": "boolean"},
                        "is_link": {"type": "boolean"},
                        "size_bytes": {"type": "integer", "minimum": 0},
                        "generated": {"type": "boolean"},
                    },
                },
            },
            "skipped_paths": {
                "type": "array",
                "maxItems": 10_000,
                "items": {"type": "string", "maxLength": 2048},
            },
            "truncated": {"type": "boolean"},
        },
    }


def _mutation_output_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": [
            "operation",
            "relative_path",
            "task_id",
            "invocation_id",
            "before_sha256",
            "after_sha256",
            "bytes_before",
            "bytes_after",
            "created",
            "generated",
            "atomic",
            "source_relative_path",
            "timestamp",
        ],
        "properties": {
            "operation": {"enum": ["create", "write", "patch", "rename", "delete"]},
            "relative_path": {"type": "string", "maxLength": 2048},
            "source_relative_path": {"type": ["string", "null"], "maxLength": 2048},
            "task_id": {"type": "string", "maxLength": 128},
            "invocation_id": {"type": "string", "maxLength": 128},
            "before_sha256": _sha256_schema(nullable=True),
            "after_sha256": _sha256_schema(nullable=True),
            "bytes_before": {"type": "integer", "minimum": 0},
            "bytes_after": {"type": "integer", "minimum": 0},
            "created": {"type": "boolean"},
            "generated": {"type": "boolean"},
            "atomic": {"type": "boolean"},
            "timestamp": {"type": "string", "format": "date-time"},
        },
    }


def _git_stage_output_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": [
            "repository_root",
            "paths",
            "task_id",
            "invocation_id",
            "timestamp",
        ],
        "properties": {
            "repository_root": {"type": "string", "maxLength": 2048},
            "paths": _paths_schema(),
            "task_id": {"type": "string", "maxLength": 128},
            "invocation_id": {"type": "string", "maxLength": 128},
            "timestamp": {"type": "string", "format": "date-time"},
        },
    }


def _git_commit_output_schema() -> dict[str, Any]:
    schema = _git_stage_output_schema()
    schema["required"].extend(
        ["parent_commit", "commit", "message_sha256"]
    )
    schema["properties"].update(
        {
            "parent_commit": {"type": "string", "pattern": "^[a-f0-9]{40,64}$"},
            "commit": {"type": "string", "pattern": "^[a-f0-9]{40,64}$"},
            "message_sha256": _sha256_schema(),
        }
    )
    return schema


def _classification_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": [
            "protected",
            "protected_reason",
            "generated",
            "generated_reason",
        ],
        "properties": {
            "protected": {"type": "boolean"},
            "protected_reason": {"type": ["string", "null"], "maxLength": 2048},
            "generated": {"type": "boolean"},
            "generated_reason": {"type": ["string", "null"], "maxLength": 2048},
        },
    }


def _process_executable_scope(
    configured: Iterable[str] | None,
) -> tuple[str, ...]:
    values = (
        ("python", "python3", "pytest", "node", "npm", "git")
        if configured is None
        else configured
    )
    if isinstance(values, (str, bytes)):
        raise TypeError("process executables must be a collection of aliases")
    aliases = tuple(dict.fromkeys(values))
    if not aliases:
        raise ValueError("process executables must not be empty")
    if any(not isinstance(alias, str) for alias in aliases):
        raise TypeError("process executable aliases must be strings")
    return aliases
