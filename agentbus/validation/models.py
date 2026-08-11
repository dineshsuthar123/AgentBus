from __future__ import annotations

import re
from datetime import datetime
from enum import Enum
from typing import Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


VALIDATION_SCHEMA_VERSION = 1
_STABLE_ID = re.compile(r"^[a-z0-9][a-z0-9._-]{0,79}$")
_LANGUAGE = re.compile(r"^[a-z][a-z0-9+#.-]{0,31}$")


class ValidationModel(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)


class RepositoryScale(str, Enum):
    TINY = "tiny"
    SMALL = "small"
    MEDIUM = "medium"
    LARGE = "large"
    VERY_LARGE = "very_large"
    REAL_WORLD = "real_world"
    ADVERSARIAL = "adversarial"


class FailureCategory(str, Enum):
    MANIFEST = "manifest"
    REPOSITORY = "repository"
    CONTAINMENT = "containment"
    INDEXING = "indexing"
    SEARCH = "search"
    IMPACT = "impact"
    TASK = "task"
    RESOURCE = "resource"
    SECURITY = "security"
    CANCELLATION = "cancellation"
    PROCESS = "process"
    STORAGE = "storage"
    REPLAY = "replay"
    PROTOCOL = "protocol"
    UNKNOWN = "unknown"


class ValidationStatus(str, Enum):
    PASS = "PASS"
    PASS_WITH_WARNINGS = "PASS_WITH_WARNINGS"
    FAIL = "FAIL"


class RepositorySource(str, Enum):
    GENERATED = "generated"
    LOCAL = "local"
    PUBLIC = "public"


class ScenarioKind(str, Enum):
    INDEX = "index"
    SEARCH = "search"
    CONTEXT = "context"
    IMPACT = "impact"
    TASK = "task"


class CountExpectation(ValidationModel):
    minimum: int = Field(default=0, ge=0, le=1_000_000)
    maximum: int | None = Field(default=None, ge=0, le=1_000_000)

    @model_validator(mode="after")
    def valid_range(self) -> "CountExpectation":
        if self.maximum is not None and self.maximum < self.minimum:
            raise ValueError("count expectation maximum cannot be below minimum")
        return self

    def accepts(self, value: int) -> bool:
        return value >= self.minimum and (
            self.maximum is None or value <= self.maximum
        )


class ValidationResourceLimits(ValidationModel):
    maximum_files: int = Field(default=100_000, ge=1, le=1_000_000)
    maximum_symbols: int = Field(default=1_000_000, ge=1, le=2_000_000)
    maximum_projects: int = Field(default=10_000, ge=1, le=100_000)
    maximum_repository_bytes: int = Field(
        default=5 * 1024 * 1024 * 1024,
        ge=1,
        le=100 * 1024 * 1024 * 1024,
    )
    maximum_index_seconds: float = Field(default=300.0, gt=0, le=86_400)
    maximum_query_seconds: float = Field(default=30.0, gt=0, le=3_600)
    maximum_index_database_bytes: int = Field(
        default=5 * 1024 * 1024 * 1024,
        ge=1,
        le=100 * 1024 * 1024 * 1024,
    )
    maximum_peak_memory_bytes: int = Field(
        default=2 * 1024 * 1024 * 1024,
        ge=1,
        le=64 * 1024 * 1024 * 1024,
    )


class ExpectedIndexingBehavior(ValidationModel):
    allow_partial: bool = False
    protected_content_excluded: bool = True
    provider_calls: Literal[0] = 0
    network_calls: Literal[0] = 0


class ValidationScenario(ValidationModel):
    scenario_id: str
    title: str = Field(min_length=1, max_length=256)
    kind: ScenarioKind
    query: str | None = Field(default=None, max_length=4_096)
    subjects: tuple[str, ...] = Field(default=(), max_length=1_000)
    expected_paths: tuple[str, ...] = Field(default=(), max_length=1_000)
    expected_minimum_results: int = Field(default=0, ge=0, le=10_000)
    maximum_results: int = Field(default=25, ge=1, le=200)
    byte_budget: int = Field(default=100_000, ge=1, le=10_000_000)
    token_budget: int = Field(default=16_000, ge=1, le=1_000_000)
    maximum_duration_seconds: float | None = Field(default=None, gt=0, le=3_600)
    expected_outputs: tuple[str, ...] = Field(default=(), max_length=1_000)
    tags: frozenset[str] = Field(default_factory=frozenset, max_length=64)

    @field_validator("scenario_id")
    @classmethod
    def stable_scenario_id(cls, value: str) -> str:
        if not _STABLE_ID.fullmatch(value):
            raise ValueError("scenario_id must be a stable lowercase identifier")
        return value

    @field_validator("subjects", "expected_paths", "expected_outputs")
    @classmethod
    def bounded_relative_values(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        normalized: list[str] = []
        for value in values:
            candidate = value.replace("\\", "/")
            if (
                not candidate
                or candidate.startswith("/")
                or "\x00" in candidate
                or len(candidate) > 2_048
                or any(part in {"", ".", ".."} for part in candidate.split("/"))
            ):
                raise ValueError("scenario paths and subjects must be bounded and relative")
            normalized.append(candidate)
        return tuple(normalized)

    @model_validator(mode="after")
    def valid_scenario_shape(self) -> "ValidationScenario":
        if self.kind in {ScenarioKind.SEARCH, ScenarioKind.CONTEXT, ScenarioKind.TASK}:
            if self.query is None or not self.query.strip():
                raise ValueError(f"{self.kind.value} scenarios require a query")
        if self.kind == ScenarioKind.IMPACT and not self.subjects:
            raise ValueError("impact scenarios require at least one subject")
        return self


class ValidationRepository(ValidationModel):
    repository_id: str
    title: str = Field(min_length=1, max_length=256)
    source: RepositorySource
    path: str | None = Field(default=None, max_length=4_096)
    checkout_environment: str | None = Field(default=None, max_length=128)
    remote_url: str | None = Field(default=None, max_length=2_048)
    revision: str | None = Field(default=None, max_length=256)
    scale: RepositoryScale = RepositoryScale.SMALL
    language_mix: tuple[str, ...] = Field(default=(), max_length=32)
    expected_project_count: CountExpectation = Field(
        default_factory=CountExpectation
    )
    expected_file_count: CountExpectation = Field(default_factory=CountExpectation)
    expected_symbol_count: CountExpectation = Field(default_factory=CountExpectation)
    known_characteristics: tuple[str, ...] = Field(default=(), max_length=128)
    expected_indexing: ExpectedIndexingBehavior = Field(
        default_factory=ExpectedIndexingBehavior
    )
    scenarios: tuple[ValidationScenario, ...] = Field(default=(), max_length=256)
    resource_limits: ValidationResourceLimits = Field(
        default_factory=ValidationResourceLimits
    )
    enabled_by_default: bool = True

    @field_validator("repository_id")
    @classmethod
    def stable_repository_id(cls, value: str) -> str:
        if not _STABLE_ID.fullmatch(value):
            raise ValueError("repository_id must be a stable lowercase identifier")
        return value

    @field_validator("language_mix")
    @classmethod
    def normalized_languages(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        normalized = tuple(dict.fromkeys(value.strip().lower() for value in values))
        if any(not _LANGUAGE.fullmatch(value) for value in normalized):
            raise ValueError("language_mix contains an invalid language identifier")
        return normalized

    @field_validator("path")
    @classmethod
    def safe_local_path(cls, value: str | None) -> str | None:
        if value is not None and (not value.strip() or "\x00" in value):
            raise ValueError("repository path must be non-empty and NUL-free")
        return value

    @field_validator("remote_url")
    @classmethod
    def safe_public_url(cls, value: str | None) -> str | None:
        if value is None:
            return None
        parsed = urlsplit(value)
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError(
                "public repository URLs must be credential-free HTTPS URLs"
            )
        return value

    @field_validator("revision")
    @classmethod
    def safe_revision(cls, value: str | None) -> str | None:
        if value is not None and (
            not value
            or value.startswith("-")
            or "\x00" in value
            or any(character.isspace() for character in value)
        ):
            raise ValueError("repository revision is ambiguous or unsafe")
        return value

    @model_validator(mode="after")
    def valid_source_shape(self) -> "ValidationRepository":
        if self.source == RepositorySource.GENERATED:
            if self.path is not None or self.remote_url is not None:
                raise ValueError("generated repositories cannot declare paths or URLs")
        elif self.source == RepositorySource.LOCAL:
            if self.path is None and self.checkout_environment is None:
                raise ValueError("local repositories require path or checkout_environment")
            if self.remote_url is not None:
                raise ValueError("local repositories cannot declare a remote URL")
        elif self.source == RepositorySource.PUBLIC:
            if self.remote_url is None or not self.remote_url.startswith("https://"):
                raise ValueError("public repositories require an HTTPS remote URL")
        identifiers = [scenario.scenario_id for scenario in self.scenarios]
        if len(identifiers) != len(set(identifiers)):
            raise ValueError("validation scenario IDs must be unique per repository")
        return self


class ValidationMetric(ValidationModel):
    name: str = Field(min_length=1, max_length=128, pattern=r"^[a-z0-9_.-]+$")
    unit: str = Field(min_length=1, max_length=32, pattern=r"^[a-z0-9_.-]+$")
    value: float
    lower_bound: float | None = None
    upper_bound: float | None = None
    passed: bool | None = None
    detail: str | None = Field(default=None, max_length=1_024)

    @model_validator(mode="after")
    def valid_bounds(self) -> "ValidationMetric":
        if (
            self.lower_bound is not None
            and self.upper_bound is not None
            and self.upper_bound < self.lower_bound
        ):
            raise ValueError("metric upper_bound cannot be below lower_bound")
        expected = (
            (self.lower_bound is None or self.value >= self.lower_bound)
            and (self.upper_bound is None or self.value <= self.upper_bound)
        )
        if self.passed is not None and self.passed != expected:
            raise ValueError("metric passed value disagrees with its bounds")
        if self.passed is None and (
            self.lower_bound is not None or self.upper_bound is not None
        ):
            self.passed = expected
        return self


class ValidationFailure(ValidationModel):
    category: FailureCategory
    summary: str = Field(min_length=1, max_length=512)
    detail: str | None = Field(default=None, max_length=2_048)
    repository_id: str | None = Field(default=None, max_length=80)
    scenario_id: str | None = Field(default=None, max_length=80)
    fatal: bool = True
    retryable: bool = False


class ValidationScenarioResult(ValidationModel):
    scenario_id: str = Field(min_length=1, max_length=80)
    kind: ScenarioKind
    status: ValidationStatus
    duration_seconds: float = Field(ge=0)
    observed_count: int = Field(default=0, ge=0)
    matched_expected_paths: tuple[str, ...] = Field(default=(), max_length=1_000)
    missing_expected_paths: tuple[str, ...] = Field(default=(), max_length=1_000)
    truncated: bool = False
    detail: str | None = Field(default=None, max_length=1_024)


class ValidationRun(ValidationModel):
    run_id: str = Field(min_length=16, max_length=128)
    repository_id: str = Field(min_length=1, max_length=80)
    status: ValidationStatus
    root_fingerprint: str = Field(min_length=64, max_length=64)
    commit_sha: str | None = Field(default=None, min_length=7, max_length=64)
    started_at: datetime
    finished_at: datetime
    duration_seconds: float = Field(ge=0)
    file_count: int = Field(ge=0)
    project_count: int = Field(ge=0)
    symbol_count: int = Field(ge=0)
    languages: tuple[str, ...] = Field(default=(), max_length=32)
    metrics: tuple[ValidationMetric, ...] = Field(default=(), max_length=1_000)
    scenarios: tuple[ValidationScenarioResult, ...] = Field(
        default=(), max_length=256
    )
    failures: tuple[ValidationFailure, ...] = Field(default=(), max_length=1_000)
    warnings: tuple[str, ...] = Field(default=(), max_length=1_000)
    provider_calls: Literal[0] = 0
    network_calls: Literal[0] = 0

    @model_validator(mode="after")
    def consistent_terminal_status(self) -> "ValidationRun":
        if self.finished_at < self.started_at:
            raise ValueError("validation run cannot finish before it starts")
        if self.status == ValidationStatus.FAIL and not self.failures:
            raise ValueError("failed validation runs require at least one failure")
        if self.status != ValidationStatus.FAIL and any(
            failure.fatal for failure in self.failures
        ):
            raise ValueError("fatal validation failures require FAIL status")
        return self


class ValidationReport(ValidationModel):
    schema_version: Literal[1] = VALIDATION_SCHEMA_VERSION
    status: ValidationStatus
    generated_at: datetime
    offline: bool = True
    network_used: bool = False
    runs: tuple[ValidationRun, ...] = Field(default=(), max_length=1_000)
    warnings: tuple[str, ...] = Field(default=(), max_length=1_000)

    @model_validator(mode="after")
    def consistent_network_mode(self) -> "ValidationReport":
        if self.offline and self.network_used:
            raise ValueError("offline validation reports cannot record network use")
        return self

    @property
    def ok(self) -> bool:
        return self.status != ValidationStatus.FAIL

    def to_dict(self) -> dict[str, object]:
        payload = self.model_dump(mode="json")
        payload["ok"] = self.ok
        payload["repositories"] = {
            "total": len(self.runs),
            "passed": sum(run.status == ValidationStatus.PASS for run in self.runs),
            "passed_with_warnings": sum(
                run.status == ValidationStatus.PASS_WITH_WARNINGS for run in self.runs
            ),
            "failed": sum(run.status == ValidationStatus.FAIL for run in self.runs),
        }
        return payload
