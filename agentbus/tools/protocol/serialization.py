from __future__ import annotations

import hashlib
import json
from typing import Any, TypeVar

from pydantic import BaseModel

from agentbus.security.redaction import redact_text, sanitize_json
from agentbus.tools.protocol.models import (
    ToolCapability,
    ToolOutputChunk,
    ToolOutputStream,
    ToolProtocolModel,
)


ProtocolModelT = TypeVar("ProtocolModelT", bound=ToolProtocolModel)


def canonical_json(value: Any) -> str:
    normalized = _json_value(value)
    return json.dumps(
        normalized,
        allow_nan=False,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    )


def sha256_json(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def idempotency_key_sha256(value: str | None) -> str | None:
    if value is None:
        return None
    encoded = ("agentbus-tool-idempotency-v1\0" + value).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def capability_fingerprint(capabilities: tuple[ToolCapability, ...]) -> str:
    serialized = sorted(
        [capability.model_dump(mode="json") for capability in capabilities],
        key=canonical_json,
    )
    return sha256_json(serialized)


def serialize_protocol_model(model: ToolProtocolModel, *, safe: bool = True) -> str:
    payload: Any = model.model_dump(mode="json")
    if safe:
        payload = sanitize_json(payload)
    return canonical_json(payload)


def deserialize_protocol_model(
    payload: str | bytes,
    model_type: type[ProtocolModelT],
) -> ProtocolModelT:
    return model_type.model_validate_json(payload)


def safe_protocol_dict(model: ToolProtocolModel) -> dict[str, Any]:
    payload = sanitize_json(model.model_dump(mode="json"))
    if not isinstance(payload, dict):
        raise TypeError("protocol model serialization must produce an object")
    return payload


def bound_text(value: str | None, maximum_bytes: int) -> tuple[str, int, bool]:
    if maximum_bytes < 0:
        raise ValueError("maximum_bytes must be non-negative")
    redacted = redact_text(value or "", max_chars=max(maximum_bytes * 2, 1)) or ""
    encoded = redacted.encode("utf-8")
    if len(encoded) <= maximum_bytes:
        return redacted, len(encoded), False
    bounded = encoded[:maximum_bytes]
    while bounded:
        try:
            text = bounded.decode("utf-8")
            return text, len(bounded), True
        except UnicodeDecodeError:
            bounded = bounded[:-1]
    return "", 0, True


def output_chunk(
    *,
    sequence: int,
    stream: ToolOutputStream,
    text: str,
    maximum_bytes: int,
) -> ToolOutputChunk:
    bounded, byte_count, truncated = bound_text(text, maximum_bytes)
    return ToolOutputChunk(
        sequence=sequence,
        stream=stream,
        text=bounded,
        byte_count=byte_count,
        truncated=truncated,
    )


def _json_value(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if isinstance(value, tuple):
        return [_json_value(item) for item in value]
    if isinstance(value, list):
        return [_json_value(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _json_value(item) for key, item in value.items()}
    return value
