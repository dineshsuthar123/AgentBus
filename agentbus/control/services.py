from __future__ import annotations

import re
import subprocess
import threading
from collections import Counter
from datetime import datetime, timezone
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
    McpConfiguredToolSummary,
    McpServerCheckResponse,
    McpServerListResponse,
    McpServerSummary,
    ProviderListResponse,
    ProviderSummary,
    RunListResponse,
    RunReportResponse,
    RunSummary,
    SchedulerResponse,
    TaskListResponse,
    TaskSummary,
    ToolAuditEntryResponse,
    ToolAuditListResponse,
    ToolDescriptorDetail,
    ToolDescriptorSummary,
    ToolInvocationDetail,
    ToolInvocationListResponse,
    ToolInvocationSummary,
    ToolListResponse,
    ToolPolicyEvaluationRequest,
    ToolPolicyEvaluationResponse,
    ToolPolicyResponse,
    UsageResponse,
    WorkspaceValidationRequest,
    WorkspaceValidationResponse,
    WorktreeListResponse,
    WorktreeSummary,
)
from agentbus.doctor import run_doctor
from agentbus.execution.cancellation import (
    CancellationRequested,
    CancellationState,
    CancellationToken,
)
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
    TaskNotFoundError,
    ToolApprovalNotFoundError,
    ToolInvocationNotFoundError,
)
from agentbus.git.repository import (
    GitRepository,
    GitRepositoryError,
    WorkspaceRepositoryMismatch,
)
from agentbus.mcp.errors import McpError
from agentbus.mcp.importer import build_mcp_client
from agentbus.mcp.models import (
    McpServerConfig,
    McpTransportKind,
    namespace_mcp_tool,
)
from agentbus.repo.artifact_policy import ArtifactCategory
from agentbus.policy import ToolApprovalDisposition, ToolPolicyEngine
from agentbus.policy.defaults import DEFAULT_TOOL_POLICY
from agentbus.sandbox.platform import ExecutableCatalog
from agentbus.security.redaction import (
    redact_text,
    safe_endpoint_host,
    sanitize_json,
)
from agentbus.tools.capabilities import derive_required_capabilities
from agentbus.tools.descriptors import descriptor_map
from agentbus.tools.filesystem_security import (
    FileSystemSecurityError,
    normalize_relative_tool_path,
)
from agentbus.tools.protocol import (
    ToolCapabilityEscalationError,
    ToolDescriptor,
    ToolInvocation,
    ToolInvocationContext,
    ToolPolicyOutcome,
    ToolProtocolError,
    sha256_json,
)
from agentbus.tools.records import (
    TERMINAL_TOOL_STATUSES,
    ToolApprovalRecord,
    ToolInvocationRecord,
)

MAX_DIFF_BYTES = 100_000
MAX_FILE_BYTES = 1_000_000
_SECRET_NAMES = {
    ".env",
    ".npmrc",
    ".pypirc",
    "application_default_credentials.json",
    "credentials",
    "credentials.json",
    "id_rsa",
    "id_ed25519",
    "secrets.json",
}
_SECRET_SUFFIXES = {".key", ".pem", ".p12", ".pfx", ".jks", ".keystore"}
_FORBIDDEN_PARTS = {
    ".agentbus",
    ".aws",
    ".azure",
    ".codex",
    ".docker",
    ".git",
    ".kube",
    ".ssh",
}
_COMMIT_SHA = re.compile(r"^[0-9a-fA-F]{40,64}$")
_CONTROL_PROCESS_EXECUTABLES = ("git", "pytest", "python")
_MCP_DIAGNOSTIC_TIMEOUT_SECONDS = 10.0
_POLICY_RULES = (
    {
        "rule_id": "deny.unsafe_path_syntax",
        "outcome": "deny",
        "description": "Reject traversal, device, UNC, NUL, and alternate stream paths.",
    },
    {
        "rule_id": "deny.outside_assigned_roots",
        "outcome": "deny",
        "description": "Reject resources outside the assigned workspace and worktree.",
    },
    {
        "rule_id": "deny.protected_file",
        "outcome": "deny",
        "description": "Reject credentials, keys, daemon state, and control metadata.",
    },
    {
        "rule_id": "deny.shell_execution",
        "outcome": "deny",
        "description": "Reject shell interpreters and shell-based execution.",
    },
    {
        "rule_id": "deny.destructive_git",
        "outcome": "deny",
        "description": "Reject destructive, remote, and global Git operations.",
    },
    {
        "rule_id": "deny.unrestricted_network",
        "outcome": "deny",
        "description": "Reject network access unless run policy explicitly enables it.",
    },
    {
        "rule_id": "deny.reviewer_mutation",
        "outcome": "deny",
        "description": "Keep reviewer tool access read-only by default.",
    },
    {
        "rule_id": "deny.untrusted_workspace_execution",
        "outcome": "deny",
        "description": "Reject mutation and process execution in untrusted workspaces.",
    },
    {
        "rule_id": "approval.mcp_invoke",
        "outcome": "require_approval",
        "description": "Require exact approval for configured MCP calls.",
    },
    {
        "rule_id": "approval.large_path_set",
        "outcome": "require_approval",
        "description": "Require approval when an invocation affects too many paths.",
    },
    {
        "rule_id": "approval.sensitive_project_file",
        "outcome": "require_approval",
        "description": "Require approval for CI, deployment, and security files.",
    },
    {
        "rule_id": "approval.file_delete",
        "outcome": "require_approval",
        "description": "Require exact approval for filesystem deletion.",
    },
    {
        "rule_id": "approval.package_install",
        "outcome": "require_approval",
        "description": "Require approval for package installation.",
    },
    {
        "rule_id": "approval.network_process",
        "outcome": "require_approval",
        "description": "Require destination-scoped approval for network processes.",
    },
    {
        "rule_id": "approval.nonstandard_executable",
        "outcome": "require_approval",
        "description": "Require approval for executables outside the standard allowlist.",
    },
    {
        "rule_id": "approval.extended_process_budget",
        "outcome": "require_approval",
        "description": "Require approval for extended process wall-clock budgets.",
    },
    {
        "rule_id": "allow.constrained_process",
        "outcome": "allow_with_constraints",
        "description": "Allow configured processes only inside the assigned worktree.",
    },
    {
        "rule_id": "allow.read_only",
        "outcome": "allow",
        "description": "Allow bounded reads inside assigned roots.",
    },
    {
        "rule_id": "allow.scoped_mutation",
        "outcome": "allow_with_constraints",
        "description": "Allow consented mutations constrained to a trusted worktree.",
    },
    {
        "rule_id": "approval.default_risk",
        "outcome": "require_approval",
        "description": "Require approval when no automatic allow rule applies.",
    },
    {
        "rule_id": "deny.invalid_approval",
        "outcome": "deny",
        "description": "Reject stale or scope-mismatched approval grants.",
    },
    {
        "rule_id": "allow.approved_invocation",
        "outcome": "allow_with_constraints",
        "description": "Allow only the exact capability scope bound to a valid approval.",
    },
)


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
        revisions = _integration_revisions(run)
        if revisions is not None:
            base_commit, integration_commit = revisions
            changes = repository.changed_files_between(
                base_commit,
                integration_commit,
            )
            result = []
            for path in changes:
                classification = repository.artifact_policy.classify(
                    path,
                    git_ignored=False,
                )
                result.append(
                    ChangeSummary(
                        path=path,
                        status="committed",
                        tracked=True,
                        generated=(
                            classification.category == ArtifactCategory.GENERATED
                        ),
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
        revisions = _integration_revisions(run)
        if revisions is None:
            diff = repository.full_diff(max_chars=limit, paths=paths)
        else:
            diff = repository.commit_diff(
                *revisions,
                max_chars=limit,
                paths=paths,
            )
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
        revisions = _integration_revisions(run)
        if revision == "after":
            data = (
                self._read_after(repository.workspace, relative)
                if revisions is None
                else self._read_revision(repository, revisions[1], relative)
            )
        elif revision == "before":
            data = (
                self._read_before(repository, relative)
                if revisions is None
                else self._read_revision(repository, revisions[0], relative)
            )
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
        try:
            normalized = normalize_relative_tool_path(value)
        except FileSystemSecurityError as exc:
            raise ControlPlaneForbiddenError(
                "The requested path is not safe."
            ) from exc
        relative = PurePosixPath(normalized)
        try:
            candidate = (root / Path(*relative.parts)).resolve()
        except (OSError, RuntimeError) as exc:
            raise ControlPlaneForbiddenError(
                "The requested path cannot be resolved safely."
            ) from exc
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
        return RepositoryReviewService._read_revision(
            repository,
            "HEAD",
            relative,
        )

    @staticmethod
    def _read_revision(
        repository: GitRepository,
        revision: str,
        relative: str,
    ) -> bytes:
        repository.validate_workspace()
        try:
            result = subprocess.run(
                ["git", "show", f"{revision}:{relative}"],
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
        mcp_executable_catalog: ExecutableCatalog | None = None,
    ):
        self.config = config
        self.store = store or StateStore(config.state_database_path)
        self.workspace_service = workspace_service or WorkspaceService()
        self.repository = RepositoryReviewService(self.workspace_service)
        self._mcp_server_configs = {
            server.server_id: server for server in config.mcp_server_configs
        }
        self._mcp_check_locks = {
            server_id: threading.Lock() for server_id in self._mcp_server_configs
        }
        self._mcp_executable_catalog = mcp_executable_catalog

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
        payload = report.model_dump(mode="json")
        payload["tool_runtime"] = self._tool_runtime_report(run_id)
        safe = sanitize_json(payload)
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

    def tools(self) -> ToolListResponse:
        descriptors = self._tool_descriptors(self.config.workspace_path)
        values = [
            self._tool_descriptor_summary(descriptors[name])
            for name in sorted(descriptors)
        ]
        return ToolListResponse(tools=values, total=len(values))

    def tool(self, tool_name: str) -> ToolDescriptorDetail:
        descriptors = self._tool_descriptors(self.config.workspace_path)
        try:
            descriptor = descriptors[tool_name]
        except KeyError as exc:
            raise ControlPlaneNotFoundError(
                "The requested tool descriptor was not found."
            ) from exc
        summary = self._tool_descriptor_summary(descriptor)
        return ToolDescriptorDetail(
            **summary.model_dump(mode="python"),
            argument_schema=sanitize_json(descriptor.argument_schema),
            output_schema=sanitize_json(descriptor.output_schema),
        )

    def tool_invocations(
        self,
        run_id: str,
        *,
        after_sequence: int = 0,
        limit: int = 100,
    ) -> ToolInvocationListResponse:
        self.get_run(run_id)
        records = self.store.list_tool_invocations(
            run_id,
            after_sequence=after_sequence,
            limit=min(limit + 1, 1000),
        )
        truncated = len(records) > limit
        page = records[:limit]
        return ToolInvocationListResponse(
            run_id=run_id,
            invocations=[self._tool_invocation_summary(item) for item in page],
            after_sequence=after_sequence,
            next_sequence=(
                page[-1].invocation_sequence if page else after_sequence
            ),
            truncated=truncated,
        )

    def tool_invocation(
        self,
        run_id: str,
        invocation_id: str,
    ) -> ToolInvocationDetail:
        self.get_run(run_id)
        try:
            record = self.store.get_tool_invocation(run_id, invocation_id)
        except ToolInvocationNotFoundError as exc:
            raise ControlPlaneNotFoundError(
                "The requested tool invocation was not found."
            ) from exc
        summary = self._tool_invocation_summary(record)
        return ToolInvocationDetail(
            **summary.model_dump(mode="python"),
            workspace=record.workspace_identity,
            worktree=record.worktree_identity,
            arguments_sha256=record.arguments_sha256,
            capability_fingerprint=record.capability_fingerprint,
            idempotency_key_sha256=record.idempotency_key_sha256,
            process_slot=record.process_slot,
            result=record.safe_result,
        )

    def cancellable_tool_invocation(
        self,
        run_id: str,
        invocation_id: str,
    ) -> ToolInvocationRecord:
        self.get_run(run_id)
        try:
            record = self.store.get_tool_invocation(run_id, invocation_id)
        except ToolInvocationNotFoundError as exc:
            raise ControlPlaneNotFoundError(
                "The requested tool invocation was not found."
            ) from exc
        if record.status in TERMINAL_TOOL_STATUSES:
            raise ControlPlaneConflictError(
                "A terminal tool invocation cannot be cancelled."
            )
        return record

    def tool_audit(
        self,
        run_id: str,
        *,
        after_sequence: int = 0,
        limit: int = 100,
    ) -> ToolAuditListResponse:
        self.get_run(run_id)
        entries = self.store.list_tool_audits(
            run_id,
            after_sequence=after_sequence,
            limit=min(limit + 1, 1000),
        )
        truncated = len(entries) > limit
        page = entries[:limit]
        return ToolAuditListResponse(
            run_id=run_id,
            records=[
                ToolAuditEntryResponse(
                    audit_sequence=entry.audit_sequence,
                    record=entry.record,
                )
                for entry in page
            ],
            after_sequence=after_sequence,
            next_sequence=(page[-1].audit_sequence if page else after_sequence),
            truncated=truncated,
        )

    def tool_policy(self) -> ToolPolicyResponse:
        return ToolPolicyResponse(
            outcomes=[outcome.value for outcome in ToolPolicyOutcome],
            configuration=sanitize_json(
                DEFAULT_TOOL_POLICY.model_dump(mode="json")
            ),
            rules=[dict(rule) for rule in _POLICY_RULES],
        )

    def evaluate_tool_policy(
        self,
        request: ToolPolicyEvaluationRequest,
    ) -> ToolPolicyEvaluationResponse:
        run = self.get_run(request.run_id)
        try:
            self.store.get_task(request.run_id, request.task_id)
        except TaskNotFoundError as exc:
            raise ControlPlaneNotFoundError(
                "The requested task was not found."
            ) from exc
        descriptors = self._tool_descriptors(run.workspace)
        try:
            descriptor = descriptors[request.tool_name]
        except KeyError as exc:
            raise ControlPlaneNotFoundError(
                "The requested tool descriptor was not found."
            ) from exc
        workspace = str(Path(run.workspace).expanduser().resolve())
        cancellation = self.store.get_cancellation_state(request.run_id)
        timeout = request.timeout_seconds or min(
            descriptor.maximum_timeout_seconds,
            request.resource_budget.wall_clock_seconds,
        )
        invocation = ToolInvocation(
            invocation_id=(
                "policy-eval-"
                + sha256_json(
                    {
                        "run_id": request.run_id,
                        "task_id": request.task_id,
                        "tool_name": request.tool_name,
                        "arguments": request.arguments,
                        "revision": request.invocation_revision,
                    }
                )[:40]
            ),
            run_id=request.run_id,
            task_id=request.task_id,
            tool_name=descriptor.name,
            tool_version=descriptor.version,
            arguments=request.arguments,
            requested_capabilities=descriptor.capabilities,
            context=ToolInvocationContext(
                workspace_identity=workspace,
                worktree_identity=workspace,
                caller_role=request.caller_role,
                workspace_trusted=request.workspace_trusted,
                provider_consented=request.provider_consented,
                policy_context={"control_diagnostic": True},
            ),
            timeout_seconds=timeout,
            resource_budget=request.resource_budget,
            cancellation_revision=(
                cancellation.revision if cancellation.requested else 0
            ),
            invocation_revision=request.invocation_revision,
        )
        try:
            required = derive_required_capabilities(invocation, descriptor)
            expected = [
                str(getattr(capability, "value", capability))
                for capability in request.expected_capabilities
            ]
            required_names = [capability.name.value for capability in required]
            if expected and (
                len(expected) != len(set(expected))
                or set(expected) != set(required_names)
            ):
                raise ToolCapabilityEscalationError(
                    "Expected capability names do not match runtime derivation."
                )
            invocation = invocation.model_copy(
                update={"requested_capabilities": required}
            )
            decision = ToolPolicyEngine().evaluate(invocation, descriptor)
        except (ToolProtocolError, ValueError) as exc:
            raise ControlPlaneForbiddenError(str(exc)) from exc
        return ToolPolicyEvaluationResponse(
            decision=decision,
            required_capabilities=list(required),
        )

    def mcp_servers(self) -> McpServerListResponse:
        servers = [
            self._mcp_server_summary(self._mcp_server_configs[server_id])
            for server_id in sorted(self._mcp_server_configs)
        ]
        return McpServerListResponse(servers=servers, total=len(servers))

    def check_mcp_server(self, server_id: str) -> McpServerCheckResponse:
        try:
            config = self._mcp_server_configs[server_id]
        except KeyError as exc:
            raise ControlPlaneNotFoundError(
                "The requested MCP server is not configured."
            ) from exc
        lock = self._mcp_check_locks[server_id]
        if not lock.acquire(blocking=False):
            raise ControlPlaneConflictError(
                "An MCP diagnostic check is already running for this server."
            )

        client = None
        timer = None
        ready = False
        cleanup_completed = True
        message = None
        protocol_version = None
        server_name = None
        server_version = None
        capabilities: list[str] = []
        advertised_tools: list[str] = []
        try:
            diagnostic_config = config.model_copy(
                update={
                    "startup_timeout_seconds": min(
                        config.startup_timeout_seconds,
                        _MCP_DIAGNOSTIC_TIMEOUT_SECONDS,
                    ),
                    "request_timeout_seconds": min(
                        config.request_timeout_seconds,
                        _MCP_DIAGNOSTIC_TIMEOUT_SECONDS,
                    ),
                }
            )
            catalog = self._mcp_executable_catalog
            if (
                diagnostic_config.transport == McpTransportKind.STDIO
                and catalog is None
            ):
                alias = diagnostic_config.executable_alias
                if alias is None:
                    raise ValueError(
                        "Configured stdio MCP server has no executable alias."
                    )
                catalog = ExecutableCatalog.standard((alias,))
            cancellation = CancellationToken()
            timer = threading.Timer(
                _MCP_DIAGNOSTIC_TIMEOUT_SECONDS,
                cancellation.request,
                args=("MCP diagnostic deadline exceeded.",),
            )
            timer.daemon = True
            timer.start()
            client = build_mcp_client(
                diagnostic_config,
                worktree=self.config.workspace_path,
                executable_catalog=catalog,
            )
            connection = client.connect(cancellation=cancellation)
            client.ping(cancellation=cancellation)
            tools = client.list_tools(cancellation=cancellation)
            protocol_version = connection.protocol_version
            server_name = redact_text(connection.server_name, max_chars=512)
            server_version = redact_text(connection.server_version, max_chars=512)
            capabilities = [
                redact_text(name, max_chars=128) or "[redacted]"
                for name in connection.capabilities[:64]
            ]
            advertised_tools = [tool.namespaced_name for tool in tools]
            ready = True
        except CancellationRequested:
            message = "The configured MCP server check exceeded its deadline."
        except (McpError, ValueError) as exc:
            message = redact_text(str(exc), max_chars=512) or (
                "The configured MCP server check failed."
            )
        except Exception:
            message = "The configured MCP server check failed safely."
        finally:
            if timer is not None:
                timer.cancel()
            if client is not None:
                try:
                    client.close()
                except Exception:
                    cleanup_completed = False
                    ready = False
                    message = "MCP diagnostic transport cleanup could not be confirmed."
            lock.release()

        return McpServerCheckResponse(
            server=self._mcp_server_summary(config),
            ready=ready,
            checked_at=datetime.now(timezone.utc),
            diagnostic_timeout_seconds=_MCP_DIAGNOSTIC_TIMEOUT_SECONDS,
            protocol_version=protocol_version,
            server_name=server_name,
            server_version=server_version,
            capabilities=capabilities,
            advertised_tools=advertised_tools,
            tool_count=len(advertised_tools),
            cleanup_completed=cleanup_completed,
            message=message,
        )

    def approvals(self, run_id: str) -> ApprovalListResponse:
        self.get_run(run_id)
        tool_records = self.store.list_tool_approvals(run_id, limit=1000)
        pending_tool_task_ids = {
            record.request.task_id
            for record in tool_records
            if record.disposition is None
        }
        approvals = [
            self._approval_summary(task)
            for task in self.store.list_tasks(run_id)
            if (
                task.status == TaskStatus.WAITING_FOR_APPROVAL
                and task.task_id not in pending_tool_task_ids
            )
            or self.store.latest_approval(run_id, task.task_id) is not None
        ]
        approvals.extend(self._tool_approval_summary(item) for item in tool_records)
        approvals.sort(key=lambda item: (item.created_at, item.approval_id))
        return ApprovalListResponse(run_id=run_id, approvals=approvals)

    def decide_approval(
        self,
        run_id: str,
        approval_id: str,
        request: ApprovalDecisionRequest,
        decision: ApprovalOutcome,
    ) -> ApprovalDecisionResponse:
        self.get_run(run_id)
        try:
            tool_approval = self.store.get_tool_approval(run_id, approval_id)
        except ToolApprovalNotFoundError:
            tool_approval = None
        if tool_approval is not None:
            return self._decide_tool_approval(
                tool_approval,
                request,
                decision,
            )
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

    def _decide_tool_approval(
        self,
        record: ToolApprovalRecord,
        request: ApprovalDecisionRequest,
        decision: ApprovalOutcome,
    ) -> ApprovalDecisionResponse:
        desired = (
            ToolApprovalDisposition.APPROVED
            if decision == ApprovalOutcome.APPROVED
            else ToolApprovalDisposition.REJECTED
        )
        if record.disposition is not None:
            if record.disposition == desired.value:
                return ApprovalDecisionResponse(
                    approval=self._tool_approval_summary(record),
                    idempotent=True,
                )
            raise ControlPlaneConflictError(
                "This tool approval already has a terminal decision."
            )
        if request.revision != record.request.invocation_revision:
            raise ControlPlaneConflictError(
                "The tool approval revision is stale; refresh before deciding."
            )
        updated = self.store.decide_tool_approval(
            record.request.run_id,
            record.approval_id,
            disposition=desired,
            reason=request.reason,
        )
        return ApprovalDecisionResponse(
            approval=self._tool_approval_summary(updated)
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

    @staticmethod
    def _tool_approval_summary(record: ToolApprovalRecord) -> ApprovalSummary:
        request = record.request
        command = None
        if request.executable:
            command = [request.executable, *request.arguments_summary]
        return ApprovalSummary(
            approval_id=record.approval_id,
            run_id=request.run_id,
            task_id=request.task_id,
            risk_category="tool",
            reason=request.reason,
            requested_action=f"Invoke {request.tool_name}",
            affected_paths=list(request.affected_paths),
            command=command,
            created_at=request.created_at,
            state=record.disposition or "pending",
            revision=request.invocation_revision,
            approval_kind="tool",
            tool_name=request.tool_name,
            tool_version=request.tool_version,
            capabilities=list(request.requested_capabilities),
            arguments_summary=list(request.arguments_summary),
            executable=request.executable,
            working_directory=request.working_directory,
            network_destination=request.network_destination,
            policy_rule=request.policy_rule,
            proposed_constraints=list(request.proposed_constraints),
            resource_budget=request.resource_budget,
            expires_at=request.expires_at,
        )

    @staticmethod
    def _tool_descriptor_summary(
        descriptor: ToolDescriptor,
    ) -> ToolDescriptorSummary:
        return ToolDescriptorSummary(
            name=descriptor.name,
            version=descriptor.version,
            protocol_version=descriptor.protocol_version,
            description=descriptor.description,
            capabilities=list(descriptor.capabilities),
            safety=descriptor.safety.value,
            idempotent=descriptor.idempotent,
            supports_cancellation=descriptor.supports_cancellation,
            maximum_timeout_seconds=descriptor.maximum_timeout_seconds,
        )

    @staticmethod
    def _tool_invocation_summary(
        record: ToolInvocationRecord,
    ) -> ToolInvocationSummary:
        return ToolInvocationSummary(
            invocation_sequence=record.invocation_sequence,
            invocation_id=record.invocation_id,
            invocation_revision=record.invocation_revision,
            run_id=record.run_id,
            task_id=record.task_id,
            tool_name=record.tool_name,
            tool_version=record.tool_version,
            protocol_version=record.protocol_version,
            caller_role=record.caller_role,
            status=record.status.value,
            capabilities=list(record.capabilities),
            policy_decision=record.policy_decision,
            approval_id=record.approval_id,
            resource_budget=record.resource_budget,
            resource_usage=record.resource_usage,
            cancellation=record.cancellation,
            error_category=(
                record.error_category.value if record.error_category else None
            ),
            error_message=record.error_message,
            requested_at=record.requested_at,
            started_at=record.started_at,
            completed_at=record.completed_at,
            updated_at=record.updated_at,
        )

    @staticmethod
    def _tool_descriptors(workspace: str | Path) -> dict[str, ToolDescriptor]:
        return descriptor_map(
            workspace=workspace,
            process_executables=_CONTROL_PROCESS_EXECUTABLES,
        )

    @staticmethod
    def _mcp_server_summary(config: McpServerConfig) -> McpServerSummary:
        configured_tools = [
            McpConfiguredToolSummary(
                name=name,
                namespaced_name=namespace_mcp_tool(config.server_id, name),
                capabilities=list(config.capability_map[name]),
            )
            for name in sorted(config.capability_map)
        ]
        return McpServerSummary(
            server_id=config.server_id,
            transport=config.transport.value,
            executable_alias=config.executable_alias,
            endpoint_host=safe_endpoint_host(config.endpoint_url),
            configured_tools=configured_tools,
            supported_protocol_versions=list(config.supported_protocol_versions),
            startup_timeout_seconds=config.startup_timeout_seconds,
            request_timeout_seconds=config.request_timeout_seconds,
        )

    def _tool_runtime_report(self, run_id: str) -> dict[str, Any]:
        invocations = self.store.list_tool_invocations(run_id, limit=1000)
        invocation_records_truncated = bool(
            len(invocations) == 1000
            and self.store.list_tool_invocations(
                run_id,
                after_sequence=invocations[-1].invocation_sequence,
                limit=1,
            )
        )
        approvals = self.store.list_tool_approvals(run_id, limit=1000)
        approval_records_truncated = bool(
            len(approvals) == 1000
            and self.store.list_tool_approvals(
                run_id,
                after_sequence=approvals[-1].approval_sequence,
                limit=1,
            )
        )
        audits = self.store.list_tool_audits(run_id, limit=1000)
        audit_records_truncated = bool(
            len(audits) == 1000
            and self.store.list_tool_audits(
                run_id,
                after_sequence=audits[-1].audit_sequence,
                limit=1,
            )
        )

        status_counts: Counter[str] = Counter()
        policy_outcomes: Counter[str] = Counter()
        policy_rules: Counter[str] = Counter()
        versions: dict[str, set[str]] = {}
        mcp_servers: set[str] = set()
        resource_totals: Counter[str] = Counter()
        denied_operations: list[dict[str, Any]] = []
        artifacts: list[dict[str, Any]] = []
        artifact_count = 0
        output_truncation_count = 0
        timeout_count = 0
        cancellation_count = 0

        for invocation in invocations:
            status_counts[invocation.status.value] += 1
            versions.setdefault(invocation.tool_name, set()).add(
                str(invocation.tool_version)
            )
            decision = invocation.policy_decision
            if decision is not None:
                policy_outcomes[decision.outcome.value] += 1
                policy_rules[decision.rule_id] += 1
            if invocation.status.value == "denied" and len(denied_operations) < 50:
                denied_operations.append(
                    {
                        "invocation_id": invocation.invocation_id,
                        "tool_name": invocation.tool_name,
                        "rule_id": decision.rule_id if decision else None,
                        "error_category": (
                            invocation.error_category.value
                            if invocation.error_category
                            else None
                        ),
                    }
                )
            if invocation.status.value == "timed_out":
                timeout_count += 1
            if invocation.status.value == "cancelled":
                cancellation_count += 1
            for capability in invocation.capabilities:
                mcp_servers.update(capability.scope.mcp_servers)
            usage = invocation.resource_usage
            for name in (
                "wall_clock_seconds",
                "stdout_bytes",
                "stderr_bytes",
                "artifact_bytes",
                "child_processes",
                "file_mutations",
                "written_bytes",
            ):
                resource_totals[name] += getattr(usage, name)
            if usage.memory_bytes is not None:
                resource_totals["memory_bytes"] += usage.memory_bytes
            if usage.cpu_seconds is not None:
                resource_totals["cpu_seconds"] += usage.cpu_seconds
            result = invocation.safe_result
            if result is None:
                continue
            if result.stdout_truncated or result.stderr_truncated:
                output_truncation_count += 1
            for artifact in result.artifacts:
                artifact_count += 1
                if len(artifacts) < 256:
                    artifacts.append(artifact.model_dump(mode="json"))

        approval_states = Counter(
            approval.disposition or "pending" for approval in approvals
        )
        return {
            "invocation_count": len(invocations),
            "invocation_records_truncated": invocation_records_truncated,
            "aggregates_truncated": invocation_records_truncated,
            "status_counts": dict(sorted(status_counts.items())),
            "tool_versions": [
                {"tool_name": name, "versions": sorted(values)}
                for name, values in sorted(versions.items())
            ],
            "policy_decisions": {
                "outcomes": dict(sorted(policy_outcomes.items())),
                "rules": dict(sorted(policy_rules.items())),
            },
            "approvals": {
                "count": len(approvals),
                "states": dict(sorted(approval_states.items())),
                "records_truncated": approval_records_truncated,
            },
            "denied_operations": denied_operations,
            "denied_operations_truncated": status_counts["denied"] > 50,
            "resource_consumption": dict(sorted(resource_totals.items())),
            "output_truncation_count": output_truncation_count,
            "timeout_count": timeout_count,
            "cancellation_count": cancellation_count,
            "mcp_usage": {
                "servers": sorted(mcp_servers),
                "invocation_count": sum(
                    count
                    for tool_name, count in Counter(
                        invocation.tool_name for invocation in invocations
                    ).items()
                    if tool_name.startswith("mcp.")
                ),
            },
            "artifacts": artifacts,
            "artifact_count": artifact_count,
            "artifacts_truncated": artifact_count > len(artifacts),
            "audit_record_count": len(audits),
            "audit_records_truncated": audit_records_truncated,
            "output_bodies_persisted": False,
            "security_constraints": [
                "Tool arguments are represented by hashes or bounded summaries.",
                "Raw stdout, stderr, and structured output bodies are not persisted.",
                "Artifacts are metadata-only and require repository-contained reads.",
                "MCP, filesystem, process, and Git calls use the same policy runtime.",
            ],
        }

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


def _integration_revisions(run: RunRecord) -> tuple[str, str] | None:
    parallel = run.metadata.get("parallel_execution", {})
    if not isinstance(parallel, dict):
        return None
    integration_commit = parallel.get("integration_commit")
    if not integration_commit:
        return None
    base_commit = parallel.get("base_commit")
    if not (
        isinstance(base_commit, str)
        and isinstance(integration_commit, str)
        and _COMMIT_SHA.fullmatch(base_commit)
        and _COMMIT_SHA.fullmatch(integration_commit)
    ):
        raise ControlPlaneConflictError(
            "Persisted integration revision metadata is invalid."
        )
    return base_commit, integration_commit


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
