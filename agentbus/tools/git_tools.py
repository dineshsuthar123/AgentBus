from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from itertools import islice
from pathlib import Path
from typing import Iterable

from agentbus.git.repository import (
    GitRepository,
    GitRepositoryError,
    WorkspaceRepositoryMismatch,
)
from agentbus.security.redaction import redact_text


class GitToolAuthorizationError(GitRepositoryError):
    """Raised when a mutating Git tool lacks an owned worktree."""


@dataclass(frozen=True)
class GitStageRecord:
    repository_root: str
    paths: tuple[str, ...]
    task_id: str
    invocation_id: str
    timestamp: datetime


@dataclass(frozen=True)
class GitCommitRecord:
    repository_root: str
    parent_commit: str
    commit: str
    paths: tuple[str, ...]
    task_id: str
    invocation_id: str
    message_sha256: str
    timestamp: datetime


class GitTools:
    def __init__(
        self,
        workspace: str = "workspace",
        max_diff_chars: int = 30_000,
        *,
        owned_worktree: bool = False,
        repository: GitRepository | None = None,
    ) -> None:
        if max_diff_chars < 1:
            raise ValueError("max_diff_chars must be positive")
        self.workspace = Path(workspace).expanduser().resolve()
        self.max_diff_chars = max_diff_chars
        self.owned_worktree = owned_worktree
        self.repository = repository or GitRepository(str(self.workspace))
        if self.repository.workspace != self.workspace:
            raise WorkspaceRepositoryMismatch(
                "GitTools repository does not match the configured workspace."
            )

    def status(self, *, max_chars: int | None = None) -> str:
        limit = self._limit(max_chars)
        return self._safe_output(self.repository.bounded_status(limit), limit)

    def diff(
        self,
        *,
        paths: Iterable[str] | None = None,
        max_chars: int | None = None,
    ) -> str:
        limit = self._limit(max_chars)
        selected = None if paths is None else self._tool_paths(paths)
        return self._safe_output(
            self.repository.review_diff(max_chars=limit, paths=selected),
            limit,
        )

    def git_diff(self) -> str:
        try:
            return self.diff()
        except WorkspaceRepositoryMismatch:
            raise
        except Exception as exc:
            diagnostic = redact_text(str(exc), max_chars=2_048) or "unknown error"
            return f"git_diff error: {diagnostic}"

    def show(
        self,
        revision: str = "HEAD",
        *,
        path: str | None = None,
        max_chars: int | None = None,
    ) -> str:
        limit = self._limit(max_chars)
        return self._safe_output(
            self.repository.show_commit(
                revision,
                path=path,
                max_chars=limit,
            ),
            limit,
        )

    def log(
        self,
        *,
        maximum_entries: int = 20,
        max_chars: int | None = None,
    ) -> str:
        limit = self._limit(max_chars)
        return self._safe_output(
            self.repository.log_entries(
                maximum_entries=maximum_entries,
                max_chars=limit,
            ),
            limit,
        )

    def branches(
        self,
        *,
        maximum_entries: int = 100,
        max_chars: int | None = None,
    ) -> str:
        limit = self._limit(max_chars)
        return self._safe_output(
            self.repository.branches(
                maximum_entries=maximum_entries,
                max_chars=limit,
            ),
            limit,
        )

    def stage(
        self,
        paths: Iterable[str],
        *,
        task_id: str,
        invocation_id: str,
    ) -> GitStageRecord:
        self._require_owned_worktree()
        self._validate_attribution(task_id, invocation_id)
        staged = self.repository.stage(self._tool_paths(paths))
        return GitStageRecord(
            repository_root=str(self.workspace),
            paths=tuple(staged),
            task_id=task_id,
            invocation_id=invocation_id,
            timestamp=datetime.now(timezone.utc),
        )

    def commit(
        self,
        message: str,
        paths: Iterable[str],
        *,
        task_id: str,
        invocation_id: str,
    ) -> GitCommitRecord:
        self._require_owned_worktree()
        self._validate_attribution(task_id, invocation_id)
        changes = self.repository.change_set(self._tool_paths(paths))
        if not changes.changed_files or changes.commit_files != changes.changed_files:
            raise GitRepositoryError(
                "Commit paths must be explicit, changed, and policy eligible."
            )
        parent = self.repository.head_commit(short=False)
        self.repository.commit(message, paths=changes.commit_files)
        commit = self.repository.head_commit(short=False)
        return GitCommitRecord(
            repository_root=str(self.workspace),
            parent_commit=parent,
            commit=commit,
            paths=tuple(changes.commit_files),
            task_id=task_id,
            invocation_id=invocation_id,
            message_sha256=hashlib.sha256(message.encode("utf-8")).hexdigest(),
            timestamp=datetime.now(timezone.utc),
        )

    def _limit(self, requested: int | None) -> int:
        if requested is None:
            return self.max_diff_chars
        if requested < 1:
            raise ValueError("max_chars must be positive")
        return min(requested, self.max_diff_chars)

    def _require_owned_worktree(self) -> None:
        if not self.owned_worktree:
            raise GitToolAuthorizationError(
                "Git mutation requires an explicitly owned AgentBus worktree."
            )

    @staticmethod
    def _tool_paths(paths: Iterable[str]) -> tuple[str, ...]:
        if isinstance(paths, (str, bytes)):
            raise TypeError("Git paths must be provided as a collection.")
        selected = tuple(islice(iter(paths), 257))
        if not selected or len(selected) > 256:
            raise ValueError("Git tools require between 1 and 256 explicit paths")
        return selected

    @staticmethod
    def _validate_attribution(task_id: str, invocation_id: str) -> None:
        for name, value in (("task_id", task_id), ("invocation_id", invocation_id)):
            if (
                not isinstance(value, str)
                or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}", value) is None
            ):
                raise ValueError(f"{name} must be a safe non-empty identifier")

    @staticmethod
    def _safe_output(output: str, max_chars: int) -> str:
        redacted = redact_text(output, max_chars=max(len(output), 1)) or ""
        if len(redacted) <= max_chars:
            return redacted
        marker = "\n[output truncated]"
        if len(marker) >= max_chars:
            return marker[:max_chars]
        return redacted[: max_chars - len(marker)] + marker
