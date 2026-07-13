from __future__ import annotations

import threading
from collections.abc import Callable
from enum import Enum
from pathlib import Path
from typing import Any

from pydantic import Field

from agentbus.execution.leases import LeaseError, LeaseService, WorkerLease
from agentbus.execution.models import (
    AttemptStatus,
    DomainModel,
    FailureCategory,
    RunRecord,
    TaskExecutionContext,
    TaskExecutionResult,
    TaskRecord,
    TaskStatus,
)
from agentbus.execution.state_store import StateStore, StateStoreError
from agentbus.git.repository import GitRepository, GitRepositoryError
from agentbus.worktrees.manager import GitWorktreeManager
from agentbus.worktrees.models import TaskCommitRecord, WorktreeRecord, WorktreeStatus


class WorkerStatus(str, Enum):
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    LEASE_LOST = "lease_lost"


class WorkerResult(DomainModel):
    worker_id: str
    run_id: str
    task_id: str
    status: WorkerStatus
    lease_id: str
    fencing_token: int
    worktree_id: str | None = None
    task_commit: str | None = None
    changed_files: list[str] = Field(default_factory=list)
    summary: str = ""
    error_message: str | None = None


ExecutorFactory = Callable[[Path], Any]
WorkerCrashHook = Callable[[str, str, str], None]


class LocalTaskWorker:
    def __init__(
        self,
        *,
        worker_id: str,
        store: StateStore,
        lease_service: LeaseService,
        worktree_manager: GitWorktreeManager,
        executor_factory: ExecutorFactory,
        heartbeat_seconds: float = 30,
        cancellation: threading.Event | None = None,
        crash_hook: WorkerCrashHook | None = None,
    ):
        if heartbeat_seconds <= 0:
            raise ValueError("heartbeat_seconds must be greater than zero")
        self.worker_id = worker_id
        self.store = store
        self.lease_service = lease_service
        self.worktree_manager = worktree_manager
        self.executor_factory = executor_factory
        self.heartbeat_seconds = heartbeat_seconds
        self.cancellation = cancellation or threading.Event()
        self.crash_hook = crash_hook

    def execute(
        self,
        run: RunRecord,
        task: TaskRecord,
        lease: WorkerLease,
        base_commit: str,
    ) -> WorkerResult:
        if self.cancellation.is_set():
            return self._result(task, lease, WorkerStatus.CANCELLED, "Cancelled")
        self.lease_service.validate_fencing_token(
            lease.lease_id, self.worker_id, lease.fencing_token
        )
        self.store.record_event(
            run.run_id,
            "worker_started",
            {
                "worker_id": self.worker_id,
                "lease_id": lease.lease_id,
                "fencing_token": lease.fencing_token,
            },
            task_id=task.task_id,
        )
        worktree = self.worktree_manager.create_task_worktree(
            run.run_id,
            task.task_id,
            base_commit,
            self.worker_id,
        )
        self._crash("after_worktree_created", run.run_id, task.task_id)
        attempt = self.store.create_attempt(run.run_id, task.task_id)
        stop_heartbeat = threading.Event()
        lease_lost = threading.Event()
        heartbeat = threading.Thread(
            target=self._heartbeat,
            args=(lease, stop_heartbeat, lease_lost),
            name=f"agentbus-heartbeat-{self.worker_id}",
            daemon=True,
        )
        heartbeat.start()
        try:
            recovered = self._recover_unpersisted_commit(
                run, task, lease, attempt.attempt_id, worktree
            )
            if recovered is not None:
                return recovered
            executor = self.executor_factory(Path(worktree.path))
            snapshot = self.store.load_snapshot(run.run_id)
            context = TaskExecutionContext(
                run=run,
                task=task.spec,
                attempt_number=attempt.attempt_number,
                previous_attempts=[
                    item
                    for item in snapshot.attempts_for(task.task_id)
                    if item.attempt_id != attempt.attempt_id
                ],
            )
            result = executor.execute(context) if hasattr(executor, "execute") else executor(context)
            if not isinstance(result, TaskExecutionResult):
                result = TaskExecutionResult.model_validate(result)
            for artifact in result.artifacts:
                self.store.record_artifact(artifact)
            if not result.succeeded:
                return self._persist_failure(task, lease, attempt.attempt_id, result, worktree)
            if self.cancellation.is_set():
                return self._persist_interruption(
                    task, lease, attempt.attempt_id, worktree, "Worker cancelled."
                )
            if lease_lost.is_set():
                return self._result(
                    task,
                    lease,
                    WorkerStatus.LEASE_LOST,
                    "Worker lease was lost during execution.",
                    worktree,
                )
            repository = GitRepository(str(worktree.path))
            changes = repository.change_set(result.changed_files or repository.changed_files())
            if not changes.commit_files:
                failure = TaskExecutionResult(
                    succeeded=False,
                    summary="Task produced no commit-eligible changes.",
                    failure_category=FailureCategory.VERIFIER_FAILURE,
                    error_message="No relevant task files are available to commit.",
                    retryable=False,
                    metadata=result.metadata,
                )
                return self._persist_failure(
                    task, lease, attempt.attempt_id, failure, worktree
                )
            parent = repository.head_commit(short=False)
            commit_sha = repository.commit(
                f"feat: {task.task_id} {task.spec.title}"[:120],
                paths=changes.commit_files,
            )
            commit_sha = repository.head_commit(short=False)
            self._crash("after_task_commit", run.run_id, task.task_id)
            commit = TaskCommitRecord(
                run_id=run.run_id,
                task_id=task.task_id,
                commit_sha=commit_sha,
                parent_sha=parent,
                worktree_id=worktree.worktree_id,
                changed_files=changes.commit_files,
            )
            if lease_lost.is_set():
                return self._result(
                    task,
                    lease,
                    WorkerStatus.LEASE_LOST,
                    "Worker lease was lost before commit persistence.",
                    worktree,
                    commit_sha,
                    changes.commit_files,
                )
            self.store.complete_fenced_task_commit(
                attempt_id=attempt.attempt_id,
                lease_id=lease.lease_id,
                worker_id=self.worker_id,
                fencing_token=lease.fencing_token,
                commit=commit,
                summary=result.summary,
                metadata={
                    **result.metadata,
                    "worker_id": self.worker_id,
                    "lease_id": lease.lease_id,
                    "fencing_token": lease.fencing_token,
                    "worktree_id": worktree.worktree_id,
                },
            )
            self._crash("after_commit_persisted", run.run_id, task.task_id)
            self.store.update_worktree(
                worktree.worktree_id,
                status=WorktreeStatus.COMPLETED,
                result_commit=commit_sha,
                event_type="worktree_completed",
            )
            self.store.record_event(
                run.run_id,
                "worker_finished",
                {
                    "worker_id": self.worker_id,
                    "lease_id": lease.lease_id,
                    "fencing_token": lease.fencing_token,
                    "worktree_id": worktree.worktree_id,
                    "task_commit": commit_sha,
                },
                task_id=task.task_id,
            )
            return self._result(
                task,
                lease,
                WorkerStatus.SUCCEEDED,
                result.summary,
                worktree,
                commit_sha,
                changes.commit_files,
            )
        except (LeaseError, StateStoreError) as exc:
            return self._result(
                task,
                lease,
                WorkerStatus.LEASE_LOST,
                "Worker could not persist success under its lease.",
                worktree,
                error=str(exc),
            )
        except Exception as exc:
            return self._persist_interruption(
                task, lease, attempt.attempt_id, worktree, str(exc)
            )
        finally:
            stop_heartbeat.set()
            heartbeat.join(timeout=max(1.0, self.heartbeat_seconds * 2))
            try:
                self.lease_service.release_lease(
                    lease.lease_id, self.worker_id, lease.fencing_token
                )
            except LeaseError:
                pass

    def _recover_unpersisted_commit(
        self,
        run,
        task,
        lease,
        attempt_id,
        worktree,
    ) -> WorkerResult | None:
        repository = GitRepository(str(worktree.path))
        current = repository.head_commit(short=False)
        if current == worktree.base_commit or repository.has_uncommitted_changes():
            return None
        parent = repository._run(["git", "rev-parse", "HEAD^"])
        if parent != worktree.base_commit:
            raise GitRepositoryError(
                "Recovered task worktree has an unexpected commit history."
            )
        changed = repository.changed_files_between(parent, current)
        commit = TaskCommitRecord(
            run_id=run.run_id,
            task_id=task.task_id,
            commit_sha=current,
            parent_sha=parent,
            worktree_id=worktree.worktree_id,
            changed_files=repository.change_set(changed).commit_files,
        )
        self.store.complete_fenced_task_commit(
            attempt_id=attempt_id,
            lease_id=lease.lease_id,
            worker_id=self.worker_id,
            fencing_token=lease.fencing_token,
            commit=commit,
            summary="Recovered task commit created before state persistence.",
            metadata={"recovered": True, "worktree_id": worktree.worktree_id},
        )
        self.store.record_event(
            run.run_id,
            "task_commit_recovered",
            {"worker_id": self.worker_id, "task_commit": current},
            task_id=task.task_id,
        )
        self.store.update_worktree(
            worktree.worktree_id,
            status=WorktreeStatus.COMPLETED,
            result_commit=current,
            event_type="worktree_completed",
        )
        self.store.record_event(
            run.run_id,
            "worker_finished",
            {
                "worker_id": self.worker_id,
                "lease_id": lease.lease_id,
                "fencing_token": lease.fencing_token,
                "worktree_id": worktree.worktree_id,
                "task_commit": current,
                "recovered": True,
            },
            task_id=task.task_id,
        )
        return self._result(
            task,
            lease,
            WorkerStatus.SUCCEEDED,
            "Recovered task commit.",
            worktree,
            current,
            commit.changed_files,
        )

    def _persist_failure(self, task, lease, attempt_id, result, worktree):
        category = result.failure_category or FailureCategory.UNKNOWN
        changed_files, artifact_hygiene = self._worktree_observations(worktree)
        self.store.complete_attempt(
            attempt_id,
            AttemptStatus.FAILED,
            error_category=category,
            error_message=result.error_message or result.summary,
            observation_summary=result.summary,
            metadata={
                **result.metadata,
                "changed_files": changed_files,
                "artifact_hygiene": artifact_hygiene,
            },
        )
        current = self.store.get_task(task.run_id, task.task_id)
        retry = bool(
            result.retryable
            and current.current_attempt_count < current.spec.maximum_attempts
        )
        self.store.update_task_status(
            task.run_id,
            task.task_id,
            TaskStatus.RETRYABLE if retry else TaskStatus.FAILED,
            event_type="worker_failed",
            event_payload={
                "worker_id": self.worker_id,
                "lease_id": lease.lease_id,
                "fencing_token": lease.fencing_token,
                "error_category": category.value,
            },
        )
        return self._result(
            task,
            lease,
            WorkerStatus.FAILED,
            result.summary,
            worktree,
            changed_files=changed_files,
            error=result.error_message,
        )

    def _persist_interruption(self, task, lease, attempt_id, worktree, message):
        changed_files, artifact_hygiene = self._worktree_observations(worktree)
        try:
            self.store.complete_attempt(
                attempt_id,
                AttemptStatus.INTERRUPTED,
                error_category=FailureCategory.INTERRUPTED,
                error_message=message,
                metadata={
                    "changed_files": changed_files,
                    "artifact_hygiene": artifact_hygiene,
                },
            )
            current = self.store.get_task(task.run_id, task.task_id)
            if current.status == TaskStatus.RUNNING:
                self.store.update_task_status(
                    task.run_id,
                    task.task_id,
                    TaskStatus.RETRYABLE,
                    event_type="worker_failed",
                    event_payload={"worker_id": self.worker_id, "interrupted": True},
                )
        except StateStoreError:
            pass
        return self._result(
            task,
            lease,
            WorkerStatus.FAILED,
            "Worker interrupted.",
            worktree,
            changed_files=changed_files,
            error=message,
        )

    @staticmethod
    def _worktree_observations(worktree: WorktreeRecord) -> tuple[list[str], dict]:
        try:
            repository = GitRepository(str(worktree.path))
            changed_files = repository.changed_files()
            return changed_files, repository.change_set(changed_files).to_metadata()
        except GitRepositoryError:
            return [], {}

    def _heartbeat(self, lease, stop, lost):
        while not stop.wait(self.heartbeat_seconds):
            try:
                self.lease_service.renew_lease(
                    lease.lease_id, self.worker_id, lease.fencing_token
                )
                self.store.record_event(
                    lease.run_id,
                    "worker_heartbeat",
                    {
                        "worker_id": self.worker_id,
                        "lease_id": lease.lease_id,
                        "fencing_token": lease.fencing_token,
                    },
                    task_id=lease.task_id,
                )
            except LeaseError:
                lost.set()
                return

    def _crash(self, stage, run_id, task_id):
        if self.crash_hook is not None:
            self.crash_hook(stage, run_id, task_id)

    def _result(
        self,
        task,
        lease,
        status,
        summary,
        worktree: WorktreeRecord | None = None,
        commit_sha: str | None = None,
        changed_files: list[str] | None = None,
        error: str | None = None,
    ):
        return WorkerResult(
            worker_id=self.worker_id,
            run_id=task.run_id,
            task_id=task.task_id,
            status=status,
            lease_id=lease.lease_id,
            fencing_token=lease.fencing_token,
            worktree_id=worktree.worktree_id if worktree else None,
            task_commit=commit_sha,
            changed_files=changed_files or [],
            summary=summary,
            error_message=error,
        )
