from __future__ import annotations

import hashlib
import os
import stat as stat_module
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from itertools import islice
from pathlib import Path, PurePosixPath

from agentbus.security.redaction import redact_text
from agentbus.tools.filesystem_security import (
    ContainedPathResolver,
    FileSystemContainmentError,
    FileSystemPathClassification,
    FileSystemSecurityError,
    ProtectedFileSystemPath,
    ResolvedFileSystemPath,
)
from agentbus.tools.protocol import bound_text


class FileContentKind(str, Enum):
    TEXT = "text"
    BINARY = "binary"


class FileMutationOperation(str, Enum):
    CREATE = "create"
    WRITE = "write"
    PATCH = "patch"
    RENAME = "rename"
    DELETE = "delete"


class FileMutationError(FileSystemSecurityError):
    """Base error for contained filesystem mutation failures."""


class FileMutationConflict(FileMutationError):
    """Raised when a target no longer matches the caller's observation."""


class FileSizeLimitExceeded(FileMutationError):
    """Raised when mutation input or an existing target exceeds its budget."""


class PatchConflictError(FileMutationConflict):
    """Raised when patch context is missing, ambiguous, or stale."""


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


@dataclass(frozen=True)
class FileMutationRecord:
    operation: FileMutationOperation
    relative_path: str
    source_relative_path: str | None
    task_id: str
    invocation_id: str
    before_sha256: str | None
    after_sha256: str | None
    bytes_before: int
    bytes_after: int
    created: bool
    generated: bool
    atomic: bool
    timestamp: datetime


@dataclass(frozen=True)
class _FileSnapshot:
    identity: tuple[int, int, int, int, int, int]
    sha256: str
    size_bytes: int
    mode: int


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

    def create(
        self,
        path: str,
        content: str | bytes,
        *,
        task_id: str,
        invocation_id: str,
    ) -> FileMutationRecord:
        return self._atomic_write(
            path,
            content,
            operation=FileMutationOperation.CREATE,
            task_id=task_id,
            invocation_id=invocation_id,
            require_absent=True,
        )

    def write(
        self,
        path: str,
        content: str | bytes,
        *,
        task_id: str,
        invocation_id: str,
        expected_sha256: str | None = None,
    ) -> FileMutationRecord:
        return self._atomic_write(
            path,
            content,
            operation=FileMutationOperation.WRITE,
            task_id=task_id,
            invocation_id=invocation_id,
            expected_sha256=expected_sha256,
        )

    def patch(
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
        if not isinstance(expected, str) or not isinstance(replacement, str):
            raise TypeError("Patch context and replacement must be strings.")
        if not expected:
            raise PatchConflictError("Patch context must not be empty.")
        if expected_occurrences < 1:
            raise ValueError("expected_occurrences must be positive")
        resolved = self.resolver.resolve(path, reject_any_link=True)
        content, snapshot = self._read_mutation_file(resolved)
        self._validate_expected_hash(expected_sha256)
        if expected_sha256 is not None and snapshot.sha256 != expected_sha256:
            raise PatchConflictError("Patch target hash no longer matches.")
        if classify_file_content(content) == FileContentKind.BINARY:
            raise FileMutationError("Binary files cannot be patched as text.")
        try:
            decoded = content.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise FileMutationError("Patch target is not UTF-8 text.") from exc
        occurrences = decoded.count(expected)
        if occurrences != expected_occurrences:
            raise PatchConflictError(
                "Patch context occurrence count does not match the request."
            )
        updated = decoded.replace(expected, replacement, expected_occurrences)
        return self._atomic_write(
            path,
            updated,
            operation=FileMutationOperation.PATCH,
            task_id=task_id,
            invocation_id=invocation_id,
            expected_sha256=snapshot.sha256,
        )

    def rename(
        self,
        source: str,
        destination: str,
        *,
        task_id: str,
        invocation_id: str,
        expected_sha256: str | None = None,
    ) -> FileMutationRecord:
        self._validate_attribution(task_id, invocation_id)
        source_resolved = self.resolver.resolve(source, reject_any_link=True)
        destination_resolved = self.resolver.resolve(
            destination,
            reject_any_link=True,
        )
        if source_resolved.relative_path == destination_resolved.relative_path:
            raise FileMutationConflict("Source and destination must differ.")
        source_snapshot = self._snapshot_file(source_resolved)
        self._require_expected_hash(source_snapshot, expected_sha256)
        if destination_resolved.exists:
            raise FileExistsError(
                f"Rename destination already exists: {destination_resolved.relative_path}"
            )

        self._ensure_parent_directories(destination_resolved.relative_path)
        destination_resolved = self.resolver.resolve(
            destination_resolved.relative_path,
            reject_any_link=True,
        )
        if destination_resolved.exists:
            raise FileMutationConflict("Rename destination appeared during operation.")
        source_resolved = self.resolver.resolve(
            source_resolved.relative_path,
            reject_any_link=True,
        )
        self._require_unchanged(source_resolved, source_snapshot)

        # Hard-link publication has no-overwrite semantics on both POSIX and Windows.
        # The two-name interval is reported as non-atomic instead of overstating safety.
        os.link(
            source_resolved.lexical_path,
            destination_resolved.lexical_path,
            follow_symlinks=False,
        )
        destination_after = self.resolver.resolve(
            destination_resolved.relative_path,
            reject_any_link=True,
        )
        after_snapshot = self._snapshot_file(destination_after)
        if after_snapshot.sha256 != source_snapshot.sha256:
            raise FileMutationConflict("Renamed file content changed during operation.")
        source_resolved.lexical_path.unlink()
        self._fsync_directory(source_resolved.lexical_path.parent)
        if destination_resolved.lexical_path.parent != source_resolved.lexical_path.parent:
            self._fsync_directory(destination_resolved.lexical_path.parent)
        source_after = self.resolver.resolve(
            source_resolved.relative_path,
            reject_any_link=True,
        )
        if source_after.exists:
            raise FileMutationConflict("Rename source still exists after operation.")
        destination_after = self.resolver.resolve(
            destination_resolved.relative_path,
            reject_any_link=True,
        )
        after_snapshot = self._snapshot_file(destination_after)
        if after_snapshot.sha256 != source_snapshot.sha256:
            raise FileMutationConflict("Renamed file content changed during operation.")
        return self._mutation_record(
            operation=FileMutationOperation.RENAME,
            resolved=destination_after,
            source_relative_path=source_resolved.relative_path,
            task_id=task_id,
            invocation_id=invocation_id,
            before=source_snapshot,
            after=after_snapshot,
            created=False,
            atomic=False,
        )

    def delete(
        self,
        path: str,
        *,
        task_id: str,
        invocation_id: str,
        expected_sha256: str,
    ) -> FileMutationRecord:
        self._validate_attribution(task_id, invocation_id)
        resolved = self.resolver.resolve(path, reject_any_link=True)
        snapshot = self._snapshot_file(resolved)
        self._require_expected_hash(snapshot, expected_sha256)
        self._require_unchanged(resolved, snapshot)
        resolved.lexical_path.unlink()
        self._fsync_directory(resolved.lexical_path.parent)
        after = self.resolver.resolve(
            resolved.relative_path,
            reject_any_link=True,
        )
        if after.exists:
            raise FileMutationConflict("Deleted path reappeared during operation.")
        return self._mutation_record(
            operation=FileMutationOperation.DELETE,
            resolved=resolved,
            source_relative_path=None,
            task_id=task_id,
            invocation_id=invocation_id,
            before=snapshot,
            after=None,
            created=False,
            atomic=False,
        )

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

    def _atomic_write(
        self,
        path: str,
        content: str | bytes,
        *,
        operation: FileMutationOperation,
        task_id: str,
        invocation_id: str,
        expected_sha256: str | None = None,
        require_absent: bool = False,
    ) -> FileMutationRecord:
        self._validate_attribution(task_id, invocation_id)
        self._validate_expected_hash(expected_sha256)
        encoded = self._encode_mutation_content(content)
        if len(encoded) > self.maximum_file_bytes:
            raise FileSizeLimitExceeded(
                "Mutation content exceeds the configured file-size limit."
            )
        resolved = self.resolver.resolve(path, reject_any_link=True)
        before = self._snapshot_file(resolved) if resolved.exists else None
        if require_absent and before is not None:
            raise FileExistsError(f"File already exists: {resolved.relative_path}")
        if before is None and expected_sha256 is not None:
            raise FileMutationConflict("Expected file is missing.")
        if before is not None:
            self._require_expected_hash(before, expected_sha256)

        self._ensure_parent_directories(resolved.relative_path)
        resolved = self.resolver.resolve(
            resolved.relative_path,
            reject_any_link=True,
        )
        if before is None and resolved.exists:
            raise FileMutationConflict("Mutation target appeared during operation.")
        if before is not None:
            self._require_unchanged(resolved, before)

        temporary: Path | None = None
        try:
            descriptor, temporary_name = tempfile.mkstemp(
                prefix=f".{resolved.lexical_path.name}.agentbus-",
                suffix=".tmp",
                dir=resolved.lexical_path.parent,
            )
            temporary = Path(temporary_name)
            try:
                handle = os.fdopen(descriptor, "wb")
            except BaseException:
                os.close(descriptor)
                raise
            with handle:
                handle.write(encoded)
                handle.flush()
                os.fsync(handle.fileno())
            if before is not None:
                os.chmod(temporary, stat_module.S_IMODE(before.mode))
                self._require_unchanged(resolved, before)
            else:
                absent = self.resolver.resolve(
                    resolved.relative_path,
                    reject_any_link=True,
                )
                if absent.exists:
                    raise FileMutationConflict(
                        "Mutation target appeared before atomic publication."
                    )
            self.resolver.validate_root_identity()
            if before is None:
                os.link(
                    temporary,
                    resolved.lexical_path,
                    follow_symlinks=False,
                )
                temporary.unlink()
            else:
                os.replace(temporary, resolved.lexical_path)
            temporary = None
            self._fsync_directory(resolved.lexical_path.parent)
        finally:
            if temporary is not None:
                try:
                    temporary.unlink()
                except FileNotFoundError:
                    pass

        after_resolved = self.resolver.resolve(
            resolved.relative_path,
            reject_any_link=True,
        )
        after = self._snapshot_file(after_resolved)
        expected_after_hash = hashlib.sha256(encoded).hexdigest()
        if after.sha256 != expected_after_hash or after.size_bytes != len(encoded):
            raise FileMutationConflict("Atomic write verification failed.")
        return self._mutation_record(
            operation=operation,
            resolved=after_resolved,
            source_relative_path=None,
            task_id=task_id,
            invocation_id=invocation_id,
            before=before,
            after=after,
            created=before is None,
            atomic=True,
        )

    def _ensure_parent_directories(self, relative_path: str) -> None:
        parent_parts = PurePosixPath(relative_path).parent.parts
        current_parts: list[str] = []
        for part in parent_parts:
            current_parts.append(part)
            label = PurePosixPath(*current_parts).as_posix()
            resolved = self.resolver.resolve(label, reject_any_link=True)
            if not resolved.exists:
                try:
                    resolved.lexical_path.mkdir()
                except FileExistsError:
                    pass
                resolved = self.resolver.resolve(label, reject_any_link=True)
            if not resolved.exists or not resolved.path.is_dir():
                raise NotADirectoryError(f"Not a directory: {resolved.relative_path}")
            self._confirm_resolution(resolved.relative_path, resolved.path)
        self.resolver.validate_root_identity()

    def _read_mutation_file(
        self,
        resolved: ResolvedFileSystemPath,
    ) -> tuple[bytes, _FileSnapshot]:
        if not resolved.exists:
            raise FileNotFoundError(f"File not found: {resolved.relative_path}")
        try:
            before_stat = resolved.lexical_path.stat()
        except OSError as exc:
            raise FileMutationConflict("Mutation target cannot be inspected.") from exc
        if not stat_module.S_ISREG(before_stat.st_mode):
            raise IsADirectoryError(f"Not a file: {resolved.relative_path}")
        if before_stat.st_size > self.maximum_file_bytes:
            raise FileSizeLimitExceeded(
                "Existing file exceeds the configured file-size limit."
            )
        with resolved.lexical_path.open("rb") as handle:
            content = handle.read(self.maximum_file_bytes + 1)
        if len(content) > self.maximum_file_bytes:
            raise FileSizeLimitExceeded(
                "Existing file exceeds the configured file-size limit."
            )
        after_stat = resolved.lexical_path.stat()
        before_identity = _stat_identity(before_stat)
        if before_identity != _stat_identity(after_stat):
            raise FileMutationConflict("Mutation target changed during inspection.")
        confirmed = self.resolver.resolve(
            resolved.relative_path,
            reject_any_link=True,
        )
        if confirmed.lexical_path != resolved.lexical_path:
            raise FileMutationConflict("Mutation target path changed during inspection.")
        return content, _FileSnapshot(
            identity=before_identity,
            sha256=hashlib.sha256(content).hexdigest(),
            size_bytes=len(content),
            mode=before_stat.st_mode,
        )

    def _snapshot_file(self, resolved: ResolvedFileSystemPath) -> _FileSnapshot:
        _, snapshot = self._read_mutation_file(resolved)
        return snapshot

    def _require_unchanged(
        self,
        resolved: ResolvedFileSystemPath,
        expected: _FileSnapshot,
    ) -> None:
        current = self._snapshot_file(resolved)
        if current != expected:
            raise FileMutationConflict("Mutation target changed during operation.")

    @staticmethod
    def _require_expected_hash(
        snapshot: _FileSnapshot,
        expected_sha256: str | None,
    ) -> None:
        ContainedFileSystem._validate_expected_hash(expected_sha256)
        if expected_sha256 is not None and snapshot.sha256 != expected_sha256:
            raise FileMutationConflict("Mutation target hash no longer matches.")

    @staticmethod
    def _validate_expected_hash(expected_sha256: str | None) -> None:
        if expected_sha256 is None:
            return
        if len(expected_sha256) != 64 or any(
            character not in "0123456789abcdef" for character in expected_sha256
        ):
            raise ValueError("expected_sha256 must be a lowercase SHA-256 digest")

    @staticmethod
    def _encode_mutation_content(content: str | bytes) -> bytes:
        if isinstance(content, str):
            return content.encode("utf-8")
        if isinstance(content, bytes):
            return content
        raise TypeError("Mutation content must be text or bytes.")

    @staticmethod
    def _validate_attribution(task_id: str, invocation_id: str) -> None:
        for name, value in (("task_id", task_id), ("invocation_id", invocation_id)):
            if (
                not isinstance(value, str)
                or not value
                or len(value) > 128
                or any(ord(char) < 32 for char in value)
            ):
                raise ValueError(f"{name} must be a safe non-empty identifier")

    @staticmethod
    def _mutation_record(
        *,
        operation: FileMutationOperation,
        resolved: ResolvedFileSystemPath,
        source_relative_path: str | None,
        task_id: str,
        invocation_id: str,
        before: _FileSnapshot | None,
        after: _FileSnapshot | None,
        created: bool,
        atomic: bool,
    ) -> FileMutationRecord:
        return FileMutationRecord(
            operation=operation,
            relative_path=resolved.relative_path,
            source_relative_path=source_relative_path,
            task_id=task_id,
            invocation_id=invocation_id,
            before_sha256=before.sha256 if before is not None else None,
            after_sha256=after.sha256 if after is not None else None,
            bytes_before=before.size_bytes if before is not None else 0,
            bytes_after=after.size_bytes if after is not None else 0,
            created=created,
            generated=resolved.classification.generated,
            atomic=atomic,
            timestamp=datetime.now(timezone.utc),
        )

    @staticmethod
    def _fsync_directory(directory: Path) -> None:
        if os.name == "nt":
            return
        descriptor = os.open(directory, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

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


def _stat_identity(stat: os.stat_result) -> tuple[int, int, int, int, int, int]:
    return (
        stat.st_dev,
        stat.st_ino,
        stat.st_mode,
        stat.st_size,
        stat.st_mtime_ns,
        stat.st_ctime_ns,
    )
