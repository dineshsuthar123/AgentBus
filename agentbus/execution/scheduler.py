from __future__ import annotations

import threading
from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path
from typing import Callable

from agentbus.execution.engine import DurableExecutionEngine
from agentbus.execution.integration import (
    IntegrationConflictError,
    IntegrationCoordinator,
)
from agentbus.execution.leases import LeaseService, LeaseUnavailableError
from agentbus.execution.models import (
    ApprovalOutcome,
    ExecutionReport,
    RiskLevel,
    RunStatus,
    TaskRecord,
    TaskStatus,
)
from agentbus.execution.state_store import StateStore
from agentbus.execution.task_graph import TaskGraph
from agentbus.execution.worker import LocalTaskWorker, WorkerResult
from agentbus.git.repository import GitRepository
from agentbus.worktrees.manager import GitWorktreeManager
from agentbus.worktrees.models import WorktreePurpose


WorkerFactory = Callable[[str], LocalTaskWorker]


class ParallelSchedulerError(RuntimeError):
    """Raised when the bounded local scheduler cannot progress safely."""


class ParallelExecutionScheduler:
    def __init__(
        self,
        *,
        store: StateStore,
        worktree_manager: GitWorktreeManager,
        lease_service: LeaseService,
        integration: IntegrationCoordinator,
        worker_factory: WorkerFactory,
        max_workers: int = 1,
        cancellation: threading.Event | None = None,
    ):
        if max_workers < 1:
            raise ValueError("max_workers must be at least 1")
        self.store = store
        self.worktree_manager = worktree_manager
        self.lease_service = lease_service
        self.integration = integration
        self.worker_factory = worker_factory
        self.max_workers = max_workers
        self.cancellation = cancellation or threading.Event()
        self.workers_used: set[str] = set()

    def run(self, run_id: str, *, resume: bool = False) -> ExecutionReport:
        run = self.store.get_run(run_id)
        if run.status in {RunStatus.SUCCEEDED, RunStatus.FAILED, RunStatus.CANCELLED}:
            return self._report(run_id)
        self.store.record_event(
            run_id,
            "parallel_run_resumed" if resume else "scheduler_started",
            {"max_workers": self.max_workers},
        )
        self.lease_service.expire_stale_leases(run_id)
        active_task_ids = {
            lease.task_id
            for lease in self.lease_service.list_leases(run_id)
            if lease.status.value == "active"
        }
        DurableExecutionEngine(self.store)._recover_running_tasks(
            run_id, skip_task_ids=active_task_ids
        )
        run = self.store.get_run(run_id)
        if run.status == RunStatus.PENDING:
            self.store.update_run_status(
                run_id, RunStatus.RUNNING, event_type="durable_run_started"
            )
        elif run.status == RunStatus.WAITING_FOR_APPROVAL:
            return self._report(run_id)
        integration_worktree = self._integration_worktree(run_id)
        self.integration.recover_interrupted(run_id, integration_worktree)
        graph = TaskGraph.from_dict(self.store.get_run(run_id).graph_data)
        order = {task.task_id: index for index, task in enumerate(graph.topological_order())}

        maximum_rounds = sum(task.maximum_attempts for task in graph.tasks) + len(graph.tasks) * 4 + 10
        for _ in range(maximum_rounds):
            if self.cancellation.is_set():
                self.store.record_event(run_id, "scheduler_stopped", {"cancelled": True})
                return self._report(run_id)
            if self._integrate_pending(run_id, integration_worktree, order):
                continue
            if self.store.get_run(run_id).status in {
                RunStatus.FAILED,
                RunStatus.CANCELLED,
            }:
                return self._report(run_id)
            snapshot = self.store.load_snapshot(run_id)
            statuses = {task.task_id: task.status for task in snapshot.tasks}
            if graph.all_succeeded(statuses):
                self._finish_execution_phase(run_id, integration_worktree)
                return self._report(run_id)
            self._block_failed_dependencies(run_id, graph, statuses)
            ready, approval_waiting = self._ready_tasks(run_id, graph)
            if not ready:
                snapshot = self.store.load_snapshot(run_id)
                if any(
                    task.status in {TaskStatus.FAILED, TaskStatus.BLOCKED, TaskStatus.INTEGRATION_CONFLICT}
                    for task in snapshot.tasks
                ):
                    self._fail_run(run_id, "Parallel execution cannot progress because a task failed or conflicted.")
                elif approval_waiting:
                    current = self.store.get_run(run_id)
                    if current.status == RunStatus.RUNNING:
                        self.store.update_run_status(
                            run_id,
                            RunStatus.WAITING_FOR_APPROVAL,
                            event_type="approval_required",
                        )
                return self._report(run_id)
            base_commit = self._head(Path(integration_worktree.path))
            selected = ready[: min(self.max_workers, len(ready))]
            futures: list[tuple[TaskRecord, Future[WorkerResult]]] = []
            with ThreadPoolExecutor(
                max_workers=len(selected), thread_name_prefix="agentbus-worker"
            ) as pool:
                for index, task in enumerate(selected, start=1):
                    worker_id = f"{run_id[:8]}-worker-{index}"
                    try:
                        lease = self.lease_service.acquire_lease(
                            run_id,
                            task.task_id,
                            worker_id,
                            {"scheduler": "local-threadpool"},
                            activate_task=True,
                        )
                    except LeaseUnavailableError:
                        continue
                    worker = self.worker_factory(worker_id)
                    self.workers_used.add(worker_id)
                    self.store.record_event(
                        run_id,
                        "worker_registered",
                        {
                            "worker_id": worker_id,
                            "lease_id": lease.lease_id,
                            "fencing_token": lease.fencing_token,
                        },
                        task_id=task.task_id,
                    )
                    futures.append(
                        (
                            task,
                            pool.submit(
                                worker.execute,
                                self.store.get_run(run_id),
                                self.store.get_task(run_id, task.task_id),
                                lease,
                                base_commit,
                            ),
                        )
                    )
                for task, future in futures:
                    result = future.result()
                    self.store.record_event(
                        run_id,
                        "worker_result_collected",
                        {
                            "worker_id": result.worker_id,
                            "status": result.status.value,
                            "lease_id": result.lease_id,
                            "fencing_token": result.fencing_token,
                            "task_commit": result.task_commit,
                        },
                        task_id=task.task_id,
                    )
            if not futures:
                return self._report(run_id)
        self._fail_run(run_id, "Parallel scheduler bounded progress guard was exhausted.")
        return self._report(run_id)

    def _integration_worktree(self, run_id: str):
        run = self.store.get_run(run_id)
        parallel = run.metadata.get("parallel_execution", {})
        if not isinstance(parallel, dict) or not parallel.get("base_commit"):
            raise ParallelSchedulerError("Parallel run is missing its exact base commit.")
        records = [
            item
            for item in self.store.list_worktrees(run_id)
            if item.purpose == WorktreePurpose.INTEGRATION
        ]
        if records:
            return self.worktree_manager.recover(records[-1].worktree_id)
        record = self.worktree_manager.create_integration_worktree(
            run_id, parallel["base_commit"]
        )
        self.store.update_run_details(
            run_id,
            metadata_updates={
                "parallel_execution": {
                    **parallel,
                    "integration_worktree_id": record.worktree_id,
                    "integration_worktree": record.path,
                    "integration_branch": record.branch_ref,
                }
            },
            event_type="integration_worktree_created",
        )
        return record

    def _integrate_pending(self, run_id, integration_worktree, order) -> bool:
        pending = [
            task
            for task in self.store.list_tasks(run_id)
            if task.status == TaskStatus.INTEGRATION_PENDING
        ]
        if not pending:
            return False
        pending.sort(key=lambda task: (order[task.task_id], task.task_id))
        for task in pending:
            commit = self.store.get_task_commit(run_id, task.task_id)
            if commit is None:
                raise ParallelSchedulerError(
                    f"Task '{task.task_id}' is integration_pending without a commit."
                )
            try:
                self.integration.integrate(integration_worktree, commit)
            except IntegrationConflictError as exc:
                self._fail_run(run_id, str(exc))
                return False
        return True

    def _ready_tasks(self, run_id, graph):
        tasks = self.store.list_tasks(run_id)
        statuses = {task.task_id: task.status for task in tasks}
        by_id = {task.task_id: task for task in tasks}
        ready: list[TaskRecord] = []
        approval_waiting = False
        approvals = {
            task.task_id: self.store.latest_approval(run_id, task.task_id)
            for task in tasks
        }
        for spec in sorted(graph.ready_tasks(statuses), key=lambda item: item.task_id):
            record = by_id[spec.task_id]
            if record.status in {TaskStatus.PENDING, TaskStatus.RETRYABLE}:
                self.store.update_task_status(
                    run_id,
                    spec.task_id,
                    TaskStatus.READY,
                    event_type="durable_task_ready",
                )
                record = self.store.get_task(run_id, spec.task_id)
            approval = approvals[spec.task_id]
            if spec.risk == RiskLevel.HIGH and (
                approval is None or approval.decision != ApprovalOutcome.APPROVED
            ):
                if record.status == TaskStatus.READY:
                    self.store.update_task_status(
                        run_id,
                        spec.task_id,
                        TaskStatus.WAITING_FOR_APPROVAL,
                        event_type="approval_required",
                    )
                approval_waiting = True
                continue
            if record.status == TaskStatus.READY:
                ready.append(record)
        return ready, approval_waiting

    def _block_failed_dependencies(self, run_id, graph, statuses):
        for spec in graph.blocked_tasks(statuses):
            task = self.store.get_task(run_id, spec.task_id)
            if task.status in {TaskStatus.PENDING, TaskStatus.READY, TaskStatus.RETRYABLE}:
                self.store.update_task_status(
                    run_id, spec.task_id, TaskStatus.BLOCKED, event_type="task_blocked"
                )

    def _finish_execution_phase(self, run_id, integration_worktree):
        integration_commit = self._head(Path(integration_worktree.path))
        run = self.store.get_run(run_id)
        parallel = run.metadata.get("parallel_execution", {})
        integrations = self.store.list_integrations(run_id)
        self.store.update_run_details(
            run_id,
            metadata_updates={
                "parallel_execution": {
                    **parallel,
                    "workers_used": sorted(
                        set(parallel.get("workers_used", [])) | self.workers_used
                    ),
                    "integration_order": [
                        item.task_id
                        for item in integrations
                        if item.status.value == "integrated"
                    ],
                    "integration_commit": integration_commit,
                }
            },
            event_type="parallel_execution_completed",
        )
        current = self.store.get_run(run_id)
        if current.status == RunStatus.RUNNING:
            self.store.update_run_status(
                run_id,
                RunStatus.WAITING_FOR_REVIEW,
                event_type="final_review_required",
            )

    def _fail_run(self, run_id, reason):
        run = self.store.get_run(run_id)
        if run.status in {RunStatus.RUNNING, RunStatus.WAITING_FOR_APPROVAL}:
            self.store.update_run_status(
                run_id,
                RunStatus.FAILED,
                failure_reason=reason,
                event_type="durable_run_failed",
            )

    def _report(self, run_id):
        return DurableExecutionEngine(self.store).get_report(run_id)

    @staticmethod
    def _head(path: Path) -> str:
        return GitRepository(str(path)).head_commit(short=False)
