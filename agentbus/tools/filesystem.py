from __future__ import annotations

import uuid
from pathlib import Path

from agentbus.tools.filesystem_operations import (
    ContainedFileSystem,
    FileListResult,
    FileMutationRecord,
    FileReadResult,
    FileStatResult,
)


class FileSystemTools:
    """Compatibility facade over the contained filesystem implementation."""

    def __init__(
        self,
        workspace: str = "workspace",
        *,
        maximum_file_bytes: int = 2_097_152,
        maximum_list_entries: int = 10_000,
    ) -> None:
        self._filesystem = ContainedFileSystem(
            workspace,
            create_root=True,
            maximum_file_bytes=maximum_file_bytes,
            maximum_list_entries=maximum_list_entries,
        )
        self.workspace = self._filesystem.root

    def _safe_path(self, path: str) -> Path:
        return self._filesystem.resolver.resolve(path).lexical_path

    def list_files(self) -> str:
        result = self.list_directory(recursive=True, recurse_generated=False)
        files = [
            entry.relative_path
            for entry in result.entries
            if entry.is_file and not entry.generated
        ]
        if not files:
            return "No files found."
        return "\n".join(files)

    def read_file(self, path: str, max_chars: int = 20_000) -> str:
        if max_chars < 1:
            raise ValueError("max_chars must be positive")
        try:
            result = self.read_file_result(
                path,
                maximum_bytes=min(
                    self._filesystem.maximum_file_bytes,
                    max_chars * 4,
                ),
            )
        except FileNotFoundError:
            return f"File not found: {path}"
        except IsADirectoryError:
            return f"Not a file: {path}"
        if result.content is None:
            return (
                f"Binary file not displayed: {result.relative_path} "
                f"({result.size_bytes} bytes)."
            )
        content = result.content
        truncated = result.truncated or len(content) > max_chars
        if len(content) > max_chars:
            content = content[:max_chars]
        if truncated:
            return content + "\n\n[File truncated]"
        return content

    def write_file(
        self,
        path: str,
        content: str,
        *,
        task_id: str = "legacy-filesystem",
        invocation_id: str | None = None,
        expected_sha256: str | None = None,
    ) -> str:
        self.write_file_result(
            path,
            content,
            task_id=task_id,
            invocation_id=invocation_id or uuid.uuid4().hex,
            expected_sha256=expected_sha256,
        )
        return f"Wrote file: {path}"

    def read_file_result(
        self,
        path: str,
        *,
        maximum_bytes: int | None = None,
    ) -> FileReadResult:
        return self._filesystem.read(path, maximum_bytes=maximum_bytes)

    def stat_path(self, path: str) -> FileStatResult:
        return self._filesystem.stat(path)

    def list_directory(
        self,
        path: str | None = None,
        *,
        recursive: bool = False,
        recurse_generated: bool = True,
        maximum_entries: int | None = None,
    ) -> FileListResult:
        return self._filesystem.list_directory(
            path,
            recursive=recursive,
            recurse_generated=recurse_generated,
            maximum_entries=maximum_entries,
        )

    def create_file(
        self,
        path: str,
        content: str | bytes,
        *,
        task_id: str,
        invocation_id: str,
    ) -> FileMutationRecord:
        return self._filesystem.create(
            path,
            content,
            task_id=task_id,
            invocation_id=invocation_id,
        )

    def write_file_result(
        self,
        path: str,
        content: str | bytes,
        *,
        task_id: str,
        invocation_id: str,
        expected_sha256: str | None = None,
    ) -> FileMutationRecord:
        return self._filesystem.write(
            path,
            content,
            task_id=task_id,
            invocation_id=invocation_id,
            expected_sha256=expected_sha256,
        )

    def patch_file(
        self,
        path: str,
        expected: str,
        replacement: str,
        *,
        task_id: str,
        invocation_id: str,
        expected_sha256: str | None = None,
        expected_occurrences: int = 1,
    ) -> FileMutationRecord:
        return self._filesystem.patch(
            path,
            expected,
            replacement,
            task_id=task_id,
            invocation_id=invocation_id,
            expected_sha256=expected_sha256,
            expected_occurrences=expected_occurrences,
        )

    def rename_file(
        self,
        source: str,
        destination: str,
        *,
        task_id: str,
        invocation_id: str,
        expected_sha256: str | None = None,
    ) -> FileMutationRecord:
        return self._filesystem.rename(
            source,
            destination,
            task_id=task_id,
            invocation_id=invocation_id,
            expected_sha256=expected_sha256,
        )

    def delete_file(
        self,
        path: str,
        *,
        task_id: str,
        invocation_id: str,
        expected_sha256: str,
    ) -> FileMutationRecord:
        return self._filesystem.delete(
            path,
            task_id=task_id,
            invocation_id=invocation_id,
            expected_sha256=expected_sha256,
        )
