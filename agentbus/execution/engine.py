from __future__ import annotations

import uuid
import threading
from collections.abc import Callable
from typing import Any, Protocol

from agentbus import __version__
from agentbus.execution.cancellation import CancellationToken
from agentbus.execution.cancellation_registry import CancellationRegistry
from agentbus.execution.leases import (
    LeaseError,
    LeaseService,
    LeaseUnavailableError,
)
from agentbus.execution.models import (
    ApprovalOutcome,
    AttemptStatus,
    ExecutionReport,
    FailureCategory,
    GraphProgress,
    RetryPolicy,
    RiskLevel,
    RunRecord,
    RunSnapshot,
    RunStatus,
    TaskAttempt,
    TaskExecutionContext,
    TaskExecutionResult,
    TaskRecord,
    TaskStatus,
)
from agentbus.execution.retry import FailureClassifier, RetryController
from agentbus.execution.state_store import (
    AttemptLimitExceededError,
    NonterminalAttemptExistsError,
    RunNotFoundError,
    StateStore,
    StateStoreError,
)
from agentbus.execution.task_graph import TaskGraph
from agentbus.security.redaction import redact_text
from agentbus.trace import RuntimeTrace


class DurableExecutionError(RuntimeError):
    """Raised when the engine cannot safely make progress."""


class TaskExecutor(Protocol):
    def execute(self, context: TaskExecutionContext) -> TaskExecutionResult:
        ...


CrashHook = Callable[[str, TaskExecutionContext], None]


class DurableExecutionEngine:
    """Coordinates deterministic graph execution around persisted state."""

    def __init__(
        self,
        state_store: StateStore,
        task_executor: TaskExecutor | Callable[[TaskExecutionContext], Any] | None = None,
        *,
        retry_policy: RetryPolicy | None = None,
        failure_classifier: FailureClassifier | None = None,
        logger: Any | None = None,
        crash_hook: CrashHook | None = None,
        cancellation: CancellationToken | None = None,
        cancellation_registry: CancellationRegistry | None = None,
        runtime_trace: RuntimeTrace | None = None,
    ):
        self.store = state_store
        self.task_executor = task_executor
        self.retry = RetryController(retry_policy)
        self.failure_classifier = failure_classifier or FailureClassifier()
        self.logger = logger
        self.crash_hook = crash_hook
        self.cancellations = cancellation_registry or CancellationRegistry(
            state_store
        )
        self._explicit_cancellation = cancellation
        self._explicit_cancellation_run_id: str | None = None
        self.runtime_trace = runtime_trace
        self._resume_leases = LeaseService(state_store)
        self._resume_worker_id = f"durable-engine-{uuid.uuid4().hex}"
        self._execution_fenced = False

    def close(self) -> None:
        close = getattr(self.task_executor, "close", None)
        if close is not None:
            close()

    def __enter__(self) -> "DurableExecutionEngine":
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        self.close()

    def create_run(
        self,
        original_task: str,
        planner_output: dict[str, Any],
        *,
        workflow_type: str = "multi",
        model: str,
        workspace: str,
        context_summary: str | None = None,
        metadata: dict[str, Any] | None = None,
        run_id: str | None = None,
    ) -> RunRecord:
        graph = TaskGraph.from_planner_output(
            planner_output,
            workspace=workspace,
        )
        run_metadata = dict(metadata or {})
        run_metadata["agentbus_version"] = __version__
        raw_execution_metadata = run_metadata.get("execution", {})
        if not isinstance(raw_execution_metadata, dict):
            raise DurableExecutionError("Run execution metadata must be an object.")
        execution_metadata = dict(raw_execution_metadata)
        execution_metadata["retry_policy"] = self.retry.policy.model_dump(mode="json")
        run_metadata["execution"] = execution_metadata
        record = RunRecord(
            run_id=run_id or uuid.uuid4().hex,
            original_task=original_task,
            workflow_type=workflow_type,
            model=model,
            workspace=workspace,
            planner_output=planner_output,
            context_summary=context_summary,
            graph_data=graph.to_dict(),
            metadata=run_metadata,
        )
        try:
            self.store.get_run(record.run_id)
        except RunNotFoundError:
            self.store.create_run_with_tasks(record, graph.tasks)
        else:
            self.store.finalize_provisional_run(record, graph.tasks)
        self._cancellation_token(record.run_id)
        self.cancellations.synchronize(record.run_id)
        self._log(
            "durable_run_created",
            record.run_id,
            metadata={"workflow_type": workflow_type, "task_count": len(graph.tasks)},
        )
        self._log(
            "task_graph_validated",
            record.run_id,
            metadata={"task_count": len(graph.tasks), "graph_version": graph.VERSION},
        )
        return self.store.get_run(record.run_id)

    def run_until_blocked(self, run_id: str) -> ExecutionReport:
        self._execution_fenced = False
        if self._cancellation_token(run_id).is_requested:
            return self.finalize_cancellation(run_id)
        snapshot = self.store.load_snapshot(run_id)
        if snapshot.run.status in {
            RunStatus.SUCCEEDED,
            RunStatus.FAILED,
            RunStatus.CANCELLED,
            RunStatus.WAITING_FOR_REVIEW,
        }:
            return self._report(snapshot)

        self._recover_running_tasks(run_id)
        if self._cancellation_token(run_id).is_requested:
            return self.finalize_cancellation(run_id)
        run = self.store.get_run(run_id)
        if run.status == RunStatus.PENDING:
            self.store.update_run_status(
                run_id,
                RunStatus.RUNNING,
                event_type="durable_run_started",
            )
            self._log("durable_run_started", run_id)

        maximum_iterations = self._maximum_iterations(run_id)
        for _ in range(maximum_iterations):
            if self._cancellation_token(run_id).is_requested:
                return self.finalize_cancellation(run_id)
            report = self.execute_next(run_id)
            if self._execution_fenced:
                return report
            if report.status in {
                RunStatus.SUCCEEDED,
                RunStatus.FAILED,
                RunStatus.CANCELLED,
                RunStatus.WAITING_FOR_APPROVAL,
                RunStatus.WAITING_FOR_REVIEW,
            }:
                return report

        reason = "Execution stopped because the bounded progress guard was exhausted."
        run = self.store.get_run(run_id)
        if run.status == RunStatus.RUNNING:
            self._fail_run(run_id, reason)
        return self.get_report(run_id)

    def execute_next(self, run_id: str) -> ExecutionReport:
        cancellation = self._cancellation_token(run_id)
        if cancellation.is_requested:
            return self.finalize_cancellation(run_id)
        snapshot = self.store.load_snapshot(run_id)
        if snapshot.run.status in {
            RunStatus.SUCCEEDED,
            RunStatus.FAILED,
            RunStatus.CANCELLED,
            RunStatus.WAITING_FOR_APPROVAL,
            RunStatus.WAITING_FOR_REVIEW,
        }:
            return self._report(snapshot)
        if snapshot.run.status == RunStatus.PENDING:
            self.store.update_run_status(
                run_id,
                RunStatus.RUNNING,
                event_type="durable_run_started",
            )

        self._synchronize_graph(run_id)
        if cancellation.is_requested:
            return self.finalize_cancellation(run_id)
        snapshot = self.store.load_snapshot(run_id)
        if snapshot.run.status != RunStatus.RUNNING:
            return self._report(snapshot)

        resumable = next(
            (
                (task, attempts[-1])
                for task in snapshot.tasks
                if task.status == TaskStatus.RUNNING
                and (attempts := snapshot.attempts_for(task.task_id))
                and attempts[-1].status == AttemptStatus.RUNNING
                and _attempt_continuation(attempts[-1]) is not None
            ),
            None,
        )
        task_record = resumable[0] if resumable is not None else next(
            (task for task in snapshot.tasks if task.status == TaskStatus.READY),
            None,
        )
        if task_record is None:
            self._synchronize_graph(run_id)
            return self.get_report(run_id)

        if cancellation.is_requested:
            return self.finalize_cancellation(run_id)
        if resumable is not None:
            attempt = resumable[1]
            continuation = _attempt_continuation(attempt)
            self._log(
                "task_attempt_continued",
                run_id,
                task_id=task_record.task_id,
                attempt_number=attempt.attempt_number,
            )
        else:
            try:
                attempt = self.store.start_attempt(run_id, task_record.task_id)
            except NonterminalAttemptExistsError as exc:
                self._log(
                    "task_attempt_creation_fenced",
                    run_id,
                    task_id=task_record.task_id,
                    attempt_number=exc.attempt_number,
                    metadata={"attempt_status": exc.status.value},
                )
                return self.get_report(run_id)
            except AttemptLimitExceededError as exc:
                self.store.fail_attempt_exhaustion(run_id, task_record.task_id, str(exc))
                self._log(
                    "task_attempt_exhausted",
                    run_id,
                    task_id=task_record.task_id,
                    metadata={"maximum_attempts": exc.maximum_attempts},
                )
                return self.get_report(run_id)
            continuation = None
            self._log(
                "durable_task_started",
                run_id,
                task_id=task_record.task_id,
            )
            self._log(
                "task_attempt_started",
                run_id,
                task_id=task_record.task_id,
                attempt_number=attempt.attempt_number,
            )

        snapshot = self.store.load_snapshot(run_id)
        previous_attempts = [
            item
            for item in snapshot.attempts_for(task_record.task_id)
            if item.attempt_id != attempt.attempt_id
        ]
        context = TaskExecutionContext(
            run=snapshot.run,
            task=task_record.spec,
            attempt_number=attempt.attempt_number,
            attempt_id=attempt.attempt_id,
            previous_attempts=previous_attempts,
            continuation=continuation,
            attempt_metadata=attempt.metadata,
        )

        resume_lease = None
        heartbeat_stop: threading.Event | None = None
        heartbeat_thread: threading.Thread | None = None
        if continuation is not None:
            try:
                resume_lease = self._resume_leases.acquire_lease(
                    run_id,
                    task_record.task_id,
                    self._resume_worker_id,
                    metadata={
                        "purpose": "durable_continuation",
                        "attempt_id": attempt.attempt_id,
                        "attempt_number": attempt.attempt_number,
                    },
                )
            except LeaseUnavailableError:
                self._execution_fenced = True
                self.store.record_event(
                    run_id,
                    "task_continuation_resume_fenced",
                    {
                        "task_id": task_record.task_id,
                        "attempt_id": attempt.attempt_id,
                        "attempt_number": attempt.attempt_number,
                    },
                    task_id=task_record.task_id,
                )
                return self.get_report(run_id)
            heartbeat_stop, heartbeat_thread = self._start_resume_heartbeat(
                resume_lease
            )

        try:
            try:
                context = self._prepare_attempt_context(context)
            except Exception as exc:
                classification = self.failure_classifier.classify(exc)
                result = TaskExecutionResult(
                    succeeded=False,
                    summary="Task baseline preparation failed safely.",
                    failure_category=FailureCategory.RESUMABILITY_FAILURE,
                    error_message=classification.message,
                    retryable=False,
                    metadata=(
                        {"baseline_failure": classification.metadata}
                        if classification.metadata
                        else {}
                    ),
                )
                self._persist_execution_result(attempt, task_record, result)
                self._synchronize_graph(run_id)
                return self.get_report(run_id)

            if self.crash_hook is not None:
                # Baselines are durable before emulating a process disappearance.
                self.crash_hook("after_attempt_started", context)
            try:
                result = self._execute(context)
            except Exception as exc:
                classification = self.failure_classifier.classify(exc)
                result = TaskExecutionResult(
                    succeeded=False,
                    summary="Task executor raised an exception.",
                    failure_category=classification.category,
                    error_message=classification.message,
                    retryable=classification.retryable,
                    metadata=(
                        {"provider_failure": classification.metadata}
                        if classification.metadata
                        else {}
                    ),
                )

            if resume_lease is not None:
                self._resume_leases.validate_fencing_token(
                    resume_lease.lease_id,
                    self._resume_worker_id,
                    resume_lease.fencing_token,
                )
            self._persist_execution_result(attempt, task_record, result)
            if result.succeeded:
                self._checkpoint_completed_task(attempt, task_record, result)
        except LeaseError:
            self._execution_fenced = True
            self.store.record_event(
                run_id,
                "task_continuation_fence_lost",
                {
                    "task_id": task_record.task_id,
                    "attempt_id": attempt.attempt_id,
                    "attempt_number": attempt.attempt_number,
                },
                task_id=task_record.task_id,
            )
            return self.get_report(run_id)
        finally:
            if heartbeat_stop is not None:
                heartbeat_stop.set()
            if heartbeat_thread is not None:
                heartbeat_thread.join(timeout=5)
            if resume_lease is not None:
                try:
                    self._resume_leases.release_lease(
                        resume_lease.lease_id,
                        self._resume_worker_id,
                        resume_lease.fencing_token,
                    )
                except LeaseError:
                    pass

        if cancellation.is_requested:
            if result.succeeded:
                cancellation.record_task_completed_after_request(
                    task_record.task_id
                )
            return self.finalize_cancellation(run_id)
        self._synchronize_graph(run_id)
        return self.get_report(run_id)

    def resume(self, run_id: str) -> ExecutionReport:
        run = self.store.get_run(run_id)
        if run.status in {
            RunStatus.SUCCEEDED,
            RunStatus.FAILED,
            RunStatus.CANCELLED,
            RunStatus.WAITING_FOR_REVIEW,
        }:
            return self.get_report(run_id)
        if self._explicit_cancellation is None:
            self.cancellations.recover(run_id)
        if self._cancellation_token(run_id).is_requested:
            return self.finalize_cancellation(run_id)
        self.store.record_event(
            run_id,
            "durable_run_resumed",
            {"persisted_status": run.status.value},
        )
        self._log(
            "durable_run_resumed",
            run_id,
            metadata={"persisted_status": run.status.value},
        )
        self._recover_running_tasks(run_id)
        self._reactivate_after_persisted_approval(run_id)
        return self.run_until_blocked(run_id)

    def approve_task(
        self,
        run_id: str,
        task_id: str,
        reason: str | None = None,
    ) -> ExecutionReport:
        task = self.store.get_task(run_id, task_id)
        if task.status != TaskStatus.WAITING_FOR_APPROVAL:
            raise DurableExecutionError(
                f"Task '{task_id}' is not waiting for approval; current status is "
                f"'{task.status.value}'."
            )
        attempts = self.store.list_attempts(run_id, task_id)
        latest_attempt = attempts[-1] if attempts else None
        pending_tool = (
            _pending_tool_approval(latest_attempt)
            if latest_attempt is not None
            else None
        )
        if pending_tool is not None:
            approval_id = str(pending_tool.get("approval_id") or "")
            if not approval_id:
                raise DurableExecutionError(
                    "Persisted tool approval suspension is incomplete."
                )
            self.store.decide_tool_approval(
                run_id,
                approval_id,
                disposition="approved",
                reason=reason,
            )
            self._log(
                "tool_approval_approved",
                run_id,
                task_id=task_id,
                attempt_number=latest_attempt.attempt_number,
                metadata={"approval_id": approval_id},
            )
            self._checkpoint_tool_approval(
                run_id,
                latest_attempt,
                pending_tool,
                ApprovalOutcome.APPROVED,
            )
            self._reactivate_after_persisted_approval(run_id)
            return self.get_report(run_id)

        self.store.record_approval(
            run_id,
            task_id,
            ApprovalOutcome.APPROVED,
            reason,
        )
        self._log("task_approved", run_id, task_id=task_id)
        self.store.update_task_status(
            run_id,
            task_id,
            TaskStatus.READY,
            event_type="approved_task_ready",
        )
        run = self.store.get_run(run_id)
        if run.status == RunStatus.WAITING_FOR_APPROVAL:
            self.store.update_run_status(
                run_id,
                RunStatus.RUNNING,
                event_type="durable_run_approval_received",
            )
        self._checkpoint_approval(
            run_id,
            task_id,
            ApprovalOutcome.APPROVED,
        )
        return self.get_report(run_id)

    def reject_task(
        self,
        run_id: str,
        task_id: str,
        reason: str | None = None,
    ) -> ExecutionReport:
        task = self.store.get_task(run_id, task_id)
        if task.status != TaskStatus.WAITING_FOR_APPROVAL:
            raise DurableExecutionError(
                f"Task '{task_id}' is not waiting for approval; current status is "
                f"'{task.status.value}'."
            )
        attempts = self.store.list_attempts(run_id, task_id)
        latest_attempt = attempts[-1] if attempts else None
        pending_tool = (
            _pending_tool_approval(latest_attempt)
            if latest_attempt is not None
            else None
        )
        if pending_tool is not None:
            approval_id = str(pending_tool.get("approval_id") or "")
            if not approval_id:
                self._fail_resumability(
                    latest_attempt,
                    "Persisted tool approval suspension is incomplete.",
                )
                return self.get_report(run_id)
            self.store.decide_tool_approval(
                run_id,
                approval_id,
                disposition="rejected",
                reason=reason,
            )
            self._log(
                "tool_approval_rejected",
                run_id,
                task_id=task_id,
                attempt_number=latest_attempt.attempt_number,
                metadata={"approval_id": approval_id},
            )
            self._checkpoint_tool_approval(
                run_id,
                latest_attempt,
                pending_tool,
                ApprovalOutcome.REJECTED,
            )
            self._reactivate_after_persisted_approval(run_id)
            self._synchronize_graph(run_id)
            if self.store.get_run(run_id).status == RunStatus.RUNNING:
                self._fail_run(
                    run_id,
                    f"Tool invocation for task '{task_id}' was rejected"
                    + (f": {reason}" if reason else "."),
                )
            return self.get_report(run_id)

        self.store.record_approval(
            run_id,
            task_id,
            ApprovalOutcome.REJECTED,
            reason,
        )
        self._log("task_rejected", run_id, task_id=task_id)
        self.store.update_task_status(
            run_id,
            task_id,
            TaskStatus.REJECTED,
            event_type="task_rejected",
            event_payload={"reason": reason},
        )
        run = self.store.get_run(run_id)
        if run.status == RunStatus.WAITING_FOR_APPROVAL:
            self.store.update_run_status(
                run_id,
                RunStatus.RUNNING,
                event_type="durable_run_rejection_received",
            )
        self._synchronize_graph(run_id)
        run = self.store.get_run(run_id)
        if run.status == RunStatus.RUNNING:
            self._fail_run(
                run_id,
                f"High-risk task '{task_id}' was rejected"
                + (f": {reason}" if reason else "."),
            )
        self._checkpoint_approval(
            run_id,
            task_id,
            ApprovalOutcome.REJECTED,
        )
        return self.get_report(run_id)

    def cancel_run(
        self,
        run_id: str,
        reason: str | None = None,
    ) -> ExecutionReport:
        snapshot = self.store.load_snapshot(run_id)
        if snapshot.run.status in {
            RunStatus.SUCCEEDED,
            RunStatus.FAILED,
            RunStatus.CANCELLED,
        }:
            return self._report(snapshot)
        cancellation = self._cancellation_token(run_id)
        cancellation.request(reason or "Cancelled by user.")
        cancellation.mark_propagated("durable-engine")
        current = self.store.load_snapshot(run_id)
        active_task = any(
            task.status in {TaskStatus.RUNNING, TaskStatus.INTEGRATING}
            for task in current.tasks
        )
        active_attempt = any(
            attempt.status == AttemptStatus.RUNNING
            for attempt in current.attempts
        )
        if (
            not cancellation.snapshot().active_operations
            and not active_task
            and not active_attempt
        ):
            return self.finalize_cancellation(run_id)
        return self.get_report(run_id)

    def request_cancellation(
        self,
        run_id: str,
        reason: str | None = None,
    ) -> ExecutionReport:
        snapshot = self.store.load_snapshot(run_id)
        if snapshot.run.status in {
            RunStatus.SUCCEEDED,
            RunStatus.FAILED,
            RunStatus.CANCELLED,
        }:
            return self._report(snapshot)
        cancellation = self._cancellation_token(run_id)
        cancellation.request(reason or "Cancelled by user.")
        cancellation.mark_propagated("durable-engine")
        return self.get_report(run_id)

    def finalize_cancellation(self, run_id: str) -> ExecutionReport:
        cancellation = self._cancellation_token(run_id)
        state = cancellation.snapshot()
        if not state.requested:
            return self.get_report(run_id)
        if state.active_operations:
            return self.get_report(run_id)

        run = self.store.get_run(run_id)
        if run.status == RunStatus.CANCELLED:
            cancellation.complete_cleanup(
                terminal_reason=(
                    state.terminal_reason or "Cancellation completed safely."
                ),
                resume_eligible=False,
            )
            return self.get_report(run_id)
        if run.status in {RunStatus.SUCCEEDED, RunStatus.FAILED}:
            return self.get_report(run_id)

        cancellation.acknowledge(
            "durable-engine",
            stage="cancellation-finalization",
        )
        snapshot = self.store.load_snapshot(run_id)
        prevented = [
            task.task_id
            for task in snapshot.tasks
            if task.status
            in {
                TaskStatus.PENDING,
                TaskStatus.READY,
                TaskStatus.WAITING_FOR_APPROVAL,
                TaskStatus.RETRYABLE,
                TaskStatus.INTEGRATION_PENDING,
                TaskStatus.INTEGRATING,
            }
        ]
        cancellation.mark_scheduling_stopped(prevented)

        attempts_by_task = {
            task.task_id: snapshot.attempts_for(task.task_id)
            for task in snapshot.tasks
        }
        for task in snapshot.tasks:
            if task.status == TaskStatus.SUCCEEDED:
                continue
            if task.status not in {
                TaskStatus.PENDING,
                TaskStatus.READY,
                TaskStatus.RUNNING,
                TaskStatus.WAITING_FOR_APPROVAL,
                TaskStatus.RETRYABLE,
                TaskStatus.INTEGRATION_PENDING,
                TaskStatus.INTEGRATING,
            }:
                continue
            attempts = attempts_by_task[task.task_id]
            latest = attempts[-1] if attempts else None
            if (
                task.status == TaskStatus.RUNNING
                and latest is not None
                and latest.status == AttemptStatus.SUCCEEDED
            ):
                self.store.update_task_status(
                    run_id,
                    task.task_id,
                    TaskStatus.SUCCEEDED,
                    event_type="cancelled_run_completed_task_recovered",
                )
                cancellation.record_task_completed_after_request(task.task_id)
                continue
            if latest is not None and latest.status in {
                AttemptStatus.RUNNING,
                AttemptStatus.WAITING_FOR_APPROVAL,
            }:
                self.store.complete_attempt(
                    latest.attempt_id,
                    AttemptStatus.INTERRUPTED,
                    error_category=FailureCategory.CANCELLED,
                    error_message="Attempt stopped after cancellation was requested.",
                    event_type="task_attempt_cancelled",
                )
            self.store.update_task_status(
                run_id,
                task.task_id,
                TaskStatus.CANCELLED,
                event_type="task_cancelled",
                event_payload={"cancellation_requested": True},
            )

        reason = state.reason or "Cancelled by user."
        current = self.store.get_run(run_id)
        if current.status not in {
            RunStatus.SUCCEEDED,
            RunStatus.FAILED,
            RunStatus.CANCELLED,
        }:
            self.store.update_run_status(
                run_id,
                RunStatus.CANCELLED,
                failure_reason=reason,
                event_type="run_cancelled",
                event_payload={
                    "acknowledged": cancellation.snapshot().acknowledged,
                    "tasks_prevented_from_starting": prevented,
                },
            )
            self._log(
                "run_cancelled",
                run_id,
                metadata={"task_count_prevented": len(prevented)},
            )
        cancellation.complete_cleanup(
            terminal_reason="Cancellation completed safely.",
            resume_eligible=False,
        )
        return self.get_report(run_id)

    def get_report(self, run_id: str) -> ExecutionReport:
        return self._report(self.store.load_snapshot(run_id))

    def _start_resume_heartbeat(self, lease):
        stop = threading.Event()
        interval = max(0.1, min(30.0, self._resume_leases.lease_seconds / 3.0))

        def heartbeat() -> None:
            while not stop.wait(interval):
                try:
                    self._resume_leases.renew_lease(
                        lease.lease_id,
                        self._resume_worker_id,
                        lease.fencing_token,
                    )
                except LeaseError:
                    return

        thread = threading.Thread(
            target=heartbeat,
            name=f"agentbus-resume-{lease.lease_id[:8]}",
            daemon=True,
        )
        thread.start()
        return stop, thread

    def _execute(self, context: TaskExecutionContext) -> TaskExecutionResult:
        if self.task_executor is None:
            raise DurableExecutionError(
                "No task executor is configured; this operation can inspect state but "
                "cannot execute tasks."
            )
        if hasattr(self.task_executor, "execute"):
            raw_result = self.task_executor.execute(context)  # type: ignore[union-attr]
        else:
            raw_result = self.task_executor(context)  # type: ignore[operator]
        if isinstance(raw_result, TaskExecutionResult):
            return raw_result
        return TaskExecutionResult.model_validate(raw_result)

    def _prepare_attempt_context(
        self,
        context: TaskExecutionContext,
    ) -> TaskExecutionContext:
        if self.task_executor is None:
            return context
        prepare = getattr(self.task_executor, "prepare_attempt", None)
        if prepare is None:
            return context
        updates = prepare(context)
        if not isinstance(updates, dict) or not updates:
            raise DurableExecutionError(
                "Task executor baseline preparation returned no durable metadata."
            )
        if context.attempt_id is None:
            raise DurableExecutionError(
                "Task baseline preparation requires an active attempt identity."
            )
        attempt = self.store.checkpoint_attempt_metadata(
            context.attempt_id,
            metadata_updates=updates,
            event_type="task_repository_baselines_checkpointed",
        )
        return context.model_copy(update={"attempt_metadata": attempt.metadata})

    def _persist_execution_result(
        self,
        attempt: TaskAttempt,
        task_record: TaskRecord,
        result: TaskExecutionResult,
    ) -> None:
        run_id = attempt.run_id
        task_id = attempt.task_id
        for artifact in result.artifacts:
            if artifact.run_id != run_id or artifact.task_id not in {None, task_id}:
                raise DurableExecutionError(
                    "Executor artifact identifiers must match the active run and task."
                )
            self.store.record_artifact(artifact)

        current_run = self.store.get_run(run_id)
        changed_files = sorted(set(current_run.changed_files) | set(result.changed_files))
        self.store.update_run_details(
            run_id,
            verifier_status=result.verifier_status,
            reviewer_status=result.reviewer_status,
            changed_files=changed_files if changed_files else None,
            event_type="task_execution_observed",
        )

        attempt_metadata = dict(result.metadata)
        checkpointed = self.store.get_attempt(attempt.attempt_id).metadata.get(
            "repository_baselines"
        )
        if isinstance(checkpointed, dict):
            attempt_metadata["repository_baselines"] = checkpointed
        agentbus_metadata = dict(attempt_metadata.get("_agentbus", {}))
        agentbus_metadata["retryable_override"] = result.retryable
        attempt_metadata["_agentbus"] = agentbus_metadata

        pending_tool_approval = agentbus_metadata.get("tool_approval_pending")
        if isinstance(pending_tool_approval, dict):
            approval_id = str(pending_tool_approval.get("approval_id") or "")
            invocation_id = str(
                pending_tool_approval.get("invocation_id") or ""
            )
            if not approval_id or not invocation_id:
                self._fail_resumability(
                    attempt,
                    "Tool approval suspension metadata is incomplete.",
                    metadata=attempt_metadata,
                )
                return
            continuation = agentbus_metadata.get("task_continuation")
            if not isinstance(continuation, dict) or not continuation:
                continuation = agentbus_metadata.get("loop_continuation")
            if not isinstance(continuation, dict) or not continuation:
                self._fail_resumability(
                    attempt,
                    "Tool approval suspension omitted its durable task continuation.",
                    metadata=attempt_metadata,
                )
                return
            self.store.suspend_attempt_for_tool_approval(
                attempt.attempt_id,
                metadata=attempt_metadata,
                approval_id=approval_id,
                invocation_id=invocation_id,
                tool_name=str(pending_tool_approval.get("tool_name") or "unknown"),
                observation_summary=result.summary,
            )
            self._log(
                "task_awaiting_tool_approval",
                run_id,
                task_id=task_id,
                attempt_number=attempt.attempt_number,
                metadata={"approval_id": approval_id},
            )
            self._checkpoint_tool_approval_suspension(
                attempt,
                pending_tool_approval,
            )
            return

        if result.succeeded:
            self.store.complete_attempt(
                attempt.attempt_id,
                AttemptStatus.SUCCEEDED,
                observation_summary=result.summary,
                metadata=attempt_metadata,
            )
            self._log(
                "task_attempt_succeeded",
                run_id,
                task_id=task_id,
                attempt_number=attempt.attempt_number,
            )
            self.store.update_task_status(
                run_id,
                task_id,
                TaskStatus.SUCCEEDED,
                event_type="durable_task_succeeded",
                event_payload={"attempt_number": attempt.attempt_number},
            )
            return

        category = result.failure_category or FailureCategory.UNKNOWN
        message = result.error_message or result.summary
        if category == FailureCategory.CANCELLED:
            self.store.complete_attempt(
                attempt.attempt_id,
                AttemptStatus.INTERRUPTED,
                error_category=category,
                error_message=message,
                observation_summary=result.summary,
                metadata=attempt_metadata,
                event_type="task_attempt_cancelled",
            )
            current = self.store.get_task(run_id, task_id)
            if current.status == TaskStatus.RUNNING:
                self.store.update_task_status(
                    run_id,
                    task_id,
                    TaskStatus.CANCELLED,
                    event_type="task_cancelled",
                    event_payload={"cancellation_requested": True},
                )
            return
        self.store.complete_attempt(
            attempt.attempt_id,
            AttemptStatus.FAILED,
            error_category=category,
            error_message=message,
            observation_summary=result.summary,
            metadata=attempt_metadata,
        )
        self._log(
            "task_attempt_failed",
            run_id,
            task_id=task_id,
            attempt_number=attempt.attempt_number,
            metadata={"error_category": category.value},
        )
        decision = self._retry_controller(self.store.get_run(run_id)).decide(
            category=category,
            attempt_number=attempt.attempt_number,
            task_maximum_attempts=task_record.spec.maximum_attempts,
            retryable_override=result.retryable,
        )
        if decision.should_retry:
            self.store.update_task_status(
                run_id,
                task_id,
                TaskStatus.RETRYABLE,
                event_type="task_retry_scheduled",
                event_payload={
                    "attempt_number": attempt.attempt_number,
                    "next_attempt_number": attempt.attempt_number + 1,
                    "delay_seconds": decision.delay_seconds,
                    "error_category": category.value,
                },
            )
            self._log(
                "task_retry_scheduled",
                run_id,
                task_id=task_id,
                attempt_number=attempt.attempt_number,
                metadata={"delay_seconds": decision.delay_seconds},
            )
            self.store.update_task_status(
                run_id,
                task_id,
                TaskStatus.READY,
                event_type="durable_task_ready",
                event_payload={"retry": True},
            )
            return

        self.store.update_task_status(
            run_id,
            task_id,
            TaskStatus.FAILED,
            event_type="durable_task_failed",
            event_payload={
                "attempt_number": attempt.attempt_number,
                "error_category": category.value,
                "retry_exhausted": decision.exhausted,
            },
        )

    def _checkpoint_completed_task(
        self,
        attempt: TaskAttempt,
        task_record: TaskRecord,
        result: TaskExecutionResult,
    ) -> None:
        if self.runtime_trace is None:
            return
        snapshot = self.store.load_snapshot(attempt.run_id)
        completed = sorted(
            task.task_id
            for task in snapshot.tasks
            if task.status
            in {
                TaskStatus.SUCCEEDED,
                TaskStatus.INTEGRATION_PENDING,
            }
        )
        self.runtime_trace.replay_checkpoint(
            "task_completed",
            f"task-completed-{task_record.task_id}",
            task_id=task_record.task_id,
            completed_task_ids=completed,
            required_task_ids=task_record.spec.dependency_ids,
            durable_state={
                "attempt_id": attempt.attempt_id,
                "attempt_number": attempt.attempt_number,
                "task_status": self.store.get_task(
                    attempt.run_id,
                    task_record.task_id,
                ).status.value,
                "changed_files": result.changed_files,
                **_repository_baseline_trace_metadata(result.metadata),
            },
        )

    def _checkpoint_approval(
        self,
        run_id: str,
        task_id: str,
        outcome: ApprovalOutcome,
    ) -> None:
        if self.runtime_trace is None:
            return
        snapshot = self.store.load_snapshot(run_id)
        completed = sorted(
            task.task_id
            for task in snapshot.tasks
            if task.status == TaskStatus.SUCCEEDED
        )
        self.runtime_trace.replay_checkpoint(
            "approval_decided",
            f"approval-{outcome.value}-{task_id}",
            task_id=task_id,
            completed_task_ids=completed,
            durable_state={
                "decision": outcome.value,
                "task_status": self.store.get_task(run_id, task_id).status.value,
            },
        )

    def _checkpoint_tool_approval(
        self,
        run_id: str,
        attempt: TaskAttempt,
        pending: dict[str, Any],
        outcome: ApprovalOutcome,
    ) -> None:
        if self.runtime_trace is None:
            return
        self.runtime_trace.replay_checkpoint(
            "approval_decided",
            f"tool-approval-{outcome.value}-{attempt.task_id}",
            task_id=attempt.task_id,
            durable_state={
                "approval_kind": "tool",
                "approval_id": pending.get("approval_id"),
                "decision": outcome.value,
                "attempt_id": attempt.attempt_id,
                "attempt_number": attempt.attempt_number,
                "invocation_id": pending.get("invocation_id"),
                "tool_name": pending.get("tool_name"),
            },
        )

    def _checkpoint_tool_approval_suspension(
        self,
        attempt: TaskAttempt,
        pending: dict[str, Any],
    ) -> None:
        if self.runtime_trace is None:
            return
        self.runtime_trace.replay_checkpoint(
            "approval_requested",
            f"tool-approval-requested-{attempt.task_id}",
            task_id=attempt.task_id,
            durable_state={
                "approval_kind": "tool",
                "approval_id": pending.get("approval_id"),
                "attempt_id": attempt.attempt_id,
                "attempt_number": attempt.attempt_number,
                "invocation_id": pending.get("invocation_id"),
                "tool_name": pending.get("tool_name"),
                "attempt_status": AttemptStatus.WAITING_FOR_APPROVAL.value,
            },
        )

    def _fail_resumability(
        self,
        attempt: TaskAttempt,
        message: str,
        *,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        self.store.fail_attempt_resumability(
            attempt.attempt_id,
            message,
            metadata=metadata,
        )
        self._log(
            "task_attempt_resumability_failed",
            attempt.run_id,
            task_id=attempt.task_id,
            attempt_number=attempt.attempt_number,
            metadata={"error_category": FailureCategory.RESUMABILITY_FAILURE.value},
        )

    def _recover_running_tasks(
        self,
        run_id: str,
        *,
        skip_task_ids: set[str] | None = None,
    ) -> None:
        snapshot = self.store.load_snapshot(run_id)
        if snapshot.run.status in {
            RunStatus.SUCCEEDED,
            RunStatus.FAILED,
            RunStatus.CANCELLED,
        }:
            return
        protected_tasks = skip_task_ids or set()
        for task in snapshot.tasks:
            if task.status != TaskStatus.RUNNING:
                continue
            if task.task_id in protected_tasks:
                continue
            attempts = snapshot.attempts_for(task.task_id)
            latest = attempts[-1] if attempts else None
            if latest is not None and latest.status == AttemptStatus.SUCCEEDED:
                self.store.update_task_status(
                    run_id,
                    task.task_id,
                    TaskStatus.SUCCEEDED,
                    event_type="interrupted_attempt_recovered",
                    event_payload={
                        "attempt_number": latest.attempt_number,
                        "policy": "promote_completed_attempt",
                    },
                )
                self._log(
                    "interrupted_attempt_recovered",
                    run_id,
                    task_id=task.task_id,
                    attempt_number=latest.attempt_number,
                    metadata={"policy": "promote_completed_attempt"},
                )
                continue

            if (
                latest is not None
                and latest.status == AttemptStatus.RUNNING
                and _attempt_continuation(latest) is not None
            ):
                self._log(
                    "task_attempt_continuation_recovered",
                    run_id,
                    task_id=task.task_id,
                    attempt_number=latest.attempt_number,
                )
                continue

            if (
                latest is not None
                and latest.status == AttemptStatus.RUNNING
                and _pending_tool_approval(latest) is not None
            ):
                self._fail_resumability(
                    latest,
                    "Suspended tool approval has no valid durable continuation.",
                    metadata=latest.metadata,
                )
                continue

            category = FailureCategory.INTERRUPTED
            attempt_number = task.current_attempt_count
            if latest is not None:
                attempt_number = latest.attempt_number
                if latest.status == AttemptStatus.RUNNING:
                    self.store.complete_attempt(
                        latest.attempt_id,
                        AttemptStatus.INTERRUPTED,
                        error_category=FailureCategory.INTERRUPTED,
                        error_message="Process ended before the attempt completed.",
                        event_type="interrupted_attempt_recovered",
                    )
                elif latest.error_category is not None:
                    category = latest.error_category

            retryable_override = None
            if latest is not None:
                persisted_metadata = latest.metadata.get("_agentbus", {})
                persisted_override = (
                    persisted_metadata.get("retryable_override")
                    if isinstance(persisted_metadata, dict)
                    else None
                )
                if isinstance(persisted_override, bool):
                    retryable_override = persisted_override
            decision = self._retry_controller(snapshot.run).decide(
                category=category,
                attempt_number=attempt_number,
                task_maximum_attempts=task.spec.maximum_attempts,
                retryable_override=(
                    True
                    if category == FailureCategory.INTERRUPTED
                    else retryable_override
                ),
            )
            if decision.should_retry or attempt_number == 0:
                self.store.update_task_status(
                    run_id,
                    task.task_id,
                    TaskStatus.RETRYABLE,
                    event_type="interrupted_attempt_recovered",
                    event_payload={
                        "attempt_number": attempt_number,
                        "policy": "retry",
                        "next_attempt_number": attempt_number + 1,
                    },
                )
                self.store.update_task_status(
                    run_id,
                    task.task_id,
                    TaskStatus.READY,
                    event_type="durable_task_ready",
                    event_payload={"recovered": True},
                )
            else:
                self.store.update_task_status(
                    run_id,
                    task.task_id,
                    TaskStatus.FAILED,
                    event_type="interrupted_attempt_recovered",
                    event_payload={
                        "attempt_number": attempt_number,
                        "policy": "fail_exhausted",
                    },
                )
            self._log(
                "interrupted_attempt_recovered",
                run_id,
                task_id=task.task_id,
                attempt_number=attempt_number or None,
                metadata={"policy": "retry" if decision.should_retry else "fail_exhausted"},
            )

    def _reactivate_after_persisted_approval(self, run_id: str) -> None:
        snapshot = self.store.load_snapshot(run_id)
        changed = False
        latest = {approval.task_id: approval for approval in snapshot.approvals}
        for task in snapshot.tasks:
            if task.status != TaskStatus.WAITING_FOR_APPROVAL:
                continue
            attempts = snapshot.attempts_for(task.task_id)
            latest_attempt = attempts[-1] if attempts else None
            pending_tool = None
            if latest_attempt is not None:
                pending_tool = _pending_tool_approval(latest_attempt)
            if pending_tool is not None:
                approval_id = str(pending_tool.get("approval_id") or "")
                if not approval_id:
                    if latest_attempt is not None:
                        self._fail_resumability(
                            latest_attempt,
                            "Persisted tool approval suspension is incomplete.",
                        )
                        changed = True
                        continue
                    raise DurableExecutionError(
                        "Tool approval suspension has no durable attempt identity."
                    )
                try:
                    tool_approval = self.store.get_tool_approval(
                        run_id,
                        approval_id,
                    )
                except StateStoreError:
                    if latest_attempt is None:
                        raise
                    self._fail_resumability(
                        latest_attempt,
                        "Persisted exact tool approval could not be recovered.",
                    )
                    changed = True
                    continue
                if tool_approval.disposition is None:
                    continue
                if tool_approval.disposition == "approved":
                    if latest_attempt is None or _attempt_continuation(
                        latest_attempt
                    ) is None:
                        if latest_attempt is None:
                            raise DurableExecutionError(
                                "Approved tool invocation has no attempt identity."
                            )
                        self._fail_resumability(
                            latest_attempt,
                            "Approved tool invocation has no safe durable continuation.",
                        )
                        changed = True
                        continue
                    try:
                        self.store.resume_attempt_after_tool_approval(
                            latest_attempt.attempt_id,
                            approval_id=approval_id,
                            invocation_id=str(
                                pending_tool.get("invocation_id") or ""
                            ),
                        )
                    except StateStoreError:
                        self._fail_resumability(
                            latest_attempt,
                            "Approved tool continuation failed exact identity validation.",
                        )
                        changed = True
                        continue
                else:
                    if latest_attempt is not None and latest_attempt.status == (
                        AttemptStatus.WAITING_FOR_APPROVAL
                    ):
                        self.store.complete_attempt(
                            latest_attempt.attempt_id,
                            AttemptStatus.FAILED,
                            error_category=FailureCategory.POLICY_VIOLATION,
                            error_message="The exact tool approval was rejected.",
                            metadata=latest_attempt.metadata,
                            event_type="task_attempt_tool_approval_rejected",
                        )
                    self.store.update_task_status(
                        run_id,
                        task.task_id,
                        TaskStatus.REJECTED,
                        event_type="rejected_tool_task_recovered",
                        event_payload={
                            "approval_id": approval_id,
                            "invocation_id": pending_tool.get("invocation_id"),
                        },
                    )
                changed = True
                continue
            approval = latest.get(task.task_id)
            if approval is None:
                continue
            if approval.decision == ApprovalOutcome.APPROVED:
                self.store.update_task_status(
                    run_id,
                    task.task_id,
                    TaskStatus.READY,
                    event_type="approved_task_recovered",
                )
                changed = True
            else:
                self.store.update_task_status(
                    run_id,
                    task.task_id,
                    TaskStatus.REJECTED,
                    event_type="rejected_task_recovered",
                )
                changed = True
        run = self.store.get_run(run_id)
        still_waiting = any(
            task.status == TaskStatus.WAITING_FOR_APPROVAL
            for task in self.store.list_tasks(run_id)
        )
        if (
            changed
            and not still_waiting
            and run.status == RunStatus.WAITING_FOR_APPROVAL
        ):
            self.store.update_run_status(
                run_id,
                RunStatus.RUNNING,
                event_type="durable_run_approval_recovered",
            )

    def _synchronize_graph(self, run_id: str) -> None:
        if self._cancellation_token(run_id).is_requested:
            return
        snapshot = self.store.load_snapshot(run_id)
        if snapshot.run.status != RunStatus.RUNNING:
            return
        graph = TaskGraph.from_dict(snapshot.run.graph_data)

        while True:
            if self._cancellation_token(run_id).is_requested:
                return
            snapshot = self.store.load_snapshot(run_id)
            statuses = {task.task_id: task.status for task in snapshot.tasks}
            blocked = graph.blocked_tasks(statuses)
            if not blocked:
                break
            for task in blocked:
                self.store.update_task_status(
                    run_id,
                    task.task_id,
                    TaskStatus.BLOCKED,
                    event_type="task_blocked",
                    event_payload={
                        "failed_dependencies": [
                            dependency_id
                            for dependency_id in task.dependency_ids
                            if statuses.get(dependency_id)
                            in {
                                TaskStatus.FAILED,
                                TaskStatus.REJECTED,
                                TaskStatus.BLOCKED,
                                TaskStatus.CANCELLED,
                            }
                        ]
                    },
                )
                self._log("task_blocked", run_id, task_id=task.task_id)

        snapshot = self.store.load_snapshot(run_id)
        statuses = {task.task_id: task.status for task in snapshot.tasks}
        for task in graph.ready_tasks(statuses):
            if self._cancellation_token(run_id).is_requested:
                return
            current = statuses.get(task.task_id, TaskStatus.PENDING)
            if current in {TaskStatus.PENDING, TaskStatus.RETRYABLE}:
                self.store.update_task_status(
                    run_id,
                    task.task_id,
                    TaskStatus.READY,
                    event_type="durable_task_ready",
                    event_payload={"risk": task.risk.value},
                )
                self._log(
                    "durable_task_ready",
                    run_id,
                    task_id=task.task_id,
                    metadata={"risk": task.risk.value},
                )

        snapshot = self.store.load_snapshot(run_id)
        latest_approvals = {
            approval.task_id: approval.decision for approval in snapshot.approvals
        }
        for task in snapshot.tasks:
            if self._cancellation_token(run_id).is_requested:
                return
            if (
                task.status == TaskStatus.READY
                and task.spec.risk == RiskLevel.HIGH
                and latest_approvals.get(task.task_id) != ApprovalOutcome.APPROVED
            ):
                self.store.update_task_status(
                    run_id,
                    task.task_id,
                    TaskStatus.WAITING_FOR_APPROVAL,
                    event_type="approval_required",
                    event_payload={"risk": task.spec.risk.value},
                )
                self._log("approval_required", run_id, task_id=task.task_id)
                self.store.update_run_status(
                    run_id,
                    RunStatus.WAITING_FOR_APPROVAL,
                    event_type="approval_required",
                    event_payload={"task_id": task.task_id},
                )
                return

        snapshot = self.store.load_snapshot(run_id)
        statuses = {task.task_id: task.status for task in snapshot.tasks}
        if graph.all_succeeded(statuses):
            final_review = snapshot.run.metadata.get("final_review", {})
            if isinstance(final_review, dict) and final_review.get("required"):
                self.store.update_run_status(
                    run_id,
                    RunStatus.WAITING_FOR_REVIEW,
                    event_type="final_review_required",
                )
                self._log("final_review_required", run_id)
                return
            self.store.update_run_status(
                run_id,
                RunStatus.SUCCEEDED,
                event_type="durable_run_succeeded",
            )
            self._log("durable_run_succeeded", run_id)
            return

        if any(status == TaskStatus.WAITING_FOR_APPROVAL for status in statuses.values()):
            self.store.update_run_status(
                run_id,
                RunStatus.WAITING_FOR_APPROVAL,
                event_type="approval_required",
            )
            return

        if any(status == TaskStatus.READY for status in statuses.values()):
            return
        if any(status == TaskStatus.RUNNING for status in statuses.values()):
            return

        failed = [
            task_id
            for task_id, status in statuses.items()
            if status in {TaskStatus.FAILED, TaskStatus.REJECTED, TaskStatus.BLOCKED}
        ]
        if failed:
            self._fail_run(
                run_id,
                "No valid progress is possible; failed, rejected, or blocked tasks: "
                + ", ".join(failed),
            )
        else:
            self._fail_run(
                run_id,
                "No valid progress is possible from the persisted task states.",
            )

    def _fail_run(self, run_id: str, reason: str) -> None:
        if self._cancellation_token(run_id).is_requested:
            self.finalize_cancellation(run_id)
            return
        run = self.store.get_run(run_id)
        if run.status not in {
            RunStatus.RUNNING,
            RunStatus.WAITING_FOR_APPROVAL,
            RunStatus.WAITING_FOR_REVIEW,
        }:
            return
        self.store.update_run_status(
            run_id,
            RunStatus.FAILED,
            failure_reason=reason,
            event_type="durable_run_failed",
            event_payload={"reason": reason},
        )
        self._log("durable_run_failed", run_id, metadata={"reason": reason})

    def _maximum_iterations(self, run_id: str) -> int:
        tasks = self.store.list_tasks(run_id)
        return sum(task.spec.maximum_attempts for task in tasks) + len(tasks) + 5

    def _retry_controller(self, run: RunRecord) -> RetryController:
        execution_metadata = run.metadata.get("execution", {})
        if not isinstance(execution_metadata, dict):
            raise DurableExecutionError(
                "Persisted execution metadata is invalid; recovery cannot continue safely."
            )
        raw_policy = execution_metadata.get("retry_policy")
        if raw_policy is None:
            return self.retry
        try:
            return RetryController(RetryPolicy.model_validate(raw_policy))
        except (TypeError, ValueError) as exc:
            raise DurableExecutionError(
                "Persisted retry policy is invalid; recovery cannot continue safely."
            ) from exc

    def _cancellation_token(self, run_id: str) -> CancellationToken:
        if self._explicit_cancellation is None:
            return self.cancellations.get(run_id)
        if (
            self._explicit_cancellation_run_id is not None
            and self._explicit_cancellation_run_id != run_id
        ):
            raise DurableExecutionError(
                "One explicit cancellation token cannot control multiple runs."
            )
        self._explicit_cancellation_run_id = run_id
        return self.cancellations.register(
            run_id,
            self._explicit_cancellation,
        )

    def _report(self, snapshot: RunSnapshot) -> ExecutionReport:
        successful = [
            task.task_id for task in snapshot.tasks if task.status == TaskStatus.SUCCEEDED
        ]
        failed = [
            task.task_id
            for task in snapshot.tasks
            if task.status in {TaskStatus.FAILED, TaskStatus.REJECTED}
            or task.status == TaskStatus.INTEGRATION_CONFLICT
        ]
        blocked = [
            task.task_id for task in snapshot.tasks if task.status == TaskStatus.BLOCKED
        ]
        approvals = [
            task.task_id
            for task in snapshot.tasks
            if task.status == TaskStatus.WAITING_FOR_APPROVAL
        ]
        approval_details: list[dict[str, Any]] = []
        for task in snapshot.tasks:
            if task.status != TaskStatus.WAITING_FOR_APPROVAL:
                continue
            attempts = snapshot.attempts_for(task.task_id)
            latest_attempt = attempts[-1] if attempts else None
            pending_tool = (
                _pending_tool_approval(latest_attempt)
                if latest_attempt is not None
                else None
            )
            if pending_tool is None:
                approval_details.append(
                    {
                        "approval_kind": "task",
                        "task_id": task.task_id,
                        "approval_id": f"{snapshot.run.run_id}:{task.task_id}",
                        "risk": task.spec.risk.value,
                        "safe_reason": redact_text(
                            str(task.spec.metadata.get("risk_reason") or "")
                        ),
                    }
                )
                continue
            detail: dict[str, Any] = {
                "approval_kind": "tool",
                "task_id": task.task_id,
                "attempt_number": latest_attempt.attempt_number,
                "tool_name": str(
                    pending_tool.get("tool_name") or "unknown"
                )[:128],
                "approval_id": str(
                    pending_tool.get("approval_id") or "unknown"
                )[:128],
                "capabilities": [],
                "safe_reason": None,
            }
            try:
                tool_approval = self.store.get_tool_approval(
                    snapshot.run.run_id,
                    detail["approval_id"],
                )
            except StateStoreError:
                pass
            else:
                detail["tool_name"] = tool_approval.request.tool_name
                detail["capabilities"] = sorted(
                    {
                        str(getattr(capability, "value", capability))[:128]
                        for capability in tool_approval.request.requested_capabilities
                    }
                )[:64]
                detail["safe_reason"] = redact_text(
                    tool_approval.request.reason,
                    max_chars=2_000,
                )
            approval_details.append(detail)
        terminal_count = len(successful) + len(failed) + len(blocked) + sum(
            task.status == TaskStatus.CANCELLED for task in snapshot.tasks
        )
        final_review = snapshot.run.metadata.get("final_review", {})
        if not isinstance(final_review, dict):
            final_review = {}
        workspace_repository = snapshot.run.metadata.get("workspace_repository", {})
        if not isinstance(workspace_repository, dict):
            workspace_repository = {}
        reviewer_summary = final_review.get("summary")
        reviewer_issues = final_review.get("issues", [])
        required_fixes = final_review.get("required_fixes", [])
        reviewer_stage = (
            "final"
            if final_review.get("status")
            in {"approved", "rejected", "blocked_by_verification"}
            else None
        )
        if (
            not reviewer_summary
            and reviewer_stage is None
            and snapshot.run.status
            not in {RunStatus.RUNNING, RunStatus.WAITING_FOR_APPROVAL}
        ):
            latest_review: dict[str, Any] = {}
            for task in reversed(snapshot.tasks):
                attempts = snapshot.attempts_for(task.task_id)
                if not attempts:
                    continue
                latest_attempt = attempts[-1]
                candidate = latest_attempt.metadata.get(
                    "task_review"
                ) or latest_attempt.metadata.get("reviewer_feedback")
                if isinstance(candidate, dict):
                    latest_review = candidate
                    reviewer_stage = "task"
                    break
            if isinstance(latest_review, dict):
                reviewer_summary = latest_review.get("summary")
                reviewer_issues = latest_review.get("issues", [])
                required_fixes = latest_review.get("required_fixes", [])
        task_failures = []
        resumability_failure = snapshot.run.metadata.get(
            "resumability_failure",
            {},
        )
        if not isinstance(resumability_failure, dict):
            resumability_failure = {}
        for task_id in failed:
            if resumability_failure.get("task_id") == task_id:
                task_failures.append(
                    {
                        "task_id": task_id,
                        "category": FailureCategory.RESUMABILITY_FAILURE.value,
                        "message": str(
                            resumability_failure.get("message")
                            or "Task continuation could not be resumed safely."
                        ),
                    }
                )
                continue
            attempt = next(
                (
                    item
                    for item in reversed(snapshot.attempts)
                    if item.task_id == task_id and item.error_category is not None
                ),
                None,
            )
            if attempt is not None:
                task_failures.append(
                    {
                        "task_id": task_id,
                        "category": attempt.error_category.value,
                        "message": attempt.error_message or "Task attempt failed.",
                    }
                )
        hygiene_lists = {
            "relevant_changed_files": set(),
            "generated_artifacts": set(),
            "ignored_files": set(),
            "commit_eligible_files": set(),
            "review_excluded_files": set(),
            "tracked_generated_artifacts": set(),
        }
        suppression_active = bool(
            snapshot.run.metadata.get("verifier_artifact_suppression_active")
        )
        hygiene_sources = [snapshot.run.metadata.get("artifact_hygiene", {})]
        observed_changed_files = set(snapshot.run.changed_files)
        for attempt in snapshot.attempts:
            hygiene_sources.append(attempt.metadata.get("artifact_hygiene", {}))
            raw_changed = attempt.metadata.get("changed_files", [])
            if isinstance(raw_changed, list):
                observed_changed_files.update(
                    item for item in raw_changed if isinstance(item, str)
                )
            verifier_metadata = attempt.metadata.get("verifier", {})
            if isinstance(verifier_metadata, dict):
                suppression_active = suppression_active or bool(
                    verifier_metadata.get("artifact_suppression_active")
                )
        for source in hygiene_sources:
            if not isinstance(source, dict):
                continue
            for key, values in hygiene_lists.items():
                raw_values = source.get(key, [])
                if isinstance(raw_values, list):
                    values.update(item for item in raw_values if isinstance(item, str))
        parallel = snapshot.run.metadata.get("parallel_execution", {})
        if not isinstance(parallel, dict):
            parallel = {}
        worktrees = self.store.list_worktrees(snapshot.run.run_id)
        leases = self.store.list_worker_lease_rows(snapshot.run.run_id)
        task_commits = self.store.list_task_commits(snapshot.run.run_id)
        for task_commit in task_commits:
            observed_changed_files.update(task_commit.changed_files)
        integrations = self.store.list_integrations(snapshot.run.run_id)
        active_leases = [item for item in leases if item["status"] == "active"]
        expired_leases = [item for item in leases if item["status"] == "expired"]
        task_worktrees = {
            item.task_id: item.path
            for item in worktrees
            if item.task_id is not None and item.status.value != "removed"
        }
        retained = [
            item.path for item in worktrees if item.status.value != "removed"
        ]
        conflicts = [
            {
                "task_id": item.task_id,
                "conflict_files": item.conflict_files,
                "error_message": item.error_message,
            }
            for item in integrations
            if item.status.value == "integration_conflict"
        ]
        cancellation_state = self.store.get_cancellation_state(
            snapshot.run.run_id
        )
        cancellation = cancellation_state.model_dump(mode="json")
        cancellation["active_non_interruptible_operations"] = (
            cancellation_state.active_non_interruptible_operations
        )
        return ExecutionReport(
            run_id=snapshot.run.run_id,
            original_task=snapshot.run.original_task,
            status=snapshot.run.status,
            graph_progress=GraphProgress(
                total=len(snapshot.tasks),
                succeeded=len(successful),
                failed=len(failed),
                blocked=len(blocked),
                waiting_for_approval=len(approvals),
                remaining=max(0, len(snapshot.tasks) - terminal_count),
            ),
            successful_tasks=successful,
            failed_tasks=failed,
            blocked_tasks=blocked,
            pending_approvals=approvals,
            pending_approval_details=approval_details,
            attempts_per_task={
                task.task_id: task.current_attempt_count for task in snapshot.tasks
            },
            verifier_status=snapshot.run.verifier_status,
            reviewer_status=snapshot.run.reviewer_status,
            changed_files=sorted(observed_changed_files),
            relevant_changed_files=sorted(hygiene_lists["relevant_changed_files"]),
            generated_artifacts=sorted(hygiene_lists["generated_artifacts"]),
            ignored_files=sorted(hygiene_lists["ignored_files"]),
            commit_eligible_files=sorted(hygiene_lists["commit_eligible_files"]),
            review_excluded_files=sorted(hygiene_lists["review_excluded_files"]),
            tracked_generated_artifacts=sorted(
                hygiene_lists["tracked_generated_artifacts"]
            ),
            verifier_artifact_suppression_active=suppression_active,
            commit_identifier=snapshot.run.commit_identifier,
            pr_url=snapshot.run.pr_url,
            finalization_error=snapshot.run.finalization_error,
            failure_reason=snapshot.run.failure_reason,
            workspace=snapshot.run.workspace,
            git_top_level=workspace_repository.get("git_top_level"),
            reviewer_stage=reviewer_stage,
            reviewer_summary=reviewer_summary,
            reviewer_issues=(reviewer_issues if isinstance(reviewer_issues, list) else []),
            required_fixes=(required_fixes if isinstance(required_fixes, list) else []),
            task_failures=task_failures,
            side_effects_persisted=bool(
                observed_changed_files
                and snapshot.run.status in {RunStatus.FAILED, RunStatus.CANCELLED}
            ),
            parallel_mode_enabled=bool(parallel.get("enabled", False)),
            configured_max_workers=int(parallel.get("max_workers", 1)),
            workers_used=list(parallel.get("workers_used", [])),
            current_leases=active_leases,
            expired_leases=expired_leases,
            task_worktrees=task_worktrees,
            task_commits={item.task_id: item.commit_sha for item in task_commits},
            original_base_commit=parallel.get("base_commit"),
            integration_order=list(parallel.get("integration_order", [])),
            integration_commit=parallel.get("integration_commit"),
            final_branch=parallel.get("final_branch"),
            integration_conflicts=conflicts,
            retained_worktrees=retained,
            cleanup_recommendations=(
                [
                    "Inspect retained worktrees, then use the explicit cleanup command "
                    "for clean AgentBus-owned paths."
                ]
                if retained
                else []
            ),
            cancellation=cancellation,
            resume_command=(
                f"python -m agentbus.main --resume {snapshot.run.run_id}"
                if snapshot.run.status
                in {
                    RunStatus.PENDING,
                    RunStatus.RUNNING,
                    RunStatus.WAITING_FOR_APPROVAL,
                    RunStatus.WAITING_FOR_REVIEW,
                }
                else None
            ),
        )

    def _log(
        self,
        event_type: str,
        run_id: str,
        *,
        task_id: str | None = None,
        attempt_number: int | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        if self.logger is None:
            return
        data: dict[str, Any] = {"run_id": run_id}
        if task_id is not None:
            data["task_id"] = task_id
        if attempt_number is not None:
            data["attempt_number"] = attempt_number
        if metadata:
            data["metadata"] = metadata
        self.logger.log(event_type, data)


def _pending_tool_approval(attempt: TaskAttempt) -> dict[str, Any] | None:
    internal = attempt.metadata.get("_agentbus", {})
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


def _attempt_continuation(attempt: TaskAttempt) -> dict[str, Any] | None:
    internal = attempt.metadata.get("_agentbus", {})
    if not isinstance(internal, dict):
        return None
    continuation = internal.get("task_continuation")
    if not isinstance(continuation, dict) or not continuation:
        continuation = internal.get("loop_continuation")
    return continuation if isinstance(continuation, dict) and continuation else None
