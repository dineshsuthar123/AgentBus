from __future__ import annotations

import re
from datetime import datetime
from enum import Enum
from pathlib import PurePosixPath
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from agentbus.intelligence.version import INTELLIGENCE_SCHEMA_VERSION


_IDENTITY_PATTERN = re.compile(
    r"^(repo|workspace|project|module|file|symbol|reference|edge|snapshot|plan|impact|testimpact)_[a-f0-9]{32,64}$"
)
_HASH_PATTERN = re.compile(r"^[a-f0-9]{64}$")
_INDEX_OPERATION_PATTERN = re.compile(r"^indexop_[a-f0-9]{32,64}$")
_MAX_PATH_CHARS = 2_048
_MAX_TEXT_CHARS = 8_192


class IntelligenceModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class SourceLanguage(str, Enum):
    PYTHON = "python"
    TYPESCRIPT = "typescript"
    JAVASCRIPT = "javascript"
    JAVA = "java"
    GO = "go"
    JSON = "json"
    YAML = "yaml"
    TOML = "toml"
    MARKDOWN = "markdown"
    TEXT = "text"
    UNKNOWN = "unknown"


class SymbolKind(str, Enum):
    MODULE = "module"
    PACKAGE = "package"
    NAMESPACE = "namespace"
    CLASS = "class"
    INTERFACE = "interface"
    ENUM = "enum"
    RECORD = "record"
    FUNCTION = "function"
    METHOD = "method"
    CONSTRUCTOR = "constructor"
    FIELD = "field"
    PROPERTY = "property"
    VARIABLE = "variable"
    CONSTANT = "constant"
    TYPE_ALIAS = "type_alias"
    ENDPOINT = "endpoint"
    TEST = "test"
    CONFIGURATION_UNIT = "configuration_unit"


class DependencyKind(str, Enum):
    IMPORTS = "imports"
    EXPORTS = "exports"
    CALLS = "calls"
    REFERENCES = "references"
    INHERITS = "inherits"
    IMPLEMENTS = "implements"
    INSTANTIATES = "instantiates"
    READS = "reads"
    WRITES = "writes"
    CONFIGURES = "configures"
    TESTS = "tests"
    OWNS = "owns"
    GENERATED_FROM = "generated_from"


class ProjectKind(str, Enum):
    PYTHON = "python"
    NODE = "node"
    JAVA = "java"
    GO = "go"
    GENERIC = "generic"


class IndexState(str, Enum):
    ABSENT = "absent"
    BUILDING = "building"
    CURRENT = "current"
    PARTIALLY_CURRENT = "partially_current"
    STALE = "stale"
    CORRUPTED = "corrupted"
    INCOMPATIBLE = "incompatible"
    PAUSED = "paused"


class IndexOperationKind(str, Enum):
    BUILD = "build"
    UPDATE = "update"
    REPAIR = "repair"


class IndexOperationState(str, Enum):
    RUNNING = "running"
    COMPLETED = "completed"
    PAUSED = "paused"
    FAILED = "failed"
    CANCELLED = "cancelled"


class DiagnosticSeverity(str, Enum):
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"


class ContextRole(str, Enum):
    PLANNER = "planner"
    CODER = "coder"
    VERIFIER = "verifier"
    REVIEWER = "reviewer"


class ImpactRisk(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class RepositoryIdentity(IntelligenceModel):
    repository_id: str
    key_hash: str
    display_name: str | None = Field(default=None, max_length=256)
    schema_version: int = Field(default=INTELLIGENCE_SCHEMA_VERSION, ge=1)

    @field_validator("repository_id")
    @classmethod
    def validate_repository_id(cls, value: str) -> str:
        return _identity(value, "repo")

    @field_validator("key_hash")
    @classmethod
    def validate_key_hash(cls, value: str) -> str:
        return _hash(value)


class WorkspaceIdentity(IntelligenceModel):
    workspace_id: str
    repository_id: str
    roots: tuple[str, ...] = Field(default=("",), max_length=128)

    @field_validator("workspace_id")
    @classmethod
    def validate_workspace_id(cls, value: str) -> str:
        return _identity(value, "workspace")

    @field_validator("repository_id")
    @classmethod
    def validate_repository_id(cls, value: str) -> str:
        return _identity(value, "repo")

    @field_validator("roots")
    @classmethod
    def validate_roots(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        if not values:
            raise ValueError("workspace roots must not be empty")
        normalized = tuple(_relative_path(value, allow_root=True) for value in values)
        if len(set(normalized)) != len(normalized):
            raise ValueError("workspace roots must be unique")
        return normalized


class Project(IntelligenceModel):
    project_id: str
    repository_id: str
    name: str = Field(min_length=1, max_length=256)
    kind: ProjectKind
    root: str
    source_roots: tuple[str, ...] = Field(default=(), max_length=128)
    test_roots: tuple[str, ...] = Field(default=(), max_length=128)
    generated_roots: tuple[str, ...] = Field(default=(), max_length=128)
    manifest_paths: tuple[str, ...] = Field(default=(), max_length=128)
    workspace_project_ids: tuple[str, ...] = Field(default=(), max_length=256)

    @field_validator("project_id")
    @classmethod
    def validate_project_id(cls, value: str) -> str:
        return _identity(value, "project")

    @field_validator("repository_id")
    @classmethod
    def validate_repository_id(cls, value: str) -> str:
        return _identity(value, "repo")

    @field_validator(
        "root",
        "source_roots",
        "test_roots",
        "generated_roots",
        "manifest_paths",
    )
    @classmethod
    def validate_paths(cls, value: Any) -> Any:
        if isinstance(value, tuple):
            return tuple(_relative_path(item, allow_root=True) for item in value)
        return _relative_path(value, allow_root=True)

    @field_validator("workspace_project_ids")
    @classmethod
    def validate_workspace_projects(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        return tuple(_identity(value, "project") for value in values)


class Module(IntelligenceModel):
    module_id: str
    project_id: str
    name: str = Field(min_length=1, max_length=512)
    qualified_name: str = Field(min_length=1, max_length=2_048)
    relative_path: str
    language: SourceLanguage
    public: bool = False

    @field_validator("module_id")
    @classmethod
    def validate_module_id(cls, value: str) -> str:
        return _identity(value, "module")

    @field_validator("project_id")
    @classmethod
    def validate_project_id(cls, value: str) -> str:
        return _identity(value, "project")

    @field_validator("relative_path")
    @classmethod
    def validate_relative_path(cls, value: str) -> str:
        return _relative_path(value)


class SourceFile(IntelligenceModel):
    file_id: str
    repository_id: str
    project_id: str | None = None
    relative_path: str
    language: SourceLanguage
    content_hash: str
    size_bytes: int = Field(ge=0, le=100_000_000)
    parser_name: str = Field(min_length=1, max_length=128)
    parser_version: str = Field(min_length=1, max_length=128)
    generated: bool = False
    test: bool = False
    protected: bool = False

    @field_validator("file_id")
    @classmethod
    def validate_file_id(cls, value: str) -> str:
        return _identity(value, "file")

    @field_validator("repository_id")
    @classmethod
    def validate_repository_id(cls, value: str) -> str:
        return _identity(value, "repo")

    @field_validator("project_id")
    @classmethod
    def validate_project_id(cls, value: str | None) -> str | None:
        return _identity(value, "project") if value else None

    @field_validator("relative_path")
    @classmethod
    def validate_relative_path(cls, value: str) -> str:
        return _relative_path(value)

    @field_validator("content_hash")
    @classmethod
    def validate_content_hash(cls, value: str) -> str:
        return _hash(value)


class SymbolLocation(IntelligenceModel):
    relative_path: str
    start_line: int = Field(ge=1)
    start_column: int = Field(ge=0)
    end_line: int = Field(ge=1)
    end_column: int = Field(ge=0)

    @field_validator("relative_path")
    @classmethod
    def validate_relative_path(cls, value: str) -> str:
        return _relative_path(value)

    @model_validator(mode="after")
    def validate_range(self) -> SymbolLocation:
        if (self.end_line, self.end_column) < (
            self.start_line,
            self.start_column,
        ):
            raise ValueError("symbol location end must not precede its start")
        return self


class Symbol(IntelligenceModel):
    symbol_id: str
    file_id: str
    project_id: str | None = None
    module_id: str | None = None
    name: str = Field(min_length=1, max_length=512)
    qualified_name: str = Field(min_length=1, max_length=2_048)
    kind: SymbolKind
    language: SourceLanguage
    location: SymbolLocation
    signature: str | None = Field(default=None, max_length=4_096)
    documentation: str | None = Field(default=None, max_length=_MAX_TEXT_CHARS)
    parent_symbol_id: str | None = None
    exported: bool = False
    test: bool = False
    endpoint: str | None = Field(default=None, max_length=2_048)
    confidence: float = Field(default=1.0, ge=0, le=1)
    attributes: dict[str, Any] = Field(default_factory=dict)

    @field_validator("symbol_id")
    @classmethod
    def validate_symbol_id(cls, value: str) -> str:
        return _identity(value, "symbol")

    @field_validator("file_id")
    @classmethod
    def validate_file_id(cls, value: str) -> str:
        return _identity(value, "file")

    @field_validator("project_id")
    @classmethod
    def validate_project_id(cls, value: str | None) -> str | None:
        return _identity(value, "project") if value else None

    @field_validator("module_id")
    @classmethod
    def validate_module_id(cls, value: str | None) -> str | None:
        return _identity(value, "module") if value else None

    @field_validator("parent_symbol_id")
    @classmethod
    def validate_parent_symbol_id(cls, value: str | None) -> str | None:
        return _identity(value, "symbol") if value else None

    @field_validator("attributes")
    @classmethod
    def validate_attributes(cls, value: dict[str, Any]) -> dict[str, Any]:
        if len(value) > 64:
            raise ValueError("symbol attributes exceed the maximum entry count")
        return value


class SymbolReference(IntelligenceModel):
    reference_id: str
    source_symbol_id: str | None = None
    source_file_id: str
    target_symbol_id: str | None = None
    unresolved_target: str | None = Field(default=None, max_length=2_048)
    kind: DependencyKind = DependencyKind.REFERENCES
    location: SymbolLocation
    confidence: float = Field(default=1.0, ge=0, le=1)
    explanation: str = Field(min_length=1, max_length=2_048)

    @field_validator("reference_id")
    @classmethod
    def validate_reference_id(cls, value: str) -> str:
        return _identity(value, "reference")

    @field_validator("source_symbol_id", "target_symbol_id")
    @classmethod
    def validate_symbol_ids(cls, value: str | None) -> str | None:
        return _identity(value, "symbol") if value else None

    @field_validator("source_file_id")
    @classmethod
    def validate_source_file_id(cls, value: str) -> str:
        return _identity(value, "file")

    @model_validator(mode="after")
    def require_target(self) -> SymbolReference:
        if not self.target_symbol_id and not self.unresolved_target:
            raise ValueError("a reference requires a resolved or unresolved target")
        return self


class DependencyEdge(IntelligenceModel):
    edge_id: str
    kind: DependencyKind
    source_id: str = Field(min_length=1, max_length=256)
    target_id: str = Field(min_length=1, max_length=256)
    location: SymbolLocation | None = None
    confidence: float = Field(default=1.0, ge=0, le=1)
    parser_name: str = Field(min_length=1, max_length=128)
    parser_version: str = Field(min_length=1, max_length=128)
    explanation: str = Field(min_length=1, max_length=2_048)
    resolved: bool = True

    @field_validator("edge_id")
    @classmethod
    def validate_edge_id(cls, value: str) -> str:
        return _identity(value, "edge")


class OwnershipRule(IntelligenceModel):
    rule_id: str = Field(min_length=1, max_length=256)
    pattern: str = Field(min_length=1, max_length=2_048)
    owners: tuple[str, ...] = Field(min_length=1, max_length=128)
    source_path: str
    confidence: float = Field(default=1.0, ge=0, le=1)
    explanation: str = Field(min_length=1, max_length=2_048)

    @field_validator("source_path")
    @classmethod
    def validate_source_path(cls, value: str) -> str:
        return _relative_path(value)


class ArchitectureBoundary(IntelligenceModel):
    boundary_id: str = Field(min_length=1, max_length=256)
    name: str = Field(min_length=1, max_length=512)
    scope: tuple[str, ...] = Field(min_length=1, max_length=256)
    boundary_type: Literal[
        "layer",
        "component",
        "service",
        "shared_library",
        "generated",
        "security_sensitive",
        "forbidden_dependency",
    ]
    source_evidence: tuple[str, ...] = Field(min_length=1, max_length=128)
    confidence: float = Field(ge=0, le=1)
    explanation: str = Field(min_length=1, max_length=2_048)
    forbidden_targets: tuple[str, ...] = Field(default=(), max_length=256)

    @field_validator("scope", "source_evidence", "forbidden_targets")
    @classmethod
    def validate_relative_scopes(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        return tuple(_relative_path(value, allow_pattern=True) for value in values)


class IndexDiagnostic(IntelligenceModel):
    code: str = Field(min_length=1, max_length=128)
    severity: DiagnosticSeverity
    message: str = Field(min_length=1, max_length=2_048)
    relative_path: str | None = None
    parser_name: str | None = Field(default=None, max_length=128)
    recoverable: bool = True
    details: dict[str, Any] = Field(default_factory=dict)

    @field_validator("relative_path")
    @classmethod
    def validate_relative_path(cls, value: str | None) -> str | None:
        return _relative_path(value) if value else None

    @field_validator("details")
    @classmethod
    def validate_details(cls, value: dict[str, Any]) -> dict[str, Any]:
        if len(value) > 32:
            raise ValueError("diagnostic details exceed the maximum entry count")
        return value


class IndexSnapshot(IntelligenceModel):
    snapshot_id: str
    repository_id: str
    workspace_id: str
    state: IndexState
    created_at: datetime
    completed_at: datetime | None = None
    file_count: int = Field(default=0, ge=0)
    symbol_count: int = Field(default=0, ge=0)
    reference_count: int = Field(default=0, ge=0)
    edge_count: int = Field(default=0, ge=0)
    project_map_hash: str
    graph_hash: str
    parser_versions: dict[str, str] = Field(default_factory=dict)
    source_fingerprint: str
    diagnostics: tuple[IndexDiagnostic, ...] = Field(default=(), max_length=1_000)

    @field_validator("snapshot_id")
    @classmethod
    def validate_snapshot_id(cls, value: str) -> str:
        return _identity(value, "snapshot")

    @field_validator("repository_id")
    @classmethod
    def validate_repository_id(cls, value: str) -> str:
        return _identity(value, "repo")

    @field_validator("workspace_id")
    @classmethod
    def validate_workspace_id(cls, value: str) -> str:
        return _identity(value, "workspace")

    @field_validator("project_map_hash", "graph_hash", "source_fingerprint")
    @classmethod
    def validate_hashes(cls, value: str) -> str:
        return _hash(value)

    @field_validator("parser_versions")
    @classmethod
    def validate_parser_versions(cls, value: dict[str, str]) -> dict[str, str]:
        if len(value) > 64:
            raise ValueError("parser version map exceeds the maximum entry count")
        return value


class IndexStatus(IntelligenceModel):
    repository_id: str
    workspace_id: str
    state: IndexState
    snapshot_id: str | None = None
    stale_paths: tuple[str, ...] = Field(default=(), max_length=1_000)
    indexed_files: int = Field(default=0, ge=0)
    total_files: int = Field(default=0, ge=0)
    message: str | None = Field(default=None, max_length=2_048)
    diagnostics: tuple[IndexDiagnostic, ...] = Field(default=(), max_length=1_000)

    @field_validator("repository_id")
    @classmethod
    def validate_repository_id(cls, value: str) -> str:
        return _identity(value, "repo")

    @field_validator("workspace_id")
    @classmethod
    def validate_workspace_id(cls, value: str) -> str:
        return _identity(value, "workspace")

    @field_validator("snapshot_id")
    @classmethod
    def validate_snapshot_id(cls, value: str | None) -> str | None:
        return _identity(value, "snapshot") if value else None

    @field_validator("stale_paths")
    @classmethod
    def validate_stale_paths(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        return tuple(_relative_path(value) for value in values)


class IndexOperation(IntelligenceModel):
    repository_id: str
    operation_id: str
    operation_kind: IndexOperationKind
    state: IndexOperationState
    owner_pid: int = Field(ge=1)
    started_at: datetime
    heartbeat_at: datetime
    cancellation_requested: bool = False

    @field_validator("repository_id")
    @classmethod
    def validate_repository_id(cls, value: str) -> str:
        return _identity(value, "repo")

    @field_validator("operation_id")
    @classmethod
    def validate_operation_id(cls, value: str) -> str:
        if not _INDEX_OPERATION_PATTERN.fullmatch(value):
            raise ValueError(
                "index operation id must be a portable indexop identity"
            )
        return value

    @field_validator("started_at", "heartbeat_at")
    @classmethod
    def validate_aware_timestamp(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError(
                "index operation timestamps must include a timezone"
            )
        return value

    @model_validator(mode="after")
    def validate_timestamps(self) -> IndexOperation:
        if self.heartbeat_at < self.started_at:
            raise ValueError(
                "index operation heartbeat cannot precede its start"
            )
        return self


class SearchQuery(IntelligenceModel):
    text: str = Field(min_length=1, max_length=2_048)
    project_ids: tuple[str, ...] = Field(default=(), max_length=128)
    languages: tuple[SourceLanguage, ...] = Field(default=(), max_length=32)
    symbol_kinds: tuple[SymbolKind, ...] = Field(default=(), max_length=64)
    path_prefixes: tuple[str, ...] = Field(default=(), max_length=128)
    test_only: bool = False
    limit: int = Field(default=25, ge=1, le=200)
    offset: int = Field(default=0, ge=0, le=100_000)

    @field_validator("project_ids")
    @classmethod
    def validate_project_ids(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        return tuple(_identity(value, "project") for value in values)

    @field_validator("path_prefixes")
    @classmethod
    def validate_path_prefixes(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        return tuple(_relative_path(value, allow_root=True) for value in values)


class SearchResult(IntelligenceModel):
    rank: int = Field(ge=1)
    score: float = Field(ge=0)
    score_components: dict[str, float] = Field(default_factory=dict)
    matched_terms: tuple[str, ...] = Field(default=(), max_length=128)
    relative_path: str
    source_hash: str
    project_id: str | None = None
    symbol: Symbol | None = None
    dependency_path: tuple[str, ...] = Field(default=(), max_length=64)
    stale: bool = False
    explanation: str = Field(min_length=1, max_length=2_048)

    @field_validator("relative_path")
    @classmethod
    def validate_relative_path(cls, value: str) -> str:
        return _relative_path(value)

    @field_validator("source_hash")
    @classmethod
    def validate_source_hash(cls, value: str) -> str:
        return _hash(value)

    @field_validator("project_id")
    @classmethod
    def validate_project_id(cls, value: str | None) -> str | None:
        return _identity(value, "project") if value else None

    @field_validator("score_components")
    @classmethod
    def validate_score_components(
        cls,
        value: dict[str, float],
    ) -> dict[str, float]:
        if len(value) > 32:
            raise ValueError("score components exceed the maximum entry count")
        if any(score < 0 for score in value.values()):
            raise ValueError("score components must not be negative")
        return value


class ContextCandidate(IntelligenceModel):
    candidate_id: str = Field(min_length=1, max_length=256)
    relative_path: str
    source_hash: str
    symbol_id: str | None = None
    role: ContextRole
    score: float = Field(ge=0)
    byte_count: int = Field(ge=0)
    estimated_tokens: int = Field(ge=0)
    selected: bool = False
    reasons: tuple[str, ...] = Field(default=(), max_length=64)
    exclusion_reason: str | None = Field(default=None, max_length=1_024)
    content: str | None = Field(default=None, max_length=100_000, repr=False)

    @field_validator("relative_path")
    @classmethod
    def validate_relative_path(cls, value: str) -> str:
        return _relative_path(value)

    @field_validator("source_hash")
    @classmethod
    def validate_source_hash(cls, value: str) -> str:
        return _hash(value)

    @field_validator("symbol_id")
    @classmethod
    def validate_symbol_id(cls, value: str | None) -> str | None:
        return _identity(value, "symbol") if value else None


class ContextPlan(IntelligenceModel):
    plan_id: str
    snapshot_id: str | None = None
    role: ContextRole
    task_hash: str
    byte_budget: int = Field(gt=0, le=10_000_000)
    token_budget: int = Field(gt=0, le=2_000_000)
    selected_bytes: int = Field(ge=0)
    selected_tokens: int = Field(ge=0)
    candidates: tuple[ContextCandidate, ...] = Field(default=(), max_length=2_000)
    stale_warning: str | None = Field(default=None, max_length=2_048)
    plan_hash: str

    @field_validator("plan_id")
    @classmethod
    def validate_plan_id(cls, value: str) -> str:
        return _identity(value, "plan")

    @field_validator("snapshot_id")
    @classmethod
    def validate_snapshot_id(cls, value: str | None) -> str | None:
        return _identity(value, "snapshot") if value else None

    @field_validator("task_hash", "plan_hash")
    @classmethod
    def validate_hashes(cls, value: str) -> str:
        return _hash(value)

    @model_validator(mode="after")
    def validate_budgets(self) -> ContextPlan:
        if self.selected_bytes > self.byte_budget:
            raise ValueError("selected context exceeds the byte budget")
        if self.selected_tokens > self.token_budget:
            raise ValueError("selected context exceeds the token budget")
        return self


class ImpactRequest(IntelligenceModel):
    paths: tuple[str, ...] = Field(default=(), max_length=1_000)
    symbol_ids: tuple[str, ...] = Field(default=(), max_length=1_000)
    max_depth: int = Field(default=4, ge=0, le=16)
    max_nodes: int = Field(default=500, ge=1, le=10_000)

    @field_validator("paths")
    @classmethod
    def validate_paths(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        return tuple(_relative_path(value) for value in values)

    @field_validator("symbol_ids")
    @classmethod
    def validate_symbol_ids(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        return tuple(_identity(value, "symbol") for value in values)

    @model_validator(mode="after")
    def require_subject(self) -> ImpactRequest:
        if not self.paths and not self.symbol_ids:
            raise ValueError("impact analysis requires at least one path or symbol")
        return self


class TestImpactResult(IntelligenceModel):
    result_id: str
    selected_tests: tuple[str, ...] = Field(default=(), max_length=2_000)
    mandatory_tests: tuple[str, ...] = Field(default=(), max_length=2_000)
    optional_tests: tuple[str, ...] = Field(default=(), max_length=2_000)
    full_suite_recommended: bool = False
    confidence: float = Field(ge=0, le=1)
    evidence: tuple[str, ...] = Field(default=(), max_length=2_000)
    escalation_reasons: tuple[str, ...] = Field(default=(), max_length=256)

    @field_validator("result_id")
    @classmethod
    def validate_result_id(cls, value: str) -> str:
        return _identity(value, "testimpact")

    @field_validator("selected_tests", "mandatory_tests", "optional_tests")
    @classmethod
    def validate_test_paths(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        return tuple(_relative_path(value) for value in values)


class ImpactResult(IntelligenceModel):
    result_id: str
    snapshot_id: str | None = None
    changed_paths: tuple[str, ...] = Field(default=(), max_length=1_000)
    changed_symbols: tuple[str, ...] = Field(default=(), max_length=1_000)
    direct_dependents: tuple[str, ...] = Field(default=(), max_length=5_000)
    transitive_dependents: tuple[str, ...] = Field(default=(), max_length=10_000)
    affected_projects: tuple[str, ...] = Field(default=(), max_length=1_000)
    affected_public_apis: tuple[str, ...] = Field(default=(), max_length=2_000)
    affected_endpoints: tuple[str, ...] = Field(default=(), max_length=2_000)
    affected_configurations: tuple[str, ...] = Field(default=(), max_length=2_000)
    architecture_crossings: tuple[str, ...] = Field(default=(), max_length=2_000)
    ownership_rules: tuple[str, ...] = Field(default=(), max_length=1_000)
    integration_hotspots: tuple[str, ...] = Field(default=(), max_length=2_000)
    risk: ImpactRisk
    confidence: float = Field(ge=0, le=1)
    uncertainty: tuple[str, ...] = Field(default=(), max_length=256)
    evidence: tuple[str, ...] = Field(default=(), max_length=5_000)
    tests: TestImpactResult
    truncated: bool = False

    @field_validator("result_id")
    @classmethod
    def validate_result_id(cls, value: str) -> str:
        return _identity(value, "impact")

    @field_validator("snapshot_id")
    @classmethod
    def validate_snapshot_id(cls, value: str | None) -> str | None:
        return _identity(value, "snapshot") if value else None

    @field_validator("changed_paths")
    @classmethod
    def validate_changed_paths(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        return tuple(_relative_path(value) for value in values)

    @field_validator(
        "changed_symbols",
        "direct_dependents",
        "transitive_dependents",
        "affected_public_apis",
        "affected_endpoints",
        "affected_configurations",
        "integration_hotspots",
    )
    @classmethod
    def validate_node_ids(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        if any(not value or len(value) > 2_048 for value in values):
            raise ValueError("impact node identifiers must be bounded")
        return values

    @field_validator("affected_projects")
    @classmethod
    def validate_project_ids(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        return tuple(_identity(value, "project") for value in values)


def _identity(value: str, prefix: str) -> str:
    if not _IDENTITY_PATTERN.fullmatch(value) or not value.startswith(f"{prefix}_"):
        raise ValueError(f"expected a deterministic {prefix} identity")
    return value


def _hash(value: str) -> str:
    if not _HASH_PATTERN.fullmatch(value):
        raise ValueError("expected a lowercase SHA-256 hash")
    return value


def _relative_path(
    value: str,
    *,
    allow_root: bool = False,
    allow_pattern: bool = False,
) -> str:
    if not isinstance(value, str):
        raise TypeError("repository path must be text")
    if "\x00" in value or len(value) > _MAX_PATH_CHARS:
        raise ValueError("repository path is invalid or too long")
    normalized = value.replace("\\", "/").strip()
    if allow_root and normalized in {"", "."}:
        return ""
    if not normalized or normalized.startswith(("/", "//")):
        raise ValueError("repository path must be relative")
    if re.match(r"^[A-Za-z]:", normalized):
        raise ValueError("repository path must not contain a drive prefix")
    parts = PurePosixPath(normalized).parts
    if any(part in {"", ".", ".."} for part in parts):
        raise ValueError("repository path must not traverse")
    if not allow_pattern and any(character in normalized for character in "*?"):
        raise ValueError("repository path must not contain glob wildcard syntax")
    return PurePosixPath(*parts).as_posix()
