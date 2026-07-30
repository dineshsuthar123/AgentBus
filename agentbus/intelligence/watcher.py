from __future__ import annotations

import math
import threading
import time
from dataclasses import dataclass
from enum import Enum
from pathlib import Path, PurePosixPath
from typing import Callable

from agentbus.intelligence.indexer import IndexingResult, RepositoryIndexer
from agentbus.intelligence.models import _relative_path
from agentbus.intelligence.parsers import CancellationSignal
from agentbus.repo.artifact_policy import GeneratedArtifactPolicy
from agentbus.tools.filesystem_security import (
    ContainedPathResolver,
    FileSystemSecurityError,
    ProtectedFileSystemPath,
)


class FileChangeKind(str, Enum):
    CREATED = "created"
    MODIFIED = "modified"
    DELETED = "deleted"


@dataclass(frozen=True)
class WatchLimits:
    debounce_seconds: float = 0.25
    maximum_pending_paths: int = 10_000

    def __post_init__(self) -> None:
        if (
            not math.isfinite(self.debounce_seconds)
            or self.debounce_seconds < 0
            or self.debounce_seconds > 60
        ):
            raise ValueError(
                "debounce_seconds must be between 0 and 60"
            )
        if (
            self.maximum_pending_paths < 1
            or self.maximum_pending_paths > 100_000
        ):
            raise ValueError(
                "maximum_pending_paths must be between 1 and 100000"
            )


@dataclass(frozen=True)
class WatchBatch:
    changed_paths: tuple[str, ...]
    full_rescan_required: bool = False
    overflowed: bool = False
    paused: bool = False
    reason: str | None = None


@dataclass(frozen=True)
class WatchUpdate:
    batch: WatchBatch
    indexing_result: IndexingResult | None = None


class RepositoryChangeBuffer:
    """Coalesce contained watcher events without trusting watcher paths."""

    def __init__(
        self,
        workspace: str | Path,
        *,
        limits: WatchLimits | None = None,
        monotonic: Callable[[], float] | None = None,
    ) -> None:
        self.resolver = ContainedPathResolver(workspace)
        self.workspace = self.resolver.root
        self.limits = limits or WatchLimits()
        self.monotonic = monotonic or time.monotonic
        self._artifacts = GeneratedArtifactPolicy()
        self._lock = threading.RLock()
        self._pending: dict[str, FileChangeKind] = {}
        self._transition_paths: set[str] = set()
        self._last_event_at: float | None = None
        self._full_rescan_required = False
        self._overflowed = False
        self._closed = False

    def observe(
        self,
        path: str | Path,
        kind: FileChangeKind = FileChangeKind.MODIFIED,
    ) -> bool:
        normalized = self._contained_relative_path(path)
        if normalized is None:
            return False
        change_kind = FileChangeKind(kind)
        with self._lock:
            if self._closed:
                return False
            now = self.monotonic()
            transition_key = _git_transition_key(normalized)
            if transition_key is not None:
                if change_kind == FileChangeKind.DELETED:
                    self._transition_paths.discard(transition_key)
                else:
                    self._transition_paths.add(transition_key)
                self._full_rescan_required = True
                self._last_event_at = now
                return True
            if normalized.casefold().startswith(".git/"):
                return False
            try:
                self.resolver.resolve(normalized, reject_any_link=True)
            except (ProtectedFileSystemPath, FileSystemSecurityError):
                return False
            if self._artifacts.is_generated(normalized):
                return False

            previous = self._pending.get(normalized)
            merged = _merge_change(previous, change_kind)
            if merged is None:
                self._pending.pop(normalized, None)
            else:
                if (
                    previous is None
                    and len(self._pending)
                    >= self.limits.maximum_pending_paths
                ):
                    self._pending.clear()
                    self._overflowed = True
                    self._full_rescan_required = True
                else:
                    self._pending[normalized] = merged
            self._last_event_at = now
            return True

    def mark_overflow(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._pending.clear()
            self._overflowed = True
            self._full_rescan_required = True
            self._last_event_at = self.monotonic()

    def drain(
        self,
        *,
        force: bool = False,
        cancellation: CancellationSignal | None = None,
    ) -> WatchBatch | None:
        with self._lock:
            if self._closed or _cancelled(cancellation):
                return WatchBatch(
                    changed_paths=(),
                    paused=True,
                    reason="Repository watcher is stopping.",
                )
            if self._transition_paths:
                return WatchBatch(
                    changed_paths=(),
                    full_rescan_required=True,
                    paused=True,
                    reason=(
                        "Repository update is paused during a Git transition."
                    ),
                )
            if (
                not self._pending
                and not self._full_rescan_required
                and not self._overflowed
            ):
                return None
            if (
                not force
                and self._last_event_at is not None
                and self.monotonic() - self._last_event_at
                < self.limits.debounce_seconds
            ):
                return None
            batch = WatchBatch(
                changed_paths=tuple(sorted(self._pending)),
                full_rescan_required=self._full_rescan_required,
                overflowed=self._overflowed,
            )
            self._pending.clear()
            self._full_rescan_required = False
            self._overflowed = False
            self._last_event_at = None
            return batch

    def close(self) -> None:
        with self._lock:
            self._closed = True
            self._pending.clear()
            self._transition_paths.clear()

    def _contained_relative_path(
        self,
        path: str | Path,
    ) -> str | None:
        candidate = Path(path)
        try:
            if candidate.is_absolute():
                resolved = candidate.expanduser().resolve(strict=False)
                relative = resolved.relative_to(self.workspace).as_posix()
            else:
                relative = _relative_path(str(path))
                resolved = (
                    self.workspace.joinpath(
                        *PurePosixPath(relative).parts
                    ).resolve(strict=False)
                )
                resolved.relative_to(self.workspace)
            return _relative_path(relative)
        except (OSError, RuntimeError, TypeError, ValueError):
            return None


class RepositoryWatchUpdater:
    def __init__(
        self,
        changes: RepositoryChangeBuffer,
        indexer: RepositoryIndexer,
    ) -> None:
        if changes.workspace != indexer.workspace:
            raise ValueError(
                "watcher and indexer must own the same workspace"
            )
        self.changes = changes
        self.indexer = indexer

    def process_ready(
        self,
        *,
        force: bool = False,
        cancellation: CancellationSignal | None = None,
    ) -> WatchUpdate | None:
        batch = self.changes.drain(
            force=force,
            cancellation=cancellation,
        )
        if batch is None:
            return None
        if batch.paused:
            return WatchUpdate(batch=batch)
        result = self.indexer.update(cancellation=cancellation)
        return WatchUpdate(batch=batch, indexing_result=result)


def _merge_change(
    previous: FileChangeKind | None,
    current: FileChangeKind,
) -> FileChangeKind | None:
    if previous is None:
        return current
    if previous == FileChangeKind.CREATED:
        return None if current == FileChangeKind.DELETED else previous
    if previous == FileChangeKind.DELETED:
        if current == FileChangeKind.CREATED:
            return FileChangeKind.MODIFIED
        return previous
    return (
        FileChangeKind.DELETED
        if current == FileChangeKind.DELETED
        else FileChangeKind.MODIFIED
    )


def _git_transition_key(relative_path: str) -> str | None:
    value = relative_path.casefold()
    exact = {
        ".git/cherry_pick_head",
        ".git/index.lock",
        ".git/merge_head",
    }
    if value in exact:
        return value
    prefixes = (
        ".git/rebase-apply",
        ".git/rebase-merge",
    )
    return next(
        (
            prefix
            for prefix in prefixes
            if value == prefix or value.startswith(f"{prefix}/")
        ),
        None,
    )


def _cancelled(cancellation: CancellationSignal | None) -> bool:
    return bool(cancellation is not None and cancellation.is_set())
