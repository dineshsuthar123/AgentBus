from __future__ import annotations

import json
from datetime import datetime
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from agentbus.tools.protocol import (
    ToolAuditRecord,
    ToolCapability,
    ToolCapabilityName,
    ToolCancellationSnapshot,
    ToolPolicyDecision,
    ToolResourceBudget,
    ToolResourceUsage,
    ToolResult,
    ToolVersion,
)

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


class DeterministicProviderOptions(ProtocolModel):
    profile: Literal[
        "python-calculator",
        "cancellation-two-task",
        "tool-safe-read",
        "tool-atomic-write",
        "tool-source-patch",
        "tool-pytest",
        "tool-git-diff",
        "tool-git-commit",
        "tool-delete-approval",
        "tool-deny-outside-read",
        "tool-deny-credential-read",
        "tool-process-timeout",
        "tool-process-cancel",
        "tool-excessive-output",
        "tool-budget-exhaustion",
        "tool-local-mcp",
        "tool-loop-limit",
    ] = "python-calculator"
    latency_seconds: float = Field(default=0.0, ge=0, le=60)
    latency_roles: list[
        Literal["planner", "coder", "reviewer", "summarizer"]
    ] = Field(default_factory=list, max_length=4)
    failure_kind: Literal[
        "output_error",
        "timeout",
        "service_unavailable",
    ] = "service_unavailable"
    failure_calls: list[int] = Field(default_factory=list, max_length=32)
    failure_roles: list[
        Literal["planner", "coder", "reviewer", "summarizer"]
    ] = Field(default_factory=list, max_length=4)

    @field_validator("failure_calls")
    @classmethod
    def failure_calls_are_positive_and_unique(cls, value: list[int]) -> list[int]:
        if any(call < 1 for call in value):
            raise ValueError("failure_calls must contain positive integers")
        if len(value) != len(set(value)):
            raise ValueError("failure_calls must not contain duplicates")
        return value


class RunCreateRequest(ProtocolModel):
    task: str = Field(min_length=1, max_length=100_000)
    workspace: str = Field(min_length=1, max_length=4096)
    provider: Literal["ollama", "azure", "deterministic"] = "ollama"
    fallback_provider: Literal["ollama", "azure", "deterministic"] | None = None
    workflow: WorkflowMode = WorkflowMode.MULTI
    durable: bool = False
    parallel: bool = False
    max_workers: int = Field(default=1, ge=1, le=32)
    role_models: RoleModelOverrides = Field(default_factory=RoleModelOverrides)
    deterministic: DeterministicProviderOptions = Field(
        default_factory=DeterministicProviderOptions
    )
    tool_budget: ToolResourceBudget = Field(default_factory=ToolResourceBudget)
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


class CancellationLifecycle(ProtocolModel):
    requested: bool = False
    requested_at: datetime | None = None
    reason: str | None = None
    propagated_at: datetime | None = None
    propagation_sources: list[str] = Field(default_factory=list)
    provider_cancellation_signalled: bool = False
    provider_cancellation_requested_at: datetime | None = None
    provider_names: list[str] = Field(default_factory=list)
    provider_cancellation_acknowledged: bool = False
    provider_cancellation_acknowledged_at: datetime | None = None
    provider_acknowledgement_source: str | None = None
    acknowledged: bool = False
    acknowledged_at: datetime | None = None
    acknowledgement_source: str | None = None
    acknowledgement_stage: str | None = None
    active_non_interruptible_operation: str | None = None
    active_non_interruptible_operations: list[str] = Field(default_factory=list)
    operations_completed_after_request: list[str] = Field(default_factory=list)
    completed_after_cancellation_request: bool = False
    tasks_prevented_from_starting: list[str] = Field(default_factory=list)
    tasks_completed_after_request: list[str] = Field(default_factory=list)
    scheduling_stopped: bool = False
    scheduling_stopped_at: datetime | None = None
    cleanup_completed: bool = False
    cleanup_completed_at: datetime | None = None
    resume_eligible: bool = True
    terminal_reason: str | None = None
    revision: int = Field(default=0, ge=0)


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
    cancellation: CancellationLifecycle = Field(
        default_factory=CancellationLifecycle
    )


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
    cancellation: CancellationLifecycle = Field(
        default_factory=CancellationLifecycle
    )


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
    cancellation: CancellationLifecycle = Field(
        default_factory=CancellationLifecycle
    )


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
    approval_kind: Literal["task", "tool"] = "task"
    tool_name: str | None = None
    tool_version: ToolVersion | None = None
    capabilities: list[ToolCapability] = Field(default_factory=list)
    arguments_summary: list[str] = Field(default_factory=list)
    executable: str | None = None
    working_directory: str | None = None
    network_destination: str | None = None
    policy_rule: str | None = None
    proposed_constraints: list[ToolCapability] = Field(default_factory=list)
    resource_budget: ToolResourceBudget | None = None
    expires_at: datetime | None = None


class ApprovalListResponse(ProtocolModel):
    run_id: str
    approvals: list[ApprovalSummary]


class ApprovalDecisionRequest(ProtocolModel):
    revision: int = Field(ge=1)
    reason: str | None = Field(default=None, max_length=2000)


class ApprovalDecisionResponse(ProtocolModel):
    approval: ApprovalSummary
    idempotent: bool = False


class ToolDescriptorSummary(ProtocolModel):
    name: str
    version: ToolVersion
    protocol_version: str
    description: str
    capabilities: list[ToolCapability]
    safety: str
    idempotent: bool
    supports_cancellation: bool
    maximum_timeout_seconds: float = Field(gt=0)


class ToolDescriptorDetail(ToolDescriptorSummary):
    argument_schema: dict[str, Any]
    output_schema: dict[str, Any]


class ToolListResponse(ProtocolModel):
    tools: list[ToolDescriptorSummary]
    total: int = Field(ge=0)


class ToolInvocationSummary(ProtocolModel):
    invocation_sequence: int = Field(ge=1)
    invocation_id: str
    invocation_revision: int = Field(ge=1)
    run_id: str
    task_id: str
    tool_name: str
    tool_version: ToolVersion
    protocol_version: str
    caller_role: str
    status: str
    capabilities: list[ToolCapability]
    policy_decision: ToolPolicyDecision | None = None
    approval_id: str | None = None
    resource_budget: ToolResourceBudget
    resource_usage: ToolResourceUsage
    cancellation: ToolCancellationSnapshot
    error_category: str | None = None
    error_message: str | None = None
    requested_at: datetime
    started_at: datetime | None = None
    completed_at: datetime | None = None
    updated_at: datetime


class ToolInvocationDetail(ToolInvocationSummary):
    workspace: str
    worktree: str
    arguments_sha256: str
    capability_fingerprint: str
    idempotency_key_sha256: str | None = None
    process_slot: bool = False
    result: ToolResult | None = None


class ToolInvocationListResponse(ProtocolModel):
    run_id: str
    invocations: list[ToolInvocationSummary]
    after_sequence: int = Field(default=0, ge=0)
    next_sequence: int = Field(default=0, ge=0)
    truncated: bool = False


class ToolInvocationCancelRequest(ProtocolModel):
    reason: str | None = Field(default=None, max_length=2000)


class ToolInvocationCancelResponse(ProtocolModel):
    run_id: str
    invocation_id: str
    invocation_status: str
    run_cancellation_requested: bool
    cancellation: CancellationLifecycle = Field(
        default_factory=CancellationLifecycle
    )


class ToolAuditEntryResponse(ProtocolModel):
    audit_sequence: int = Field(ge=1)
    record: ToolAuditRecord


class ToolAuditListResponse(ProtocolModel):
    run_id: str
    records: list[ToolAuditEntryResponse]
    after_sequence: int = Field(default=0, ge=0)
    next_sequence: int = Field(default=0, ge=0)
    truncated: bool = False


class ToolPolicyResponse(ProtocolModel):
    policy_id: Literal["agentbus-default-v1"] = "agentbus-default-v1"
    outcomes: list[str]
    configuration: dict[str, Any]
    rules: list[dict[str, str]]


class ToolPolicyEvaluationRequest(ProtocolModel):
    run_id: str = Field(min_length=1, max_length=128)
    task_id: str = Field(min_length=1, max_length=128)
    tool_name: str = Field(min_length=1, max_length=128)
    arguments: dict[str, Any] = Field(default_factory=dict)
    expected_capabilities: list[ToolCapabilityName] = Field(
        default_factory=list,
        max_length=64,
    )
    caller_role: Literal["planner", "coder", "verifier", "reviewer"] = "coder"
    workspace_trusted: bool = False
    provider_consented: bool = False
    timeout_seconds: float | None = Field(default=None, gt=0, le=86_400)
    resource_budget: ToolResourceBudget = Field(default_factory=ToolResourceBudget)
    invocation_revision: int = Field(default=1, ge=1)

    @field_validator("arguments")
    @classmethod
    def arguments_are_bounded_json(cls, value: dict[str, Any]) -> dict[str, Any]:
        try:
            encoded = json.dumps(
                value,
                ensure_ascii=True,
                allow_nan=False,
                separators=(",", ":"),
                sort_keys=True,
            ).encode("utf-8")
        except (TypeError, ValueError) as exc:
            raise ValueError("tool policy arguments must be finite JSON") from exc
        if len(encoded) > 65_536:
            raise ValueError("tool policy arguments must be at most 65536 bytes")
        return value


class ToolPolicyEvaluationResponse(ProtocolModel):
    diagnostic_only: Literal[True] = True
    persisted: Literal[False] = False
    decision: ToolPolicyDecision
    required_capabilities: list[ToolCapability]


class McpConfiguredToolSummary(ProtocolModel):
    name: str = Field(min_length=1, max_length=128)
    namespaced_name: str = Field(min_length=1, max_length=128)
    capabilities: list[ToolCapability] = Field(max_length=64)


class McpServerSummary(ProtocolModel):
    server_id: str = Field(min_length=1, max_length=64)
    transport: Literal["stdio", "loopback_http"]
    executable_alias: str | None = Field(default=None, max_length=64)
    endpoint_host: str | None = Field(default=None, max_length=255)
    configured_tools: list[McpConfiguredToolSummary] = Field(max_length=256)
    supported_protocol_versions: list[str] = Field(max_length=8)
    startup_timeout_seconds: float = Field(gt=0, le=60)
    request_timeout_seconds: float = Field(gt=0, le=600)


class McpServerListResponse(ProtocolModel):
    servers: list[McpServerSummary] = Field(max_length=64)
    total: int = Field(ge=0, le=64)


class McpServerCheckResponse(ProtocolModel):
    server: McpServerSummary
    ready: bool
    checked_at: datetime
    diagnostic_timeout_seconds: float = Field(gt=0, le=10)
    protocol_version: str | None = Field(default=None, max_length=32)
    server_name: str | None = Field(default=None, max_length=512)
    server_version: str | None = Field(default=None, max_length=512)
    capabilities: list[str] = Field(default_factory=list, max_length=64)
    advertised_tools: list[str] = Field(default_factory=list, max_length=256)
    tool_count: int = Field(default=0, ge=0, le=256)
    cleanup_completed: bool
    message: str | None = Field(default=None, max_length=512)


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
    provider: Literal["ollama", "azure", "deterministic"]
    live_consent: bool = False


class DoctorResponse(ProtocolModel):
    status: str
    checks: list[dict[str, Any]]


class CancelResponse(ProtocolModel):
    run_id: str
    status: str
    cancellation_requested: bool
    cancellation: CancellationLifecycle = Field(
        default_factory=CancellationLifecycle
    )


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
