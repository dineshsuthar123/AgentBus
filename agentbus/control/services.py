from __future__ import annotations

import subprocess
from pathlib import Path, PurePosixPath
from typing import Any

from agentbus.config import AgentBusConfig
from agentbus.control.errors import (
    ControlPlaneConflictError,
    ControlPlaneForbiddenError,
    ControlPlaneNotFoundError,
)
from agentbus.control.models import (
    ApprovalDecisionRequest,
    ApprovalDecisionResponse,
    ApprovalListResponse,
    ApprovalSummary,
    CancellationLifecycle,
    ChangeListResponse,
    ChangeSummary,
    DiffResponse,
    DoctorResponse,
    FileContentResponse,
    ProviderListResponse,
    ProviderSummary,
    RunListResponse,
    RunReportResponse,
    RunSummary,
    SchedulerResponse,
    TaskListResponse,
    TaskSummary,
    UsageResponse,
    WorkspaceValidationRequest,
    WorkspaceValidationResponse,
    WorktreeListResponse,
    WorktreeSummary,
)
from agentbus.doctor import run_doctor
from agentbus.execution.cancellation import CancellationState
from agentbus.execution.engine import DurableExecutionEngine, DurableExecutionError
from agentbus.execution.models import (
    ApprovalOutcome,
    RunRecord,
    TaskRecord,
    TaskStatus,
)
from agentbus.execution.state_store import (
    RunNotFoundError,
    StateStore,
)
from agentbus.git.repository import (
    GitRepository,
    GitRepositoryError,
    WorkspaceRepositoryMismatch,
)
from agentbus.repo.artifact_policy import ArtifactCategory
from agentbus.security.redaction import sanitize_json

MAX_DIFF_BYTES = 100_000
MAX_FILE_BYTES = 1_000_000
_SECRET_NAMES = {
    ".env",
    ".npmrc",
    ".pypirc",
    "credentials",
    "credentials.json",
    "id_rsa",
    "id_ed25519",
}
_SECRET_SUFFIXES = {".key", ".pem", ".p12", ".pfx", ".jks", ".keystore"}
_FORBIDDEN_PARTS = {".git", ".agentbus"}


class WorkspaceService:
    def validate(
        self,
        request: WorkspaceValidationRequest,
    ) -> WorkspaceValidationResponse:
        workspace = Path(request.workspace).expanduser().resolve()
        if not workspace.is_dir():
            return WorkspaceValidationResponse(
                valid=False,
                workspace=str(workspace),
                message="The selected workspace does not exist or is not a directory.",
            )
        repository = GitRepository(str(workspace))
        try:
            top_level = repository.validate_workspace()
        except WorkspaceRepositoryMismatch:
            raise
        except GitRepositoryError:
            if request.require_git:
                return WorkspaceValidationResponse(
                    valid=False,
                    workspace=str(workspace),
                    message="The selected workspace must be an isolated Git repository.",
                )
            return WorkspaceValidationResponse(
                valid=True,
                workspace=str(workspace),
                is_git_repository=False,
            )
        return WorkspaceValidationResponse(
            valid=True,
            workspace=str(workspace),
            git_top_level=str(top_level),
            is_git_repository=True,
        )

    def require_repository(self, workspace: str) -> GitRepository:
        validation = self.validate(
            WorkspaceValidationRequest(workspace=workspace, require_git=True)
        )
        if not validation.valid:
            raise ControlPlaneForbiddenError(
                validation.message or "The workspace is not a valid repository."
            )
        repository = GitRepository(validation.workspace)
        repository.validate_workspace()
        return repository


class RepositoryReviewService:
    def __init__(self, workspace_service: WorkspaceService | None = None):
        self.workspace_service = workspace_service or WorkspaceService()

    def list_changes(self, run: RunRecord) -> ChangeListResponse:
        repository = self.workspace_service.require_repository(run.workspace)
        entries = {entry.path: entry for entry in repository.status_entries()}
        changes = repository.change_set()
        result: list[ChangeSummary] = []
        for path in changes.changed_files:
            entry = entries.get(path)
            classification = repository.artifact_policy.classify(
                path,
                git_ignored=bool(entry and entry.ignored),
            )
            result.append(
                ChangeSummary(
                    path=path,
                    status=entry.status if entry else "changed",
                    tracked=bool(entry and entry.tracked),
                    generated=classification.category == ArtifactCategory.GENERATED,
                    generated_reason=classification.reason,
                    classification=classification.category.value,
                    task_id=_task_attribution(run, path),
                )
            )
        return ChangeListResponse(
            run_id=run.run_id,
            workspace=str(repository.workspace),
            changes=result,
        )

    def diff(
        self,
        run: RunRecord,
        *,
        path: str | None = None,
        byte_limit: int = 30_000,
    ) -> DiffResponse:
        limit = min(max(byte_limit, 1), MAX_DIFF_BYTES)
        repository = self.workspace_service.require_repository(run.workspace)
        paths = [self._safe_relative(repository.workspace, path)] if path else None
        if path:
            self._ensure_public_file(paths[0])
        diff = repository.full_diff(max_chars=limit, paths=paths)
        truncated = diff.endswith("[diff truncated]")
        return DiffResponse(
            run_id=run.run_id,
            path=paths[0] if paths else None,
            diff=diff,
            truncated=truncated,
            byte_limit=limit,
        )

    def file_content(
        self,
        run: RunRecord,
        path: str,
        *,
        revision: str,
    ) -> FileContentResponse:
        repository = self.workspace_service.require_repository(run.workspace)
        relative = self._safe_relative(repository.workspace, path)
        self._ensure_public_file(relative)
        if revision == "after":
            data = self._read_after(repository.workspace, relative)
        elif revision == "before":
            data = self._read_before(repository, relative)
        else:
            raise ControlPlaneForbiddenError("Unsupported file revision.")
        if len(data) > MAX_FILE_BYTES:
            raise ControlPlaneForbiddenError(
                "The requested file is too large for control-plane access."
            )
        if b"\x00" in data:
            raise ControlPlaneForbiddenError(
                "Binary files are not available through the control plane."
            )
        try:
            content = data.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ControlPlaneForbiddenError(
                "The requested file is not UTF-8 text."
            ) from exc
        return FileContentResponse(
            run_id=run.run_id,
            path=relative,
            content=content,
            revision=revision,
        )

    @staticmethod
    def _safe_relative(root: Path, value: str) -> str:
        normalized = value.replace("\\", "/")
        relative = PurePosixPath(normalized)
        if (
            not normalized
            or relative.is_absolute()
            or ".." in relative.parts
            or "." == normalized
            or ":" in relative.parts[0]
        ):
            raise ControlPlaneForbiddenError("The requested path is not safe.")
        candidate = (root / Path(*relative.parts)).resolve()
        try:
            candidate.relative_to(root)
        except ValueError as exc:
            raise ControlPlaneForbiddenError(
                "The requested path is outside the workspace."
            ) from exc
        return relative.as_posix()

    @staticmethod
    def _ensure_public_file(relative: str) -> None:
        path = PurePosixPath(relative)
        lowered_parts = {part.lower() for part in path.parts}
        name = path.name.lower()
        if (
            lowered_parts & _FORBIDDEN_PARTS
            or name in _SECRET_NAMES
            or name.startswith(".env.")
            or path.suffix.lower() in _SECRET_SUFFIXES
        ):
            raise ControlPlaneForbiddenError(
                "Secret or control metadata files are not available."
            )

    @staticmethod
    def _read_after(root: Path, relative: str) -> bytes:
        path = root / Path(*PurePosixPath(relative).parts)
        if not path.is_file():
            raise ControlPlaneNotFoundError("The requested file was not found.")
        if path.stat().st_size > MAX_FILE_BYTES:
            raise ControlPlaneForbiddenError(
                "The requested file is too large for control-plane access."
            )
        return path.read_bytes()

    @staticmethod
    def _read_before(repository: GitRepository, relative: str) -> bytes:
        repository.validate_workspace()
        try:
            result = subprocess.run(
                ["git", "show", f"HEAD:{relative}"],
                cwd=repository.workspace,
                capture_output=True,
                timeout=repository.timeout_seconds,
                shell=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise ControlPlaneNotFoundError(
                "The previous file revision is unavailable."
            ) from exc
        if result.returncode != 0:
            raise ControlPlaneNotFoundError(
                "The previous file revision is unavailable."
            )
        return result.stdout


class ControlQueryService:
    def __init__(
        self,
        config: AgentBusConfig,
        store: StateStore | None = None,
        workspace_service: WorkspaceService | None = None,
    ):
        self.config = config
        self.store = store or StateStore(config.state_database_path)
        self.workspace_service = workspace_service or WorkspaceService()
        self.repository = RepositoryReviewService(self.workspace_service)

    def get_run(self, run_id: str) -> RunRecord:
        try:
            return self.store.get_run(run_id)
        except RunNotFoundError as exc:
            raise ControlPlaneNotFoundError("The requested run was not found.") from exc

    def list_runs(self, limit: int = 100) -> RunListResponse:
        runs = [self.run_summary(run) for run in self.store.list_runs(limit=limit)]
        return RunListResponse(runs=runs, total=len(runs))

    def run_summary(self, run: RunRecord) -> RunSummary:
        return RunSummary(
            run_id=run.run_id,
            status=run.status.value,
            workflow=run.workflow_type,
            workspace=run.workspace,
            original_task=run.original_task,
            created_at=run.created_at,
            updated_at=run.updated_at,
            completed_at=run.completed_at,
            verifier_status=run.verifier_status,
            reviewer_status=run.reviewer_status,
            failure_reason=run.failure_reason,
            changed_files=run.changed_files,
            version=run.version,
            cancellation=cancellation_lifecycle(
                self.store.get_cancellation_state(run.run_id)
            ),
        )

    def tasks(self, run_id: str) -> TaskListResponse:
        self.get_run(run_id)
        attempts = self.store.list_attempts(run_id)
        latest = {
            task_id: max(
                (attempt for attempt in attempts if attempt.task_id == task_id),
                key=lambda attempt: attempt.attempt_number,
                default=None,
            )
            for task_id in {attempt.task_id for attempt in attempts}
        }
        tasks = [
            self._task_summary(task, latest.get(task.task_id))
            for task in self.store.list_tasks(run_id)
        ]
        return TaskListResponse(run_id=run_id, tasks=tasks)

    @staticmethod
    def _task_summary(task: TaskRecord, attempt: Any) -> TaskSummary:
        metadata = attempt.metadata if attempt else {}
        model_requests = metadata.get("model_requests", [])
        last_route = model_requests[-1] if model_requests else {}
        return TaskSummary(
            task_id=task.task_id,
            title=task.spec.title,
            description=task.spec.description,
            status=task.status.value,
            position=task.position,
            dependencies=task.spec.dependency_ids,
            expected_outputs=task.spec.expected_outputs,
            done_criteria=task.spec.done_criteria,
            assigned_role=task.spec.assigned_role,
            risk=task.spec.risk.value,
            attempts=task.current_attempt_count,
            worker_id=_nested(metadata, "worker", "worker_id"),
            provider=last_route.get("provider"),
            model=last_route.get("model"),
            failure_category=(
                attempt.error_category.value
                if attempt and attempt.error_category
                else None
            ),
            failure_message=attempt.error_message if attempt else None,
            verifier_status=_nested(metadata, "verifier", "passed"),
            reviewer_status=_nested(metadata, "task_review", "approved"),
            created_at=task.created_at,
            updated_at=task.updated_at,
        )

    def report(self, run_id: str) -> RunReportResponse:
        self.get_run(run_id)
        report = DurableExecutionEngine(self.store).get_report(run_id)
        safe = sanitize_json(report.model_dump(mode="json"))
        cancellation = cancellation_lifecycle(
            self.store.get_cancellation_state(run_id)
        )
        return RunReportResponse(
            run_id=run_id,
            status=report.status.value,
            report=safe,
            cancellation=cancellation,
        )

    def scheduler(self, run_id: str) -> SchedulerResponse:
        report = DurableExecutionEngine(self.store).get_report(run_id)
        return SchedulerResponse(
            run_id=run_id,
            configured_max_workers=report.configured_max_workers,
            parallel_enabled=report.parallel_mode_enabled,
            workers_used=report.workers_used,
            current_leases=sanitize_json(report.current_leases),
            expired_leases=sanitize_json(report.expired_leases),
            integration_order=report.integration_order,
            integration_conflicts=sanitize_json(report.integration_conflicts),
            cancellation=cancellation_lifecycle(
                self.store.get_cancellation_state(run_id)
            ),
        )

    def worktrees(self, run_id: str) -> WorktreeListResponse:
        self.get_run(run_id)
        worktrees = [
            WorktreeSummary(
                worktree_id=item.worktree_id,
                task_id=item.task_id,
                path=item.path,
                branch=item.branch_ref,
                status=item.status.value,
                retained=item.status.value not in {"removed", "cleanup_pending"},
            )
            for item in self.store.list_worktrees(run_id)
        ]
        return WorktreeListResponse(run_id=run_id, worktrees=worktrees)

    def usage(self, run_id: str) -> UsageResponse:
        self.get_run(run_id)
        requests: list[dict[str, Any]] = []
        for attempt in self.store.list_attempts(run_id):
            values = attempt.metadata.get("model_requests", [])
            if isinstance(values, list):
                requests.extend(item for item in values if isinstance(item, dict))
        usage = UsageResponse(run_id=run_id, requests=len(requests))
        routes: list[dict[str, Any]] = []
        for item in requests:
            raw_usage = item.get("usage", {})
            usage.input_tokens += int(raw_usage.get("input_tokens", 0) or 0)
            usage.output_tokens += int(raw_usage.get("output_tokens", 0) or 0)
            usage.total_tokens += int(raw_usage.get("total_tokens", 0) or 0)
            usage.retries += int(item.get("retry_count", 0) or 0)
            usage.fallbacks += int(bool(item.get("fallback_used")))
            usage.latency_seconds += float(item.get("latency_seconds", 0) or 0)
            routes.append(
                sanitize_json(
                    {
                        "provider": item.get("provider"),
                        "model": item.get("model"),
                        "role": item.get("role"),
                    }
                )
            )
        usage.routes = routes
        return usage

    def approvals(self, run_id: str) -> ApprovalListResponse:
        self.get_run(run_id)
        approvals = [
            self._approval_summary(task)
            for task in self.store.list_tasks(run_id)
            if task.status == TaskStatus.WAITING_FOR_APPROVAL
            or self.store.latest_approval(run_id, task.task_id) is not None
        ]
        return ApprovalListResponse(run_id=run_id, approvals=approvals)

    def decide_approval(
        self,
        run_id: str,
        approval_id: str,
        request: ApprovalDecisionRequest,
        decision: ApprovalOutcome,
    ) -> ApprovalDecisionResponse:
        task_id = _task_id_from_approval(run_id, approval_id)
        task = self.store.get_task(run_id, task_id)
        expected_revision = task.current_attempt_count + 1
        latest = self.store.latest_approval(run_id, task_id)
        if latest is not None:
            if latest.decision == decision:
                return ApprovalDecisionResponse(
                    approval=self._approval_summary(task),
                    idempotent=True,
                )
            raise ControlPlaneConflictError(
                "This approval already has a terminal decision."
            )
        if request.revision != expected_revision:
            raise ControlPlaneConflictError(
                "The approval revision is stale; refresh before deciding."
            )
        engine = DurableExecutionEngine(self.store)
        try:
            if decision == ApprovalOutcome.APPROVED:
                engine.approve_task(run_id, task_id, request.reason)
            else:
                engine.reject_task(run_id, task_id, request.reason)
        except DurableExecutionError as exc:
            raise ControlPlaneConflictError(str(exc)) from exc
        return ApprovalDecisionResponse(
            approval=self._approval_summary(self.store.get_task(run_id, task_id)),
        )

    def _approval_summary(self, task: TaskRecord) -> ApprovalSummary:
        latest = self.store.latest_approval(task.run_id, task.task_id)
        metadata = task.spec.metadata
        action = metadata.get("requested_action") or task.spec.description
        paths = metadata.get("affected_paths") or task.spec.expected_outputs
        command = metadata.get("command")
        return ApprovalSummary(
            approval_id=f"{task.run_id}:{task.task_id}",
            run_id=task.run_id,
            task_id=task.task_id,
            risk_category=task.spec.risk.value,
            reason=latest.reason if latest else metadata.get("risk_reason"),
            requested_action=str(action),
            affected_paths=[str(path) for path in paths],
            command=[str(part) for part in command] if isinstance(command, list) else None,
            created_at=latest.created_at if latest else task.updated_at,
            state=latest.decision.value if latest else "pending",
            revision=task.current_attempt_count + 1,
        )

    def providers(self) -> ProviderListResponse:
        values: list[ProviderSummary] = []
        for name in ("ollama", "azure", "deterministic"):
            try:
                model = self.config.resolve_model("default", provider=name)
                self.config.validate_provider_configuration(name)
                ready = True
                message = None
            except ValueError as exc:
                model = None
                ready = False
                message = str(exc)
            values.append(
                ProviderSummary(
                    name=name,
                    configured=ready,
                    ready=ready,
                    model=model,
                    endpoint_host=(
                        self.config.safe_model_summary().get("endpoint_host")
                        if name == "azure"
                        else ("localhost" if name == "ollama" else None)
                    ),
                    message=message,
                )
            )
        return ProviderListResponse(providers=values)

    def doctor(self, workspace: str | None = None) -> DoctorResponse:
        config = (
            self.config.with_overrides(workspace_dir=workspace)
            if workspace
            else self.config
        )
        report = run_doctor(config)
        payload = report.to_dict()
        return DoctorResponse(
            status=str(payload["status"]),
            checks=sanitize_json(payload["checks"]),
        )


def _task_attribution(run: RunRecord, path: str) -> str | None:
    value = run.metadata.get("file_task_attribution", {})
    if isinstance(value, dict) and value.get(path):
        return str(value[path])
    return None


def cancellation_lifecycle(state: CancellationState) -> CancellationLifecycle:
    active = state.active_non_interruptible_operations
    return CancellationLifecycle(
        requested=state.requested,
        requested_at=state.requested_at,
        reason=state.reason,
        propagated_at=state.propagated_at,
        propagation_sources=state.propagation_sources,
        provider_cancellation_signalled=(
            state.provider_cancellation_requested_at is not None
        ),
        provider_cancellation_requested_at=(
            state.provider_cancellation_requested_at
        ),
        provider_names=state.provider_names,
        provider_cancellation_acknowledged=(
            state.provider_cancellation_acknowledged_at is not None
        ),
        provider_cancellation_acknowledged_at=(
            state.provider_cancellation_acknowledged_at
        ),
        provider_acknowledgement_source=(
            state.provider_acknowledgement_source
        ),
        acknowledged=state.acknowledged,
        acknowledged_at=state.acknowledged_at,
        acknowledgement_source=state.acknowledgement_source,
        acknowledgement_stage=state.acknowledgement_stage,
        active_non_interruptible_operation=active[0] if active else None,
        active_non_interruptible_operations=active,
        operations_completed_after_request=(
            state.operations_completed_after_request
        ),
        completed_after_cancellation_request=bool(
            state.operations_completed_after_request
            or state.tasks_completed_after_request
        ),
        tasks_prevented_from_starting=state.tasks_prevented_from_starting,
        tasks_completed_after_request=state.tasks_completed_after_request,
        scheduling_stopped=state.scheduling_stopped_at is not None,
        scheduling_stopped_at=state.scheduling_stopped_at,
        cleanup_completed=state.cleanup_completed_at is not None,
        cleanup_completed_at=state.cleanup_completed_at,
        resume_eligible=state.resume_eligible,
        terminal_reason=state.terminal_reason,
        revision=state.revision,
    )


def _nested(value: dict[str, Any], *keys: str) -> Any:
    current: Any = value
    for key in keys:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return str(current).lower() if isinstance(current, bool) else current


def _task_id_from_approval(run_id: str, approval_id: str) -> str:
    prefix = f"{run_id}:"
    if not approval_id.startswith(prefix) or len(approval_id) <= len(prefix):
        raise ControlPlaneNotFoundError("The requested approval was not found.")
    return approval_id[len(prefix) :]
