from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

from agentbus.tools.protocol.version import TOOL_PROTOCOL_VERSION


MAX_DESCRIPTOR_TEXT_CHARS = 4_096
MAX_INLINE_OUTPUT_CHARS = 65_536
MAX_SAFE_DIAGNOSTIC_CHARS = 8_192
MAX_SCHEMA_BYTES = 65_536
MAX_METADATA_BYTES = 65_536
MAX_INVOCATION_ARGUMENT_BYTES = 1_048_576
MAX_STRUCTURED_OUTPUT_BYTES = 1_048_576
MAX_COLLECTION_ITEMS = 256
_NAME_PATTERN = re.compile(r"^[a-z][a-z0-9]*(?:[._-][a-z0-9]+)*$")
_IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class ToolProtocolModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        str_strip_whitespace=True,
        use_enum_values=False,
    )


class ToolCapabilityName(str, Enum):
    FILESYSTEM_READ = "filesystem.read"
    FILESYSTEM_WRITE = "filesystem.write"
    FILESYSTEM_CREATE = "filesystem.create"
    FILESYSTEM_DELETE = "filesystem.delete"
    FILESYSTEM_RENAME = "filesystem.rename"
    PROCESS_EXECUTE = "process.execute"
    PROCESS_NETWORK = "process.network"
    GIT_READ = "git.read"
    GIT_WRITE = "git.write"
    GIT_COMMIT = "git.commit"
    GIT_BRANCH = "git.branch"
    GIT_WORKTREE = "git.worktree"
    TEST_EXECUTE = "test.execute"
    PACKAGE_INSTALL = "package.install"
    ENVIRONMENT_READ_SAFE = "environment.read_safe"
    MCP_CONNECT = "mcp.connect"
    MCP_INVOKE = "mcp.invoke"


class ToolSafetyClassification(str, Enum):
    SAFE = "safe"
    SENSITIVE = "sensitive"
    RISKY = "risky"
    DANGEROUS = "dangerous"


class ToolPolicyOutcome(str, Enum):
    ALLOW = "allow"
    DENY = "deny"
    REQUIRE_APPROVAL = "require_approval"
    ALLOW_WITH_CONSTRAINTS = "allow_with_constraints"


class ToolInvocationStatus(str, Enum):
    REQUESTED = "requested"
    AWAITING_APPROVAL = "awaiting_approval"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    DENIED = "denied"
    CANCELLED = "cancelled"
    TIMED_OUT = "timed_out"


class ToolOutputStream(str, Enum):
    STDOUT = "stdout"
    STDERR = "stderr"
    DIAGNOSTIC = "diagnostic"


class ToolErrorCategory(str, Enum):
    VALIDATION = "validation"
    POLICY_DENIED = "policy_denied"
    APPROVAL_REQUIRED = "approval_required"
    APPROVAL_INVALID = "approval_invalid"
    CANCELLED = "cancelled"
    TIMED_OUT = "timed_out"
    RESOURCE_EXHAUSTED = "resource_exhausted"
    FILESYSTEM = "filesystem"
    PROCESS = "process"
    GIT = "git"
    MCP = "mcp"
    PROTOCOL = "protocol"
    INTERNAL = "internal"


class ToolArtifactKind(str, Enum):
    FILE = "file"
    DIFF = "diff"
    REPORT = "report"
    TEST_RESULT = "test_result"
    PROCESS_OUTPUT = "process_output"
    METADATA = "metadata"


class ToolVersion(ToolProtocolModel):
    major: int = Field(ge=0, le=65_535)
    minor: int = Field(default=0, ge=0, le=65_535)
    patch: int = Field(default=0, ge=0, le=65_535)

    def __str__(self) -> str:
        return f"{self.major}.{self.minor}.{self.patch}"


class CapabilityScope(ToolProtocolModel):
    roots: tuple[str, ...] = ()
    patterns: tuple[str, ...] = ()
    affected_paths: tuple[str, ...] = ()
    executables: tuple[str, ...] = ()
    working_directories: tuple[str, ...] = ()
    network_allowed: bool = False
    network_destinations: tuple[str, ...] = ()
    environment_keys: tuple[str, ...] = ()
    git_operations: tuple[str, ...] = ()
    mcp_servers: tuple[str, ...] = ()

    @field_validator(
        "roots",
        "patterns",
        "affected_paths",
        "executables",
        "working_directories",
        "network_destinations",
        "environment_keys",
        "git_operations",
        "mcp_servers",
    )
    @classmethod
    def entries_are_bounded_and_unique(
        cls,
        values: tuple[str, ...],
    ) -> tuple[str, ...]:
        if len(values) > MAX_COLLECTION_ITEMS:
            raise ValueError("capability scopes support at most 256 entries per field")
        normalized: list[str] = []
        for raw in values:
            value = raw.strip()
            if not value:
                raise ValueError("capability scope entries must not be empty")
            if len(value) > 1_024:
                raise ValueError("capability scope entries must be at most 1024 chars")
            if value in {"*", "**", "all", "unrestricted"}:
                raise ValueError("unrestricted capability scopes are not supported")
            if "\x00" in value:
                raise ValueError("capability scope entries must not contain NUL")
            if value not in normalized:
                normalized.append(value)
        return tuple(normalized)

    @model_validator(mode="after")
    def destinations_require_network(self) -> "CapabilityScope":
        if self.network_destinations and not self.network_allowed:
            raise ValueError("network destinations require network_allowed=true")
        return self


class ToolCapability(ToolProtocolModel):
    name: ToolCapabilityName
    scope: CapabilityScope = Field(default_factory=CapabilityScope)


class ToolResourceBudget(ToolProtocolModel):
    wall_clock_seconds: float = Field(default=90.0, gt=0, le=86_400)
    stdout_bytes: int = Field(default=65_536, ge=0, le=16_777_216)
    stderr_bytes: int = Field(default=65_536, ge=0, le=16_777_216)
    combined_output_bytes: int = Field(default=131_072, ge=0, le=33_554_432)
    artifact_bytes: int = Field(default=5_242_880, ge=0, le=268_435_456)
    child_processes: int = Field(default=8, ge=0, le=256)
    concurrent_processes: int = Field(default=2, ge=1, le=64)
    invocations_per_task: int = Field(default=64, ge=1, le=10_000)
    invocations_per_run: int = Field(default=512, ge=1, le=100_000)
    file_mutations: int = Field(default=100, ge=0, le=100_000)
    total_written_bytes: int = Field(default=10_485_760, ge=0, le=1_073_741_824)
    maximum_file_bytes: int = Field(default=2_097_152, ge=0, le=268_435_456)
    memory_bytes: int | None = Field(default=None, ge=1_048_576)
    cpu_seconds: float | None = Field(default=None, gt=0, le=86_400)

    @model_validator(mode="after")
    def combined_output_is_consistent(self) -> "ToolResourceBudget":
        if self.combined_output_bytes > self.stdout_bytes + self.stderr_bytes:
            raise ValueError(
                "combined_output_bytes cannot exceed stdout_bytes + stderr_bytes"
            )
        if self.invocations_per_task > self.invocations_per_run:
            raise ValueError(
                "invocations_per_task cannot exceed invocations_per_run"
            )
        return self


class ToolLimitUsage(ToolProtocolModel):
    requested: int | float | None = None
    supported: bool
    enforced: bool
    observed: int | float | None = None
    diagnostic: str | None = Field(default=None, max_length=512)

    @model_validator(mode="after")
    def enforcement_requires_support(self) -> "ToolLimitUsage":
        if self.enforced and not self.supported:
            raise ValueError("an unsupported resource limit cannot be enforced")
        return self


class ToolResourceUsage(ToolProtocolModel):
    wall_clock_seconds: float = Field(default=0.0, ge=0)
    stdout_bytes: int = Field(default=0, ge=0)
    stderr_bytes: int = Field(default=0, ge=0)
    artifact_bytes: int = Field(default=0, ge=0)
    child_processes: int = Field(default=0, ge=0)
    file_mutations: int = Field(default=0, ge=0)
    written_bytes: int = Field(default=0, ge=0)
    memory_bytes: int | None = Field(default=None, ge=0)
    cpu_seconds: float | None = Field(default=None, ge=0)
    limits: dict[str, ToolLimitUsage] = Field(default_factory=dict)


class ToolInvocationContext(ToolProtocolModel):
    workspace_identity: str = Field(min_length=1, max_length=2_048)
    worktree_identity: str = Field(min_length=1, max_length=2_048)
    caller_role: str = Field(min_length=1, max_length=64)
    workspace_trusted: bool = False
    provider_consented: bool = False
    policy_context: dict[str, Any] = Field(default_factory=dict)

    @field_validator("policy_context")
    @classmethod
    def policy_context_is_json(cls, value: dict[str, Any]) -> dict[str, Any]:
        _require_json(value, "policy context", maximum_bytes=MAX_METADATA_BYTES)
        return value


class ToolDescriptor(ToolProtocolModel):
    name: str = Field(min_length=1, max_length=128)
    version: ToolVersion
    protocol_version: str = TOOL_PROTOCOL_VERSION
    description: str = Field(min_length=1, max_length=MAX_DESCRIPTOR_TEXT_CHARS)
    capabilities: tuple[ToolCapability, ...]
    argument_schema: dict[str, Any]
    output_schema: dict[str, Any]
    safety: ToolSafetyClassification = ToolSafetyClassification.SAFE
    idempotent: bool = True
    supports_cancellation: bool = False
    maximum_timeout_seconds: float = Field(default=90.0, gt=0, le=86_400)

    @field_validator("name")
    @classmethod
    def name_is_namespaced(cls, value: str) -> str:
        if not _NAME_PATTERN.fullmatch(value):
            raise ValueError("tool name must be a lowercase dotted identifier")
        return value

    @field_validator("capabilities")
    @classmethod
    def capabilities_are_unique(
        cls,
        capabilities: tuple[ToolCapability, ...],
    ) -> tuple[ToolCapability, ...]:
        if not capabilities:
            raise ValueError("tool descriptors must declare at least one capability")
        if len(capabilities) > 64:
            raise ValueError("tool descriptors support at most 64 capabilities")
        fingerprints = [capability.model_dump_json() for capability in capabilities]
        if len(fingerprints) != len(set(fingerprints)):
            raise ValueError("tool descriptor capabilities must be unique")
        return capabilities

    @field_validator("argument_schema", "output_schema")
    @classmethod
    def schemas_are_bounded(cls, value: dict[str, Any]) -> dict[str, Any]:
        _require_json(value, "tool schema")
        encoded = json.dumps(value, sort_keys=True, separators=(",", ":"))
        if len(encoded.encode("utf-8")) > MAX_SCHEMA_BYTES:
            raise ValueError("tool schemas must be at most 65536 bytes")
        return value


class ToolInvocation(ToolProtocolModel):
    invocation_id: str = Field(min_length=1, max_length=128)
    run_id: str = Field(min_length=1, max_length=128)
    task_id: str = Field(min_length=1, max_length=128)
    tool_name: str = Field(min_length=1, max_length=128)
    tool_version: ToolVersion
    protocol_version: str = TOOL_PROTOCOL_VERSION
    arguments: dict[str, Any] = Field(default_factory=dict)
    requested_capabilities: tuple[ToolCapability, ...]
    context: ToolInvocationContext
    timeout_seconds: float = Field(default=90.0, gt=0, le=86_400)
    resource_budget: ToolResourceBudget = Field(default_factory=ToolResourceBudget)
    cancellation_revision: int = Field(default=0, ge=0)
    invocation_revision: int = Field(default=1, ge=1)
    idempotency_key: str | None = Field(default=None, min_length=1, max_length=256)
    requested_at: datetime = Field(default_factory=utc_now)

    @field_validator("invocation_id", "run_id", "task_id")
    @classmethod
    def identifiers_are_safe(cls, value: str) -> str:
        if not _IDENTIFIER_PATTERN.fullmatch(value):
            raise ValueError("tool identifiers contain unsupported characters")
        return value

    @field_validator("tool_name")
    @classmethod
    def tool_name_is_valid(cls, value: str) -> str:
        if not _NAME_PATTERN.fullmatch(value):
            raise ValueError("tool name must be a lowercase dotted identifier")
        return value

    @field_validator("arguments")
    @classmethod
    def arguments_are_json(cls, value: dict[str, Any]) -> dict[str, Any]:
        _require_json(
            value,
            "tool arguments",
            maximum_bytes=MAX_INVOCATION_ARGUMENT_BYTES,
        )
        return value

    @field_validator("requested_capabilities")
    @classmethod
    def requested_capabilities_are_unique(
        cls,
        capabilities: tuple[ToolCapability, ...],
    ) -> tuple[ToolCapability, ...]:
        if not capabilities:
            raise ValueError("tool invocations must request at least one capability")
        if len(capabilities) > 64:
            raise ValueError("tool invocations support at most 64 capabilities")
        fingerprints = [capability.model_dump_json() for capability in capabilities]
        if len(fingerprints) != len(set(fingerprints)):
            raise ValueError("requested capabilities must be unique")
        return capabilities


class ToolPolicyDecision(ToolProtocolModel):
    outcome: ToolPolicyOutcome
    rule_id: str = Field(min_length=1, max_length=128)
    reason: str = Field(min_length=1, max_length=2_048)
    invocation_id: str = Field(min_length=1, max_length=128)
    invocation_revision: int = Field(ge=1)
    capability_fingerprint: str = Field(min_length=64, max_length=64)
    arguments_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    constraints: tuple[ToolCapability, ...] = ()
    evaluated_at: datetime = Field(default_factory=utc_now)
    safe_metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("safe_metadata")
    @classmethod
    def safe_metadata_is_json(cls, value: dict[str, Any]) -> dict[str, Any]:
        _require_json(value, "policy metadata", maximum_bytes=MAX_METADATA_BYTES)
        return value


class ToolApprovalRequest(ToolProtocolModel):
    approval_id: str = Field(min_length=1, max_length=128)
    invocation_id: str = Field(min_length=1, max_length=128)
    invocation_revision: int = Field(ge=1)
    run_id: str = Field(min_length=1, max_length=128)
    task_id: str = Field(min_length=1, max_length=128)
    tool_name: str = Field(min_length=1, max_length=128)
    tool_version: ToolVersion
    protocol_version: str = TOOL_PROTOCOL_VERSION
    requested_capabilities: tuple[ToolCapability, ...]
    capability_fingerprint: str = Field(min_length=64, max_length=64)
    arguments_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    workspace_identity: str = Field(min_length=1, max_length=2_048)
    worktree_identity: str = Field(min_length=1, max_length=2_048)
    affected_paths: tuple[str, ...] = ()
    executable: str | None = Field(default=None, max_length=1_024)
    arguments_summary: tuple[str, ...] = ()
    working_directory: str | None = Field(default=None, max_length=2_048)
    network_destination: str | None = Field(default=None, max_length=2_048)
    resource_budget: ToolResourceBudget
    policy_rule: str = Field(min_length=1, max_length=128)
    reason: str = Field(min_length=1, max_length=2_048)
    proposed_constraints: tuple[ToolCapability, ...] = ()
    expires_at: datetime | None = None
    created_at: datetime = Field(default_factory=utc_now)


class ToolCancellationSnapshot(ToolProtocolModel):
    requested: bool = False
    revision: int = Field(default=0, ge=0)
    requested_at: datetime | None = None
    signal_sent: bool = False
    acknowledged: bool = False
    process_terminated: bool = False
    operation_completed_after_request: bool = False
    cleanup_completed: bool = False
    reason: str | None = Field(default=None, max_length=1_024)


class ToolError(ToolProtocolModel):
    category: ToolErrorCategory
    code: str = Field(min_length=1, max_length=128)
    message: str = Field(min_length=1, max_length=2_048)
    retryable: bool = False
    safe_metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("safe_metadata")
    @classmethod
    def metadata_is_bounded(cls, value: dict[str, Any]) -> dict[str, Any]:
        _require_json(value, "tool error metadata", maximum_bytes=MAX_METADATA_BYTES)
        return value


class ToolArtifact(ToolProtocolModel):
    artifact_id: str = Field(min_length=1, max_length=128)
    kind: ToolArtifactKind
    relative_path: str | None = Field(default=None, max_length=2_048)
    media_type: str = Field(default="application/octet-stream", max_length=255)
    size_bytes: int = Field(ge=0)
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    truncated: bool = False
    safe_metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("safe_metadata")
    @classmethod
    def metadata_is_bounded(cls, value: dict[str, Any]) -> dict[str, Any]:
        _require_json(
            value,
            "tool artifact metadata",
            maximum_bytes=MAX_METADATA_BYTES,
        )
        return value


class ToolOutputChunk(ToolProtocolModel):
    sequence: int = Field(ge=1)
    stream: ToolOutputStream
    text: str = Field(max_length=MAX_INLINE_OUTPUT_CHARS)
    byte_count: int = Field(ge=0)
    truncated: bool = False
    created_at: datetime = Field(default_factory=utc_now)


class ToolResult(ToolProtocolModel):
    invocation_id: str = Field(min_length=1, max_length=128)
    invocation_revision: int = Field(ge=1)
    status: ToolInvocationStatus
    structured_output: dict[str, Any] = Field(default_factory=dict)
    stdout: str = Field(default="", max_length=MAX_INLINE_OUTPUT_CHARS)
    stderr: str = Field(default="", max_length=MAX_INLINE_OUTPUT_CHARS)
    stdout_truncated: bool = False
    stderr_truncated: bool = False
    artifacts: tuple[ToolArtifact, ...] = ()
    error: ToolError | None = None
    exit_code: int | None = None
    duration_seconds: float = Field(default=0.0, ge=0)
    timed_out: bool = False
    cancellation: ToolCancellationSnapshot = Field(
        default_factory=ToolCancellationSnapshot
    )
    resource_usage: ToolResourceUsage = Field(default_factory=ToolResourceUsage)
    policy_decision: ToolPolicyDecision
    approval_id: str | None = Field(default=None, max_length=128)
    safe_diagnostic_metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("structured_output")
    @classmethod
    def structured_output_is_bounded(cls, value: dict[str, Any]) -> dict[str, Any]:
        _require_json(
            value,
            "structured tool output",
            maximum_bytes=MAX_STRUCTURED_OUTPUT_BYTES,
        )
        return value

    @field_validator("safe_diagnostic_metadata")
    @classmethod
    def result_metadata_is_bounded(cls, value: dict[str, Any]) -> dict[str, Any]:
        _require_json(
            value,
            "tool result metadata",
            maximum_bytes=MAX_METADATA_BYTES,
        )
        return value

    @field_validator("artifacts")
    @classmethod
    def artifacts_are_bounded(
        cls,
        value: tuple[ToolArtifact, ...],
    ) -> tuple[ToolArtifact, ...]:
        if len(value) > MAX_COLLECTION_ITEMS:
            raise ValueError("tool results support at most 256 artifacts")
        return value

    @model_validator(mode="after")
    def status_flags_are_consistent(self) -> "ToolResult":
        if self.status == ToolInvocationStatus.TIMED_OUT and not self.timed_out:
            raise ValueError("timed_out results must set timed_out=true")
        if self.timed_out and self.status != ToolInvocationStatus.TIMED_OUT:
            raise ValueError("timed_out=true requires timed_out status")
        if self.status == ToolInvocationStatus.CANCELLED and not self.cancellation.requested:
            raise ValueError("cancelled results require a cancellation request")
        if self.status in {
            ToolInvocationStatus.FAILED,
            ToolInvocationStatus.DENIED,
            ToolInvocationStatus.TIMED_OUT,
        } and self.error is None:
            raise ValueError("failed, denied, and timed out results require an error")
        return self


class ToolAuditRecord(ToolProtocolModel):
    audit_id: str = Field(min_length=1, max_length=128)
    invocation_id: str = Field(min_length=1, max_length=128)
    invocation_revision: int = Field(ge=1)
    run_id: str = Field(min_length=1, max_length=128)
    task_id: str = Field(min_length=1, max_length=128)
    tool_name: str = Field(min_length=1, max_length=128)
    tool_version: ToolVersion
    protocol_version: str = TOOL_PROTOCOL_VERSION
    caller_role: str = Field(min_length=1, max_length=64)
    capabilities: tuple[ToolCapability, ...]
    policy_decision: ToolPolicyDecision
    approval_id: str | None = Field(default=None, max_length=128)
    arguments_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    affected_resource_hashes: dict[str, str] = Field(default_factory=dict)
    started_at: datetime | None = None
    completed_at: datetime | None = None
    cancellation: ToolCancellationSnapshot = Field(
        default_factory=ToolCancellationSnapshot
    )
    timed_out: bool = False
    resource_usage: ToolResourceUsage = Field(default_factory=ToolResourceUsage)
    artifacts: tuple[ToolArtifact, ...] = ()
    outcome: ToolInvocationStatus
    error_category: ToolErrorCategory | None = None
    created_at: datetime = Field(default_factory=utc_now)

    @field_validator("affected_resource_hashes")
    @classmethod
    def resource_hashes_are_sha256(cls, value: dict[str, str]) -> dict[str, str]:
        if len(value) > MAX_COLLECTION_ITEMS:
            raise ValueError("tool audits support at most 256 resource hashes")
        for digest in value.values():
            if not re.fullmatch(r"[a-f0-9]{64}", digest):
                raise ValueError("affected resource hashes must be SHA-256 digests")
        return value


def _require_json(
    value: Any,
    description: str,
    *,
    maximum_bytes: int | None = None,
) -> None:
    try:
        encoded = json.dumps(
            value,
            allow_nan=False,
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{description} must be JSON serializable") from exc
    if maximum_bytes is not None and len(encoded) > maximum_bytes:
        raise ValueError(
            f"{description} must be at most {maximum_bytes} encoded bytes"
        )
