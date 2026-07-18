from __future__ import annotations

import os
import threading
import uuid
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Protocol

from agentbus.config import AgentBusConfig
from agentbus.control.authentication import safe_error_message
from agentbus.control.errors import (
    ControlPlaneConflictError,
    ControlPlaneNotFoundError,
    ControlPlaneUnavailableError,
)
from agentbus.control.models import (
    CancelResponse,
    ResumeResponse,
    RunAcceptedResponse,
    RunCreateRequest,
)
from agentbus.control.services import WorkspaceService, cancellation_lifecycle
from agentbus.execution.cancellation import (
    CancellationRequested,
    CancellationState,
)
from agentbus.execution.engine import DurableExecutionEngine
from agentbus.execution.cancellation_registry import CancellationRegistry
from agentbus.execution.models import (
    AttemptStatus,
    FailureCategory,
    RunRecord,
    RunStatus,
    TaskSpec,
    TaskStatus,
    utc_now,
)
from agentbus.execution.state_store import RunNotFoundError, StateStore
from agentbus.git.repository import GitRepository, GitRepositoryError
from agentbus.memory.run_log import RunLogger
from agentbus.models.errors import ModelCancellationError
from agentbus.runtime.loop import AgentLoop
from agentbus.runtime.orchestrator import MultiAgentOrchestrator


class RunBackend(Protocol):
    def prepare(self, run_id: str) -> None: ...

    def execute_new(self, request: RunCreateRequest, run_id: str) -> None: ...

    def resume(self, run_id: str) -> None: ...

    def cancel(self, run_id: str, reason: str | None = None) -> RunStatus: ...

    def workspace_for(self, run_id: str) -> str: ...

    def finalize_cancellation(self, run_id: str) -> RunStatus: ...

    def cancellation_state(self, run_id: str) -> CancellationState: ...


@dataclass
class ActiveRun:
    run_id: str
    workspace: str
    created_at: datetime
    future: Future[None]
    operation: str


class AgentBusRunBackend:
    def __init__(self, config: AgentBusConfig, store: StateStore | None = None):
        self.base_config = config
        self.store = store or StateStore(config.state_database_path)
        self.cancellations = CancellationRegistry(self.store)

    def prepare(self, run_id: str) -> None:
        self.cancellations.prepare(run_id)

    def execute_new(self, request: RunCreateRequest, run_id: str) -> None:
        config = self._config_for(request)
        if request.durable:
            orchestrator = self._orchestrator(config, request, run_id)
            try:
                orchestrator.create_durable_run(request.task, run_id=run_id)
            except (CancellationRequested, ModelCancellationError):
                self._persist_preplanning_cancellation(
                    config,
                    request,
                    run_id,
                )
                return
            orchestrator.run_durable(run_id)
            return
        self._execute_persisted_non_durable(config, request, run_id)

    def resume(self, run_id: str) -> None:
        run = self.store.get_run(run_id)
        self.cancellations.recover(run_id)
        parallel = run.metadata.get("parallel_execution", {})
        routing = run.metadata.get("model_routing", {})
        config = self.base_config.with_overrides(
            workspace_dir=run.workspace,
            provider_name=(
                routing.get("provider")
                if isinstance(routing, dict) and routing.get("provider")
                else None
            ),
            parallel_execution=bool(
                isinstance(parallel, dict) and parallel.get("enabled")
            ),
            max_workers=(
                int(parallel.get("max_workers", 1))
                if isinstance(parallel, dict)
                else 1
            ),
            keep_worktrees=(
                bool(parallel.get("keep_worktrees", True))
                if isinstance(parallel, dict)
                else True
            ),
        )
        MultiAgentOrchestrator(
            config=config,
            state_store=self.store,
            logger=RunLogger(log_dir=config.runs_dir, run_id=run_id),
            cancellation_registry=self.cancellations,
        ).resume_durable(run_id)

    def cancel(self, run_id: str, reason: str | None = None) -> RunStatus:
        try:
            return DurableExecutionEngine(
                self.store,
                logger=RunLogger(
                    log_dir=self.base_config.runs_dir,
                    run_id=run_id,
                ),
                cancellation_registry=self.cancellations,
            ).request_cancellation(run_id, reason).status
        except RunNotFoundError:
            if run_id not in self.cancellations.run_ids:
                raise
            self.cancellations.get(run_id).request(reason or "Cancelled by user.")
            return RunStatus.PENDING

    def finalize_cancellation(self, run_id: str) -> RunStatus:
        return DurableExecutionEngine(
            self.store,
            logger=RunLogger(log_dir=self.base_config.runs_dir, run_id=run_id),
            cancellation_registry=self.cancellations,
        ).finalize_cancellation(run_id).status

    def cancellation_state(self, run_id: str) -> CancellationState:
        if run_id in self.cancellations.run_ids:
            return self.cancellations.get(run_id).snapshot()
        return self.store.get_cancellation_state(run_id)

    def shutdown(self) -> None:
        self.cancellations.shutdown()

    def workspace_for(self, run_id: str) -> str:
        return self.store.get_run(run_id).workspace

    def _config_for(self, request: RunCreateRequest) -> AgentBusConfig:
        return self.base_config.with_overrides(
            workspace_dir=request.workspace,
            provider_name=request.provider,
            fallback_provider_name=request.fallback_provider,
            enable_provider_fallback=request.fallback_enabled,
            planner_model=request.role_models.planner,
            coder_model=request.role_models.coder,
            reviewer_model=request.role_models.reviewer,
            summarizer_model=request.role_models.summarizer,
            deterministic_profile=request.deterministic.profile,
            deterministic_latency_seconds=request.deterministic.latency_seconds,
            deterministic_latency_roles=tuple(request.deterministic.latency_roles),
            deterministic_failure_kind=request.deterministic.failure_kind,
            deterministic_failure_calls=tuple(request.deterministic.failure_calls),
            deterministic_failure_roles=tuple(request.deterministic.failure_roles),
            parallel_execution=request.parallel,
            max_workers=request.max_workers,
            keep_worktrees=request.keep_worktrees,
        )

    def _orchestrator(
        self,
        config: AgentBusConfig,
        request: RunCreateRequest,
        run_id: str,
    ) -> MultiAgentOrchestrator:
        return MultiAgentOrchestrator(
            config=config,
            state_store=self.store,
            logger=RunLogger(log_dir=config.runs_dir, run_id=run_id),
            commit_changes=request.commit_changes,
            open_pr=request.create_pr,
            cancellation=self.cancellations.get(run_id),
            cancellation_registry=self.cancellations,
        )

    def _execute_persisted_non_durable(
        self,
        config: AgentBusConfig,
        request: RunCreateRequest,
        run_id: str,
    ) -> None:
        task = TaskSpec(
            task_id="run-task",
            title="Execute requested task",
            description=request.task,
            assigned_role="coder",
            maximum_attempts=max(1, request.retry_limit + 1),
            metadata={"control_plane": True},
        )
        run = RunRecord(
            run_id=run_id,
            original_task=request.task,
            workflow_type=request.workflow,
            model=config.resolve_model("coder"),
            workspace=str(config.workspace_path),
            metadata={
                "control_request": request.model_dump(
                    mode="json",
                    exclude={"metadata"},
                ),
                "model_routing": config.safe_model_summary(),
            },
        )
        self.store.create_run_with_tasks(run, [task])
        self.cancellations.synchronize(run_id)
        cancellation = self.cancellations.get(run_id)
        if cancellation.is_requested:
            DurableExecutionEngine(
                self.store,
                cancellation_registry=self.cancellations,
            ).finalize_cancellation(run_id)
            return
        self.store.update_run_status(
            run_id,
            RunStatus.RUNNING,
            event_type="run_started",
        )
        self.store.update_task_status(run_id, task.task_id, TaskStatus.READY)
        self.store.update_task_status(run_id, task.task_id, TaskStatus.RUNNING)
        attempt = self.store.create_attempt(run_id, task.task_id)
        try:
            if request.workflow == "single":
                summary = AgentLoop(
                    config=config,
                    cancellation=cancellation,
                ).run(request.task)
                approved = True
                verifier_status = None
                reviewer_status = None
                changed_files = self._changed_files(config.workspace_path)
            else:
                result = self._orchestrator(config, request, run_id).run(request.task)
                summary = result.final_summary
                approved = result.approved
                verifier_status = (
                    "passed" if result.verifier_result.get("passed") else "failed"
                )
                reviewer_status = "approved" if result.approved else "rejected"
                changed_files = result.changed_files
            self.store.complete_attempt(
                attempt.attempt_id,
                AttemptStatus.SUCCEEDED if approved else AttemptStatus.FAILED,
                error_category=(
                    None if approved else FailureCategory.REVIEWER_REJECTION
                ),
                error_message=None if approved else summary,
                observation_summary=summary,
            )
            self.store.update_task_status(
                run_id,
                task.task_id,
                TaskStatus.SUCCEEDED if approved else TaskStatus.FAILED,
                event_type="task_succeeded" if approved else "task_failed",
            )
            self.store.update_run_details(
                run_id,
                verifier_status=verifier_status,
                reviewer_status=reviewer_status,
                changed_files=changed_files,
                event_type="run_observed",
            )
            self.store.update_run_status(
                run_id,
                RunStatus.SUCCEEDED if approved else RunStatus.FAILED,
                failure_reason=None if approved else summary,
                event_type="run_succeeded" if approved else "run_failed",
            )
        except (CancellationRequested, ModelCancellationError):
            changed_files = self._changed_files(config.workspace_path)
            if changed_files:
                self.store.update_run_details(
                    run_id,
                    changed_files=changed_files,
                    event_type="cancelled_run_side_effects_observed",
                )
            DurableExecutionEngine(
                self.store,
                cancellation_registry=self.cancellations,
            ).finalize_cancellation(run_id)
        except Exception as exc:
            message = safe_error_message(exc)
            self.store.complete_attempt(
                attempt.attempt_id,
                AttemptStatus.FAILED,
                error_category=FailureCategory.UNKNOWN,
                error_message=message,
            )
            self.store.update_task_status(
                run_id,
                task.task_id,
                TaskStatus.FAILED,
                event_type="task_failed",
                event_payload={"error": message},
            )
            self.store.update_run_status(
                run_id,
                RunStatus.FAILED,
                failure_reason=message,
                event_type="run_failed",
            )
            raise

    def _persist_preplanning_cancellation(
        self,
        config: AgentBusConfig,
        request: RunCreateRequest,
        run_id: str,
    ) -> None:
        plan = {
            "goal": request.task,
            "steps": [
                {
                    "id": "planning",
                    "title": "Plan requested work",
                    "description": "Planning stopped after cancellation was requested.",
                    "risk": "low",
                    "maximum_attempts": 1,
                    "expected_outputs": [],
                    "done_criteria": [],
                }
            ],
            "test_strategy": "Not started because cancellation was requested.",
            "done_criteria": [],
        }
        engine = DurableExecutionEngine(
            self.store,
            cancellation_registry=self.cancellations,
        )
        engine.create_run(
            request.task,
            plan,
            workflow_type=request.workflow,
            model=config.resolve_model("coder"),
            workspace=str(config.workspace_path),
            metadata={
                "control_request": request.model_dump(
                    mode="json",
                    exclude={"metadata"},
                ),
                "model_routing": config.safe_model_summary(),
                "cancelled_before_planning_completed": True,
            },
            run_id=run_id,
        )
        engine.finalize_cancellation(run_id)

    @staticmethod
    def _changed_files(workspace: Path) -> list[str]:
        try:
            return GitRepository(str(workspace)).changed_files()
        except GitRepositoryError:
            return []


class BackgroundRunSupervisor:
    def __init__(
        self,
        backend: RunBackend,
        *,
        workspace_service: WorkspaceService | None = None,
        max_background_runs: int = 4,
    ):
        if max_background_runs < 1 or max_background_runs > 32:
            raise ValueError("max_background_runs must be between 1 and 32")
        self.backend = backend
        self.workspace_service = workspace_service or WorkspaceService()
        self._executor = ThreadPoolExecutor(
            max_workers=max_background_runs,
            thread_name_prefix="agentbus-control",
        )
        self._active: dict[str, ActiveRun] = {}
        self._workspace_owners: dict[str, str] = {}
        self._lock = threading.RLock()
        self._closed = False

    def submit(self, request: RunCreateRequest) -> RunAcceptedResponse:
        with self._lock:
            self._ensure_open()
        workspace = self.workspace_service.validate(
            request=self._workspace_request(request)
        )
        if not workspace.valid:
            raise ControlPlaneConflictError(
                workspace.message or "The selected workspace is invalid."
            )
        run_id = uuid.uuid4().hex
        created_at = utc_now()
        with self._lock:
            self._ensure_open()
            self._claim_workspace(run_id, workspace.workspace)
            try:
                prepare = getattr(self.backend, "prepare", None)
                if prepare is not None:
                    prepare(run_id)
                future = self._executor.submit(
                    self.backend.execute_new,
                    request.model_copy(update={"workspace": workspace.workspace}),
                    run_id,
                )
            except Exception:
                self._release(run_id, workspace.workspace)
                raise
            active = ActiveRun(
                run_id=run_id,
                workspace=workspace.workspace,
                created_at=created_at,
                future=future,
                operation="create",
            )
            self._active[run_id] = active
            future.add_done_callback(
                lambda _future, item=active: self._completed(item)
            )
        return RunAcceptedResponse(
            run_id=run_id,
            status="pending",
            workspace=workspace.workspace,
            created_at=created_at,
        )

    def resume(self, run_id: str) -> ResumeResponse:
        try:
            workspace = str(Path(self.backend.workspace_for(run_id)).resolve())
        except RunNotFoundError as exc:
            raise ControlPlaneNotFoundError("The requested run was not found.") from exc
        with self._lock:
            self._ensure_open()
            if run_id in self._active:
                raise ControlPlaneConflictError("The run already has an active owner.")
            self._claim_workspace(run_id, workspace)
            try:
                future = self._executor.submit(self.backend.resume, run_id)
            except Exception:
                self._release(run_id, workspace)
                raise
            active = ActiveRun(
                run_id=run_id,
                workspace=workspace,
                created_at=utc_now(),
                future=future,
                operation="resume",
            )
            self._active[run_id] = active
            future.add_done_callback(
                lambda _future, item=active: self._completed(item)
            )
        return ResumeResponse(run_id=run_id, status="running", resumed=True)

    def cancel(self, run_id: str, reason: str | None = None) -> CancelResponse:
        with self._lock:
            active = self._active.get(run_id)
        try:
            status = self.backend.cancel(run_id, reason)
        except RunNotFoundError as exc:
            raise ControlPlaneNotFoundError("The requested run was not found.") from exc
        if active is None or active.future.done():
            finalize = getattr(self.backend, "finalize_cancellation", None)
            if finalize is not None:
                status = finalize(run_id)
        lifecycle = None
        get_cancellation_state = getattr(
            self.backend,
            "cancellation_state",
            None,
        )
        if get_cancellation_state is not None:
            lifecycle = cancellation_lifecycle(get_cancellation_state(run_id))
        return CancelResponse(
            run_id=run_id,
            status=status.value,
            cancellation_requested=True,
            **({"cancellation": lifecycle} if lifecycle is not None else {}),
        )

    def is_active(self, run_id: str) -> bool:
        with self._lock:
            return run_id in self._active

    def has_active_runs(self) -> bool:
        with self._lock:
            return bool(self._active)

    def shutdown(self, *, wait: bool = True) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
        self._executor.shutdown(wait=wait, cancel_futures=False)
        backend_shutdown = getattr(self.backend, "shutdown", None)
        if backend_shutdown is not None:
            backend_shutdown()

    @staticmethod
    def _workspace_request(request: RunCreateRequest):
        from agentbus.control.models import WorkspaceValidationRequest

        return WorkspaceValidationRequest(
            workspace=request.workspace,
            require_git=bool(
                request.durable
                or request.parallel
                or request.commit_changes
                or request.create_pr
            ),
        )

    def _claim_workspace(self, run_id: str, workspace: str) -> None:
        key = _workspace_key(workspace)
        owner = self._workspace_owners.get(key)
        if owner is not None:
            raise ControlPlaneConflictError(
                f"Workspace already has an active AgentBus run: {owner}."
            )
        self._workspace_owners[key] = run_id

    def _completed(self, active: ActiveRun) -> None:
        finalize = getattr(self.backend, "finalize_cancellation", None)
        if finalize is not None:
            try:
                finalize(active.run_id)
            except RunNotFoundError:
                pass
        with self._lock:
            self._release(active.run_id, active.workspace)

    def _release(self, run_id: str, workspace: str) -> None:
        self._active.pop(run_id, None)
        key = _workspace_key(workspace)
        if self._workspace_owners.get(key) == run_id:
            self._workspace_owners.pop(key, None)

    def _ensure_open(self) -> None:
        if self._closed:
            raise ControlPlaneUnavailableError(
                "The control-plane supervisor is shutting down."
            )


def _workspace_key(workspace: str) -> str:
    return os.path.normcase(str(Path(workspace).resolve()))
