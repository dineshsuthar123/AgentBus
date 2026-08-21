from __future__ import annotations

import hashlib
import os
import re
import subprocess
import threading
import uuid
import weakref
from pathlib import Path

from agentbus.execution.state_store import (
    StateStore,
    StateStoreError,
    WorktreeRecordConflictError,
)
from agentbus.git.repository import (
    GitRepository,
    GitRepositoryError,
    safe_git_environment,
)
from agentbus.sandbox.platform import ExecutableCatalog
from agentbus.security.redaction import redact_text
from agentbus.worktrees.errors import (
    WorktreeAlreadyExistsError,
    WorktreeDirtyError,
    WorktreeError,
    WorktreeOwnershipError,
    WorktreeRemovalUnsafeError,
    WorktreeRepositoryMismatchError,
)
from agentbus.worktrees.models import (
    WorktreePurpose,
    WorktreeRecord,
    WorktreeStatus,
)


_GIT_LOCKS_GUARD = threading.Lock()
_GIT_LOCKS: weakref.WeakValueDictionary[str, threading.RLock] = (
    weakref.WeakValueDictionary()
)


def _git_lock_for(path: Path) -> threading.RLock:
    key = os.path.normcase(str(path.expanduser().resolve()))
    with _GIT_LOCKS_GUARD:
        return _GIT_LOCKS.setdefault(key, threading.RLock())


class GitWorktreeManager:
    def __init__(
        self,
        repository_root: str | Path,
        worktree_root: str | Path,
        store: StateStore,
        *,
        timeout_seconds: int = 60,
        executable_catalog: ExecutableCatalog | None = None,
        maximum_command_output_chars: int = 65_536,
    ) -> None:
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be greater than zero")
        if (
            maximum_command_output_chars < 1
            or maximum_command_output_chars > 4_194_304
        ):
            raise ValueError(
                "maximum_command_output_chars must be between 1 and 4194304"
            )
        self.repository_root = Path(repository_root).expanduser().resolve()
        self.worktree_root = Path(worktree_root).expanduser().resolve()
        self.store = store
        self.timeout_seconds = timeout_seconds
        self.executable_catalog = executable_catalog or ExecutableCatalog.standard(
            ("git",)
        )
        self.maximum_command_output_chars = maximum_command_output_chars
        self._git_lock = _git_lock_for(self.repository_root)
        try:
            GitRepository(
                str(self.repository_root),
                timeout_seconds=timeout_seconds,
                executable_catalog=self.executable_catalog,
            ).validate_workspace()
        except GitRepositoryError as exc:
            raise WorktreeRepositoryMismatchError(str(exc)) from exc
        if self._is_within(self.worktree_root, self.repository_root):
            raise WorktreeRepositoryMismatchError(
                "Worktree root must be outside the target repository working tree."
            )
        self.worktree_root.mkdir(parents=True, exist_ok=True)
        self._source_common_dir = self._common_git_dir(self.repository_root)
        self._git_lock = _git_lock_for(self._source_common_dir)

    def create_integration_worktree(
        self,
        run_id: str,
        base_commit: str,
    ) -> WorktreeRecord:
        existing = [
            item
            for item in self.store.list_worktrees(run_id)
            if item.purpose == WorktreePurpose.INTEGRATION
            and item.status != WorktreeStatus.REMOVED
        ]
        if len(existing) > 1:
            raise WorktreeOwnershipError(
                "Multiple non-removed integration worktrees claim the same run."
            )
        if existing:
            return self._recover_reusable(
                existing[0],
                expected_status=WorktreeStatus.READY,
                scope="integration",
            )
        return self._create(
            run_id=run_id,
            task_id=None,
            base_commit=base_commit,
            purpose=WorktreePurpose.INTEGRATION,
            worker_id=None,
        )

    def create_task_worktree(
        self,
        run_id: str,
        task_id: str,
        base_commit: str,
        worker_id: str,
    ) -> WorktreeRecord:
        existing = [
            item
            for item in self.store.list_worktrees(run_id, task_id=task_id)
            if item.purpose == WorktreePurpose.TASK
            and item.status != WorktreeStatus.REMOVED
        ]
        if len(existing) > 1:
            raise WorktreeOwnershipError(
                f"Multiple non-removed worktrees claim task '{task_id}'."
            )
        if existing:
            record = self._recover_reusable(
                existing[-1],
                expected_status=WorktreeStatus.ACTIVE,
                scope=f"task '{task_id}'",
            )
            if record.base_commit != base_commit:
                raise WorktreeAlreadyExistsError(
                    f"Task '{task_id}' has a worktree from a different base commit."
                )
            return self.store.update_worktree(
                record.worktree_id,
                status=WorktreeStatus.ACTIVE,
                worker_id=worker_id,
                event_type="worktree_recovered",
            )
        return self._create(
            run_id=run_id,
            task_id=task_id,
            base_commit=base_commit,
            purpose=WorktreePurpose.TASK,
            worker_id=worker_id,
        )

    def create_replay_worktree(
        self,
        run_id: str,
        replay_id: str,
        base_commit: str,
    ) -> WorktreeRecord:
        """Create a fresh isolated worktree for one replay session."""
        return self._create(
            run_id=run_id,
            task_id=None,
            base_commit=base_commit,
            purpose=WorktreePurpose.REPLAY,
            worker_id=replay_id,
        )

    def recover(self, worktree_id: str) -> WorktreeRecord:
        try:
            record = self.store.get_worktree(worktree_id)
        except StateStoreError as exc:
            raise WorktreeOwnershipError(str(exc)) from exc
        path = Path(record.path)
        if not path.is_dir():
            return self.store.update_worktree(
                worktree_id,
                status=WorktreeStatus.ORPHANED,
                event_type="worktree_orphaned",
            )
        self.validate(record)
        return record

    def _recover_reusable(
        self,
        record: WorktreeRecord,
        *,
        expected_status: WorktreeStatus,
        scope: str,
    ) -> WorktreeRecord:
        if record.status != expected_status:
            raise WorktreeAlreadyExistsError(
                f"The {scope} worktree has unresolved status '{record.status.value}'."
            )
        recovered = self.recover(record.worktree_id)
        if recovered.status != expected_status:
            raise WorktreeOwnershipError(
                f"The {scope} worktree is missing or no longer recoverable."
            )
        return recovered

    def validate(self, record: WorktreeRecord) -> Path:
        path = Path(record.path).expanduser().resolve()
        if not self._is_within(path, self.worktree_root):
            raise WorktreeOwnershipError(
                f"Worktree path is outside the AgentBus-owned root: {path}"
            )
        if os.path.normcase(str(Path(record.repository_root).resolve())) != os.path.normcase(
            str(self.repository_root)
        ):
            raise WorktreeRepositoryMismatchError(
                "Persisted worktree repository does not match the configured repository."
            )
        top_level = Path(
            self._run_git(["rev-parse", "--show-toplevel"], cwd=path)
        ).resolve()
        if os.path.normcase(str(top_level)) != os.path.normcase(str(path)):
            raise WorktreeRepositoryMismatchError(
                f"Worktree Git top-level mismatch: {top_level} != {path}"
            )
        if self._common_git_dir(path) != self._source_common_dir:
            raise WorktreeRepositoryMismatchError(
                "Worktree belongs to a different Git common repository."
            )
        expected_branch = f"refs/heads/{record.branch_ref}"
        try:
            actual_branch = self._run_git(
                ["symbolic-ref", "--quiet", "HEAD"],
                cwd=path,
            )
        except WorktreeError as exc:
            raise WorktreeOwnershipError(
                "Persisted worktree branch identity could not be verified."
            ) from exc
        if actual_branch != expected_branch:
            raise WorktreeOwnershipError(
                "Persisted worktree branch does not match the checked-out branch."
            )
        return path

    def list_owned(self, run_id: str | None = None) -> list[WorktreeRecord]:
        return self.store.list_worktrees(run_id)

    def detect_orphans(self, run_id: str | None = None) -> list[WorktreeRecord]:
        orphaned: list[WorktreeRecord] = []
        for record in self.store.list_worktrees(run_id):
            if record.status == WorktreeStatus.REMOVED:
                continue
            if not Path(record.path).is_dir():
                orphaned.append(
                    self.store.update_worktree(
                        record.worktree_id,
                        status=WorktreeStatus.ORPHANED,
                        event_type="worktree_orphaned",
                    )
                )
        return orphaned

    def mark_cleanup_pending(self, worktree_id: str) -> WorktreeRecord:
        record = self.recover(worktree_id)
        return self.store.update_worktree(
            record.worktree_id,
            status=WorktreeStatus.CLEANUP_PENDING,
            event_type="worktree_cleanup_requested",
        )

    def is_clean(self, record: WorktreeRecord) -> bool:
        path = self.validate(record)
        status = self._run_git(
            ["status", "--porcelain=v1", "--untracked-files=all"],
            cwd=path,
        )
        return not status

    def remove(self, worktree_id: str) -> WorktreeRecord:
        try:
            record = self.store.get_worktree(worktree_id)
        except StateStoreError as exc:
            raise WorktreeOwnershipError(
                "Refusing to remove an unknown or non-AgentBus worktree."
            ) from exc
        if record.status != WorktreeStatus.CLEANUP_PENDING:
            raise WorktreeRemovalUnsafeError(
                "Worktree must be explicitly marked cleanup_pending before removal."
            )
        path = self.validate(record)
        if not self.is_clean(record):
            raise WorktreeDirtyError(
                f"Refusing to remove dirty worktree '{worktree_id}'."
            )
        self._run_git(["worktree", "remove", str(path)], cwd=self.repository_root)
        return self.store.update_worktree(
            worktree_id,
            status=WorktreeStatus.REMOVED,
            event_type="worktree_removed",
        )

    def _create(
        self,
        *,
        run_id: str,
        task_id: str | None,
        base_commit: str,
        purpose: WorktreePurpose,
        worker_id: str | None,
    ) -> WorktreeRecord:
        resolved_base = self._run_git(
            ["rev-parse", "--verify", f"{base_commit}^{{commit}}"],
            cwd=self.repository_root,
        )
        worktree_id = uuid.uuid4().hex
        safe_task = self._safe_component(
            task_id
            or (
                "integration"
                if purpose == WorktreePurpose.INTEGRATION
                else "replay"
            )
        )
        path = (
            self.worktree_root
            / self._safe_component(run_id)
            / f"{purpose.value}-{safe_task}-{worktree_id[:8]}"
        ).resolve()
        if not self._is_within(path, self.worktree_root):
            raise WorktreeOwnershipError("Computed worktree path escaped its root.")
        if path.exists():
            raise WorktreeAlreadyExistsError(f"Worktree path already exists: {path}")
        branch_ref = self._branch_ref(run_id, task_id, purpose, worktree_id)
        record = WorktreeRecord(
            worktree_id=worktree_id,
            run_id=run_id,
            task_id=task_id,
            path=str(path),
            repository_root=str(self.repository_root),
            base_commit=resolved_base,
            branch_ref=branch_ref,
            purpose=purpose,
            status=WorktreeStatus.CREATING,
            worker_id=worker_id,
        )
        try:
            self.store.record_worktree(record)
        except WorktreeRecordConflictError as exc:
            raise WorktreeAlreadyExistsError(
                "Another non-removed worktree already owns this run and task scope."
            ) from exc
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            self._run_git(
                ["worktree", "add", "-b", branch_ref, str(path), resolved_base],
                cwd=self.repository_root,
            )
            self.validate(record)
        except Exception as exc:
            self.store.update_worktree(
                worktree_id,
                status=WorktreeStatus.ORPHANED,
                metadata_updates={"creation_error": str(exc)},
                event_type="worktree_orphaned",
            )
            if isinstance(exc, WorktreeError):
                raise
            raise WorktreeError(f"Unable to create worktree: {exc}") from exc
        return self.store.update_worktree(
            worktree_id,
            status=(
                WorktreeStatus.ACTIVE
                if purpose == WorktreePurpose.TASK
                else WorktreeStatus.READY
            ),
            worker_id=worker_id,
            event_type="worktree_created",
        )

    def _common_git_dir(self, cwd: Path) -> Path:
        raw = self._run_git(["rev-parse", "--git-common-dir"], cwd=cwd)
        candidate = Path(raw)
        if not candidate.is_absolute():
            candidate = cwd / candidate
        return candidate.resolve()

    def _run_git(self, arguments: list[str], *, cwd: Path) -> str:
        if not arguments:
            raise WorktreeError("A Git worktree operation must be specified.")
        operation = arguments[0]
        identity = self.executable_catalog.resolve("git")
        command = identity.command(
            [
                "--no-pager",
                "--literal-pathspecs",
                "-c",
                f"core.hooksPath={os.devnull}",
                "-c",
                "core.fsmonitor=false",
                "-c",
                "commit.gpgSign=false",
                *arguments,
            ]
        )
        try:
            with self._git_lock:
                result = subprocess.run(
                    command,
                    cwd=cwd,
                    capture_output=True,
                    text=True,
                    timeout=self.timeout_seconds,
                    shell=False,
                    env=safe_git_environment(),
                )
        except subprocess.TimeoutExpired as exc:
            raise WorktreeError(
                f"Git worktree operation '{operation}' timed out."
            ) from exc
        except OSError as exc:
            raise WorktreeError(
                f"Git worktree operation '{operation}' could not run: "
                f"{redact_text(type(exc).__name__, max_chars=128)}"
            ) from exc
        if result.returncode != 0:
            detail = result.stderr.strip() or result.stdout.strip()
            raise WorktreeError(
                f"Git worktree operation '{operation}' failed: "
                f"{redact_text(detail, max_chars=2_048)}"
            )
        if len(result.stdout) > self.maximum_command_output_chars:
            raise WorktreeError(
                f"Git worktree operation '{operation}' exceeded the output limit."
            )
        return result.stdout.rstrip("\r\n")

    @staticmethod
    def _safe_component(value: str) -> str:
        normalized = re.sub(r"[^a-z0-9._-]+", "-", value.lower()).strip("-.")
        digest = hashlib.sha256(value.encode("utf-8")).hexdigest()[:8]
        return f"{normalized[:32] or 'item'}-{digest}"

    def _branch_ref(
        self,
        run_id: str,
        task_id: str | None,
        purpose: WorktreePurpose,
        worktree_id: str,
    ) -> str:
        run = self._safe_component(run_id)
        if purpose == WorktreePurpose.INTEGRATION:
            return f"agentbus/run/{run}/integration-{worktree_id[:8]}"
        if purpose == WorktreePurpose.REPLAY:
            return f"agentbus/run/{run}/replay-{worktree_id[:8]}"
        task = self._safe_component(task_id or "task")
        return f"agentbus/run/{run}/task/{task}-{worktree_id[:8]}"

    @staticmethod
    def _is_within(path: Path, root: Path) -> bool:
        try:
            path.resolve().relative_to(root.resolve())
            return True
        except ValueError:
            return False
