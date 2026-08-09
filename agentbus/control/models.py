from __future__ import annotations

import json
from datetime import datetime
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from agentbus.control.version import API_PREFIX, CONTROL_PROTOCOL_VERSION
from agentbus.intelligence.models import (
    ImpactResult,
    IndexStatus,
    SourceLanguage,
    SymbolKind,
    TestImpactResult,
)
from agentbus.intelligence.service import (
    ContextPlanSummary,
    GraphEdgeSummary,
    GraphNodeSummary,
    IndexMutationReport,
    IndexVerificationReport,
    RepositoryOverview,
    RepositorySearchReport,
    SymbolSummary,
)
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


class WorkspaceIndexCreateRequest(ProtocolModel):
    workspace: str = Field(min_length=1, max_length=4_096)
    workspace_trusted: bool = False


class WorkspaceIndexAttachRequest(ProtocolModel):
    workspace: str = Field(min_length=1, max_length=4_096)


class WorkspaceIndexActionRequest(ProtocolModel):
    workspace_trusted: bool = False


class WorkspaceIndexMutationResponse(ProtocolModel):
    workspace_id: str = Field(min_length=1, max_length=256)
    repository_id: str = Field(min_length=1, max_length=256)
    result: IndexMutationReport


class WorkspaceIndexStatusResponse(ProtocolModel):
    workspace_id: str = Field(min_length=1, max_length=256)
    repository_id: str = Field(min_length=1, max_length=256)
    status: IndexStatus
    overview: RepositoryOverview | None = None
    provider_calls: Literal[0] = 0
    network_calls: Literal[0] = 0


class WorkspaceIndexVerificationResponse(ProtocolModel):
    workspace_id: str = Field(min_length=1, max_length=256)
    repository_id: str = Field(min_length=1, max_length=256)
    result: IndexVerificationReport


class WorkspaceIndexCancellationResponse(ProtocolModel):
    workspace_id: str = Field(min_length=1, max_length=256)
    repository_id: str = Field(min_length=1, max_length=256)
    cancellation_requested: bool
    operation_id: str | None = Field(default=None, max_length=128)
    operation_state: str | None = Field(default=None, max_length=64)
    provider_calls: Literal[0] = 0
    network_calls: Literal[0] = 0


class WorkspaceSearchRequest(ProtocolModel):
    query: str = Field(min_length=1, max_length=2_048)
    projects: list[str] = Field(default_factory=list, max_length=128)
    languages: list[SourceLanguage] = Field(default_factory=list, max_length=32)
    symbol_kinds: list[SymbolKind] = Field(default_factory=list, max_length=64)
    path_prefixes: list[str] = Field(default_factory=list, max_length=128)
    test_only: bool = False
    offset: int = Field(default=0, ge=0, le=100_000)
    limit: int = Field(default=25, ge=1, le=200)
    include_evidence: bool = False

    @field_validator("projects", "path_prefixes")
    @classmethod
    def search_filters_are_bounded(cls, values: list[str]) -> list[str]:
        if any(not item.strip() or len(item) > 2_048 for item in values):
            raise ValueError("repository search filters must be bounded text")
        return values


class WorkspaceSearchResponse(ProtocolModel):
    workspace_id: str = Field(min_length=1, max_length=256)
    report: RepositorySearchReport


class WorkspaceSymbolResponse(ProtocolModel):
    workspace_id: str = Field(min_length=1, max_length=256)
    snapshot_id: str = Field(min_length=1, max_length=256)
    index_state: str = Field(min_length=1, max_length=64)
    symbol: SymbolSummary
    provider_calls: Literal[0] = 0
    network_calls: Literal[0] = 0


class WorkspaceGraphResponse(ProtocolModel):
    workspace_id: str = Field(min_length=1, max_length=256)
    snapshot_id: str = Field(min_length=1, max_length=256)
    index_state: str = Field(min_length=1, max_length=64)
    direction: Literal["dependencies", "dependents"]
    subject: SymbolSummary
    nodes: list[GraphNodeSummary] = Field(default_factory=list, max_length=1_001)
    edges: list[GraphEdgeSummary] = Field(default_factory=list, max_length=500)
    offset: int = Field(ge=0)
    limit: int = Field(ge=1, le=500)
    total_edges: int = Field(ge=0)
    next_offset: int | None = Field(default=None, ge=0)
    maximum_depth_reached: int = Field(ge=0, le=16)
    truncated: bool = False
    provider_calls: Literal[0] = 0
    network_calls: Literal[0] = 0


class WorkspaceImpactRequest(ProtocolModel):
    subjects: list[str] = Field(min_length=1, max_length=256)
    projects: list[str] = Field(default_factory=list, max_length=128)
    languages: list[SourceLanguage] = Field(default_factory=list, max_length=32)
    max_depth: int = Field(default=4, ge=0, le=8)
    max_nodes: int = Field(default=500, ge=1, le=2_000)
    include_evidence: bool = False

    @field_validator("subjects", "projects")
    @classmethod
    def impact_values_are_bounded(cls, values: list[str]) -> list[str]:
        if any(not item.strip() or len(item) > 2_048 for item in values):
            raise ValueError("repository impact values must be bounded text")
        return values


class WorkspaceImpactResponse(ProtocolModel):
    workspace_id: str = Field(min_length=1, max_length=256)
    result: ImpactResult
    provider_calls: Literal[0] = 0
    network_calls: Literal[0] = 0


class WorkspaceTestsResponse(ProtocolModel):
    workspace_id: str = Field(min_length=1, max_length=256)
    result: TestImpactResult
    provider_calls: Literal[0] = 0
    network_calls: Literal[0] = 0


class WorkspaceContextPlanRequest(ProtocolModel):
    task: str = Field(min_length=1, max_length=20_000)
    role: Literal["planner", "coder", "verifier", "reviewer"] = "planner"
    projects: list[str] = Field(default_factory=list, max_length=128)
    changed_paths: list[str] = Field(default_factory=list, max_length=1_000)
    byte_budget: int = Field(default=100_000, ge=1, le=1_000_000)
    token_budget: int = Field(default=16_000, ge=1, le=200_000)
    include_evidence: bool = False

    @field_validator("projects", "changed_paths")
    @classmethod
    def context_filters_are_bounded(cls, values: list[str]) -> list[str]:
        if any(not item.strip() or len(item) > 2_048 for item in values):
            raise ValueError("repository context filters must be bounded text")
        return values


class WorkspaceContextPlanResponse(ProtocolModel):
    workspace_id: str = Field(min_length=1, max_length=256)
    result: ContextPlanSummary


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
        "tool-control-acceptance",
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


class TraceValueReferenceSummary(ProtocolModel):
    reference_id: str = Field(min_length=1, max_length=128)
    name: str = Field(min_length=1, max_length=256)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    media_type: str = Field(min_length=1, max_length=200)
    byte_length: int = Field(ge=0)
    redacted: bool = True
    required_for_replay: bool | None = None
    replayable: bool | None = None


class TraceArtifactSummary(ProtocolModel):
    artifact_id: str = Field(min_length=1, max_length=128)
    artifact_type: str = Field(min_length=1, max_length=256)
    identifier: str = Field(min_length=1, max_length=2048)
    sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    byte_length: int | None = Field(default=None, ge=0)
    media_type: str | None = Field(default=None, max_length=200)


class TraceFailureSummary(ProtocolModel):
    category: str = Field(min_length=1, max_length=256)
    message: str = Field(min_length=1, max_length=4000)
    retryable: bool = False


class TraceLinkSummary(ProtocolModel):
    link_type: str = Field(min_length=1, max_length=64)
    trace_id: str = Field(min_length=1, max_length=128)
    span_id: str | None = Field(default=None, max_length=128)


class TraceSpanSummary(ProtocolModel):
    trace_id: str = Field(min_length=1, max_length=128)
    span_id: str = Field(min_length=1, max_length=128)
    parent_span_id: str | None = Field(default=None, max_length=128)
    run_id: str = Field(min_length=1, max_length=128)
    task_id: str | None = Field(default=None, max_length=128)
    worker_id: str | None = Field(default=None, max_length=128)
    invocation_id: str | None = Field(default=None, max_length=128)
    span_type: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=256)
    sequence: int = Field(ge=1)
    started_at: datetime
    ended_at: datetime | None = None
    status: str = Field(min_length=1, max_length=64)
    input_count: int = Field(default=0, ge=0)
    output_count: int = Field(default=0, ge=0)
    artifact_count: int = Field(default=0, ge=0)
    failure: TraceFailureSummary | None = None


class TraceSpanDetailResponse(TraceSpanSummary):
    inputs: list[TraceValueReferenceSummary] = Field(
        default_factory=list,
        max_length=256,
    )
    outputs: list[TraceValueReferenceSummary] = Field(
        default_factory=list,
        max_length=256,
    )
    policy_decision_references: list[str] = Field(
        default_factory=list,
        max_length=256,
    )
    approval_references: list[str] = Field(
        default_factory=list,
        max_length=256,
    )
    artifacts: list[TraceArtifactSummary] = Field(
        default_factory=list,
        max_length=256,
    )
    links: list[TraceLinkSummary] = Field(default_factory=list, max_length=256)
    cancellation_state: dict[str, Any] = Field(default_factory=dict)
    resource_usage: dict[str, Any] = Field(default_factory=dict)
    attributes: dict[str, Any] = Field(default_factory=dict)

    @field_validator("cancellation_state", "resource_usage", "attributes")
    @classmethod
    def detail_json_is_bounded(cls, value: dict[str, Any]) -> dict[str, Any]:
        return _bounded_json(value, max_bytes=65_536, label="trace span detail")


class TraceSpanListResponse(ProtocolModel):
    trace_id: str = Field(min_length=1, max_length=128)
    run_id: str = Field(min_length=1, max_length=128)
    spans: list[TraceSpanSummary] = Field(max_length=500)
    after_sequence: int = Field(default=0, ge=0)
    next_sequence: int = Field(default=0, ge=0)
    truncated: bool = False


class TraceCheckpointSummary(ProtocolModel):
    checkpoint_id: str = Field(min_length=1, max_length=128)
    span_id: str = Field(min_length=1, max_length=128)
    sequence: int = Field(ge=1)
    label: str = Field(min_length=1, max_length=256)
    replayable: bool
    created_at: datetime


class TraceResponse(ProtocolModel):
    trace_id: str = Field(min_length=1, max_length=128)
    run_id: str = Field(min_length=1, max_length=128)
    root_span_id: str = Field(min_length=1, max_length=128)
    schema_version: int = Field(ge=1)
    status: str = Field(min_length=1, max_length=64)
    created_at: datetime
    completed_at: datetime | None = None
    span_count: int = Field(ge=1)
    event_count: int = Field(ge=0)
    checkpoint_count: int = Field(ge=0)
    link_count: int = Field(default=0, ge=0)
    checkpoints: list[TraceCheckpointSummary] = Field(
        default_factory=list,
        max_length=500,
    )
    checkpoints_truncated: bool = False
    replay_id: str | None = Field(default=None, max_length=128)
    source_trace_id: str | None = Field(default=None, max_length=128)
    replay_mode: str | None = Field(default=None, max_length=64)
    providerless: bool | None = None


class ProvenanceProviderRouteSummary(ProtocolModel):
    role: str = Field(min_length=1, max_length=128)
    provider: str = Field(min_length=1, max_length=128)
    model_identifier: str = Field(min_length=1, max_length=256)
    deployment_identifier: str | None = Field(default=None, max_length=256)


class ProvenanceToolSummary(ProtocolModel):
    name: str = Field(min_length=1, max_length=256)
    version: str = Field(min_length=1, max_length=128)
    protocol_version: str = Field(min_length=1, max_length=128)
    descriptor_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class ProvenanceResponse(ProtocolModel):
    trace_id: str = Field(min_length=1, max_length=128)
    run_id: str = Field(min_length=1, max_length=128)
    generated_at: datetime
    schema_version: int = Field(ge=1)
    trace_schema_version: int = Field(ge=1)
    agentbus_version: str = Field(min_length=1, max_length=128)
    operating_system: str = Field(min_length=1, max_length=256)
    python_version: str = Field(min_length=1, max_length=128)
    node_version: str | None = Field(default=None, max_length=128)
    vscode_version: str | None = Field(default=None, max_length=128)
    configuration_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    provider_routes: list[ProvenanceProviderRouteSummary] = Field(max_length=256)
    tool_descriptors: list[ProvenanceToolSummary] = Field(max_length=500)
    tool_descriptors_truncated: bool = False
    policy_version: str = Field(min_length=1, max_length=128)
    policy_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    protocol_hashes: dict[str, str] = Field(default_factory=dict)
    input_object_count: int = Field(ge=0)
    output_object_count: int = Field(ge=0)
    generated_artifact_count: int = Field(ge=0)
    integrity_object_count: int = Field(ge=0)
    task_graph_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    event_count: int = Field(ge=0)
    final_repository_tree_sha256: str | None = Field(
        default=None,
        pattern=r"^[0-9a-f]{64}$",
    )
    replayability: str = Field(min_length=1, max_length=64)
    replayability_reasons: list[str] = Field(
        default_factory=list,
        max_length=1024,
    )
    integrity_algorithm: str = Field(min_length=1, max_length=128)
    integrity_root: str = Field(pattern=r"^[0-9a-f]{64}$")


class SpanReplayabilityResponse(ProtocolModel):
    span_id: str = Field(min_length=1, max_length=128)
    span_type: str = Field(min_length=1, max_length=64)
    level: str = Field(min_length=1, max_length=64)
    reasons: list[str] = Field(min_length=1, max_length=128)
    required_input_count: int = Field(ge=0)
    missing_input_hashes: list[str] = Field(
        default_factory=list,
        max_length=256,
    )
    substitution_kinds: list[str] = Field(default_factory=list, max_length=64)
    requires_isolated_workspace: bool = False
    live_provider_consent_required: bool = False


class RunReplayabilityResponse(ProtocolModel):
    trace_id: str = Field(min_length=1, max_length=128)
    run_id: str = Field(min_length=1, max_length=128)
    level: str = Field(min_length=1, max_length=64)
    replayable_offline: bool
    reasons: list[str] = Field(default_factory=list, max_length=1024)
    missing_input_hashes: list[str] = Field(
        default_factory=list,
        max_length=1024,
    )
    missing_inputs_truncated: bool = False
    live_provider_consent_required: bool = False
    spans: list[SpanReplayabilityResponse] = Field(max_length=500)
    after_sequence: int = Field(default=0, ge=0)
    next_sequence: int = Field(default=0, ge=0)
    truncated: bool = False


class ReplayCreateRequest(ProtocolModel):
    mode: Literal["strict", "offline", "verify", "simulate"]
    from_span_id: str | None = Field(default=None, max_length=128)
    from_checkpoint_id: str | None = Field(default=None, max_length=128)
    fork: bool = False
    changed_inputs: dict[str, Any] = Field(default_factory=dict)
    tool_strategies: dict[
        str,
        Literal[
            "reuse_captured",
            "rerun_sandbox",
            "simulate_mutation",
            "reject",
        ],
    ] = Field(default_factory=dict)
    live_provider_consent: bool = False

    @field_validator("changed_inputs", "tool_strategies")
    @classmethod
    def replay_json_is_bounded(cls, value: dict[str, Any]) -> dict[str, Any]:
        return _bounded_json(value, max_bytes=65_536, label="replay request")

    @model_validator(mode="after")
    def replay_selection_is_consistent(self) -> "ReplayCreateRequest":
        if self.from_span_id is not None and self.from_checkpoint_id is not None:
            raise ValueError("choose either from_span_id or from_checkpoint_id")
        if self.changed_inputs and not self.fork:
            raise ValueError("changed_inputs require fork=true")
        if self.fork and not self.changed_inputs:
            raise ValueError("fork=true requires at least one changed input")
        if self.fork and (
            self.from_span_id is not None
            or self.from_checkpoint_id is not None
            or self.tool_strategies
        ):
            raise ValueError(
                "fork replay cannot select a checkpoint, span, or tool strategy"
            )
        allowed_changes = {
            "approval_decisions",
            "deterministic_provider_profile",
            "model_route",
            "policy_configuration",
            "resource_budgets",
            "retry_limit",
            "selected_source_patch",
            "task_text",
            "tool_response",
        }
        unknown = sorted(set(self.changed_inputs) - allowed_changes)
        if unknown:
            raise ValueError(
                "unsupported fork input changes: " + ", ".join(unknown)
            )
        route = self.changed_inputs.get("model_route")
        live_provider = (
            str(route.get("provider", "")).lower()
            if isinstance(route, dict)
            else ""
        )
        if (
            live_provider in {"azure", "ollama"}
            and not self.live_provider_consent
        ):
            raise ValueError(
                "a live provider route requires explicit live_provider_consent"
            )
        return self


class ReplaySpanResultResponse(ProtocolModel):
    span_id: str = Field(min_length=1, max_length=128)
    action: str = Field(min_length=1, max_length=64)
    succeeded: bool
    summary: str = Field(min_length=1, max_length=4000)
    output_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    drift: list[str] = Field(default_factory=list, max_length=256)


class ReplaySessionResponse(ProtocolModel):
    replay_id: str = Field(min_length=1, max_length=128)
    source_trace_id: str = Field(min_length=1, max_length=128)
    source_run_id: str = Field(min_length=1, max_length=128)
    mode: str = Field(min_length=1, max_length=64)
    status: str = Field(min_length=1, max_length=64)
    created_at: datetime
    started_at: datetime | None = None
    completed_at: datetime | None = None
    from_span_id: str | None = Field(default=None, max_length=128)
    from_checkpoint_id: str | None = Field(default=None, max_length=128)
    fork: bool = False
    changed_input_names: list[str] = Field(default_factory=list, max_length=1024)
    result_trace_id: str | None = Field(default=None, max_length=128)
    comparison_id: str | None = Field(default=None, max_length=128)
    isolated: bool = False
    isolation_scope: Literal["daemon_managed_temporary_workspace"] | None = None
    span_results: list[ReplaySpanResultResponse] = Field(
        default_factory=list,
        max_length=500,
    )
    span_results_truncated: bool = False
    diagnostics_truncated: bool = False
    substitutions: list[str] = Field(default_factory=list, max_length=4096)
    missing_inputs: list[str] = Field(default_factory=list, max_length=4096)
    policy_drift: list[str] = Field(default_factory=list, max_length=1024)
    intelligence_drift: list[str] = Field(default_factory=list, max_length=32)
    failure_category: str | None = Field(default=None, max_length=256)
    failure_message: str | None = Field(default=None, max_length=4000)
    provider_calls: int = Field(default=0, ge=0)
    network_calls: int = Field(default=0, ge=0)


class ReplayAcceptedResponse(ReplaySessionResponse):
    pass


class ReplayListResponse(ProtocolModel):
    replays: list[ReplaySessionResponse] = Field(max_length=500)
    total: int = Field(ge=0)
    truncated: bool = False


class ReplayCancelResponse(ProtocolModel):
    replay_id: str = Field(min_length=1, max_length=128)
    status: str = Field(min_length=1, max_length=64)
    cancellation_requested: bool


class ComparisonCreateRequest(ProtocolModel):
    left: str = Field(min_length=1, max_length=128)
    right: str = Field(min_length=1, max_length=128)


class ComparisonDifferenceResponse(ProtocolModel):
    field: str = Field(min_length=1, max_length=128)
    left_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    right_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    category: str = Field(min_length=1, max_length=64)
    summary: str = Field(min_length=1, max_length=1000)


class SpanComparisonResponse(ProtocolModel):
    semantic_key: str = Field(min_length=1, max_length=512)
    left_span_id: str | None = Field(default=None, max_length=128)
    right_span_id: str | None = Field(default=None, max_length=128)
    unchanged: bool
    categories: list[str] = Field(default_factory=list, max_length=32)
    differences: list[ComparisonDifferenceResponse] = Field(
        default_factory=list,
        max_length=256,
    )


class ComparisonSummaryResponse(ProtocolModel):
    unchanged_spans: int = Field(ge=0)
    changed_spans: int = Field(ge=0)
    added_spans: int = Field(ge=0)
    removed_spans: int = Field(ge=0)
    category_counts: dict[str, int] = Field(default_factory=dict)
    final_status_changed: bool = False
    provenance_root_changed: bool = False


class ComparisonResponse(ProtocolModel):
    comparison_id: str = Field(min_length=1, max_length=128)
    left_trace_id: str = Field(min_length=1, max_length=128)
    right_trace_id: str = Field(min_length=1, max_length=128)
    created_at: datetime
    summary: ComparisonSummaryResponse
    categories: list[str] = Field(default_factory=list, max_length=32)
    left_status: str = Field(min_length=1, max_length=64)
    right_status: str = Field(min_length=1, max_length=64)
    left_provenance_root: str | None = Field(
        default=None,
        pattern=r"^[0-9a-f]{64}$",
    )
    right_provenance_root: str | None = Field(
        default=None,
        pattern=r"^[0-9a-f]{64}$",
    )
    spans: list[SpanComparisonResponse] = Field(max_length=500)
    after: int = Field(default=0, ge=0)
    next_after: int = Field(default=0, ge=0)
    truncated: bool = False


class TraceArchiveImportRequest(ProtocolModel):
    archive_base64: str = Field(min_length=1, max_length=900_000)
    allow_source_content: bool = False


class TraceArchiveImportResponse(ProtocolModel):
    trace_id: str = Field(min_length=1, max_length=128)
    run_id: str = Field(min_length=1, max_length=128)
    provenance_root: str = Field(pattern=r"^[0-9a-f]{64}$")
    archive_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    objects_imported: bool
    replay_started: Literal[False] = False


class TraceArchiveExportResponse(ProtocolModel):
    trace_id: str = Field(min_length=1, max_length=128)
    run_id: str = Field(min_length=1, max_length=128)
    provenance_root: str = Field(pattern=r"^[0-9a-f]{64}$")
    archive_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    archive_base64: str = Field(min_length=1, max_length=900_000)
    source_content_included: bool = False


class RegressionFixtureCaptureRequest(ProtocolModel):
    include_source_content: bool = False


class RegressionFixtureCaptureResponse(ProtocolModel):
    trace_id: str = Field(min_length=1, max_length=128)
    run_id: str = Field(min_length=1, max_length=128)
    provenance_root: str = Field(pattern=r"^[0-9a-f]{64}$")
    archive_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    archive_base64: str = Field(min_length=1, max_length=900_000)
    source_content_included: bool = False
    source_warning: str | None = Field(default=None, max_length=512)
    license_warning: str | None = Field(default=None, max_length=512)
    replay_command: str = Field(min_length=1, max_length=1_024)
    assertions_validated: Literal[True] = True
    replay_started: Literal[False] = False


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
    idle_timeout_seconds: float = Field(default=86_400, ge=0)
    log_path: str | None = None


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


def _bounded_json(
    value: dict[str, Any],
    *,
    max_bytes: int,
    label: str,
) -> dict[str, Any]:
    try:
        encoded = json.dumps(
            value,
            ensure_ascii=True,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} must be finite JSON") from exc
    if len(encoded) > max_bytes:
        raise ValueError(f"{label} must be at most {max_bytes} bytes")
    return value
