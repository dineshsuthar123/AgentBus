from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlsplit


_SENSITIVE_KEYS = {
    "api_key",
    "authorization",
    "cookie",
    "credential",
    "credentials",
    "env",
    "environment",
    "password",
    "secret",
    "token",
}
_SECRET_PATTERN = re.compile(
    r"(?i)\b(api[_-]?key|authorization|password|secret|token)"
    r"\s*[:=]\s*([^\s,;]+)"
)
_BEARER_PATTERN = re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]+")
_URL_QUERY_PATTERN = re.compile(r"(https?://[^\s?#]+)[?#][^\s]+", re.IGNORECASE)


def redact_text(value: str | None, *, max_chars: int = 20_000) -> str | None:
    if value is None:
        return None
    text = str(value)
    text = _SECRET_PATTERN.sub(
        lambda match: f"{match.group(1)}=[REDACTED]",
        text,
    )
    text = _BEARER_PATTERN.sub("Bearer [REDACTED]", text)
    text = _URL_QUERY_PATTERN.sub(r"\1?[REDACTED]", text)
    if len(text) > max_chars:
        return text[:max_chars] + "\n[truncated]"
    return text


def sanitize_json(
    value: Any,
    *,
    key: str | None = None,
    depth: int = 0,
    max_chars: int = 20_000,
) -> Any:
    if key and is_sensitive_key(key):
        return "[REDACTED]"
    if depth > 12:
        return "[maximum depth reached]"
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return redact_text(value, max_chars=max_chars)
    if isinstance(value, dict):
        return {
            str(item_key): sanitize_json(
                item_value,
                key=str(item_key),
                depth=depth + 1,
                max_chars=max_chars,
            )
            for item_key, item_value in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [
            sanitize_json(item, depth=depth + 1, max_chars=max_chars)
            for item in value
        ]
    if isinstance(value, set):
        return [
            sanitize_json(item, depth=depth + 1, max_chars=max_chars)
            for item in sorted(value, key=repr)
        ]
    return redact_text(str(value), max_chars=max_chars)


def safe_endpoint_host(endpoint: str | None) -> str | None:
    if not endpoint:
        return None
    try:
        parsed = urlsplit(endpoint)
    except ValueError:
        return "[invalid endpoint]"
    return parsed.hostname or "[invalid endpoint]"


def is_sensitive_key(key: str) -> bool:
    normalized = key.strip().lower().replace("-", "_")
    return normalized in _SENSITIVE_KEYS or any(
        normalized.endswith(f"_{suffix}")
        for suffix in ("api_key", "authorization", "password", "secret", "token")
    )
