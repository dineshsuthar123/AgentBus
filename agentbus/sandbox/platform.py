from __future__ import annotations

import hashlib
import os
import re
import shutil
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from agentbus.sandbox.errors import (
    ExecutableValidationError,
    WorkingDirectoryValidationError,
)


ExecutableCommand = str | Path | Sequence[str | Path]
_ALIAS_PATTERN = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")


@dataclass(frozen=True)
class ExecutableIdentity:
    alias: str
    path: Path
    argument_prefix: tuple[str, ...]
    sha256: str
    device: int
    inode: int
    size_bytes: int
    modified_ns: int

    def command(self, arguments: Sequence[str]) -> list[str]:
        return [str(self.path), *self.argument_prefix, *map(str, arguments)]

    def safe_metadata(self) -> dict[str, object]:
        return {
            "alias": self.alias,
            "path": str(self.path),
            "sha256": self.sha256,
            "size_bytes": self.size_bytes,
        }


class ExecutableCatalog:
    """Resolve allowlisted executable aliases once and detect later substitution."""

    def __init__(
        self,
        commands: Mapping[str, ExecutableCommand],
        *,
        search_path: str | None = None,
    ) -> None:
        if not commands:
            raise ExecutableValidationError(
                "At least one executable must be explicitly allowlisted."
            )
        self._identities: dict[str, ExecutableIdentity] = {}
        self._absolute_paths: dict[str, list[ExecutableIdentity]] = {}
        for raw_alias, raw_command in commands.items():
            alias = _normalize_alias(raw_alias)
            if alias in self._identities:
                raise ExecutableValidationError(
                    f"Duplicate executable alias: {raw_alias}."
                )
            command = _command_parts(raw_command)
            path = _resolve_executable(command[0], search_path=search_path)
            identity = _capture_identity(
                alias=alias,
                path=path,
                argument_prefix=command[1:],
            )
            self._identities[alias] = identity
            path_key = _path_key(path)
            self._absolute_paths.setdefault(path_key, []).append(identity)

    @classmethod
    def standard(
        cls,
        aliases: Sequence[str] = ("python", "pytest", "git"),
        *,
        search_path: str | None = None,
    ) -> "ExecutableCatalog":
        commands: dict[str, ExecutableCommand] = {}
        for raw_alias in aliases:
            alias = _normalize_alias(raw_alias)
            if alias in {"python", "python3"}:
                commands[alias] = (sys.executable,)
            elif alias == "pytest":
                commands[alias] = (sys.executable, "-m", "pytest")
            else:
                commands[alias] = (raw_alias,)
        return cls(commands, search_path=search_path)

    @property
    def executable_directories(self) -> tuple[Path, ...]:
        directories: list[Path] = []
        seen: set[str] = set()
        for identity in self._identities.values():
            key = _path_key(identity.path.parent)
            if key not in seen:
                directories.append(identity.path.parent)
                seen.add(key)
        return tuple(directories)

    @property
    def aliases(self) -> tuple[str, ...]:
        return tuple(sorted(self._identities))

    def resolve(self, requested: str | Path) -> ExecutableIdentity:
        raw = str(requested).strip()
        if not raw or "\x00" in raw:
            raise ExecutableValidationError("Executable name must be non-empty and NUL-free.")

        identity = None
        if not Path(raw).is_absolute() and not any(
            separator in raw for separator in ("/", "\\")
        ):
            identity = self._identities.get(_normalize_alias(raw))
        if identity is None and Path(raw).is_absolute():
            try:
                requested_path = Path(raw).expanduser().resolve(strict=True)
            except (OSError, RuntimeError) as exc:
                raise ExecutableValidationError(
                    f"Allowlisted executable is unavailable: {raw}."
                ) from exc
            matches = self._absolute_paths.get(_path_key(requested_path), [])
            if len(matches) > 1:
                raise ExecutableValidationError(
                    "Executable path is ambiguous; use its configured alias."
                )
            identity = matches[0] if matches else None
        if identity is None:
            raise ExecutableValidationError(
                f"Executable is not explicitly allowlisted: {raw}."
            )
        _revalidate_identity(identity)
        return identity


def validate_working_directory(
    worktree: str | Path,
    requested: str | Path | None = None,
) -> Path:
    root = _resolve_directory(worktree, description="Assigned worktree")
    if requested is None:
        return root

    raw = str(requested).strip()
    if not raw or "\x00" in raw:
        raise WorkingDirectoryValidationError(
            "Working directory must be non-empty and NUL-free."
        )
    _reject_special_path(raw)
    candidate = Path(raw).expanduser()
    if not candidate.is_absolute():
        candidate = root / candidate
    resolved = _resolve_directory(candidate, description="Working directory")
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise WorkingDirectoryValidationError(
            "Working directory must remain inside the assigned worktree."
        ) from exc
    return resolved


def _command_parts(command: ExecutableCommand) -> tuple[str, ...]:
    if isinstance(command, (str, Path)):
        parts = (str(command),)
    else:
        parts = tuple(str(part) for part in command)
    if not parts or any(not part or "\x00" in part for part in parts):
        raise ExecutableValidationError(
            "Executable commands must contain non-empty, NUL-free arguments."
        )
    return parts


def _resolve_executable(requested: str, *, search_path: str | None) -> Path:
    candidate = Path(requested).expanduser()
    if candidate.is_absolute():
        located = str(candidate)
    else:
        located = shutil.which(requested, path=search_path)
        if located is None:
            raise ExecutableValidationError(
                f"Allowlisted executable could not be resolved: {requested}."
            )
    try:
        path = Path(located).resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise ExecutableValidationError(
            f"Allowlisted executable is unavailable: {requested}."
        ) from exc
    if not path.is_file():
        raise ExecutableValidationError(
            f"Allowlisted executable is not a regular file: {requested}."
        )
    return path


def _capture_identity(
    *,
    alias: str,
    path: Path,
    argument_prefix: Sequence[str],
) -> ExecutableIdentity:
    try:
        stat = path.stat()
        digest = _sha256_file(path)
    except OSError as exc:
        raise ExecutableValidationError(
            f"Could not inspect allowlisted executable: {alias}."
        ) from exc
    return ExecutableIdentity(
        alias=alias,
        path=path,
        argument_prefix=tuple(argument_prefix),
        sha256=digest,
        device=stat.st_dev,
        inode=stat.st_ino,
        size_bytes=stat.st_size,
        modified_ns=stat.st_mtime_ns,
    )


def _revalidate_identity(identity: ExecutableIdentity) -> None:
    try:
        canonical = identity.path.resolve(strict=True)
        stat = canonical.stat()
        digest = _sha256_file(canonical)
    except OSError as exc:
        raise ExecutableValidationError(
            f"Allowlisted executable is no longer available: {identity.alias}."
        ) from exc
    current = (
        _path_key(canonical),
        stat.st_dev,
        stat.st_ino,
        stat.st_size,
        stat.st_mtime_ns,
        digest,
    )
    expected = (
        _path_key(identity.path),
        identity.device,
        identity.inode,
        identity.size_bytes,
        identity.modified_ns,
        identity.sha256,
    )
    if current != expected:
        raise ExecutableValidationError(
            f"Allowlisted executable identity changed after registration: {identity.alias}."
        )


def _resolve_directory(path: str | Path, *, description: str) -> Path:
    try:
        resolved = Path(path).expanduser().resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise WorkingDirectoryValidationError(
            f"{description} does not exist or cannot be resolved."
        ) from exc
    if not resolved.is_dir():
        raise WorkingDirectoryValidationError(f"{description} must be a directory.")
    return resolved


def _reject_special_path(raw: str) -> None:
    normalized = raw.replace("/", "\\")
    if normalized.startswith(("\\\\", "\\?\\", "\\.\\")):
        raise WorkingDirectoryValidationError(
            "UNC and Windows device working directories are not supported."
        )
    _, tail = os.path.splitdrive(normalized)
    if ":" in tail:
        raise WorkingDirectoryValidationError(
            "Windows alternate data stream paths are not supported."
        )


def _normalize_alias(value: str | Path) -> str:
    raw = str(value).strip().lower()
    if not raw or "\x00" in raw:
        raise ExecutableValidationError("Executable aliases must be non-empty and NUL-free.")
    for suffix in (".exe", ".cmd", ".bat"):
        if raw.endswith(suffix):
            raw = raw[: -len(suffix)]
            break
    if not _ALIAS_PATTERN.fullmatch(raw):
        raise ExecutableValidationError(
            "Executable aliases must use lowercase letters, digits, dots, dashes, or underscores."
        )
    return raw


def _path_key(path: Path) -> str:
    return os.path.normcase(str(path))


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
