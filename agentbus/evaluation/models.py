from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from agentbus import __version__

from agentbus.execution.models import RiskLevel, RunStatus
from agentbus.security.redaction import sanitize_json


EVALUATION_SCHEMA_VERSION = 1
_CASE_ID = re.compile(r"^[a-z0-9][a-z0-9._-]{0,79}$")


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class EvaluationModel(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)


class WorkflowMode(str, Enum):
    SINGLE = "single"
    MULTI = "multi"


class AssertionDimension(str, Enum):
    FUNCTIONAL_CORRECTNESS = "functional_correctness"
    TESTS = "tests"
    TASK_COMPLETION = "task_completion"
    SCOPE_DISCIPLINE = "scope_discipline"
    SAFETY = "safety"
    RECOVERY_INTEGRATION = "recovery_integration"
    REVIEW = "review"
    EFFICIENCY = "efficiency"


class AssertionKind(str, Enum):
    RUN_STATUS = "run_status"
    VERIFIER = "verifier"
    REVIEWER = "reviewer"
    EXPECTED_FILE = "expected_file"
    FORBIDDEN_FILE = "forbidden_file"
    CONTENT_EXACT = "content_exact"
    CONTENT_PATTERN = "content_pattern"
    CHANGED_FILES = "changed_files"
    RELEVANT_CHANGED_FILES = "relevant_changed_files"
    GENERATED_EXCLUDED = "generated_excluded"
    NO_PARENT_LEAKAGE = "no_parent_leakage"
    COMMIT_CREATED = "commit_created"
    PR_ATTEMPTED = "pr_attempted"
    APPROVAL_REQUIRED = "approval_required"
    TASK_EXECUTION_COUNT = "task_execution_count"
    NO_SUCCESSFUL_TASK_RERUN = "no_successful_task_rerun"
    CONFLICT_FILES = "conflict_files"
    SOURCE_UNCHANGED = "source_unchanged"
    NO_SECRET_PATTERNS = "no_secret_patterns"
    MAX_TOKENS = "max_tokens"
    MAX_REQUESTS = "max_requests"
    MAX_ELAPSED_SECONDS = "max_elapsed_seconds"
    MAX_RETRIES = "max_retries"
    TEST_COMMAND = "test_command"
    TEST_EXIT_CODE = "test_exit_code"
    SAFETY_VIOLATIONS = "safety_violations"


class FailureInjectionKind(str, Enum):
    MALFORMED_PLANNER = "malformed_planner"
    CODER_TRANSPORT_FAILURE = "coder_transport_failure"
    VERIFIER_FAILURE = "verifier_failure"
    REVIEWER_REJECTION = "reviewer_rejection"
    WORKER_CRASH = "worker_crash"
    LEASE_EXPIRY = "lease_expiry"
    AFTER_TASK_COMMIT = "after_task_commit"
    DURING_INTEGRATION = "during_integration"
    MERGE_CONFLICT = "merge_conflict"
    APPROVAL_REJECTION = "approval_rejection"
    PROVIDER_FALLBACK = "provider_fallback"


class EvaluationFailureInjection(EvaluationModel):
    kind: FailureInjectionKind
    task_id: str | None = None
    attempt: int | None = Field(default=None, ge=1)
    stage: str | None = None
    options: dict[str, Any] = Field(default_factory=dict)

    @field_validator("options")
    @classmethod
    def sanitize_options(cls, value: dict[str, Any]) -> dict[str, Any]:
        return _safe_json(value)


class ContentExpectation(EvaluationModel):
    path: str
    exact: str | None = None
    pattern: str | None = None

    @field_validator("path")
    @classmethod
    def relative_path_only(cls, value: str) -> str:
        return _relative_path(value)

    @model_validator(mode="after")
    def exactly_one_matcher(self) -> "ContentExpectation":
        if (self.exact is None) == (self.pattern is None):
            raise ValueError("content expectation requires exactly one of exact or pattern")
        if self.pattern is not None:
            re.compile(self.pattern)
        return self


class EvaluationAssertion(EvaluationModel):
    assertion_id: str
    kind: AssertionKind
    dimension: AssertionDimension
    expected: Any = None
    actual: Any = None
    passed: bool | None = None
    hard_failure: bool = False
    message: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("expected", "actual")
    @classmethod
    def safe_values(cls, value: Any) -> Any:
        return _safe_json(value)

    @field_validator("metadata")
    @classmethod
    def safe_metadata(cls, value: dict[str, Any]) -> dict[str, Any]:
        return _safe_json(value)


class EvaluationArtifact(EvaluationModel):
    artifact_type: str
    identifier: str
    retained: bool = False
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("metadata")
    @classmethod
    def safe_metadata(cls, value: dict[str, Any]) -> dict[str, Any]:
        return _safe_json(value)


class EvaluationVariant(EvaluationModel):
    variant_id: str
    title: str
    workflow: WorkflowMode = WorkflowMode.MULTI
    provider: str = "fake"
    models_by_role: dict[str, str] = Field(default_factory=dict)
    durable: bool = True
    parallel: bool = False
    max_workers: int = Field(default=1, ge=1)
    max_retries: int = Field(default=0, ge=0)
    fallback_provider: str | None = None
    fallback_enabled: bool = False
    prompt_version: str = "agentbus-current"
    live: bool = False
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("variant_id")
    @classmethod
    def valid_variant_id(cls, value: str) -> str:
        if not _CASE_ID.fullmatch(value):
            raise ValueError("variant_id must be a stable lowercase identifier")
        return value

    @field_validator("metadata")
    @classmethod
    def safe_metadata(cls, value: dict[str, Any]) -> dict[str, Any]:
        return _safe_json(value)

    @model_validator(mode="after")
    def valid_execution_shape(self) -> "EvaluationVariant":
        if self.parallel and not self.durable:
            raise ValueError("parallel evaluation variants must be durable")
        if self.parallel and self.max_workers < 2:
            raise ValueError("parallel evaluation variants require max_workers >= 2")
        if self.fallback_enabled and not self.fallback_provider:
            raise ValueError("fallback_enabled requires fallback_provider")
        if self.live and self.provider == "fake":
            raise ValueError("fake-provider variants cannot be marked live")
        return self


class EvaluationCase(EvaluationModel):
    case_id: str
    title: str
    task_prompt: str
    fixture_repository_source: str
    expected_files: list[str] = Field(default_factory=list)
    forbidden_files: list[str] = Field(default_factory=list)
    expected_test_command: list[str] = Field(default_factory=list)
    content_expectations: list[ContentExpectation] = Field(default_factory=list)
    expected_behavioral_assertions: list[EvaluationAssertion] = Field(
        default_factory=list
    )
    expected_run_status: RunStatus = RunStatus.SUCCEEDED
    expected_verifier_passed: bool | None = True
    expected_reviewer_approved: bool | None = True
    maximum_attempts: int = Field(default=2, ge=1)
    timeout_seconds: float = Field(default=60.0, gt=0)
    workflow_mode: WorkflowMode = WorkflowMode.MULTI
    durable_mode: bool = True
    parallel_mode: bool = False
    maximum_workers: int = Field(default=1, ge=1)
    provider_route: dict[str, str] = Field(default_factory=dict)
    risk_level: RiskLevel = RiskLevel.LOW
    failure_injections: list[EvaluationFailureInjection] = Field(default_factory=list)
    tags: set[str] = Field(default_factory=set)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("case_id")
    @classmethod
    def valid_case_id(cls, value: str) -> str:
        if not _CASE_ID.fullmatch(value):
            raise ValueError("case_id must be a stable lowercase identifier")
        return value

    @field_validator("fixture_repository_source")
    @classmethod
    def local_fixture_only(cls, value: str) -> str:
        if "://" in value:
            raise ValueError("fixture_repository_source must be local")
        return value

    @field_validator("expected_files", "forbidden_files")
    @classmethod
    def relative_files_only(cls, value: list[str]) -> list[str]:
        return [_relative_path(item) for item in value]

    @field_validator("provider_route", "metadata")
    @classmethod
    def safe_mapping(cls, value: dict[str, Any]) -> dict[str, Any]:
        return _safe_json(value)

    @model_validator(mode="after")
    def valid_case_shape(self) -> "EvaluationCase":
        if self.parallel_mode and not self.durable_mode:
            raise ValueError("parallel evaluation cases must be durable")
        if self.parallel_mode and self.maximum_workers < 2:
            raise ValueError("parallel evaluation cases require maximum_workers >= 2")
        overlap = set(self.expected_files) & set(self.forbidden_files)
        if overlap:
            raise ValueError(f"files cannot be both expected and forbidden: {sorted(overlap)}")
        return self


class EvaluationSuite(EvaluationModel):
    suite_id: str
    title: str
    description: str
    cases: list[EvaluationCase]
    default_variant: str
    tags: set[str] = Field(default_factory=set)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("suite_id")
    @classmethod
    def valid_suite_id(cls, value: str) -> str:
        if not _CASE_ID.fullmatch(value):
            raise ValueError("suite_id must be a stable lowercase identifier")
        return value

    @field_validator("metadata")
    @classmethod
    def safe_metadata(cls, value: dict[str, Any]) -> dict[str, Any]:
        return _safe_json(value)

    @model_validator(mode="after")
    def unique_cases(self) -> "EvaluationSuite":
        identifiers = [case.case_id for case in self.cases]
        if len(identifiers) != len(set(identifiers)):
            raise ValueError("evaluation suite case IDs must be unique")
        if not identifiers:
            raise ValueError("evaluation suite must contain at least one case")
        return self


class ScoringWeights(EvaluationModel):
    functional_correctness: float = Field(default=30, ge=0)
    tests: float = Field(default=20, ge=0)
    task_completion: float = Field(default=0, ge=0)
    scope_discipline: float = Field(default=15, ge=0)
    safety: float = Field(default=15, ge=0)
    recovery_integration: float = Field(default=10, ge=0)
    review: float = Field(default=5, ge=0)
    efficiency: float = Field(default=5, ge=0)

    @model_validator(mode="after")
    def totals_one_hundred(self) -> "ScoringWeights":
        if abs(sum(self.model_dump().values()) - 100.0) > 0.0001:
            raise ValueError("evaluation scoring weights must total 100")
        return self


class EvaluationScore(EvaluationModel):
    total: float = Field(ge=0, le=100)
    dimensions: dict[str, float] = Field(default_factory=dict)
    weights: ScoringWeights = Field(default_factory=ScoringWeights)
    hard_failure: bool = False
    formula: str = "sum(weight * passed_assertions / applicable_assertions)"


class QualityMetrics(EvaluationModel):
    success: bool = False
    assertion_pass_rate: float = Field(default=0, ge=0, le=1)
    verifier_passed: bool | None = None
    reviewer_approved: bool | None = None
    relevant_file_count: int = Field(default=0, ge=0)
    unrelated_file_count: int = Field(default=0, ge=0)
    conflict_count: int = Field(default=0, ge=0)
    safety_violation_count: int = Field(default=0, ge=0)


class ExecutionMetrics(EvaluationModel):
    total_duration_seconds: float = Field(default=0, ge=0)
    planning_duration_seconds: float | None = Field(default=None, ge=0)
    coding_duration_seconds: float | None = Field(default=None, ge=0)
    verification_duration_seconds: float | None = Field(default=None, ge=0)
    review_duration_seconds: float | None = Field(default=None, ge=0)
    integration_duration_seconds: float | None = Field(default=None, ge=0)
    tasks_attempted: int = Field(default=0, ge=0)
    retries: int = Field(default=0, ge=0)
    recoveries: int = Field(default=0, ge=0)
    workers_used: int = Field(default=0, ge=0)
    maximum_observed_concurrency: int = Field(default=0, ge=0)


class ProviderMetrics(EvaluationModel):
    requests: int = Field(default=0, ge=0)
    input_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(default=0, ge=0)
    cached_tokens: int = Field(default=0, ge=0)
    total_tokens: int = Field(default=0, ge=0)
    latency_seconds: float = Field(default=0, ge=0)
    retries: int = Field(default=0, ge=0)
    fallbacks: int = Field(default=0, ge=0)
    by_role: dict[str, dict[str, Any]] = Field(default_factory=dict)

    @field_validator("by_role")
    @classmethod
    def safe_role_metadata(cls, value: dict[str, dict[str, Any]]) -> dict[str, Any]:
        return _safe_json(value)


class GitMetrics(EvaluationModel):
    files_changed: list[str] = Field(default_factory=list)
    lines_added: int = Field(default=0, ge=0)
    lines_removed: int = Field(default=0, ge=0)
    task_commits: int = Field(default=0, ge=0)
    integration_commits: int = Field(default=0, ge=0)
    conflict_count: int = Field(default=0, ge=0)


class EvaluationMetrics(EvaluationModel):
    quality: QualityMetrics = Field(default_factory=QualityMetrics)
    execution: ExecutionMetrics = Field(default_factory=ExecutionMetrics)
    provider: ProviderMetrics = Field(default_factory=ProviderMetrics)
    git: GitMetrics = Field(default_factory=GitMetrics)


class EvaluationCaseResult(EvaluationModel):
    case_id: str
    title: str
    passed: bool
    run_status: str
    verifier_passed: bool | None = None
    reviewer_approved: bool | None = None
    assertions: list[EvaluationAssertion] = Field(default_factory=list)
    score: EvaluationScore
    metrics: EvaluationMetrics = Field(default_factory=EvaluationMetrics)
    artifacts: list[EvaluationArtifact] = Field(default_factory=list)
    relevant_changed_files: list[str] = Field(default_factory=list)
    failure_category: str | None = None
    failure_message: str | None = None
    retained_fixture_path: str | None = None
    runtime_run_id: str | None = None
    raw_metrics: dict[str, Any] = Field(default_factory=dict)

    @field_validator("raw_metrics")
    @classmethod
    def safe_raw_metrics(cls, value: dict[str, Any]) -> dict[str, Any]:
        return _safe_json(value)


class EvaluationRun(EvaluationModel):
    schema_version: int = EVALUATION_SCHEMA_VERSION
    evaluation_run_id: str
    suite_id: str
    variant: EvaluationVariant
    status: Literal["running", "completed", "failed"] = "running"
    started_at: datetime = Field(default_factory=utc_now)
    completed_at: datetime | None = None
    agentbus_commit_sha: str
    agentbus_version: str = __version__
    configuration_fingerprint: str
    case_results: list[EvaluationCaseResult] = Field(default_factory=list)
    aggregate_metrics: EvaluationMetrics = Field(default_factory=EvaluationMetrics)
    aggregate_score: float = Field(default=0, ge=0, le=100)
    passed: bool = False
    partial: bool = False
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("metadata")
    @classmethod
    def safe_metadata(cls, value: dict[str, Any]) -> dict[str, Any]:
        return _safe_json(value)


class SampleStatistics(EvaluationModel):
    samples: int = Field(ge=0)
    mean: float = 0
    median: float = 0
    minimum: float = 0
    sample_standard_deviation: float = Field(default=0, ge=0)


class RepeatedEvaluationStatistics(EvaluationModel):
    samples: int = Field(ge=0)
    success_rate: float = Field(default=0, ge=0, le=1)
    score: SampleStatistics
    duration_seconds: SampleStatistics
    tokens: SampleStatistics
    retry_distribution: dict[str, int] = Field(default_factory=dict)
    fallback_rate: float = Field(default=0, ge=0, le=1)
    reviewer_approval_rate: float | None = Field(default=None, ge=0, le=1)
    verifier_pass_rate: float | None = Field(default=None, ge=0, le=1)
    file_scope_violation_rate: float = Field(default=0, ge=0, le=1)
    conflict_rate: float = Field(default=0, ge=0, le=1)
    interpretation_note: str = (
        "Descriptive sample statistics only; small samples do not establish "
        "statistical significance."
    )


class EvaluationSeries(EvaluationModel):
    schema_version: int = 1
    series_id: str
    suite_id: str
    variant: EvaluationVariant
    repeat: int = Field(ge=1)
    run_ids: list[str]
    started_at: datetime = Field(default_factory=utc_now)
    completed_at: datetime | None = None
    agentbus_commit_sha: str
    agentbus_version: str = __version__
    configuration_fingerprint: str
    aggregate: RepeatedEvaluationStatistics
    by_case: dict[str, RepeatedEvaluationStatistics] = Field(default_factory=dict)
    passed: bool = False
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("metadata")
    @classmethod
    def safe_series_metadata(cls, value: dict[str, Any]) -> dict[str, Any]:
        return _safe_json(value)


class VariantSummary(EvaluationModel):
    reference: str
    variant_id: str
    samples: int = Field(ge=1)
    success_rate: float = Field(ge=0, le=1)
    score_mean: float
    duration_mean_seconds: float = Field(ge=0)
    tokens_mean: float = Field(ge=0)
    retries_mean: float = Field(ge=0)
    fallback_rate: float = Field(ge=0, le=1)
    safety_failure_rate: float = Field(ge=0, le=1)
    scope_violation_rate: float = Field(ge=0, le=1)


class VariantComparisonReport(EvaluationModel):
    left: VariantSummary
    right: VariantSummary
    differences: dict[str, float]
    interpretation_note: str = (
        "Differences are descriptive. AgentBus does not declare a best variant "
        "from a single run or small sample."
    )


class RegressionSeverity(str, Enum):
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"


class RegressionResult(EvaluationModel):
    case_id: str | None = None
    metric: str
    baseline_value: Any = None
    current_value: Any = None
    regressed: bool
    severity: RegressionSeverity
    message: str

    @field_validator("baseline_value", "current_value")
    @classmethod
    def safe_values(cls, value: Any) -> Any:
        return _safe_json(value)


class EvaluationComparison(EvaluationModel):
    baseline_run_id: str
    current_run_id: str
    regressions: list[RegressionResult] = Field(default_factory=list)
    passed: bool
    summary: str


class ComparisonThresholds(EvaluationModel):
    score_drop: float = Field(default=2.0, ge=0)
    token_increase_ratio: float = Field(default=0.25, ge=0)
    latency_increase_ratio: float = Field(default=0.30, ge=0)
    retry_increase: int = Field(default=0, ge=0)


def _relative_path(value: str) -> str:
    normalized = value.replace("\\", "/")
    if normalized.startswith("/") or re.match(r"^[A-Za-z]:", normalized):
        raise ValueError("evaluation file paths must be relative")
    normalized = normalized.rstrip("/")
    if (
        not normalized
        or normalized in {".", ".."}
        or normalized.startswith("../")
        or "/../" in f"/{normalized}/"
    ):
        raise ValueError("evaluation file paths must stay within the fixture repository")
    return normalized


def _safe_json(value: Any) -> Any:
    sanitized = sanitize_json(value)
    try:
        json.dumps(sanitized, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise ValueError("evaluation values must be sanitized JSON") from exc
    return sanitized
