from __future__ import annotations

import subprocess
import uuid
from pathlib import Path

from agentbus.execution.models import TaskStatus, utc_now
from agentbus.execution.state_store import StateStore
from agentbus.git.repository import GitRepository
from agentbus.worktrees.errors import WorktreeError
from agentbus.worktrees.manager import GitWorktreeManager
from agentbus.worktrees.models import (
    IntegrationRecord,
    MergeStatus,
    TaskCommitRecord,
    WorktreeRecord,
    WorktreeStatus,
)


class IntegrationError(RuntimeError):
    """Base error for deterministic task-commit integration."""


class IntegrationConflictError(IntegrationError):
    def __init__(self, task_id: str, conflict_files: list[str]):
        self.task_id = task_id
        self.conflict_files = conflict_files
        super().__init__(
            f"Task '{task_id}' conflicts during integration: "
            + (", ".join(conflict_files) or "unknown paths")
        )


class IntegrationCoordinator:
    def __init__(
        self,
        store: StateStore,
        worktree_manager: GitWorktreeManager,
        *,
        timeout_seconds: int = 90,
    ):
        self.store = store
        self.worktree_manager = worktree_manager
        self.timeout_seconds = timeout_seconds

    def integrate(
        self,
        integration_worktree: WorktreeRecord,
        task_commit: TaskCommitRecord,
    ) -> IntegrationRecord:
        path = self.worktree_manager.validate(integration_worktree)
        for existing in reversed(self.store.list_integrations(task_commit.run_id)):
            if (
                existing.task_id == task_commit.task_id
                and existing.task_commit == task_commit.commit_sha
                and existing.status == MergeStatus.INTEGRATED
            ):
                return existing
        base_commit = self._git(path, ["rev-parse", "HEAD"])
        attempt = self.store.record_integration(
            IntegrationRecord(
                integration_id=uuid.uuid4().hex,
                run_id=task_commit.run_id,
                task_id=task_commit.task_id,
                task_commit=task_commit.commit_sha,
                base_commit=base_commit,
                status=MergeStatus.INTEGRATING,
            )
        )
        self.store.update_task_status(
            task_commit.run_id,
            task_commit.task_id,
            TaskStatus.INTEGRATING,
            event_type="integration_started",
            event_payload={"integration_id": attempt.integration_id},
        )
        result = self._run(path, ["cherry-pick", task_commit.commit_sha])
        if result.returncode == 0:
            resulting_commit = self._git(path, ["rev-parse", "HEAD"])
            completed = self.store.update_integration(
                attempt.integration_id,
                status=MergeStatus.INTEGRATED,
                resulting_commit=resulting_commit,
            )
            self.store.update_task_status(
                task_commit.run_id,
                task_commit.task_id,
                TaskStatus.SUCCEEDED,
                event_type="durable_task_integrated",
                event_payload={
                    "integration_id": attempt.integration_id,
                    "resulting_commit": resulting_commit,
                },
            )
            task_worktrees = self.store.list_worktrees(
                task_commit.run_id, task_id=task_commit.task_id
            )
            if task_worktrees:
                self.store.update_worktree(
                    task_worktrees[-1].worktree_id,
                    status=WorktreeStatus.COMPLETED,
                    result_commit=task_commit.commit_sha,
                    event_type="worktree_completed",
                )
            return completed

        conflicts = self._conflict_files(path)
        self._abort_owned_cherry_pick(path)
        current = self._git(path, ["rev-parse", "HEAD"])
        if current != base_commit:
            raise IntegrationError(
                "Integration worktree did not return to its persisted base after abort."
            )
        completed = self.store.update_integration(
            attempt.integration_id,
            status=MergeStatus.INTEGRATION_CONFLICT,
            conflict_files=conflicts,
            error_message="Cherry-pick conflict; automatic resolution is disabled.",
        )
        self.store.update_task_status(
            task_commit.run_id,
            task_commit.task_id,
            TaskStatus.INTEGRATION_CONFLICT,
            event_type="integration_conflict",
            event_payload={
                "integration_id": attempt.integration_id,
                "conflict_files": conflicts,
            },
        )
        self.store.update_worktree(
            integration_worktree.worktree_id,
            status=WorktreeStatus.CONFLICTED,
            event_type="integration_conflict",
        )
        raise IntegrationConflictError(task_commit.task_id, conflicts)

    def recover_interrupted(
        self,
        run_id: str,
        integration_worktree: WorktreeRecord,
    ) -> list[IntegrationRecord]:
        path = self.worktree_manager.validate(integration_worktree)
        recovered: list[IntegrationRecord] = []
        for attempt in self.store.list_integrations(run_id):
            if attempt.status == MergeStatus.INTEGRATED:
                task = self.store.get_task(run_id, attempt.task_id)
                if task.status == TaskStatus.INTEGRATING:
                    self.store.update_task_status(
                        run_id,
                        attempt.task_id,
                        TaskStatus.SUCCEEDED,
                        event_type="integration_recovered",
                    )
                continue
            if attempt.status != MergeStatus.INTEGRATING:
                continue
            current_head = self._git(path, ["rev-parse", "HEAD"])
            cherry_pick_head = self._cherry_pick_head(path)
            if current_head != attempt.base_commit:
                parent = self._git(path, ["rev-parse", "HEAD^"])
                if parent != attempt.base_commit or cherry_pick_head.exists():
                    raise IntegrationError(
                        "Integration recovery found ambiguous Git history; refusing "
                        "to reset, retry, or guess the completed operation."
                    )
                recovered.append(
                    self.store.update_integration(
                        attempt.integration_id,
                        status=MergeStatus.INTEGRATED,
                        resulting_commit=current_head,
                    )
                )
                task = self.store.get_task(run_id, attempt.task_id)
                if task.status == TaskStatus.INTEGRATING:
                    self.store.update_task_status(
                        run_id,
                        attempt.task_id,
                        TaskStatus.SUCCEEDED,
                        event_type="integration_recovered",
                    )
                continue
            if cherry_pick_head.exists():
                self._abort_owned_cherry_pick(path)
                if self._git(path, ["rev-parse", "HEAD"]) != attempt.base_commit:
                    raise IntegrationError(
                        "Interrupted integration did not return to its persisted base."
                    )
            recovered.append(
                self.store.update_integration(
                    attempt.integration_id,
                    status=MergeStatus.INTEGRATION_FAILED,
                    error_message="Interrupted integration was safely aborted for retry.",
                )
            )
            task = self.store.get_task(run_id, attempt.task_id)
            if task.status == TaskStatus.INTEGRATING:
                self.store.update_task_status(
                    run_id,
                    attempt.task_id,
                    TaskStatus.INTEGRATION_PENDING,
                    event_type="integration_interrupted",
                )
        return recovered

    def _conflict_files(self, path: Path) -> list[str]:
        output = self._git(
            path, ["diff", "--name-only", "--diff-filter=U", "-z", "--", "."]
        )
        policy = GitRepository(str(path)).artifact_policy
        return sorted(
            policy.normalize(item) for item in output.split("\0") if item
        )

    def _abort_owned_cherry_pick(self, path: Path) -> None:
        result = self._run(path, ["cherry-pick", "--abort"])
        if result.returncode != 0:
            raise IntegrationError(
                "Unable to abort AgentBus-owned cherry-pick state safely: "
                + (result.stderr.strip() or result.stdout.strip())
            )

    def _cherry_pick_head(self, path: Path) -> Path:
        cherry_pick_head = Path(
            self._git(path, ["rev-parse", "--git-path", "CHERRY_PICK_HEAD"])
        )
        if not cherry_pick_head.is_absolute():
            cherry_pick_head = path / cherry_pick_head
        return cherry_pick_head

    def _git(self, path: Path, arguments: list[str]) -> str:
        result = self._run(path, arguments)
        if result.returncode != 0:
            raise IntegrationError(
                f"Git integration command failed ({' '.join(arguments)}): "
                + (result.stderr.strip() or result.stdout.strip())
            )
        return result.stdout.rstrip("\r\n")

    def _run(self, path: Path, arguments: list[str]):
        try:
            return subprocess.run(
                ["git", *arguments],
                cwd=path,
                capture_output=True,
                text=True,
                timeout=self.timeout_seconds,
                shell=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise IntegrationError(f"Git integration command could not run: {exc}") from exc
