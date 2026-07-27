from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from pydantic import Field

from agentbus.security.redaction import is_sensitive_key, redact_text
from agentbus.trace.models import TraceModel

REDACTED = "[REDACTED]"
PRIVATE_PATH = "[PRIVATE_PATH]"
HIDDEN_CONTENT = "[HIDDEN_CONTENT]"
TRUNCATED = "[TRUNCATED]"

DEFAULT_MAX_TEXT_CHARS = 20_000
DEFAULT_MAX_COLLECTION_ITEMS = 10_000
DEFAULT_MAX_NODES = 100_000
DEFAULT_MAX_DEPTH = 12

_HIDDEN_KEYS = {
    "chain_of_thought",
    "hidden_prompt",
    "hidden_reasoning",
    "internal_prompt",
    "raw_prompt",
    "reasoning_content",
    "system_prompt",
}
_PRIVATE_KEY_PATTERN = re.compile(
    r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----",
    re.IGNORECASE,
)
_BEARER_PATTERN = re.compile(
    r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]{8,}",
)
_SECRET_ASSIGNMENT_PATTERN = re.compile(
    r"(?i)\b(?:api[_-]?key|authorization|password|secret|token)"
    r"\s*[:=]\s*(?:\"([^\"]{4,})\"|'([^']{4,})'|([^\s,;]{4,}))"
)
_WINDOWS_HOME_PATTERN = re.compile(
    r"(?i)(?<![A-Za-z0-9])(?:[A-Z]:[\\/](?:Users|Documents and Settings)"
    r"[\\/][^\\/\s\"']+(?:[\\/][^\s\"';,]*)?)"
)
_POSIX_HOME_PATTERN = re.compile(
    r"(?<![A-Za-z0-9])/(?:home|Users)/[^/\s\"']+(?:/[^\s\"';,]*)?"
)


class RedactionMetadata(TraceModel):
    applied: bool = False
    replacement_count: int = Field(default=0, ge=0)
    secret_field_count: int = Field(default=0, ge=0)
    hidden_field_count: int = Field(default=0, ge=0)
    private_path_count: int = Field(default=0, ge=0)
    truncated_value_count: int = Field(default=0, ge=0)
    original_bytes: int = Field(default=0, ge=0)
    retained_bytes: int = Field(default=0, ge=0)
    reasons: list[str] = Field(default_factory=list, max_length=32)


@dataclass(frozen=True)
class SanitizedDocument:
    value: Any
    canonical_bytes: bytes
    redaction: RedactionMetadata


@dataclass
class _RedactionCounters:
    replacements: int = 0
    secret_fields: int = 0
    hidden_fields: int = 0
    private_paths: int = 0
    truncated_values: int = 0
    nodes: int = 0


def sanitize_document(
    value: Any,
    *,
    private_roots: Iterable[str | Path] = (),
    max_text_chars: int = DEFAULT_MAX_TEXT_CHARS,
    max_collection_items: int = DEFAULT_MAX_COLLECTION_ITEMS,
    max_nodes: int = DEFAULT_MAX_NODES,
    max_depth: int = DEFAULT_MAX_DEPTH,
) -> SanitizedDocument:
    if max_text_chars < 1:
        raise ValueError("max_text_chars must be positive")
    if max_collection_items < 1 or max_nodes < 1 or max_depth < 1:
        raise ValueError("trace redaction bounds must be positive")
    roots = tuple(
        sorted(
            {
                str(Path(root).expanduser().resolve())
                for root in private_roots
                if str(root).strip()
            },
            key=len,
            reverse=True,
        )
    )
    original_bytes = _estimated_json_size(value)
    counters = _RedactionCounters()
    sanitized = _sanitize_value(
        value,
        key=None,
        roots=roots,
        counters=counters,
        depth=0,
        max_text_chars=max_text_chars,
        max_collection_items=max_collection_items,
        max_nodes=max_nodes,
        max_depth=max_depth,
    )
    canonical = canonical_json_bytes(sanitized)
    reasons = []
    if counters.secret_fields:
        reasons.append("secret_fields")
    if counters.hidden_fields:
        reasons.append("hidden_content")
    if counters.private_paths:
        reasons.append("private_paths")
    if counters.truncated_values:
        reasons.append("bounded_content")
    metadata = RedactionMetadata(
        applied=bool(counters.replacements),
        replacement_count=counters.replacements,
        secret_field_count=counters.secret_fields,
        hidden_field_count=counters.hidden_fields,
        private_path_count=counters.private_paths,
        truncated_value_count=counters.truncated_values,
        original_bytes=original_bytes,
        retained_bytes=len(canonical),
        reasons=reasons,
    )
    return SanitizedDocument(
        value=sanitized,
        canonical_bytes=canonical,
        redaction=metadata,
    )


def sanitize_text(
    value: str,
    *,
    private_roots: Iterable[str | Path] = (),
    max_chars: int = DEFAULT_MAX_TEXT_CHARS,
) -> SanitizedDocument:
    document = sanitize_document(
        value,
        private_roots=private_roots,
        max_text_chars=max_chars,
    )
    text = str(document.value)
    return SanitizedDocument(
        value=text,
        canonical_bytes=text.encode("utf-8"),
        redaction=document.redaction.model_copy(
            update={"retained_bytes": len(text.encode("utf-8"))}
        ),
    )


def contains_secret_material(payload: str | bytes) -> bool:
    if isinstance(payload, bytes):
        try:
            text = payload.decode("utf-8")
        except UnicodeDecodeError:
            return True
    else:
        text = payload
    if _PRIVATE_KEY_PATTERN.search(text) or _BEARER_PATTERN.search(text):
        return True
    for match in _SECRET_ASSIGNMENT_PATTERN.finditer(text):
        captured = next((item for item in match.groups() if item is not None), "")
        if not captured.startswith(REDACTED):
            return True
    return False


def canonical_json_bytes(value: Any) -> bytes:
    try:
        return json.dumps(
            value,
            allow_nan=False,
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ValueError("Trace payload must be finite JSON.") from exc


def configuration_fingerprint(
    value: dict[str, Any],
    *,
    private_roots: Iterable[str | Path] = (),
) -> str:
    sanitized = sanitize_document(value, private_roots=private_roots)
    return hashlib.sha256(sanitized.canonical_bytes).hexdigest()


def _sanitize_value(
    value: Any,
    *,
    key: str | None,
    roots: tuple[str, ...],
    counters: _RedactionCounters,
    depth: int,
    max_text_chars: int,
    max_collection_items: int,
    max_nodes: int,
    max_depth: int,
) -> Any:
    counters.nodes += 1
    if counters.nodes > max_nodes:
        raise ValueError("Trace payload exceeds the maximum node count.")
    normalized_key = _normalize_key(key)
    if normalized_key in _HIDDEN_KEYS:
        counters.replacements += 1
        counters.hidden_fields += 1
        return HIDDEN_CONTENT
    if key is not None and is_sensitive_key(key):
        counters.replacements += 1
        counters.secret_fields += 1
        return REDACTED
    if depth > max_depth:
        counters.replacements += 1
        counters.truncated_values += 1
        return TRUNCATED
    if value is None or isinstance(value, bool | int):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("Trace payload contains a non-finite number.")
        return value
    if isinstance(value, str):
        return _sanitize_string(
            value,
            roots=roots,
            counters=counters,
            max_text_chars=max_text_chars,
        )
    if isinstance(value, dict):
        result: dict[str, Any] = {}
        items = list(value.items())
        if len(items) > max_collection_items:
            counters.replacements += 1
            counters.truncated_values += 1
            items = items[:max_collection_items]
            result[TRUNCATED] = len(value) - max_collection_items
        for item_key, item_value in items:
            text_key = str(item_key)
            result[text_key] = _sanitize_value(
                item_value,
                key=text_key,
                roots=roots,
                counters=counters,
                depth=depth + 1,
                max_text_chars=max_text_chars,
                max_collection_items=max_collection_items,
                max_nodes=max_nodes,
                max_depth=max_depth,
            )
        return result
    if isinstance(value, (list, tuple)):
        values = list(value)
        omitted = max(0, len(values) - max_collection_items)
        values = values[:max_collection_items]
        result = [
            _sanitize_value(
                item,
                key=None,
                roots=roots,
                counters=counters,
                depth=depth + 1,
                max_text_chars=max_text_chars,
                max_collection_items=max_collection_items,
                max_nodes=max_nodes,
                max_depth=max_depth,
            )
            for item in values
        ]
        if omitted:
            counters.replacements += 1
            counters.truncated_values += 1
            result.append({TRUNCATED: omitted})
        return result
    if isinstance(value, set):
        return _sanitize_value(
            sorted(value, key=repr),
            key=key,
            roots=roots,
            counters=counters,
            depth=depth,
            max_text_chars=max_text_chars,
            max_collection_items=max_collection_items,
            max_nodes=max_nodes,
            max_depth=max_depth,
        )
    raise ValueError(
        f"Trace payload contains unsupported value type {type(value).__name__}."
    )


def _sanitize_string(
    value: str,
    *,
    roots: tuple[str, ...],
    counters: _RedactionCounters,
    max_text_chars: int,
) -> str:
    sanitized = redact_text(value, max_chars=max_text_chars) or ""
    if sanitized != value:
        counters.replacements += 1
        if sanitized.endswith("\n[truncated]"):
            counters.truncated_values += 1
    for root in roots:
        replacement_count = sanitized.lower().count(root.lower())
        if replacement_count:
            sanitized = re.sub(
                re.escape(root),
                PRIVATE_PATH,
                sanitized,
                flags=re.IGNORECASE,
            )
            counters.replacements += replacement_count
            counters.private_paths += replacement_count
    for pattern in (_WINDOWS_HOME_PATTERN, _POSIX_HOME_PATTERN):
        sanitized, replacement_count = pattern.subn(PRIVATE_PATH, sanitized)
        counters.replacements += replacement_count
        counters.private_paths += replacement_count
    if _PRIVATE_KEY_PATTERN.search(sanitized):
        counters.replacements += 1
        counters.secret_fields += 1
        return REDACTED
    return sanitized


def _normalize_key(key: str | None) -> str:
    if key is None:
        return ""
    return key.strip().lower().replace("-", "_")


def _estimated_json_size(value: Any) -> int:
    try:
        return len(
            json.dumps(
                value,
                ensure_ascii=True,
                default=lambda item: f"<{type(item).__name__}>",
            ).encode("utf-8")
        )
    except (TypeError, ValueError, RecursionError):
        return 0


__all__ = [
    "DEFAULT_MAX_COLLECTION_ITEMS",
    "DEFAULT_MAX_DEPTH",
    "DEFAULT_MAX_NODES",
    "DEFAULT_MAX_TEXT_CHARS",
    "HIDDEN_CONTENT",
    "PRIVATE_PATH",
    "REDACTED",
    "RedactionMetadata",
    "SanitizedDocument",
    "canonical_json_bytes",
    "configuration_fingerprint",
    "contains_secret_material",
    "sanitize_document",
    "sanitize_text",
]
