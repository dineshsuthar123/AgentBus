from __future__ import annotations

import json
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class DomainModel(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)


class RunStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    WAITING_FOR_APPROVAL = "waiting_for_approval"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class TaskStatus(str, Enum):
    PENDING = "pending"
    READY = "ready"
    RUNNING = "running"
    WAITING_FOR_APPROVAL = "waiting_for_approval"
    RETRYABLE = "retryable"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    REJECTED = "rejected"
    BLOCKED = "blocked"
    CANCELLED = "cancelled"


class AttemptStatus(str, Enum):
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    INTERRUPTED = "interrupted"


class RiskLevel(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class FailureCategory(str, Enum):
    MODEL_OUTPUT_ERROR = "model_output_error"
    MODEL_TRANSPORT_ERROR = "model_transport_error"
    TOOL_VALIDATION_ERROR = "tool_validation_error"
    COMMAND_FAILURE = "command_failure"
    VERIFIER_FAILURE = "verifier_failure"
    REVIEWER_REJECTION = "reviewer_rejection"
    POLICY_VIOLATION = "policy_violation"
    INTERRUPTED = "interrupted"
    UNKNOWN = "unknown"


class ApprovalOutcome(str, Enum):
    APPROVED = "approved"
    REJECTED = "rejected"


class TaskDependency(DomainModel):
    task_id: str = Field(min_length=1)
    required: bool = True


class TaskSpec(DomainModel):
    task_id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    description: str = Field(min_length=1)
    dependencies: list[TaskDependency] = Field(default_factory=list)
    assigned_role: str = "coder"
    risk: RiskLevel = RiskLevel.LOW
    maximum_attempts: int = Field(default=2, ge=1)
    expected_outputs: list[str] = Field(default_factory=list)
    done_criteria: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("metadata")
    @classmethod
    def metadata_must_be_json_compatible(cls, value: dict[str, Any]) -> dict[str, Any]:
        try:
            json.dumps(value, allow_nan=False)
        except (TypeError, ValueError) as exc:
            raise ValueError("task metadata must be JSON serializable") from exc
        return value

    @property
    def dependency_ids(self) -> list[str]:
        return [dependency.task_id for dependency in self.dependencies if dependency.required]


class TaskRecord(DomainModel):
    run_id: str
    spec: TaskSpec
    status: TaskStatus = TaskStatus.PENDING
    position: int = Field(default=0, ge=0)
    current_attempt_count: int = Field(default=0, ge=0)
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)

    @property
    def task_id(self) -> str:
        return self.spec.task_id


class TaskAttempt(DomainModel):
    attempt_id: str
    run_id: str
    task_id: str
    attempt_number: int = Field(ge=1)
    status: AttemptStatus = AttemptStatus.RUNNING
    started_at: datetime = Field(default_factory=utc_now)
    completed_at: datetime | None = None
    error_category: FailureCategory | None = None
    error_message: str | None = None
    observation_summary: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("metadata")
    @classmethod
    def attempt_metadata_must_be_json_compatible(
        cls, value: dict[str, Any]
    ) -> dict[str, Any]:
        try:
            json.dumps(value, allow_nan=False)
        except (TypeError, ValueError) as exc:
            raise ValueError("attempt metadata must be JSON serializable") from exc
        return value


class ExecutionArtifact(DomainModel):
    artifact_id: str
    run_id: str
    task_id: str | None = None
    artifact_type: str
    identifier: str
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=utc_now)


class ApprovalDecision(DomainModel):
    approval_id: int | None = None
    run_id: str
    task_id: str
    decision: ApprovalOutcome
    reason: str | None = None
    created_at: datetime = Field(default_factory=utc_now)


class RetryPolicy(DomainModel):
    maximum_attempts: int = Field(default=2, ge=1)
    retryable_categories: set[FailureCategory] = Field(
        default_factory=lambda: {
            FailureCategory.MODEL_OUTPUT_ERROR,
            FailureCategory.MODEL_TRANSPORT_ERROR,
            FailureCategory.COMMAND_FAILURE,
            FailureCategory.VERIFIER_FAILURE,
            FailureCategory.REVIEWER_REJECTION,
            FailureCategory.INTERRUPTED,
        }
    )
    initial_delay_seconds: float = Field(default=0.0, ge=0)
    delay_multiplier: float = Field(default=2.0, ge=1)
    maximum_delay_seconds: float = Field(default=60.0, ge=0)


class RunRecord(DomainModel):
    run_id: str
    original_task: str
    workflow_type: str = "multi"
    status: RunStatus = RunStatus.PENDING
    model: str
    workspace: str
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)
    completed_at: datetime | None = None
    planner_output: dict[str, Any] = Field(default_factory=dict)
    context_summary: str | None = None
    failure_reason: str | None = None
    version: int = Field(default=1, ge=1)
    graph_data: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)
    verifier_status: str | None = None
    reviewer_status: str | None = None
    changed_files: list[str] = Field(default_factory=list)
    commit_identifier: str | None = None
    pr_url: str | None = None
    finalization_error: str | None = None


class RunSnapshot(DomainModel):
    run: RunRecord
    tasks: list[TaskRecord] = Field(default_factory=list)
    attempts: list[TaskAttempt] = Field(default_factory=list)
    artifacts: list[ExecutionArtifact] = Field(default_factory=list)
    approvals: list[ApprovalDecision] = Field(default_factory=list)

    def attempts_for(self, task_id: str) -> list[TaskAttempt]:
        return [attempt for attempt in self.attempts if attempt.task_id == task_id]


class TaskExecutionContext(DomainModel):
    run: RunRecord
    task: TaskSpec
    attempt_number: int
    previous_attempts: list[TaskAttempt] = Field(default_factory=list)


class TaskExecutionResult(DomainModel):
    succeeded: bool
    summary: str
    artifacts: list[ExecutionArtifact] = Field(default_factory=list)
    failure_category: FailureCategory | None = None
    error_message: str | None = None
    retryable: bool | None = None
    verifier_status: str | None = None
    reviewer_status: str | None = None
    changed_files: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class GraphProgress(DomainModel):
    total: int
    succeeded: int
    failed: int
    blocked: int
    waiting_for_approval: int
    remaining: int


class ExecutionReport(DomainModel):
    run_id: str
    original_task: str
    status: RunStatus
    graph_progress: GraphProgress
    successful_tasks: list[str] = Field(default_factory=list)
    failed_tasks: list[str] = Field(default_factory=list)
    blocked_tasks: list[str] = Field(default_factory=list)
    pending_approvals: list[str] = Field(default_factory=list)
    attempts_per_task: dict[str, int] = Field(default_factory=dict)
    verifier_status: str | None = None
    reviewer_status: str | None = None
    changed_files: list[str] = Field(default_factory=list)
    commit_identifier: str | None = None
    pr_url: str | None = None
    finalization_error: str | None = None
    failure_reason: str | None = None
    resume_command: str | None = None
