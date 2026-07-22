from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from agentbus.policy.defaults import DEFAULT_TOOL_POLICY
from agentbus.repo.artifact_policy import (
    ArtifactCategory,
    GeneratedArtifactPolicy,
)


_WINDOWS_ABSOLUTE_PATTERN = re.compile(r"^[A-Za-z]:[/\\]")
_WINDOWS_DEVICE_NAMES = re.compile(
    r"(?i)^(?:con|prn|aux|nul|clock\$|com[1-9]|lpt[1-9])(?:\..*)?$"
)
_PROTECTED_SEGMENTS = frozenset(
    {
        ".agentbus",
        ".aws",
        ".azure",
        ".codex",
        ".docker",
        ".git",
        ".kube",
        ".ssh",
    }
)
_ADDITIONAL_PROTECTED_NAMES = frozenset(
    {
        ".npmrc",
        "application_default_credentials.json",
        "secrets.json",
    }
)


class FileSystemSecurityError(ValueError):
    """Base error for filesystem boundary violations."""


class UnsafeFileSystemPath(FileSystemSecurityError):
    """Raised when path syntax is ambiguous, special, or traversal-based."""


class FileSystemContainmentError(FileSystemSecurityError):
    """Raised when a path or link resolves outside the assigned root."""


class ProtectedFileSystemPath(FileSystemSecurityError):
    """Raised when a tool attempts to access a credential or control-plane path."""


class FileSystemRootChanged(FileSystemSecurityError):
    """Raised when the assigned root identity changes after construction."""


@dataclass(frozen=True)
class FileSystemPathClassification:
    protected: bool
    protected_reason: str | None
    generated: bool
    generated_reason: str | None


@dataclass(frozen=True)
class ResolvedFileSystemPath:
    root: Path
    relative_path: str
    path: Path
    lexical_path: Path
    exists: bool
    final_component_is_link: bool
    classification: FileSystemPathClassification


class ContainedPathResolver:
    def __init__(
        self,
        root: str | Path,
        *,
        create_root: bool = False,
    ) -> None:
        candidate = Path(root).expanduser()
        if create_root:
            candidate.mkdir(parents=True, exist_ok=True)
        try:
            self.root = candidate.resolve(strict=True)
        except (OSError, RuntimeError) as exc:
            raise FileSystemSecurityError(
                "Assigned filesystem root does not exist or cannot be resolved."
            ) from exc
        if not self.root.is_dir():
            raise FileSystemSecurityError("Assigned filesystem root must be a directory.")
        stat = self.root.stat()
        self._root_identity = (stat.st_dev, stat.st_ino)
        self._artifacts = GeneratedArtifactPolicy()

    def resolve(
        self,
        path: str,
        *,
        allow_protected: bool = False,
        reject_final_link: bool = False,
    ) -> ResolvedFileSystemPath:
        self.validate_root_identity()
        normalized = normalize_relative_tool_path(path)
        classification = self.classify(normalized)
        if classification.protected and not allow_protected:
            raise ProtectedFileSystemPath(
                classification.protected_reason or "Protected path access is denied."
            )

        lexical = self.root.joinpath(*PurePosixPath(normalized).parts)
        final_is_link = False
        current = self.root
        parts = PurePosixPath(normalized).parts
        for index, part in enumerate(parts):
            current = current / part
            if not os.path.lexists(current):
                continue
            is_link = current.is_symlink() or _is_junction(current)
            if index == len(parts) - 1:
                final_is_link = is_link
            if is_link:
                linked_target = _resolve_existing(current)
                _require_contained(linked_target, self.root)

        resolved = lexical.resolve(strict=False)
        _require_contained(resolved, self.root)
        if reject_final_link and final_is_link:
            raise FileSystemContainmentError(
                "Mutating a symlink or junction target is not supported."
            )
        self.validate_root_identity()
        return ResolvedFileSystemPath(
            root=self.root,
            relative_path=normalized,
            path=resolved,
            lexical_path=lexical,
            exists=os.path.lexists(lexical),
            final_component_is_link=final_is_link,
            classification=classification,
        )

    def classify(self, normalized_path: str) -> FileSystemPathClassification:
        parts = tuple(part.lower() for part in PurePosixPath(normalized_path).parts)
        name = parts[-1]
        protected_names = {
            item.lower() for item in DEFAULT_TOOL_POLICY.protected_file_names
        } | _ADDITIONAL_PROTECTED_NAMES
        protected_suffixes = tuple(
            suffix.lower() for suffix in DEFAULT_TOOL_POLICY.protected_suffixes
        )
        protected_reason: str | None = None
        if any(part in _PROTECTED_SEGMENTS for part in parts):
            segment = next(part for part in parts if part in _PROTECTED_SEGMENTS)
            protected_reason = f"Path is under protected directory '{segment}'."
        elif name in protected_names:
            protected_reason = f"Protected file access is denied: {name}."
        elif name.startswith(".env.") and name not in {
            ".env.example",
            ".env.sample",
            ".env.template",
        }:
            protected_reason = f"Environment credential file is denied: {name}."
        elif name.endswith((".sqlite", ".sqlite3")) or name.endswith("state.db"):
            protected_reason = f"Control-plane database path is denied: {name}."
        elif any(name.endswith(suffix) for suffix in protected_suffixes):
            protected_reason = f"Protected credential suffix is denied: {name}."

        artifact = self._artifacts.classify(normalized_path)
        return FileSystemPathClassification(
            protected=protected_reason is not None,
            protected_reason=protected_reason,
            generated=artifact.category == ArtifactCategory.GENERATED,
            generated_reason=(
                artifact.reason
                if artifact.category == ArtifactCategory.GENERATED
                else None
            ),
        )

    def validate_root_identity(self) -> None:
        try:
            canonical = self.root.resolve(strict=True)
            stat = canonical.stat()
        except (OSError, RuntimeError) as exc:
            raise FileSystemRootChanged(
                "Assigned filesystem root is no longer available."
            ) from exc
        if canonical != self.root or (stat.st_dev, stat.st_ino) != self._root_identity:
            raise FileSystemRootChanged(
                "Assigned filesystem root identity changed after initialization."
            )


def normalize_relative_tool_path(path: str) -> str:
    if not isinstance(path, str):
        raise UnsafeFileSystemPath("Filesystem path must be a string.")
    raw = path
    if raw != raw.strip():
        raise UnsafeFileSystemPath(
            "Filesystem paths cannot have leading or trailing whitespace."
        )
    if not raw or "\x00" in raw or len(raw) > 2_048:
        raise UnsafeFileSystemPath(
            "Filesystem path must be non-empty, NUL-free, and at most 2048 characters."
        )
    if raw.startswith(("/", "\\", "//")) or _WINDOWS_ABSOLUTE_PATTERN.match(raw):
        raise UnsafeFileSystemPath("Absolute, UNC, and device paths are not supported.")
    normalized = raw.replace("\\", "/")
    while normalized.startswith("./"):
        normalized = normalized[2:]
    parts = normalized.split("/")
    if not parts or any(part in {"", ".", ".."} for part in parts):
        raise UnsafeFileSystemPath(
            "Filesystem path is not canonical or attempts traversal."
        )
    for part in parts:
        if part.endswith((" ", ".")):
            raise UnsafeFileSystemPath(
                "Filesystem path components cannot end with spaces or dots."
            )
        if ":" in part:
            raise UnsafeFileSystemPath(
                "Windows alternate data stream syntax is not supported."
            )
        device_candidate = part.rstrip(" .")
        if _WINDOWS_DEVICE_NAMES.match(device_candidate):
            raise UnsafeFileSystemPath(
                f"Windows device path component is not supported: {part}."
            )
    return PurePosixPath(*parts).as_posix()


def _resolve_existing(path: Path) -> Path:
    try:
        return path.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise FileSystemContainmentError(
            "Filesystem link target cannot be resolved safely."
        ) from exc


def _require_contained(path: Path, root: Path) -> None:
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise FileSystemContainmentError(
            "Filesystem path resolves outside the assigned worktree."
        ) from exc


def _is_junction(path: Path) -> bool:
    checker = getattr(path, "is_junction", None)
    if checker is None:
        return False
    try:
        return bool(checker())
    except OSError:
        return False
