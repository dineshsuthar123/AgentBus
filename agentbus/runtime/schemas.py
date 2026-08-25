import json
from pathlib import PurePosixPath, PureWindowsPath
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from agentbus.tools.protocol import ToolCapabilityName, ToolVersion


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
