from __future__ import annotations

import threading
from collections.abc import Callable
from contextlib import nullcontext
from enum import Enum
from pathlib import Path
from typing import Any

from pydantic import Field

from agentbus.execution.cancellation import CancellationRequested, CancellationToken
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
from agentbus.execution.retry import FailureClassifier, TaskExecutionError
from agentbus.execution.state_store import StateStore, StateStoreError
from agentbus.git.repository import GitRepository, GitRepositoryError
from agentbus.models.errors import ModelCancellationError
from agentbus.trace import RuntimeTrace
from agentbus.worktrees.manager import GitWorktreeManager
from agentbus.worktrees.models import TaskCommitRecord, WorktreeRecord, WorktreeStatus


class WorkerStatus(str, Enum):
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    WAITING_FOR_APPROVAL = "waiting_for_approval"
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


class _WorkerCancellation(RuntimeError):
    pass


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
        cancellation: CancellationToken | threading.Event | None = None,
        crash_hook: WorkerCrashHook | None = None,
        runtime_trace: RuntimeTrace | None = None,
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
        self.runtime_trace = runtime_trace

    def execute(
        self,
        run: RunRecord,
        task: TaskRecord,
        lease: WorkerLease,
        base_commit: str,
    ) -> WorkerResult:
        try:
            self._checkpoint("before-worker-start")
        except (CancellationRequested, ModelCancellationError, _WorkerCancellation):
            return self._cancel_without_attempt(task, lease)
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
        try:
            with self._operation("worker.create_worktree"):
                worktree = self.worktree_manager.create_task_worktree(
                    run.run_id,
                    task.task_id,
                    base_commit,
                    self.worker_id,
                )
            self._checkpoint("after-worktree-created")
        except (CancellationRequested, ModelCancellationError, _WorkerCancellation):
            return self._cancel_without_attempt(
                task,
                lease,
                worktree=locals().get("worktree"),
            )
        self._crash("after_worktree_created", run.run_id, task.task_id)
        attempts = self.store.list_attempts(run.run_id, task.task_id)
        latest_attempt = attempts[-1] if attempts else None
        continuation = (
            _attempt_continuation(latest_attempt)
            if latest_attempt is not None
            and latest_attempt.status == AttemptStatus.RUNNING
            else None
        )
        attempt = (
            latest_attempt
            if latest_attempt is not None and continuation is not None
            else self.store.create_attempt(run.run_id, task.task_id)
        )
        stop_heartbeat = threading.Event()
        lease_lost = threading.Event()
        heartbeat = threading.Thread(
            target=self._heartbeat,
            args=(lease, stop_heartbeat, lease_lost),
            name=f"agentbus-heartbeat-{self.worker_id}",
            daemon=True,
        )
        heartbeat.start()
        executor = None
        context = None
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
                attempt_id=attempt.attempt_id,
                previous_attempts=[
                    item
                    for item in snapshot.attempts_for(task.task_id)
                    if item.attempt_id != attempt.attempt_id
                ],
                continuation=continuation,
                attempt_metadata=attempt.metadata,
            )
            context = self._prepare_attempt_context(executor, context)
            self._checkpoint("before-task-executor")
            try:
                raw_result = (
                    executor.execute(context)
                    if hasattr(executor, "execute")
                    else executor(context)
                )
                result = (
                    raw_result
                    if isinstance(raw_result, TaskExecutionResult)
                    else TaskExecutionResult.model_validate(raw_result)
                )
                result = self._prepare_retry_result(executor, context, result)
            finally:
                close = getattr(executor, "close", None)
                if close is not None:
                    close()
            result = result.model_copy(
                update={
                    "metadata": self._with_repository_baselines(
                        attempt.attempt_id,
                        result.metadata,
                    )
                }
            )
            for artifact in result.artifacts:
                self.store.record_artifact(artifact)
            if result.failure_category == FailureCategory.CANCELLED:
                return self._persist_cancellation(
                    task,
                    lease,
                    attempt.attempt_id,
                    worktree,
                )
            if _pending_tool_approval(result) is not None:
                return self._persist_approval_pause(
                    task,
                    lease,
                    attempt.attempt_id,
                    result,
                    worktree,
                )
            if not result.succeeded:
                return self._persist_failure(task, lease, attempt.attempt_id, result, worktree)
            self._checkpoint("after-task-executor")
            if lease_lost.is_set():
                return self._result(
                    task,
                    lease,
                    WorkerStatus.LEASE_LOST,
                    "Worker lease was lost during execution.",
                    worktree,
                )
            with self._operation("worker.task_commit"):
                repository = GitRepository(str(worktree.path))
                changes = repository.change_set(
                    result.changed_files or repository.changed_files()
                )
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
                self._checkpoint_completed_task(
                    run,
                    task,
                    attempt.attempt_id,
                    result,
                    commit_sha,
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
            if self._is_cancelled() and isinstance(
                self.cancellation,
                CancellationToken,
            ):
                self.cancellation.record_task_completed_after_request(
                    task.task_id
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
        except (CancellationRequested, ModelCancellationError, _WorkerCancellation):
            return self._persist_cancellation(
                task,
                lease,
                attempt.attempt_id,
                worktree,
            )
        except TaskExecutionError as exc:
            result = TaskExecutionResult(
                succeeded=False,
                summary="Task execution stopped safely.",
                failure_category=exc.category,
                error_message=str(exc),
                retryable=exc.retryable,
            )
            if executor is not None and context is not None:
                result = self._prepare_retry_result(executor, context, result)
            return self._persist_failure(
                task,
                lease,
                attempt.attempt_id,
                result,
                worktree,
            )
        except Exception as exc:
            classification = FailureClassifier().classify(exc)
            interrupted = classification.category == FailureCategory.UNKNOWN
            result = TaskExecutionResult(
                succeeded=False,
                summary=(
                    "Worker interrupted."
                    if interrupted
                    else "Task executor raised an exception."
                ),
                failure_category=(
                    FailureCategory.INTERRUPTED
                    if interrupted
                    else classification.category
                ),
                error_message=classification.message,
                retryable=True if interrupted else classification.retryable,
                metadata=(
                    {"provider_failure": classification.metadata}
                    if classification.metadata
                    else {}
                ),
            )
            if executor is not None and context is not None:
                result = self._prepare_retry_result(executor, context, result)
            if interrupted:
                return self._persist_interruption(
                    task,
                    lease,
                    attempt.attempt_id,
                    worktree,
                    classification.message,
                    result=result,
                )
            return self._persist_failure(
                task,
                lease,
                attempt.attempt_id,
                result,
                worktree,
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

    def _cancel_without_attempt(
        self,
        task: TaskRecord,
        lease: WorkerLease,
        *,
        worktree: WorktreeRecord | None = None,
    ) -> WorkerResult:
        changed_files: list[str] = []
        if worktree is not None:
            changed_files, _ = self._worktree_observations(worktree)
            self.store.update_worktree(
                worktree.worktree_id,
                status=WorktreeStatus.CLEANUP_PENDING,
                event_type="worktree_cancellation_pending",
            )
        current = self.store.get_task(task.run_id, task.task_id)
        if current.status in {
            TaskStatus.PENDING,
            TaskStatus.READY,
            TaskStatus.RUNNING,
            TaskStatus.RETRYABLE,
        }:
            self.store.update_task_status(
                task.run_id,
                task.task_id,
                TaskStatus.CANCELLED,
                event_type="worker_cancelled",
                event_payload={
                    "worker_id": self.worker_id,
                    "before_attempt": True,
                },
            )
        if changed_files:
            run = self.store.get_run(task.run_id)
            self.store.update_run_details(
                task.run_id,
                changed_files=sorted(set(run.changed_files) | set(changed_files)),
                event_type="cancelled_worker_side_effects_observed",
            )
        self._release_lease(lease)
        return self._result(
            task,
            lease,
            WorkerStatus.CANCELLED,
            "Worker stopped before task execution.",
            worktree,
            changed_files=changed_files,
        )

    def _persist_cancellation(
        self,
        task: TaskRecord,
        lease: WorkerLease,
        attempt_id: str,
        worktree: WorktreeRecord,
    ) -> WorkerResult:
        changed_files, artifact_hygiene = self._worktree_observations(worktree)
        try:
            self.store.complete_attempt(
                attempt_id,
                AttemptStatus.INTERRUPTED,
                error_category=FailureCategory.CANCELLED,
                error_message="Worker stopped after cancellation was requested.",
                metadata={
                    **self._repository_baseline_metadata(attempt_id),
                    "changed_files": changed_files,
                    "artifact_hygiene": artifact_hygiene,
                },
                event_type="task_attempt_cancelled",
            )
            current = self.store.get_task(task.run_id, task.task_id)
            if current.status == TaskStatus.RUNNING:
                self.store.update_task_status(
                    task.run_id,
                    task.task_id,
                    TaskStatus.CANCELLED,
                    event_type="worker_cancelled",
                    event_payload={"worker_id": self.worker_id},
                )
            self.store.update_worktree(
                worktree.worktree_id,
                status=WorktreeStatus.CLEANUP_PENDING,
                event_type="worktree_cancellation_pending",
            )
        except StateStoreError:
            pass
        return self._result(
            task,
            lease,
            WorkerStatus.CANCELLED,
            "Worker stopped after cancellation was requested.",
            worktree,
            changed_files=changed_files,
        )

    def _persist_failure(self, task, lease, attempt_id, result, worktree):
        if (
            result.failure_category == FailureCategory.CANCELLED
            or self._is_cancelled()
        ):
            return self._persist_cancellation(
                task,
                lease,
                attempt_id,
                worktree,
            )
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

    def _persist_approval_pause(
        self,
        task: TaskRecord,
        lease: WorkerLease,
        attempt_id: str,
        result: TaskExecutionResult,
        worktree: WorktreeRecord,
    ) -> WorkerResult:
        pending = _pending_tool_approval(result)
        assert pending is not None
        internal = result.metadata.get("_agentbus", {})
        continuation = (
            internal.get("task_continuation")
            if isinstance(internal, dict)
            else None
        )
        if not isinstance(continuation, dict) or not continuation:
            continuation = (
                internal.get("loop_continuation")
                if isinstance(internal, dict)
                else None
            )
        changed_files, artifact_hygiene = self._worktree_observations(worktree)
        metadata = {
            **result.metadata,
            "worker_id": self.worker_id,
            "lease_id": lease.lease_id,
            "fencing_token": lease.fencing_token,
            "worktree_id": worktree.worktree_id,
            "changed_files": sorted(
                set(changed_files) | set(result.changed_files)
            ),
            "artifact_hygiene": artifact_hygiene,
        }
        if not isinstance(continuation, dict) or not continuation:
            self.store.fail_attempt_resumability(
                attempt_id,
                "Parallel tool approval pause omitted its durable continuation.",
                metadata=metadata,
            )
            return self._result(
                task,
                lease,
                WorkerStatus.FAILED,
                "Parallel task continuation could not be persisted safely.",
                worktree,
                changed_files=changed_files,
            )
        approval_id = str(pending.get("approval_id") or "")
        invocation_id = str(pending.get("invocation_id") or "")
        if not approval_id or not invocation_id:
            self.store.fail_attempt_resumability(
                attempt_id,
                "Parallel tool approval suspension metadata is incomplete.",
                metadata=metadata,
            )
            return self._result(
                task,
                lease,
                WorkerStatus.FAILED,
                "Parallel task continuation could not be persisted safely.",
                worktree,
                changed_files=changed_files,
            )
        self.store.suspend_attempt_for_tool_approval(
            attempt_id,
            metadata=metadata,
            approval_id=approval_id,
            invocation_id=invocation_id,
            tool_name=str(pending.get("tool_name") or "unknown"),
            observation_summary=result.summary,
        )
        if self.runtime_trace is not None:
            attempt = self.store.get_attempt(attempt_id)
            self.runtime_trace.replay_checkpoint(
                "approval_requested",
                f"tool-approval-requested-{task.task_id}",
                task_id=task.task_id,
                durable_state={
                    "approval_kind": "tool",
                    "approval_id": approval_id,
                    "attempt_id": attempt_id,
                    "attempt_number": attempt.attempt_number,
                    "invocation_id": invocation_id,
                    "tool_name": pending.get("tool_name"),
                    "worker_id": self.worker_id,
                },
            )
        return self._result(
            task,
            lease,
            WorkerStatus.WAITING_FOR_APPROVAL,
            result.summary,
            worktree,
            changed_files=changed_files,
        )

    def _persist_interruption(
        self,
        task,
        lease,
        attempt_id,
        worktree,
        message,
        *,
        result: TaskExecutionResult | None = None,
    ):
        changed_files, artifact_hygiene = self._worktree_observations(worktree)
        try:
            self.store.complete_attempt(
                attempt_id,
                AttemptStatus.INTERRUPTED,
                error_category=FailureCategory.INTERRUPTED,
                error_message=message,
                metadata={
                    **self._repository_baseline_metadata(attempt_id),
                    **(result.metadata if result is not None else {}),
                    "changed_files": changed_files,
                    "artifact_hygiene": artifact_hygiene,
                },
            )
            current = self.store.get_task(task.run_id, task.task_id)
            if current.status == TaskStatus.RUNNING:
                retry = current.current_attempt_count < current.spec.maximum_attempts
                self.store.update_task_status(
                    task.run_id,
                    task.task_id,
                    TaskStatus.RETRYABLE if retry else TaskStatus.FAILED,
                    event_type="worker_failed",
                    event_payload={
                        "worker_id": self.worker_id,
                        "interrupted": True,
                        "retry_exhausted": not retry,
                    },
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

    def _prepare_attempt_context(
        self,
        executor,
        context: TaskExecutionContext,
    ) -> TaskExecutionContext:
        prepare = getattr(executor, "prepare_attempt", None)
        if prepare is None:
            return context
        updates = prepare(context)
        if not isinstance(updates, dict) or not updates:
            raise StateStoreError(
                "Task executor baseline preparation returned no durable metadata."
            )
        if context.attempt_id is None:
            raise StateStoreError(
                "Task baseline preparation requires an active attempt identity."
            )
        attempt = self.store.checkpoint_attempt_metadata(
            context.attempt_id,
            metadata_updates=updates,
            event_type="task_repository_baselines_checkpointed",
        )
        return context.model_copy(update={"attempt_metadata": attempt.metadata})

    @staticmethod
    def _prepare_retry_result(
        executor,
        context: TaskExecutionContext,
        result: TaskExecutionResult,
    ) -> TaskExecutionResult:
        prepare = getattr(executor, "prepare_retry_result", None)
        if prepare is None:
            return result
        try:
            prepared = prepare(context, result)
            if not isinstance(prepared, TaskExecutionResult):
                prepared = TaskExecutionResult.model_validate(prepared)
            return prepared
        except Exception as exc:
            classification = FailureClassifier().classify(exc)
            metadata = dict(result.metadata)
            if classification.metadata:
                metadata["retry_evidence_failure"] = classification.metadata
            return TaskExecutionResult(
                succeeded=False,
                summary="Corrective retry evidence could not be persisted safely.",
                artifacts=result.artifacts,
                failure_category=FailureCategory.RESUMABILITY_FAILURE,
                error_message=classification.message,
                retryable=False,
                verifier_status=result.verifier_status,
                reviewer_status=result.reviewer_status,
                changed_files=result.changed_files,
                metadata=metadata,
            )

    def _repository_baseline_metadata(self, attempt_id: str) -> dict[str, Any]:
        baselines = self.store.get_attempt(attempt_id).metadata.get(
            "repository_baselines"
        )
        return (
            {"repository_baselines": baselines}
            if isinstance(baselines, dict)
            else {}
        )

    def _with_repository_baselines(
        self,
        attempt_id: str,
        metadata: dict[str, Any],
    ) -> dict[str, Any]:
        return {
            **metadata,
            **self._repository_baseline_metadata(attempt_id),
        }

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

    def _checkpoint(self, stage: str) -> None:
        if isinstance(self.cancellation, CancellationToken):
            self.cancellation.checkpoint(
                f"worker:{self.worker_id}",
                stage=stage,
            )
        elif self.cancellation.is_set():
            raise _WorkerCancellation("Worker cancellation requested.")

    def _operation(self, name: str):
        if not isinstance(self.cancellation, CancellationToken):
            return nullcontext()
        return self.cancellation.operation(
            name,
            source=f"worker:{self.worker_id}",
            interruptible=False,
        )

    def _is_cancelled(self) -> bool:
        return self.cancellation.is_set()

    def _checkpoint_completed_task(
        self,
        run: RunRecord,
        task: TaskRecord,
        attempt_id: str,
        result: TaskExecutionResult,
        commit_sha: str,
    ) -> None:
        if self.runtime_trace is None:
            return
        snapshot = self.store.load_snapshot(run.run_id)
        completed = sorted(
            item.task_id
            for item in snapshot.tasks
            if item.status
            in {
                TaskStatus.SUCCEEDED,
                TaskStatus.INTEGRATION_PENDING,
            }
        )
        self.runtime_trace.replay_checkpoint(
            "task_completed",
            f"task-completed-{task.task_id}",
            task_id=task.task_id,
            completed_task_ids=completed,
            required_task_ids=task.spec.dependency_ids,
            durable_state={
                "attempt_id": attempt_id,
                "worker_id": self.worker_id,
                "task_commit": commit_sha,
                "changed_files": result.changed_files,
                **_repository_baseline_trace_metadata(result.metadata),
                "task_status": self.store.get_task(
                    run.run_id,
                    task.task_id,
                ).status.value,
            },
            base_commit=commit_sha,
        )

    def _release_lease(self, lease: WorkerLease) -> None:
        try:
            self.lease_service.release_lease(
                lease.lease_id,
                self.worker_id,
                lease.fencing_token,
            )
        except LeaseError:
            pass

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


def _pending_tool_approval(result: TaskExecutionResult) -> dict[str, Any] | None:
    internal = result.metadata.get("_agentbus", {})
    if not isinstance(internal, dict):
        return None
    pending = internal.get("tool_approval_pending")
    return pending if isinstance(pending, dict) else None


def _repository_baseline_trace_metadata(metadata: dict[str, Any]) -> dict[str, Any]:
    baselines = metadata.get("repository_baselines", {})
    if not isinstance(baselines, dict):
        return {}
    task = baselines.get("task", {})
    attempt = baselines.get("attempt", {})
    return {
        "task_baseline_identity_sha256": (
            task.get("identity_sha256") if isinstance(task, dict) else None
        ),
        "attempt_baseline_identity_sha256": (
            attempt.get("identity_sha256") if isinstance(attempt, dict) else None
        ),
        "candidate_source_identity_sha256": (
            metadata.get("verification_evidence", {}).get(
                "candidate_identity_sha256"
            )
            if isinstance(metadata.get("verification_evidence"), dict)
            else None
        ),
        "retry_workspace": baselines.get("retry_workspace"),
    }


def _attempt_continuation(attempt) -> dict[str, Any] | None:
    internal = attempt.metadata.get("_agentbus", {})
    if not isinstance(internal, dict):
        return None
    continuation = internal.get("task_continuation")
    if not isinstance(continuation, dict) or not continuation:
        continuation = internal.get("loop_continuation")
    return continuation if isinstance(continuation, dict) and continuation else None
