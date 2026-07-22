from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from enum import Enum
from itertools import islice
from pathlib import Path

from agentbus.security.redaction import redact_text
from agentbus.tools.filesystem_security import (
    ContainedPathResolver,
    FileSystemContainmentError,
    FileSystemPathClassification,
    FileSystemSecurityError,
    ProtectedFileSystemPath,
)
from agentbus.tools.protocol import bound_text


class FileContentKind(str, Enum):
    TEXT = "text"
    BINARY = "binary"


@dataclass(frozen=True)
class FileReadResult:
    relative_path: str
    content: str | None
    content_kind: FileContentKind
    size_bytes: int
    bytes_read: int
    sha256: str | None
    truncated: bool
    redacted: bool
    classification: FileSystemPathClassification


@dataclass(frozen=True)
class FileStatResult:
    relative_path: str
    is_file: bool
    is_directory: bool
    is_link: bool
    size_bytes: int
    modified_ns: int
    content_kind: FileContentKind | None
    classification: FileSystemPathClassification


@dataclass(frozen=True)
class FileListEntry:
    relative_path: str
    is_file: bool
    is_directory: bool
    is_link: bool
    size_bytes: int
    generated: bool


@dataclass(frozen=True)
class FileListResult:
    directory: str
    entries: tuple[FileListEntry, ...]
    skipped_paths: tuple[str, ...]
    truncated: bool


class ContainedFileSystem:
    def __init__(
        self,
        root: str | Path,
        *,
        create_root: bool = False,
        maximum_file_bytes: int = 2_097_152,
        maximum_list_entries: int = 10_000,
    ) -> None:
        if maximum_file_bytes < 1:
            raise ValueError("maximum_file_bytes must be positive")
        if maximum_list_entries < 1:
            raise ValueError("maximum_list_entries must be positive")
        self.resolver = ContainedPathResolver(root, create_root=create_root)
        self.root = self.resolver.root
        self.maximum_file_bytes = maximum_file_bytes
        self.maximum_list_entries = maximum_list_entries

    def read(
        self,
        path: str,
        *,
        maximum_bytes: int | None = None,
    ) -> FileReadResult:
        limit = self._file_limit(maximum_bytes)
        resolved = self.resolver.resolve(path)
        if not resolved.exists:
            raise FileNotFoundError(f"File not found: {resolved.relative_path}")
        if not resolved.path.is_file():
            raise IsADirectoryError(f"Not a file: {resolved.relative_path}")

        self._confirm_resolution(resolved.relative_path, resolved.path)
        before = resolved.path.stat()
        with resolved.path.open("rb") as handle:
            data = handle.read(limit + 1)
        after = resolved.path.stat()
        if (
            before.st_dev,
            before.st_ino,
            before.st_size,
            before.st_mtime_ns,
        ) != (
            after.st_dev,
            after.st_ino,
            after.st_size,
            after.st_mtime_ns,
        ):
            raise FileSystemSecurityError("File changed during bounded read.")
        self._confirm_resolution(resolved.relative_path, resolved.path)
        retained = data[:limit]
        truncated = len(data) > limit or after.st_size > len(retained)
        if truncated:
            retained = _trim_partial_utf8_character(retained)
        kind = classify_file_content(retained)
        complete_hash = (
            hashlib.sha256(retained).hexdigest()
            if not truncated and after.st_size == len(retained)
            else None
        )
        if kind == FileContentKind.BINARY:
            content = None
            redacted = False
        else:
            decoded = retained.decode("utf-8")
            content, _, text_truncated = bound_text(decoded, limit)
            truncated = truncated or text_truncated
            safe_decoded = redact_text(decoded, max_chars=max(1, len(decoded))) or ""
            redacted = safe_decoded != decoded
        self.resolver.validate_root_identity()
        return FileReadResult(
            relative_path=resolved.relative_path,
            content=content,
            content_kind=kind,
            size_bytes=after.st_size,
            bytes_read=len(retained),
            sha256=complete_hash,
            truncated=truncated,
            redacted=redacted,
            classification=resolved.classification,
        )

    def stat(self, path: str) -> FileStatResult:
        resolved = self.resolver.resolve(path)
        if not resolved.exists:
            raise FileNotFoundError(f"Path not found: {resolved.relative_path}")
        self._confirm_resolution(resolved.relative_path, resolved.path)
        before = resolved.path.stat()
        is_file = resolved.path.is_file()
        content_kind = None
        if is_file:
            with resolved.path.open("rb") as handle:
                content_kind = classify_file_content(handle.read(8_192))
        after = resolved.path.stat()
        if (
            before.st_dev,
            before.st_ino,
            before.st_size,
            before.st_mtime_ns,
        ) != (
            after.st_dev,
            after.st_ino,
            after.st_size,
            after.st_mtime_ns,
        ):
            raise FileSystemSecurityError("Path changed during metadata inspection.")
        self._confirm_resolution(resolved.relative_path, resolved.path)
        self.resolver.validate_root_identity()
        return FileStatResult(
            relative_path=resolved.relative_path,
            is_file=is_file,
            is_directory=resolved.path.is_dir(),
            is_link=resolved.final_component_is_link,
            size_bytes=after.st_size,
            modified_ns=after.st_mtime_ns,
            content_kind=content_kind,
            classification=resolved.classification,
        )

    def list_directory(
        self,
        path: str | None = None,
        *,
        recursive: bool = False,
        maximum_entries: int | None = None,
    ) -> FileListResult:
        limit = (
            self.maximum_list_entries
            if maximum_entries is None
            else maximum_entries
        )
        if limit < 1 or limit > self.maximum_list_entries:
            raise ValueError(
                "maximum_entries must be positive and cannot exceed the configured limit"
            )
        if path is None:
            directory = self.root
            directory_label = "."
        else:
            resolved_directory = self.resolver.resolve(path)
            if not resolved_directory.exists:
                raise FileNotFoundError(
                    f"Directory not found: {resolved_directory.relative_path}"
                )
            if not resolved_directory.path.is_dir():
                raise NotADirectoryError(
                    f"Not a directory: {resolved_directory.relative_path}"
                )
            directory = resolved_directory.path
            directory_label = resolved_directory.relative_path

        entries: list[FileListEntry] = []
        skipped: list[str] = []
        pending: list[tuple[str | None, Path]] = [
            (None if path is None else directory_label, directory)
        ]
        visited: set[tuple[int, int]] = set()
        truncated = False
        inspected = 0
        while pending:
            current_label, current = pending.pop()
            if current_label is None:
                self.resolver.validate_root_identity()
            else:
                self._confirm_resolution(current_label, current)
            before_scan = current.stat()
            identity = (before_scan.st_dev, before_scan.st_ino)
            if identity in visited:
                continue
            visited.add(identity)
            try:
                remaining_scan_budget = self.maximum_list_entries - inspected
                if remaining_scan_budget <= 0:
                    truncated = True
                    break
                with os.scandir(current) as scanner:
                    children = list(islice(scanner, remaining_scan_budget + 1))
            except OSError as exc:
                raise FileSystemSecurityError(
                    "Directory could not be listed safely."
                ) from exc
            if len(children) > remaining_scan_budget:
                children = children[:remaining_scan_budget]
                truncated = True
                pending.clear()
            children.sort(key=lambda item: item.name)
            after_scan = current.stat()
            if (
                before_scan.st_dev,
                before_scan.st_ino,
                before_scan.st_mtime_ns,
            ) != (
                after_scan.st_dev,
                after_scan.st_ino,
                after_scan.st_mtime_ns,
            ):
                raise FileSystemSecurityError("Directory changed while it was listed.")
            for child in children:
                inspected += 1
                child_path = Path(child.path)
                try:
                    relative = child_path.relative_to(self.root).as_posix()
                    resolved = self.resolver.resolve(relative)
                except (ProtectedFileSystemPath, FileSystemContainmentError):
                    if len(skipped) < limit:
                        skipped.append(_safe_relative_label(child_path, self.root))
                    else:
                        truncated = True
                    continue
                if len(entries) >= limit:
                    truncated = True
                    pending.clear()
                    break
                try:
                    stat = resolved.path.stat()
                except OSError as exc:
                    raise FileSystemSecurityError(
                        "Directory entry changed during inspection."
                    ) from exc
                is_directory = resolved.path.is_dir()
                entries.append(
                    FileListEntry(
                        relative_path=resolved.relative_path,
                        is_file=resolved.path.is_file(),
                        is_directory=is_directory,
                        is_link=resolved.final_component_is_link,
                        size_bytes=stat.st_size,
                        generated=resolved.classification.generated,
                    )
                )
                if recursive and is_directory:
                    pending.append((resolved.relative_path, resolved.path))
        self.resolver.validate_root_identity()
        return FileListResult(
            directory=directory_label,
            entries=tuple(sorted(entries, key=lambda entry: entry.relative_path)),
            skipped_paths=tuple(sorted(set(skipped)))[:limit],
            truncated=truncated,
        )

    def _confirm_resolution(self, relative_path: str, expected: Path) -> None:
        confirmed = self.resolver.resolve(relative_path)
        if confirmed.path != expected:
            raise FileSystemSecurityError(
                "Filesystem path target changed during operation."
            )

    def _file_limit(self, requested: int | None) -> int:
        if requested is None:
            return self.maximum_file_bytes
        if requested < 1:
            raise ValueError("maximum_bytes must be positive")
        return min(requested, self.maximum_file_bytes)


def classify_file_content(content: bytes) -> FileContentKind:
    if not content:
        return FileContentKind.TEXT
    if b"\x00" in content:
        return FileContentKind.BINARY
    try:
        decoded = content.decode("utf-8")
    except UnicodeDecodeError:
        return FileContentKind.BINARY
    controls = sum(
        1
        for character in decoded
        if ord(character) < 32 and character not in {"\b", "\t", "\n", "\f", "\r"}
    )
    return (
        FileContentKind.BINARY
        if controls > max(1, len(decoded) // 10)
        else FileContentKind.TEXT
    )


def _trim_partial_utf8_character(content: bytes) -> bytes:
    for removed in range(4):
        candidate = content if removed == 0 else content[:-removed]
        try:
            candidate.decode("utf-8")
            return candidate
        except UnicodeDecodeError as exc:
            if exc.end != len(candidate) or exc.reason != "unexpected end of data":
                return content
    return content


def _safe_relative_label(path: Path, root: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return "[outside assigned root]"
