from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

from agentbus.intelligence.discovery.ignore import GitIgnoreMatcher
from agentbus.intelligence.discovery.models import (
    DiscoveredFile,
    DiscoveryLimits,
)
from agentbus.intelligence.errors import UnsafeRepositoryPathError
from agentbus.intelligence.identities import stable_hash
from agentbus.intelligence.models import (
    DiagnosticSeverity,
    IndexDiagnostic,
    _relative_path,
)
from agentbus.repo.artifact_policy import GeneratedArtifactPolicy
from agentbus.tools.filesystem_security import (
    ContainedPathResolver,
    FileSystemContainmentError,
    FileSystemSecurityError,
    ProtectedFileSystemPath,
)


_ALWAYS_IGNORED_DIRECTORIES = {
    ".git",
    ".hg",
    ".svn",
}
_VENDORED_DIRECTORIES = {
    "third_party",
    "vendor",
    "vendors",
}
_TEST_DIRECTORY_NAMES = {
    "__tests__",
    "integration_tests",
    "test",
    "tests",
}


@dataclass
class _ScanState:
    files: list[DiscoveredFile] = field(default_factory=list)
    generated_roots: set[str] = field(default_factory=set)
    vendored_roots: set[str] = field(default_factory=set)
    diagnostics: list[IndexDiagnostic] = field(default_factory=list)
    ignored_count: int = 0
    encountered: int = 0
    truncated: bool = False


class RepositoryInventory:
    """In-memory, contained reader over a portable discovery inventory."""

    def __init__(
        self,
        root: Path,
        resolver: ContainedPathResolver,
        files: tuple[DiscoveredFile, ...],
        generated_roots: tuple[str, ...],
        vendored_roots: tuple[str, ...],
        diagnostics: tuple[IndexDiagnostic, ...],
        ignored_count: int,
        truncated: bool,
        fingerprint: str,
        limits: DiscoveryLimits,
    ) -> None:
        self.root = root
        self.resolver = resolver
        self.files = files
        self.generated_roots = generated_roots
        self.vendored_roots = vendored_roots
        self.diagnostics = diagnostics
        self.ignored_count = ignored_count
        self.truncated = truncated
        self.fingerprint = fingerprint
        self.limits = limits
        self._file_paths = {item.relative_path for item in files}

    def contains(self, relative_path: str) -> bool:
        try:
            normalized = _relative_path(relative_path)
        except (TypeError, ValueError):
            return False
        return normalized in self._file_paths

    def matching_names(self, names: set[str]) -> tuple[str, ...]:
        return tuple(
            item.relative_path
            for item in self.files
            if PurePosixPath(item.relative_path).name in names
        )

    def read_bytes(
        self,
        relative_path: str,
        *,
        maximum_bytes: int | None = None,
    ) -> bytes:
        try:
            normalized = _relative_path(relative_path)
        except (TypeError, ValueError) as exc:
            raise UnsafeRepositoryPathError(
                "Path is outside the discovered repository inventory."
            ) from exc
        if normalized not in self._file_paths:
            raise UnsafeRepositoryPathError(
                f"Path is outside the discovered inventory: {normalized}."
            )
        limit = (
            self.limits.maximum_metadata_bytes
            if maximum_bytes is None
            else maximum_bytes
        )
        if limit < 1 or limit > self.limits.maximum_file_bytes:
            raise ValueError("metadata read limit is outside discovery bounds")
        try:
            resolved = self.resolver.resolve(
                normalized,
                reject_any_link=True,
            )
            size = resolved.lexical_path.stat().st_size
            if size > limit:
                raise UnsafeRepositoryPathError(
                    f"Repository metadata exceeds the {limit}-byte limit: "
                    f"{normalized}."
                )
            with resolved.lexical_path.open("rb") as handle:
                payload = handle.read(limit + 1)
        except (OSError, FileSystemSecurityError) as exc:
            raise UnsafeRepositoryPathError(
                f"Unable to read repository metadata safely: {normalized}."
            ) from exc
        if len(payload) > limit:
            raise UnsafeRepositoryPathError(
                f"Repository metadata exceeds the {limit}-byte limit: "
                f"{normalized}."
            )
        return payload

    def read_text(
        self,
        relative_path: str,
        *,
        maximum_bytes: int | None = None,
    ) -> str:
        payload = self.read_bytes(relative_path, maximum_bytes=maximum_bytes)
        if b"\x00" in payload:
            raise UnsafeRepositoryPathError(
                f"Repository metadata is binary: {relative_path}."
            )
        try:
            return payload.decode("utf-8-sig")
        except UnicodeDecodeError as exc:
            raise UnsafeRepositoryPathError(
                f"Repository metadata is not UTF-8: {relative_path}."
            ) from exc


class RepositoryInventoryScanner:
    def __init__(
        self,
        workspace: str | Path,
        *,
        limits: DiscoveryLimits | None = None,
    ) -> None:
        self.limits = limits or DiscoveryLimits()
        try:
            self.resolver = ContainedPathResolver(workspace)
        except FileSystemSecurityError as exc:
            raise UnsafeRepositoryPathError(
                "Repository discovery root is unavailable or unsafe."
            ) from exc
        self.root = self.resolver.root
        self._artifacts = GeneratedArtifactPolicy()

    def scan(self) -> RepositoryInventory:
        state = _ScanState()
        self._walk(
            "",
            GitIgnoreMatcher(),
            depth=0,
            state=state,
        )
        files = tuple(sorted(state.files, key=lambda item: item.relative_path))
        generated_roots = tuple(sorted(state.generated_roots))
        vendored_roots = tuple(sorted(state.vendored_roots))
        diagnostics = tuple(state.diagnostics)
        fingerprint = stable_hash(
            {
                "files": [
                    {
                        "path": item.relative_path,
                        "size": item.size_bytes,
                        "test": item.test,
                    }
                    for item in files
                ],
                "generated_roots": generated_roots,
                "vendored_roots": vendored_roots,
                "ignored_count": state.ignored_count,
                "truncated": state.truncated,
            }
        )
        return RepositoryInventory(
            self.root,
            self.resolver,
            files,
            generated_roots,
            vendored_roots,
            diagnostics,
            state.ignored_count,
            state.truncated,
            fingerprint,
            self.limits,
        )

    def _walk(
        self,
        relative_directory: str,
        matcher: GitIgnoreMatcher,
        *,
        depth: int,
        state: _ScanState,
    ) -> None:
        if state.truncated:
            return
        if depth > self.limits.maximum_depth:
            state.truncated = True
            self._diagnostic(
                state,
                "discovery.depth_limit",
                DiagnosticSeverity.WARNING,
                "Repository discovery stopped at the configured depth limit.",
                relative_directory or None,
            )
            return
        try:
            directory = (
                self.root
                if not relative_directory
                else self.resolver.resolve(
                    relative_directory,
                    reject_any_link=True,
                ).lexical_path
            )
        except FileSystemSecurityError:
            self._diagnostic(
                state,
                "discovery.directory_changed",
                DiagnosticSeverity.WARNING,
                "Repository directory changed during contained discovery.",
                relative_directory or None,
            )
            return
        try:
            with os.scandir(directory) as iterator:
                entries = sorted(
                    iterator,
                    key=lambda item: (item.name.casefold(), item.name),
                )
        except OSError:
            self._diagnostic(
                state,
                "discovery.directory_unreadable",
                DiagnosticSeverity.WARNING,
                "Repository directory could not be read safely.",
                relative_directory or None,
            )
            return

        local_matcher = self._extend_matcher(
            relative_directory,
            entries,
            matcher,
            state,
        )
        for entry in entries:
            if state.truncated:
                return
            state.encountered += 1
            if state.encountered > self.limits.maximum_entries:
                state.truncated = True
                self._diagnostic(
                    state,
                    "discovery.entry_limit",
                    DiagnosticSeverity.WARNING,
                    "Repository discovery stopped at the configured entry limit.",
                )
                return
            relative_path = _join(relative_directory, entry.name)
            if _is_link(entry):
                state.ignored_count += 1
                self._diagnostic(
                    state,
                    "discovery.link_rejected",
                    DiagnosticSeverity.WARNING,
                    "Symbolic links and junctions are excluded from discovery.",
                    relative_path,
                )
                continue
            try:
                is_directory = entry.is_dir(follow_symlinks=False)
                is_file = entry.is_file(follow_symlinks=False)
            except OSError:
                state.ignored_count += 1
                continue
            if not is_directory and not is_file:
                state.ignored_count += 1
                continue
            if is_directory and entry.name in _ALWAYS_IGNORED_DIRECTORIES:
                state.ignored_count += 1
                continue
            if local_matcher.is_ignored(
                relative_path,
                is_directory=is_directory,
            ):
                state.ignored_count += 1
                continue
            try:
                resolved = self.resolver.resolve(
                    relative_path,
                    reject_any_link=True,
                )
            except ProtectedFileSystemPath:
                state.ignored_count += 1
                self._diagnostic(
                    state,
                    "discovery.protected_path",
                    DiagnosticSeverity.INFO,
                    "Protected repository path was excluded from discovery.",
                    relative_path,
                )
                continue
            except (FileSystemContainmentError, FileSystemSecurityError):
                state.ignored_count += 1
                self._diagnostic(
                    state,
                    "discovery.path_rejected",
                    DiagnosticSeverity.WARNING,
                    "Repository path failed containment validation.",
                    relative_path,
                )
                continue
            if is_directory:
                category = self._directory_category(relative_path)
                if category == "generated":
                    state.generated_roots.add(relative_path)
                    state.ignored_count += 1
                    continue
                if category == "vendored":
                    state.vendored_roots.add(relative_path)
                    state.ignored_count += 1
                    continue
                self._walk(
                    relative_path,
                    local_matcher,
                    depth=depth + 1,
                    state=state,
                )
                continue
            try:
                size = resolved.lexical_path.stat().st_size
            except OSError:
                state.ignored_count += 1
                continue
            if size > self.limits.maximum_file_bytes:
                state.ignored_count += 1
                self._diagnostic(
                    state,
                    "discovery.file_too_large",
                    DiagnosticSeverity.WARNING,
                    "Repository file exceeds the configured indexing limit.",
                    relative_path,
                )
                continue
            state.files.append(
                DiscoveredFile(
                    relative_path=relative_path,
                    size_bytes=size,
                    test=_is_test_path(relative_path),
                )
            )

    def _extend_matcher(
        self,
        relative_directory: str,
        entries: list[os.DirEntry[str]],
        matcher: GitIgnoreMatcher,
        state: _ScanState,
    ) -> GitIgnoreMatcher:
        entry = next((item for item in entries if item.name == ".gitignore"), None)
        if entry is None or _is_link(entry):
            return matcher
        try:
            if not entry.is_file(follow_symlinks=False):
                return matcher
            relative_path = _join(relative_directory, entry.name)
            resolved = self.resolver.resolve(
                relative_path,
                reject_any_link=True,
            )
            limit = self.limits.maximum_metadata_bytes
            if resolved.lexical_path.stat().st_size > limit:
                raise OSError
            with resolved.lexical_path.open("rb") as handle:
                payload = handle.read(limit + 1)
            if len(payload) > limit or b"\x00" in payload:
                raise OSError
            content = payload.decode("utf-8-sig", errors="strict")
        except (OSError, UnicodeError, FileSystemSecurityError):
            self._diagnostic(
                state,
                "discovery.gitignore_unreadable",
                DiagnosticSeverity.WARNING,
                "Git ignore rules could not be read safely.",
                _join(relative_directory, ".gitignore"),
            )
            return matcher
        return matcher.extend(relative_directory, content)

    def _directory_category(self, relative_path: str) -> str | None:
        name = PurePosixPath(relative_path).name.casefold()
        if name in _VENDORED_DIRECTORIES:
            return "vendored"
        if self._artifacts.is_generated(relative_path):
            return "generated"
        return None

    def _diagnostic(
        self,
        state: _ScanState,
        code: str,
        severity: DiagnosticSeverity,
        message: str,
        relative_path: str | None = None,
    ) -> None:
        if len(state.diagnostics) >= self.limits.maximum_diagnostics:
            return
        state.diagnostics.append(
            IndexDiagnostic(
                code=code,
                severity=severity,
                message=message,
                relative_path=relative_path,
            )
        )


def _join(parent: str, name: str) -> str:
    return (
        PurePosixPath(parent, name).as_posix()
        if parent
        else PurePosixPath(name).as_posix()
    )


def _is_link(entry: os.DirEntry[str]) -> bool:
    try:
        if entry.is_symlink():
            return True
        path = Path(entry.path)
        checker = getattr(path, "is_junction", None)
        return bool(checker and checker())
    except OSError:
        return True


def _is_test_path(relative_path: str) -> bool:
    path = PurePosixPath(relative_path)
    name = path.name.casefold()
    directories = {part.casefold() for part in path.parts[:-1]}
    return bool(
        directories & _TEST_DIRECTORY_NAMES
        or name.startswith("test_")
        or name.endswith(
            (
                ".spec.js",
                ".spec.jsx",
                ".spec.ts",
                ".spec.tsx",
                ".test.js",
                ".test.jsx",
                ".test.ts",
                ".test.tsx",
                "_test.go",
                "_test.py",
            )
        )
    )
