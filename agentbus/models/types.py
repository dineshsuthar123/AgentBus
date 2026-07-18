from __future__ import annotations

import json
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from agentbus.security.redaction import sanitize_json


class ModelRole(str, Enum):
    DEFAULT = "default"
    PLANNER = "planner"
    CODER = "coder"
    REVIEWER = "reviewer"
    SUMMARIZER = "summarizer"


class ModelUsage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    total_tokens: int | None = Field(default=None, ge=0)
    cached_tokens: int | None = Field(default=None, ge=0)

    def add(self, other: "ModelUsage") -> "ModelUsage":
        return ModelUsage(
            input_tokens=_sum_optional(self.input_tokens, other.input_tokens),
            output_tokens=_sum_optional(self.output_tokens, other.output_tokens),
            total_tokens=_sum_optional(self.total_tokens, other.total_tokens),
            cached_tokens=_sum_optional(self.cached_tokens, other.cached_tokens),
        )


class ModelResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    value: str | dict[str, Any]
    provider: str
    model: str
    role: ModelRole = ModelRole.DEFAULT
    request_id: str | None = None
    usage: ModelUsage = Field(default_factory=ModelUsage)
    finish_status: str | None = None
    latency_seconds: float | None = Field(default=None, ge=0)
    retry_count: int = Field(default=0, ge=0)
    fallback_used: bool = False
    original_provider: str | None = None
    original_error_category: str | None = None
    cancellation_requested: bool = False
    cancellation_acknowledged: bool = False
    cancellation_supported: bool = False
    completed_after_cancellation: bool = False
    provider_metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("provider_metadata")
    @classmethod
    def metadata_is_safe_json(cls, value: dict[str, Any]) -> dict[str, Any]:
        value = sanitize_json(value)
        try:
            json.dumps(value, allow_nan=False)
        except (TypeError, ValueError) as exc:
            raise ValueError("provider metadata must be JSON serializable") from exc
        return value

    def json_value(self) -> dict[str, Any]:
        if not isinstance(self.value, dict):
            raise TypeError("Model result does not contain a JSON object.")
        return self.value

    def text_value(self) -> str:
        if not isinstance(self.value, str):
            raise TypeError("Model result does not contain text.")
        return self.value

    def event_metadata(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "model": self.model,
            "role": self.role.value,
            "request_id": self.request_id,
            "usage": self.usage.model_dump(mode="json"),
            "finish_status": self.finish_status,
            "latency_seconds": self.latency_seconds,
            "retry_count": self.retry_count,
            "fallback_used": self.fallback_used,
            "original_provider": self.original_provider,
            "original_error_category": self.original_error_category,
            "cancellation_requested": self.cancellation_requested,
            "cancellation_acknowledged": self.cancellation_acknowledged,
            "cancellation_supported": self.cancellation_supported,
            "completed_after_cancellation": self.completed_after_cancellation,
            "provider_metadata": self.provider_metadata,
        }


class ModelRoute(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: str
    model: str
    role: ModelRole
    timeout_seconds: float = Field(gt=0)
    max_retries: int = Field(ge=0)
    fallback_provider: str | None = None
    fallback_enabled: bool = False


def _sum_optional(left: int | None, right: int | None) -> int | None:
    if left is None and right is None:
        return None
    return (left or 0) + (right or 0)
