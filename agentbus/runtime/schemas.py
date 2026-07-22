import json
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from agentbus.tools.protocol import ToolCapabilityName


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
