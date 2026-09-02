import hashlib
import json
from datetime import datetime
from pathlib import PurePosixPath, PureWindowsPath
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from agentbus.tools.protocol import ToolCapabilityName, ToolVersion


class RepositoryBaseline(BaseModel):
    """Bounded identity for an immutable repository source state."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = 1
    head_commit: str | None = Field(
        default=None,
        pattern=r"^(?:[a-f0-9]{40}|[a-f0-9]{64})$",
    )
    tree_id: str | None = Field(
        default=None,
        pattern=r"^(?:[a-f0-9]{40}|[a-f0-9]{64})$",
    )
    worktree_snapshot: dict[str, str] = Field(default_factory=dict)
    review_source_snapshot: dict[str, str] = Field(default_factory=dict)
    review_files: list[str] = Field(default_factory=list, max_length=512)
    state_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    identity_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")

    @field_validator("worktree_snapshot", "review_source_snapshot")
    @classmethod
    def snapshots_are_bounded(
        cls,
        value: dict[str, str],
    ) -> dict[str, str]:
        return _bounded_worktree_snapshot(value)

    @field_validator("review_files")
    @classmethod
    def review_paths_are_bounded(cls, value: list[str]) -> list[str]:
        normalized = _bounded_worktree_snapshot(
            {str(path): "deleted" for path in value}
        )
        return list(normalized)

    @model_validator(mode="after")
    def identity_matches_content(self) -> "RepositoryBaseline":
        if self.review_files != sorted(self.review_source_snapshot):
            raise ValueError("repository baseline review paths are inconsistent")
        payload = self.model_dump(mode="json", exclude={"identity_sha256"})
        if self.identity_sha256 != _json_sha256(payload):
            raise ValueError("repository baseline identity does not match its content")
        return self


class TaskRepositoryBaselines(BaseModel):
    """Distinct durable task and attempt baselines used across retries."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = 1
    task: RepositoryBaseline
    attempt: RepositoryBaseline
    task_started_attempt_id: str = Field(min_length=1, max_length=128)
    task_started_attempt_number: int = Field(ge=1)
    attempt_id: str = Field(min_length=1, max_length=128)
    attempt_number: int = Field(ge=1)
    retry_workspace: Literal[
        "initial_attempt",
        "retained_cumulative_workspace",
        "restored_to_task_baseline",
    ]


class RetryDiagnostics(BaseModel):
    """Bounded untrusted diagnostics from one retryable task failure."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal[
        "verifier",
        "reviewer",
        "model_provider",
        "tool",
        "command",
        "interrupted",
        "other",
    ]
    summary: str = Field(default="", max_length=4_096)
    command: list[str] = Field(default_factory=list, max_length=32)
    exit_status: int | None = None
    stdout: str = Field(default="", max_length=8_192)
    stderr: str = Field(default="", max_length=8_192)
    stdout_truncated: bool = False
    stderr_truncated: bool = False
    failing_tests: list[str] = Field(default_factory=list, max_length=32)
    exception_details: list[str] = Field(default_factory=list, max_length=32)
    reviewer_issues: list[str] = Field(default_factory=list, max_length=32)
    required_fixes: list[str] = Field(default_factory=list, max_length=32)

    @field_validator(
        "command",
        "failing_tests",
        "exception_details",
        "reviewer_issues",
        "required_fixes",
    )
    @classmethod
    def diagnostic_items_are_bounded(cls, value: list[str]) -> list[str]:
        if any(not item or len(item) > 1_024 for item in value):
            raise ValueError("retry diagnostic entries must be nonempty and bounded")
        return value

    @model_validator(mode="after")
    def diagnostics_are_bounded(self) -> "RetryDiagnostics":
        encoded = _encoded_json(self.model_dump(mode="json"))
        if len(encoded) > 32_768:
            raise ValueError("retry diagnostics must be at most 32768 bytes")
        return self


class RetryEvidence(BaseModel):
    """Immutable source-attempt evidence bound to the failed candidate."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = 1
    source_attempt_id: str = Field(min_length=1, max_length=128)
    source_attempt_number: int = Field(ge=1)
    failure_category: str = Field(min_length=1, max_length=64)
    candidate_identity_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    candidate_tree_id: str | None = Field(
        default=None,
        pattern=r"^(?:[a-f0-9]{40}|[a-f0-9]{64})$",
    )
    candidate_source_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    retained_changed_files: list[str] = Field(default_factory=list, max_length=512)
    diagnostics: RetryDiagnostics
    diagnostics_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    created_at: str = Field(min_length=1, max_length=64)
    evidence_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")

    @field_validator("failure_category")
    @classmethod
    def failure_category_is_known(cls, value: str) -> str:
        allowed = {
            "command_failure",
            "interrupted",
            "model_output_error",
            "model_provider_error",
            "model_transport_error",
            "reviewer_rejection",
            "tool_validation_error",
            "unknown",
            "verifier_failure",
        }
        if value not in allowed:
            raise ValueError("failure category does not support corrective retry evidence")
        return value

    @field_validator("retained_changed_files")
    @classmethod
    def changed_paths_are_bounded(cls, value: list[str]) -> list[str]:
        normalized = _bounded_worktree_snapshot(
            {str(path): "deleted" for path in value}
        )
        return list(normalized)

    @field_validator("created_at")
    @classmethod
    def created_at_is_timezone_aware(cls, value: str) -> str:
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError("retry evidence timestamp must be ISO-8601") from exc
        if parsed.tzinfo is None:
            raise ValueError("retry evidence timestamp must include a timezone")
        return value

    @model_validator(mode="after")
    def evidence_identity_matches_content(self) -> "RetryEvidence":
        diagnostics = self.diagnostics.model_dump(mode="json")
        if self.diagnostics_sha256 != _json_sha256(diagnostics):
            raise ValueError("retry diagnostic identity does not match its content")
        payload = self.model_dump(mode="json", exclude={"evidence_sha256"})
        if self.evidence_sha256 != _json_sha256(payload):
            raise ValueError("retry evidence identity does not match its content")
        if len(_encoded_json(self.model_dump(mode="json"))) > 48_000:
            raise ValueError("retry evidence must be at most 48000 bytes")
        return self


class RetryFeedback(BaseModel):
    """Destination-attempt binding for validated corrective evidence."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = 1
    source_evidence: RetryEvidence
    destination_attempt_id: str = Field(min_length=1, max_length=128)
    destination_attempt_number: int = Field(ge=2)
    source_disposition: Literal[
        "retained_candidate",
        "restored_to_task_baseline",
    ]
    active_candidate_identity_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    active_candidate_tree_id: str | None = Field(
        default=None,
        pattern=r"^(?:[a-f0-9]{40}|[a-f0-9]{64})$",
    )
    active_candidate_source_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    active_changed_files: list[str] = Field(default_factory=list, max_length=512)
    mutations_retained: bool
    context_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")

    @field_validator("active_changed_files")
    @classmethod
    def active_paths_are_bounded(cls, value: list[str]) -> list[str]:
        normalized = _bounded_worktree_snapshot(
            {str(path): "deleted" for path in value}
        )
        return list(normalized)

    @model_validator(mode="after")
    def feedback_identity_matches_content(self) -> "RetryFeedback":
        if self.destination_attempt_number <= self.source_evidence.source_attempt_number:
            raise ValueError("retry destination must follow its source attempt")
        if self.source_disposition == "retained_candidate":
            expected = (
                self.source_evidence.candidate_identity_sha256,
                self.source_evidence.candidate_tree_id,
                self.source_evidence.candidate_source_sha256,
                self.source_evidence.retained_changed_files,
            )
            current = (
                self.active_candidate_identity_sha256,
                self.active_candidate_tree_id,
                self.active_candidate_source_sha256,
                self.active_changed_files,
            )
            if current != expected or not self.mutations_retained:
                raise ValueError("retained retry feedback does not match its source")
        elif self.mutations_retained:
            raise ValueError("restored retry feedback cannot claim retained mutations")
        payload = self.model_dump(mode="json", exclude={"context_sha256"})
        if self.context_sha256 != _json_sha256(payload):
            raise ValueError("retry feedback identity does not match its content")
        if len(_encoded_json(self.model_dump(mode="json"))) > 56_000:
            raise ValueError("retry feedback must be at most 56000 bytes")
        return self


class ModelToolCall(BaseModel):
    """Bounded model-facing request; runtime derives the authoritative scopes."""

    model_config = ConfigDict(extra="forbid")

    tool_name: str = Field(min_length=1, max_length=128)
    arguments: dict[str, Any] = Field(default_factory=dict)
    expected_capabilities: tuple[ToolCapabilityName, ...]
    timeout_seconds: float | None = Field(default=None, gt=0, le=86_400)
    invocation_revision: int = Field(default=1, ge=1)
    idempotency_key: str = Field(min_length=1, max_length=256)

    @field_validator("arguments")
    @classmethod
    def arguments_are_bounded_json(cls, value: dict[str, Any]) -> dict[str, Any]:
        try:
            encoded = json.dumps(
                value,
                allow_nan=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        except (TypeError, ValueError) as exc:
            raise ValueError("tool arguments must be JSON serializable") from exc
        if len(encoded) > 1_048_576:
            raise ValueError("tool arguments must be at most 1048576 bytes")
        return value

    @field_validator("expected_capabilities")
    @classmethod
    def capabilities_are_explicit_and_unique(
        cls,
        value: tuple[ToolCapabilityName, ...],
    ) -> tuple[ToolCapabilityName, ...]:
        if not value:
            raise ValueError("tool calls require expected capabilities")
        if len(value) > 64 or len(value) != len(set(value)):
            raise ValueError("expected capabilities must be unique and bounded")
        return value


class AgentAction(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: Literal["tool_call", "finish"]
    tool_call: ModelToolCall | None = None
    summary: str | None = None

    @model_validator(mode="after")
    def validate_required_fields(self):
        if self.action == "tool_call" and self.tool_call is None:
            raise ValueError("tool_call action requires a structured tool call")
        if self.action == "finish" and self.tool_call is not None:
            raise ValueError("finish action must not include a tool call")
        if self.action == "finish" and not self.summary:
            raise ValueError("finish requires summary")
        return self


class AgentLoopContinuation(BaseModel):
    """Bounded observable loop state required for exact tool continuation."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = 1
    run_id: str = Field(min_length=1, max_length=128)
    task_id: str = Field(min_length=1, max_length=128)
    attempt_id: str | None = Field(default=None, max_length=128)
    attempt_number: int | None = Field(default=None, ge=1)
    user_task_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    step: int = Field(ge=1, le=10_000)
    maximum_steps: int = Field(ge=1, le=10_000)
    history: str = Field(default="", max_length=20_000)
    worktree_snapshot: dict[str, str] = Field(default_factory=dict)
    repository_baselines: TaskRepositoryBaselines | None = None
    pending_action: AgentAction
    pending_action_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    approval_id: str = Field(min_length=1, max_length=128)
    approval_request_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    invocation_id: str = Field(min_length=1, max_length=128)
    invocation_revision: int = Field(ge=1)
    invocation_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    operation_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    arguments_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    capability_fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")
    policy_identity_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    tool_name: str = Field(min_length=1, max_length=128)
    tool_version: ToolVersion

    @field_validator("worktree_snapshot")
    @classmethod
    def worktree_snapshot_is_bounded(
        cls,
        value: dict[str, str],
    ) -> dict[str, str]:
        return _bounded_worktree_snapshot(value)

    @model_validator(mode="after")
    def continuation_is_bounded(self) -> "AgentLoopContinuation":
        if (self.attempt_id is None) != (self.attempt_number is None):
            raise ValueError(
                "continuation attempt ID and attempt number must be present together"
            )
        if self.pending_action.action != "tool_call":
            raise ValueError("loop continuation requires a pending tool action")
        call = self.pending_action.tool_call
        if call is None or call.tool_name != self.tool_name:
            raise ValueError("continuation tool identity does not match its action")
        if call.invocation_revision != self.invocation_revision:
            raise ValueError("continuation invocation revision does not match")
        encoded = json.dumps(
            self.model_dump(mode="json"),
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        if len(encoded) > 1_250_000:
            raise ValueError("loop continuation must be at most 1250000 bytes")
        return self


class VerifierContinuation(BaseModel):
    """Bounded task-stage state for an exact managed verifier approval."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = 1
    stage: Literal["verifier"] = "verifier"
    run_id: str = Field(min_length=1, max_length=128)
    task_id: str = Field(min_length=1, max_length=128)
    attempt_id: str = Field(min_length=1, max_length=128)
    attempt_number: int = Field(ge=1)
    user_task_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    verifier_invocation_key: str = Field(min_length=1, max_length=256)
    coder_summary: str = Field(default="", max_length=20_000)
    worktree_snapshot: dict[str, str] = Field(default_factory=dict)
    source_snapshot: dict[str, str] = Field(default_factory=dict)
    repository_baselines: TaskRepositoryBaselines | None = None
    command_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    approval_id: str = Field(min_length=1, max_length=128)
    approval_request_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    invocation_id: str = Field(min_length=1, max_length=128)
    invocation_revision: int = Field(ge=1)
    invocation_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    operation_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    arguments_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    capability_fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")
    policy_identity_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    tool_name: Literal["test.execute"]
    tool_version: ToolVersion

    @field_validator("worktree_snapshot", "source_snapshot")
    @classmethod
    def snapshots_are_bounded(
        cls,
        value: dict[str, str],
    ) -> dict[str, str]:
        return _bounded_worktree_snapshot(value)

    @model_validator(mode="after")
    def continuation_is_bounded(self) -> "VerifierContinuation":
        encoded = json.dumps(
            self.model_dump(mode="json"),
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        if len(encoded) > 750_000:
            raise ValueError("verifier continuation must be at most 750000 bytes")
        return self


def _bounded_worktree_snapshot(value: dict[str, str]) -> dict[str, str]:
    if len(value) > 512:
        raise ValueError("worktree snapshot must contain at most 512 paths")
    normalized: dict[str, str] = {}
    for raw_path, raw_identity in value.items():
        path = str(raw_path).replace("\\", "/")
        if (
            not path
            or len(path) > 512
            or PurePosixPath(path).is_absolute()
            or PureWindowsPath(path).is_absolute()
            or ".." in PurePosixPath(path).parts
        ):
            raise ValueError("worktree snapshot paths must be bounded and relative")
        identity = str(raw_identity)
        if identity not in {"deleted", "directory"} and not (
            len(identity) == 64
            and all(character in "0123456789abcdef" for character in identity)
        ):
            raise ValueError("worktree snapshot identities must be SHA-256 values")
        normalized[path] = identity
    return dict(sorted(normalized.items()))


def _json_sha256(value: object) -> str:
    encoded = _encoded_json(value)
    return hashlib.sha256(encoded).hexdigest()


def _encoded_json(value: object) -> bytes:
    return json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
