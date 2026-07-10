from __future__ import annotations

from typing import Any, Protocol

from pydantic import BaseModel

from agentbus.models.errors import (
    ModelConfigurationError,
    ModelSchemaValidationError,
)
from agentbus.models.types import ModelResult


class ModelProvider(Protocol):
    @property
    def provider_name(self) -> str:
        ...

    @property
    def model_name(self) -> str:
        ...

    def generate_text(
        self,
        prompt: str,
        *,
        system_prompt: str | None = None,
        timeout_seconds: float | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> ModelResult:
        ...

    def generate_json(
        self,
        prompt: str,
        *,
        schema: type[BaseModel] | dict[str, Any] | None = None,
        system_prompt: str | None = None,
        timeout_seconds: float | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> ModelResult:
        ...


def validate_json_schema(
    value: dict[str, Any],
    schema: dict[str, Any],
    *,
    provider: str,
    model: str,
    request_id: str | None = None,
) -> None:
    """Validate provider output locally without importing jsonschema at startup."""
    try:
        import jsonschema
    except ImportError as exc:
        raise ModelConfigurationError(
            "The 'jsonschema' package is required for dictionary schemas.",
            provider=provider,
            model=model,
        ) from exc
    try:
        jsonschema.validators.validator_for(schema).check_schema(schema)
        jsonschema.validate(instance=value, schema=schema)
    except jsonschema.SchemaError as exc:
        raise ModelConfigurationError(
            "The supplied JSON Schema is invalid.",
            provider=provider,
            model=model,
            request_id=request_id,
        ) from exc
    except jsonschema.ValidationError as exc:
        raise ModelSchemaValidationError(
            f"{provider.title()} output failed local JSON Schema validation.",
            provider=provider,
            model=model,
            request_id=request_id,
        ) from exc
