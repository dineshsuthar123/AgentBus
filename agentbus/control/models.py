from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

CONTROL_PROTOCOL_VERSION = "1.0"
API_PREFIX = "/api/v1"


class ProtocolModel(BaseModel):
    model_config = ConfigDict(extra="forbid", use_enum_values=True)


class WorkflowMode(str, Enum):
    SINGLE = "single"
    MULTI = "multi"


class ErrorBody(ProtocolModel):
    code: str
    message: str
    retryable: bool = False
    details: dict[str, Any] = Field(default_factory=dict)


class ErrorResponse(ProtocolModel):
    error: ErrorBody


class HealthResponse(ProtocolModel):
    status: Literal["ok"] = "ok"
    protocol_version: str = CONTROL_PROTOCOL_VERSION


class InfoResponse(ProtocolModel):
    protocol_version: str = CONTROL_PROTOCOL_VERSION
    agentbus_version: str
    daemon_id: str
    pid: int = Field(ge=1)
    host: str
    port: int = Field(ge=0, le=65535)
    started_at: datetime
    state_database: str
    capabilities: list[str] = Field(default_factory=list)


class WorkspaceValidationRequest(ProtocolModel):
    workspace: str = Field(min_length=1, max_length=4096)
    require_git: bool = False


class WorkspaceValidationResponse(ProtocolModel):
    valid: bool
    workspace: str
    git_top_level: str | None = None
    is_git_repository: bool = False
    message: str | None = None


class RoleModelOverrides(ProtocolModel):
    planner: str | None = Field(default=None, max_length=256)
    coder: str | None = Field(default=None, max_length=256)
    reviewer: str | None = Field(default=None, max_length=256)
    summarizer: str | None = Field(default=None, max_length=256)


class RunCreateRequest(ProtocolModel):
    task: str = Field(min_length=1, max_length=100_000)
    workspace: str = Field(min_length=1, max_length=4096)
    provider: Literal["ollama", "azure"] = "ollama"
    fallback_provider: Literal["ollama", "azure"] | None = None
    workflow: WorkflowMode = WorkflowMode.MULTI
    durable: bool = False
    parallel: bool = False
    max_workers: int = Field(default=1, ge=1, le=32)
    role_models: RoleModelOverrides = Field(default_factory=RoleModelOverrides)
    fallback_enabled: bool = False
    live_provider_consent: bool = False
    create_pr: bool = False
    commit_changes: bool = False
    keep_worktrees: bool = True
    retry_limit: int = Field(default=2, ge=0, le=20)
    tags: list[str] = Field(default_factory=list, max_length=32)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("task")
    @classmethod
    def task_must_contain_text(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("task must contain non-whitespace text")
        return value

    @field_validator("tags")
    @classmethod
    def tags_are_bounded(cls, value: list[str]) -> list[str]:
        normalized: list[str] = []
        for tag in value:
            clean = tag.strip()
            if not clean or len(clean) > 64:
                raise ValueError("tags must contain between 1 and 64 characters")
            if clean not in normalized:
                normalized.append(clean)
        return normalized

    @model_validator(mode="after")
    def execution_modes_are_compatible(self) -> "RunCreateRequest":
        if self.parallel and not (
            self.durable and self.workflow == WorkflowMode.MULTI
        ):
            raise ValueError("parallel execution requires durable multi-agent mode")
        if self.max_workers > 1 and not self.parallel:
            raise ValueError("max_workers greater than one requires parallel execution")
        if self.create_pr and not self.commit_changes:
            raise ValueError("PR creation requires commit_changes")
        if self.fallback_enabled and self.fallback_provider is None:
            raise ValueError("fallback_enabled requires a fallback_provider")
        if self.provider == "azure" and not self.live_provider_consent:
            raise ValueError("Azure execution requires explicit live_provider_consent")
        return self


class RunAcceptedResponse(ProtocolModel):
    run_id: str
    status: str
    workspace: str
    created_at: datetime


class RunSummary(ProtocolModel):
    run_id: str
    status: str
    workflow: str
    workspace: str
    original_task: str
    created_at: datetime
    updated_at: datetime
    completed_at: datetime | None = None
    verifier_status: str | None = None
    reviewer_status: str | None = None
    failure_reason: str | None = None
    changed_files: list[str] = Field(default_factory=list)
    version: int = Field(ge=1)


class RunListResponse(ProtocolModel):
    runs: list[RunSummary]
    total: int = Field(ge=0)


class TaskSummary(ProtocolModel):
    task_id: str
    title: str
    description: str
    status: str
    position: int = Field(ge=0)
    dependencies: list[str] = Field(default_factory=list)
    expected_outputs: list[str] = Field(default_factory=list)
    done_criteria: list[str] = Field(default_factory=list)
    assigned_role: str
    risk: str
    attempts: int = Field(ge=0)
    worker_id: str | None = None
    provider: str | None = None
    model: str | None = None
    failure_category: str | None = None
    failure_message: str | None = None
    verifier_status: str | None = None
    reviewer_status: str | None = None
    created_at: datetime
    updated_at: datetime


class TaskListResponse(ProtocolModel):
    run_id: str
    tasks: list[TaskSummary]


class SchedulerResponse(ProtocolModel):
    run_id: str
    configured_max_workers: int = Field(ge=1)
    parallel_enabled: bool
    workers_used: list[str] = Field(default_factory=list)
    current_leases: list[dict[str, Any]] = Field(default_factory=list)
    expired_leases: list[dict[str, Any]] = Field(default_factory=list)
    integration_order: list[str] = Field(default_factory=list)
    integration_conflicts: list[dict[str, Any]] = Field(default_factory=list)


class WorktreeSummary(ProtocolModel):
    worktree_id: str
    task_id: str | None = None
    path: str
    branch: str | None = None
    status: str
    retained: bool = False


class WorktreeListResponse(ProtocolModel):
    run_id: str
    worktrees: list[WorktreeSummary]


class UsageResponse(ProtocolModel):
    run_id: str
    requests: int = Field(default=0, ge=0)
    input_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(default=0, ge=0)
    total_tokens: int = Field(default=0, ge=0)
    retries: int = Field(default=0, ge=0)
    fallbacks: int = Field(default=0, ge=0)
    latency_seconds: float = Field(default=0.0, ge=0)
    routes: list[dict[str, Any]] = Field(default_factory=list)


class RunReportResponse(ProtocolModel):
    run_id: str
    status: str
    report: dict[str, Any]


class EventEnvelope(ProtocolModel):
    sequence: int = Field(ge=1)
    event_type: str
    timestamp: datetime
    run_id: str | None = None
    task_id: str | None = None
    worker_id: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)


class EventListResponse(ProtocolModel):
    events: list[EventEnvelope]
    last_sequence: int = Field(default=0, ge=0)


class ApprovalSummary(ProtocolModel):
    approval_id: str
    run_id: str
    task_id: str
    risk_category: str
    reason: str | None = None
    requested_action: str
    affected_paths: list[str] = Field(default_factory=list)
    command: list[str] | None = None
    created_at: datetime
    state: str
    revision: int = Field(default=1, ge=1)


class ApprovalListResponse(ProtocolModel):
    run_id: str
    approvals: list[ApprovalSummary]


class ApprovalDecisionRequest(ProtocolModel):
    revision: int = Field(ge=1)
    reason: str | None = Field(default=None, max_length=2000)


class ApprovalDecisionResponse(ProtocolModel):
    approval: ApprovalSummary
    idempotent: bool = False


class ChangeSummary(ProtocolModel):
    path: str
    status: str
    tracked: bool
    additions: int | None = Field(default=None, ge=0)
    deletions: int | None = Field(default=None, ge=0)
    binary: bool = False
    generated: bool = False
    generated_reason: str | None = None
    classification: str
    task_id: str | None = None


class ChangeListResponse(ProtocolModel):
    run_id: str
    workspace: str
    changes: list[ChangeSummary]
    truncated: bool = False


class FileContentResponse(ProtocolModel):
    run_id: str
    path: str
    content: str
    revision: Literal["before", "after"]
    truncated: bool = False


class DiffResponse(ProtocolModel):
    run_id: str
    path: str | None = None
    diff: str
    truncated: bool = False
    byte_limit: int = Field(ge=1)


class ProviderSummary(ProtocolModel):
    name: str
    configured: bool
    ready: bool
    model: str | None = None
    endpoint_host: str | None = None
    message: str | None = None


class ProviderListResponse(ProtocolModel):
    providers: list[ProviderSummary]


class ProviderCheckRequest(ProtocolModel):
    provider: Literal["ollama", "azure"]
    live_consent: bool = False


class DoctorResponse(ProtocolModel):
    status: str
    checks: list[dict[str, Any]]


class CancelResponse(ProtocolModel):
    run_id: str
    status: str
    cancellation_requested: bool


class ResumeResponse(ProtocolModel):
    run_id: str
    status: str
    resumed: bool


class RunActionRequest(ProtocolModel):
    reason: str | None = Field(default=None, max_length=2000)


class DaemonRegistryEntry(ProtocolModel):
    daemon_id: str
    pid: int = Field(ge=1)
    executable: str
    process_start_identity: str
    host: str
    port: int = Field(ge=0, le=65535)
    protocol_version: str = CONTROL_PROTOCOL_VERSION
    agentbus_version: str
    started_at: datetime
    heartbeat_at: datetime
    state_database: str
    registry_path: str


class ReadyHandshake(ProtocolModel):
    protocol_version: str = CONTROL_PROTOCOL_VERSION
    host: str
    port: int = Field(ge=0, le=65535)
    daemon_id: str
    pid: int = Field(ge=1)
    agentbus_version: str
    registry_path: str
    bearer_token: str = Field(min_length=32)
    token_delivery: Literal["parent_process_stdout"] = "parent_process_stdout"
