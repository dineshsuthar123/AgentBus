from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path
from typing import Any

from agentbus.execution.cancellation import CancellationToken
from agentbus.repo.scanner import RepoScanner
from agentbus.sandbox.platform import ExecutableCatalog
from agentbus.sandbox.process import ControlledProcessSupervisor
from agentbus.tools.descriptors import descriptor_map
from agentbus.tools.filesystem import FileSystemTools
from agentbus.tools.filesystem_operations import (
    FileListResult,
    FileMutationOperation,
    FileMutationRecord,
    FileReadResult,
    FileStatResult,
)
from agentbus.tools.git_tools import GitCommitRecord, GitStageRecord, GitTools
from agentbus.tools.interfaces import ToolExecutionOutput, ToolOutputCallback
from agentbus.tools.protocol import (
    ToolArtifact,
    ToolArtifactKind,
    ToolDescriptor,
    ToolInvocation,
    ToolProtocolValidationError,
    ToolResourceUsage,
    validate_invocation_against_descriptor,
    validate_tool_output,
)
from agentbus.tools.registry import ToolRegistry


_FILESYSTEM_TOOL_NAMES = frozenset(
    {
        "filesystem.read",
        "filesystem.stat",
        "filesystem.list",
        "filesystem.create",
        "filesystem.write",
        "filesystem.patch",
        "filesystem.rename",
        "filesystem.delete",
    }
)
_GIT_TOOL_NAMES = frozenset(
    {
        "git.status",
        "git.diff",
        "git.show",
        "git.log",
        "git.branches",
        "git.stage",
        "git.commit",
    }
)
_PROCESS_TOOL_NAMES = frozenset({"process.execute", "test.execute"})
_MAX_INLINE_FILE_BYTES = 524_288


class ManagedToolContextError(ToolProtocolValidationError):
    """Raised when an invocation targets a different pinned runtime context."""


class _PinnedManagedTool:
    def __init__(
        self,
        descriptor: ToolDescriptor,
        *,
        workspace: str | Path,
        worktree: str | Path,
    ) -> None:
        self._descriptor = descriptor
        self.workspace = _existing_directory(workspace, "workspace")
        self.worktree = _existing_directory(worktree, "worktree")

    @property
    def descriptor(self) -> ToolDescriptor:
        return self._descriptor

    def _prepare(
        self,
        invocation: ToolInvocation,
        cancellation: CancellationToken | None,
    ) -> None:
        if cancellation is not None:
            cancellation.checkpoint(
                "tool-adapter",
                stage="before-execution",
            )
        validate_invocation_against_descriptor(invocation, self.descriptor)
        workspace = _invocation_directory(
            invocation.context.workspace_identity,
            "workspace",
        )
        worktree = _invocation_directory(
            invocation.context.worktree_identity,
            "worktree",
        )
        if workspace != self.workspace or worktree != self.worktree:
            raise ManagedToolContextError(
                "Invocation workspace or worktree does not match the pinned runtime."
            )

    def _output(self, output: ToolExecutionOutput) -> ToolExecutionOutput:
        validate_tool_output(output.structured_output, self.descriptor)
        return output


class RepositoryScanManagedTool(_PinnedManagedTool):
    def __init__(
        self,
        descriptor: ToolDescriptor,
        *,
        workspace: str | Path,
        worktree: str | Path,
    ) -> None:
        super().__init__(descriptor, workspace=workspace, worktree=worktree)
        self._scanner = RepoScanner(str(self.worktree))
        self._filesystem = FileSystemTools(str(self.worktree))

    def execute(
        self,
        invocation: ToolInvocation,
        *,
        cancellation: CancellationToken | None = None,
    ) -> ToolExecutionOutput:
        self._prepare(invocation, cancellation)
        listing = self._filesystem.list_directory(
            recursive=True,
            recurse_generated=False,
            maximum_entries=10_000,
        )
        scan = self._scanner.scan()
        safe_files = {
            entry.relative_path for entry in listing.entries if entry.is_file
        }
        safe_directories = {
            entry.relative_path for entry in listing.entries if entry.is_directory
        }
        truncated = listing.truncated

        def safe_paths(name: str, allowed: set[str]) -> list[str]:
            nonlocal truncated
            values = [path for path in scan[name] if path in allowed]
            if len(values) > 10_000:
                truncated = True
            return values[:10_000]

        output = {
            "workspace": str(self.worktree),
            "files": safe_paths("files", safe_files),
            "directories": safe_paths("directories", safe_directories),
            "detected_languages": _bounded_strings(scan["detected_languages"]),
            "detected_frameworks": _bounded_strings(scan["detected_frameworks"]),
            "package_managers": _bounded_strings(scan["package_managers"]),
            "test_files": safe_paths("test_files", safe_files),
            "config_files": safe_paths("config_files", safe_files),
            "entrypoints": safe_paths("entrypoints", safe_files),
            "important_files": safe_paths("important_files", safe_files),
            "ignored_dirs": _bounded_strings(scan["ignored_dirs"]),
            "skipped_paths": list(listing.skipped_paths),
            "truncated": truncated,
        }
        return self._output(ToolExecutionOutput(structured_output=output))


class FileSystemManagedTool(_PinnedManagedTool):
    def __init__(
        self,
        descriptor: ToolDescriptor,
        *,
        workspace: str | Path,
        worktree: str | Path,
    ) -> None:
        if descriptor.name not in _FILESYSTEM_TOOL_NAMES:
            raise ValueError("Descriptor is not a filesystem tool.")
        super().__init__(descriptor, workspace=workspace, worktree=worktree)
        self._filesystem = FileSystemTools(str(self.worktree))

    def execute(
        self,
        invocation: ToolInvocation,
        *,
        cancellation: CancellationToken | None = None,
    ) -> ToolExecutionOutput:
        self._prepare(invocation, cancellation)
        arguments = invocation.arguments
        name = self.descriptor.name
        if name == "filesystem.read":
            maximum_bytes = min(
                int(arguments.get("maximum_bytes", _MAX_INLINE_FILE_BYTES)),
                invocation.resource_budget.maximum_file_bytes,
                _MAX_INLINE_FILE_BYTES,
            )
            result = self._filesystem.read_file_result(
                arguments["path"],
                maximum_bytes=maximum_bytes,
            )
            return self._output(
                ToolExecutionOutput(structured_output=_file_read_output(result))
            )
        if name == "filesystem.stat":
            result = self._filesystem.stat_path(arguments["path"])
            return self._output(
                ToolExecutionOutput(structured_output=_file_stat_output(result))
            )
        if name == "filesystem.list":
            result = self._filesystem.list_directory(
                arguments.get("path"),
                recursive=bool(arguments.get("recursive", False)),
                recurse_generated=bool(arguments.get("recurse_generated", True)),
                maximum_entries=arguments.get("maximum_entries"),
            )
            return self._output(
                ToolExecutionOutput(structured_output=_file_list_output(result))
            )

        content = arguments.get("content")
        if isinstance(content, str):
            _require_file_budget(content, invocation)
        if name == "filesystem.create":
            mutation = self._filesystem.create_file(
                arguments["path"],
                content,
                task_id=invocation.task_id,
                invocation_id=invocation.invocation_id,
            )
        elif name == "filesystem.write":
            mutation = self._filesystem.write_file_result(
                arguments["path"],
                content,
                task_id=invocation.task_id,
                invocation_id=invocation.invocation_id,
                expected_sha256=arguments.get("expected_sha256"),
            )
        elif name == "filesystem.patch":
            _require_file_budget(arguments["replacement"], invocation)
            mutation = self._filesystem.patch_file(
                arguments["path"],
                arguments["expected"],
                arguments["replacement"],
                task_id=invocation.task_id,
                invocation_id=invocation.invocation_id,
                expected_sha256=arguments.get("expected_sha256"),
                expected_occurrences=int(arguments.get("expected_occurrences", 1)),
            )
        elif name == "filesystem.rename":
            mutation = self._filesystem.rename_file(
                arguments["source"],
                arguments["destination"],
                task_id=invocation.task_id,
                invocation_id=invocation.invocation_id,
                expected_sha256=arguments.get("expected_sha256"),
            )
        elif name == "filesystem.delete":
            mutation = self._filesystem.delete_file(
                arguments["path"],
                task_id=invocation.task_id,
                invocation_id=invocation.invocation_id,
                expected_sha256=arguments["expected_sha256"],
            )
        else:  # pragma: no cover - constructor and descriptor set prevent this
            raise RuntimeError("Unsupported filesystem adapter operation.")
        artifact = _mutation_artifact(mutation)
        artifact_bytes = artifact.size_bytes if artifact is not None else 0
        output = ToolExecutionOutput(
            structured_output=_file_mutation_output(mutation),
            artifacts=(artifact,) if artifact is not None else (),
            resource_usage=ToolResourceUsage(
                artifact_bytes=artifact_bytes,
                file_mutations=1,
                written_bytes=(
                    mutation.bytes_after
                    if mutation.operation.value in {"create", "write", "patch"}
                    else 0
                ),
            ),
        )
        return self._output(output)


class GitManagedTool(_PinnedManagedTool):
    def __init__(
        self,
        descriptor: ToolDescriptor,
        *,
        workspace: str | Path,
        worktree: str | Path,
        owned_worktree: bool,
    ) -> None:
        if descriptor.name not in _GIT_TOOL_NAMES:
            raise ValueError("Descriptor is not a Git tool.")
        super().__init__(descriptor, workspace=workspace, worktree=worktree)
        self._git = GitTools(
            str(self.worktree),
            owned_worktree=owned_worktree,
        )

    def execute(
        self,
        invocation: ToolInvocation,
        *,
        cancellation: CancellationToken | None = None,
    ) -> ToolExecutionOutput:
        self._prepare(invocation, cancellation)
        arguments = invocation.arguments
        name = self.descriptor.name
        max_chars = arguments.get("max_chars")
        if name == "git.status":
            return self._text(self._git.status(max_chars=max_chars))
        if name == "git.diff":
            return self._text(
                self._git.diff(paths=arguments.get("paths"), max_chars=max_chars)
            )
        if name == "git.show":
            return self._text(
                self._git.show(
                    arguments.get("revision", "HEAD"),
                    path=arguments.get("path"),
                    max_chars=max_chars,
                )
            )
        if name == "git.log":
            return self._text(
                self._git.log(
                    maximum_entries=int(arguments.get("maximum_entries", 20)),
                    max_chars=max_chars,
                )
            )
        if name == "git.branches":
            return self._text(
                self._git.branches(
                    maximum_entries=int(arguments.get("maximum_entries", 100)),
                    max_chars=max_chars,
                )
            )
        paths = arguments["paths"]
        if len(paths) > invocation.resource_budget.file_mutations:
            raise ToolProtocolValidationError(
                "Git path count exceeds the invocation mutation budget."
            )
        if name == "git.stage":
            record = self._git.stage(
                paths,
                task_id=invocation.task_id,
                invocation_id=invocation.invocation_id,
            )
            return self._output(
                ToolExecutionOutput(
                    structured_output=_git_stage_output(record),
                    resource_usage=ToolResourceUsage(file_mutations=len(paths)),
                )
            )
        if name == "git.commit":
            record = self._git.commit(
                arguments["message"],
                paths,
                task_id=invocation.task_id,
                invocation_id=invocation.invocation_id,
            )
            return self._output(
                ToolExecutionOutput(
                    structured_output=_git_commit_output(record),
                    resource_usage=ToolResourceUsage(file_mutations=len(paths)),
                )
            )
        raise RuntimeError("Unsupported Git adapter operation.")

    def _text(self, value: str) -> ToolExecutionOutput:
        output = ToolExecutionOutput(
            structured_output={
                "text": value,
                "truncated": "[output truncated]" in value[-64:],
            }
        )
        return self._output(output)


class ProcessManagedTool(_PinnedManagedTool):
    def __init__(
        self,
        descriptor: ToolDescriptor,
        *,
        workspace: str | Path,
        worktree: str | Path,
        catalog: ExecutableCatalog,
        output_callback: ToolOutputCallback | None = None,
    ) -> None:
        if descriptor.name not in _PROCESS_TOOL_NAMES:
            raise ValueError("Descriptor is not a process tool.")
        super().__init__(descriptor, workspace=workspace, worktree=worktree)
        self._supervisor = ControlledProcessSupervisor(
            self.worktree,
            catalog=catalog,
        )
        self._output_callback = output_callback

    def execute(
        self,
        invocation: ToolInvocation,
        *,
        cancellation: CancellationToken | None = None,
    ) -> ToolExecutionOutput:
        self._prepare(invocation, cancellation)
        arguments = invocation.arguments
        callback = (
            (lambda chunk: self._output_callback(invocation, chunk))
            if self._output_callback is not None
            else None
        )
        result = self._supervisor.run(
            arguments["executable"],
            arguments["arguments"],
            working_directory=arguments.get("working_directory"),
            timeout_seconds=invocation.timeout_seconds,
            resource_budget=invocation.resource_budget,
            cancellation=cancellation,
            output_callback=callback,
            task_id=invocation.task_id,
        )
        output = ToolExecutionOutput(
            structured_output={
                "executable": result.executable.alias,
                "working_directory": str(result.working_directory),
                "pid": result.pid,
                "passed": result.passed,
                "timed_out": result.timed_out,
                "cancelled": result.cancelled,
                "termination_reason": result.termination_reason,
                "stdout_truncated": result.stdout_truncated,
                "stderr_truncated": result.stderr_truncated,
            },
            stdout=result.stdout,
            stderr=result.stderr,
            stdout_truncated=result.stdout_truncated,
            stderr_truncated=result.stderr_truncated,
            exit_code=result.exit_code,
            timed_out=result.timed_out,
            cancelled=result.cancelled,
            resource_usage=result.resource_usage,
            safe_diagnostic_metadata=result.safe_diagnostic_metadata,
        )
        return self._output(output)


def builtin_tool_registry(
    *,
    workspace: str | Path,
    worktree: str | Path | None = None,
    owned_worktree: bool = False,
    executable_catalog: ExecutableCatalog | None = None,
    output_callback: ToolOutputCallback | None = None,
) -> ToolRegistry:
    workspace_root = _existing_directory(workspace, "workspace")
    worktree_root = _existing_directory(worktree or workspace_root, "worktree")
    catalog = executable_catalog or ExecutableCatalog.standard()
    descriptors = descriptor_map(
        workspace=workspace_root,
        worktree=worktree_root,
        process_executables=catalog.aliases,
    )
    registry = ToolRegistry()
    registry.register_factory(
        descriptors["repository.scan"],
        lambda: RepositoryScanManagedTool(
            descriptors["repository.scan"],
            workspace=workspace_root,
            worktree=worktree_root,
        ),
    )
    for name in sorted(_FILESYSTEM_TOOL_NAMES):
        registry.register_factory(
            descriptors[name],
            lambda name=name: FileSystemManagedTool(
                descriptors[name],
                workspace=workspace_root,
                worktree=worktree_root,
            ),
        )
    for name in sorted(_GIT_TOOL_NAMES):
        registry.register_factory(
            descriptors[name],
            lambda name=name: GitManagedTool(
                descriptors[name],
                workspace=workspace_root,
                worktree=worktree_root,
                owned_worktree=owned_worktree,
            ),
        )
    for name in sorted(_PROCESS_TOOL_NAMES):
        registry.register_factory(
            descriptors[name],
            lambda name=name: ProcessManagedTool(
                descriptors[name],
                workspace=workspace_root,
                worktree=worktree_root,
                catalog=catalog,
                output_callback=output_callback,
            ),
        )
    return registry


def _existing_directory(value: str | Path, label: str) -> Path:
    try:
        path = Path(value).expanduser().resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise ManagedToolContextError(
            f"Configured tool {label} is unavailable."
        ) from exc
    if not path.is_dir():
        raise ManagedToolContextError(
            f"Configured tool {label} must be a directory."
        )
    return path


def _invocation_directory(value: str, label: str) -> Path:
    try:
        return Path(value).expanduser().resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise ManagedToolContextError(
            f"Invocation {label} identity is unavailable."
        ) from exc


def _bounded_strings(values: Iterable[str]) -> list[str]:
    return list(values)[:10_000]


def _classification(value: Any) -> dict[str, Any]:
    return {
        "protected": value.protected,
        "protected_reason": value.protected_reason,
        "generated": value.generated,
        "generated_reason": value.generated_reason,
    }


def _file_read_output(result: FileReadResult) -> dict[str, Any]:
    return {
        "relative_path": result.relative_path,
        "content": result.content,
        "content_kind": result.content_kind.value,
        "size_bytes": result.size_bytes,
        "bytes_read": result.bytes_read,
        "sha256": result.sha256,
        "truncated": result.truncated,
        "redacted": result.redacted,
        "classification": _classification(result.classification),
    }


def _file_stat_output(result: FileStatResult) -> dict[str, Any]:
    return {
        "relative_path": result.relative_path,
        "is_file": result.is_file,
        "is_directory": result.is_directory,
        "is_link": result.is_link,
        "size_bytes": result.size_bytes,
        "modified_ns": result.modified_ns,
        "content_kind": (
            result.content_kind.value if result.content_kind is not None else None
        ),
        "classification": _classification(result.classification),
    }


def _file_list_output(result: FileListResult) -> dict[str, Any]:
    return {
        "directory": result.directory,
        "entries": [
            {
                "relative_path": entry.relative_path,
                "is_file": entry.is_file,
                "is_directory": entry.is_directory,
                "is_link": entry.is_link,
                "size_bytes": entry.size_bytes,
                "generated": entry.generated,
            }
            for entry in result.entries
        ],
        "skipped_paths": list(result.skipped_paths),
        "truncated": result.truncated,
    }


def _file_mutation_output(result: FileMutationRecord) -> dict[str, Any]:
    return {
        "operation": result.operation.value,
        "relative_path": result.relative_path,
        "source_relative_path": result.source_relative_path,
        "task_id": result.task_id,
        "invocation_id": result.invocation_id,
        "before_sha256": result.before_sha256,
        "after_sha256": result.after_sha256,
        "bytes_before": result.bytes_before,
        "bytes_after": result.bytes_after,
        "created": result.created,
        "generated": result.generated,
        "atomic": result.atomic,
        "timestamp": result.timestamp.isoformat(),
    }


def _mutation_artifact(result: FileMutationRecord) -> ToolArtifact | None:
    if result.after_sha256 is None:
        return None
    is_text_mutation = result.operation in {
        FileMutationOperation.CREATE,
        FileMutationOperation.WRITE,
        FileMutationOperation.PATCH,
    }
    return ToolArtifact(
        artifact_id=f"{result.invocation_id}-file",
        kind=ToolArtifactKind.FILE,
        relative_path=result.relative_path,
        media_type=(
            "text/plain; charset=utf-8"
            if is_text_mutation
            else "application/octet-stream"
        ),
        size_bytes=result.bytes_after,
        sha256=result.after_sha256,
        safe_metadata={
            "operation": result.operation.value,
            "encoding": "utf-8" if is_text_mutation else "unknown",
        },
    )


def _require_file_budget(content: str, invocation: ToolInvocation) -> None:
    if len(content.encode("utf-8")) > invocation.resource_budget.maximum_file_bytes:
        raise ToolProtocolValidationError(
            "File content exceeds the invocation maximum file budget."
        )


def _git_stage_output(result: GitStageRecord) -> dict[str, Any]:
    return {
        "repository_root": result.repository_root,
        "paths": list(result.paths),
        "task_id": result.task_id,
        "invocation_id": result.invocation_id,
        "timestamp": result.timestamp.isoformat(),
    }


def _git_commit_output(result: GitCommitRecord) -> dict[str, Any]:
    output = _git_stage_output(
        GitStageRecord(
            repository_root=result.repository_root,
            paths=result.paths,
            task_id=result.task_id,
            invocation_id=result.invocation_id,
            timestamp=result.timestamp,
        )
    )
    output.update(
        {
            "parent_commit": result.parent_commit,
            "commit": result.commit,
            "message_sha256": result.message_sha256,
        }
    )
    return output
