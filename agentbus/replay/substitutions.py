from __future__ import annotations

import hashlib
from collections import defaultdict, deque
from collections.abc import Iterable
from typing import Any

from pydantic import BaseModel, Field, field_validator, model_validator

from agentbus.models.base import validate_json_schema
from agentbus.models.types import ModelResult, ModelRole, ModelUsage
from agentbus.replay.errors import (
    ReplayInputUnavailableError,
    ReplayIncompatibleError,
)
from agentbus.trace.blobs import RetentionClass
from agentbus.trace.models import (
    ReplayMode,
    Sha256Digest,
    Trace,
    TraceModel,
    TraceOutput,
)
from agentbus.trace.redaction import canonical_json_bytes, sanitize_document, sanitize_text
from agentbus.trace.storage import ContentAddressedStore

MODEL_ENVELOPE_MEDIA_TYPE = "application/vnd.agentbus.model-envelope+json"
MODEL_ENVELOPE_VERSION = 1


class CapturedModelEnvelope(TraceModel):
    envelope_version: int = MODEL_ENVELOPE_VERSION
    request_id: str = Field(min_length=1, max_length=256)
    role: ModelRole
    provider: str = Field(min_length=1, max_length=128)
    model: str = Field(min_length=1, max_length=256)
    value: str | dict[str, Any]
    value_sha256: Sha256Digest
    request_fingerprint: Sha256Digest
    schema_name: str | None = Field(default=None, max_length=256)
    usage: ModelUsage = Field(default_factory=ModelUsage)
    finish_status: str | None = Field(default=None, max_length=128)

    @field_validator("envelope_version")
    @classmethod
    def version_is_supported(cls, value: int) -> int:
        if value != MODEL_ENVELOPE_VERSION:
            raise ValueError(f"unsupported model envelope version: {value}")
        return value

    @field_validator("value")
    @classmethod
    def value_is_sanitized(cls, value: str | dict[str, Any]):
        return sanitize_document(value).value

    @model_validator(mode="after")
    def value_hash_matches(self) -> "CapturedModelEnvelope":
        actual = hashlib.sha256(canonical_json_bytes(self.value)).hexdigest()
        if actual != self.value_sha256:
            raise ValueError("captured model value hash does not match")
        return self

    @classmethod
    def from_result(
        cls,
        result: ModelResult,
        *,
        prompt: str,
        request_id: str,
        schema_name: str | None = None,
    ) -> "CapturedModelEnvelope":
        safe_value = sanitize_document(result.value).value
        return cls(
            request_id=request_id,
            role=result.role,
            provider=result.provider,
            model=result.model,
            value=safe_value,
            value_sha256=hashlib.sha256(
                canonical_json_bytes(safe_value)
            ).hexdigest(),
            request_fingerprint=prompt_fingerprint(prompt),
            schema_name=schema_name,
            usage=result.usage,
            finish_status=result.finish_status,
        )


def capture_model_envelope(
    store: ContentAddressedStore,
    result: ModelResult,
    *,
    prompt: str,
    producing_span_id: str,
    reference_id: str,
    schema_name: str | None = None,
    retention_class: RetentionClass = RetentionClass.RUN,
) -> TraceOutput:
    envelope = CapturedModelEnvelope.from_result(
        result,
        prompt=prompt,
        request_id=reference_id,
        schema_name=schema_name,
    )
    metadata = store.put_json(
        envelope.model_dump(mode="json"),
        producing_span_id=producing_span_id,
        media_type=MODEL_ENVELOPE_MEDIA_TYPE,
        retention_class=retention_class,
    )
    return store.reference_output(
        metadata,
        reference_id=reference_id,
        name=f"model.response.{result.role.value}",
        replayable=True,
    )


class ModelSubstitutionCatalog:
    def __init__(self, envelopes: Iterable[CapturedModelEnvelope]):
        self._by_role: dict[ModelRole, deque[CapturedModelEnvelope]] = defaultdict(
            deque
        )
        for envelope in envelopes:
            self._by_role[envelope.role].append(envelope)

    @classmethod
    def from_trace(
        cls,
        trace: Trace,
        store: ContentAddressedStore,
    ) -> "ModelSubstitutionCatalog":
        envelopes: list[CapturedModelEnvelope] = []
        for span in sorted(trace.spans, key=lambda item: item.sequence):
            for reference in span.output_references:
                if reference.media_type != MODEL_ENVELOPE_MEDIA_TYPE:
                    continue
                stored = store.get(reference.sha256)
                if stored.metadata.media_type != MODEL_ENVELOPE_MEDIA_TYPE:
                    raise ReplayIncompatibleError(
                        "Captured model reference media type does not match storage."
                    )
                try:
                    envelope = CapturedModelEnvelope.model_validate_json(
                        stored.data
                    )
                except Exception as exc:
                    raise ReplayIncompatibleError(
                        "Captured model envelope is invalid or incompatible."
                    ) from exc
                envelopes.append(envelope)
        return cls(envelopes)

    def take(self, role: ModelRole) -> CapturedModelEnvelope:
        queue = self._by_role.get(role)
        if not queue:
            raise ReplayInputUnavailableError(
                f"No captured model response remains for role '{role.value}'."
            )
        return queue.popleft()

    def remaining(self) -> dict[str, int]:
        return {
            role.value: len(values)
            for role, values in self._by_role.items()
            if values
        }


class CapturedRoutedModel:
    """Agent-compatible model facade that can never call a live provider."""

    def __init__(
        self,
        catalog: ModelSubstitutionCatalog,
        role: ModelRole | str,
        *,
        mode: ReplayMode = ReplayMode.OFFLINE,
    ):
        self.catalog = catalog
        self.role = ModelRole(role)
        self.mode = mode
        self.last_result: ModelResult | None = None
        self._results: list[ModelResult] = []

    def generate_json(
        self,
        prompt: str,
        *,
        schema: type[BaseModel] | dict[str, Any] | None = None,
        **_: Any,
    ) -> dict[str, Any]:
        envelope = self._take(prompt)
        if not isinstance(envelope.value, dict):
            raise ReplayIncompatibleError(
                "Captured model response is text, not a JSON object."
            )
        value = envelope.value
        if isinstance(schema, type) and issubclass(schema, BaseModel):
            value = schema.model_validate(value).model_dump(mode="json")
        elif isinstance(schema, dict):
            validate_json_schema(
                value,
                schema,
                provider="replay",
                model=envelope.model,
                request_id=envelope.request_id,
            )
        self._record(envelope, value)
        return value

    def generate_text(self, prompt: str, **_: Any) -> str:
        envelope = self._take(prompt)
        if not isinstance(envelope.value, str):
            raise ReplayIncompatibleError(
                "Captured model response is JSON, not text."
            )
        self._record(envelope, envelope.value)
        return envelope.value

    def drain_results(self) -> list[ModelResult]:
        results = list(self._results)
        self._results.clear()
        return results

    def _take(self, prompt: str) -> CapturedModelEnvelope:
        envelope = self.catalog.take(self.role)
        if (
            self.mode == ReplayMode.STRICT
            and envelope.request_fingerprint != prompt_fingerprint(prompt)
        ):
            raise ReplayIncompatibleError(
                "Strict replay prompt fingerprint does not match the capture."
            )
        return envelope

    def _record(
        self,
        envelope: CapturedModelEnvelope,
        value: str | dict[str, Any],
    ) -> None:
        self.last_result = ModelResult(
            value=value,
            provider="replay",
            model=envelope.model,
            role=envelope.role,
            request_id=envelope.request_id,
            usage=envelope.usage,
            finish_status=envelope.finish_status,
            provider_metadata={
                "source_provider": envelope.provider,
                "providerless": True,
                "capture_value_sha256": envelope.value_sha256,
            },
        )
        self._results.append(self.last_result)


def prompt_fingerprint(prompt: str) -> str:
    sanitized = sanitize_text(prompt)
    return hashlib.sha256(sanitized.canonical_bytes).hexdigest()


__all__ = [
    "MODEL_ENVELOPE_MEDIA_TYPE",
    "MODEL_ENVELOPE_VERSION",
    "CapturedModelEnvelope",
    "CapturedRoutedModel",
    "ModelSubstitutionCatalog",
    "capture_model_envelope",
    "prompt_fingerprint",
]
