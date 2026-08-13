from __future__ import annotations

import os
import re
from collections.abc import Mapping
from typing import Any
from urllib.parse import urlsplit


_SENSITIVE_KEYS = {
    "access_token",
    "account_key",
    "api_key",
    "authorization",
    "client_secret",
    "connection_string",
    "cookie",
    "credential",
    "credentials",
    "env",
    "environment",
    "password",
    "private_key",
    "secret",
    "token",
}
_SECRET_PATTERN = re.compile(
    r"(?i)\b(access[_-]?token|account[_-]?key|api[_-]?key|authorization|"
    r"client[_-]?secret|password|secret|shared[_-]?access[_-]?key|token)"
    r"\s*[:=]\s*(?:\"[^\"\r\n]*\"|'[^'\r\n]*'|[^\s,;\"'}]+)"
)
_BEARER_PATTERN = re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]+")
_JWT_PATTERN = re.compile(
    r"(?<![A-Za-z0-9_-])eyJ[A-Za-z0-9_-]{5,}\."
    r"eyJ[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{8,}"
    r"(?![A-Za-z0-9_-])"
)
_PRIVATE_KEY_PATTERN = re.compile(
    r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----.*?"
    r"(?:-----END (?:RSA |EC |OPENSSH )?PRIVATE KEY-----|$)",
    re.IGNORECASE | re.DOTALL,
)
_CONNECTION_ASSIGNMENT_PATTERN = re.compile(
    r"(?i)\b(connection[_ -]?string)\s*[:=]\s*"
    r"(?:\"[^\"\r\n]*\"|'[^'\r\n]*'|[^\r\n]+)"
)
_CREDENTIAL_URI_PATTERN = re.compile(
    r"(?i)\b([a-z][a-z0-9+.-]*://)[^/\s:@]+:[^@\s/]+@"
)
_PROMPT_ASSIGNMENT_PATTERN = re.compile(
    r"(?i)\b((?:(?:system|developer|internal|hidden|raw|user)[_-]?)?prompt)"
    r"\s*[:=]\s*(?:\"[^\"\r\n]*\"|'[^'\r\n]*'|[^\r\n]+)"
)
_URL_QUERY_PATTERN = re.compile(r"(https?://[^\s?#]+)[?#][^\s]+", re.IGNORECASE)
_WINDOWS_HOME_PATTERN = re.compile(
    r"(?i)(?<![A-Za-z0-9])(?:[A-Z]:[\\/](?:Users|Documents and Settings)"
    r"[\\/][^\\/\r\n\"']+(?:[\\/][^\r\n\"';,]*)?)"
)
_POSIX_HOME_PATTERN = re.compile(
    r"(?<![A-Za-z0-9])/(?:home|Users)/[^/\r\n\"']+(?:/[^\r\n\"';,]*)?"
)
_EMAIL_PATTERN = re.compile(
    r"(?<![A-Za-z0-9._%+-])[A-Za-z0-9][A-Za-z0-9._%+-]{0,63}"
    r"@[A-Za-z0-9.-]+\.[A-Za-z]{2,63}(?![A-Za-z0-9._%+-])"
)
_DIAGNOSTIC_HIDDEN_KEYS = {
    "chain_of_thought",
    "developer_prompt",
    "hidden_prompt",
    "hidden_reasoning",
    "internal_prompt",
    "prompt",
    "raw_prompt",
    "reasoning_content",
    "system_prompt",
    "user_prompt",
}
_SENSITIVE_ENVIRONMENT_NAMES = {
    "DOCKER_AUTH_CONFIG",
    "KUBECONFIG",
    "NETRC",
    "PIP_EXTRA_INDEX_URL",
    "PIP_INDEX_URL",
    "SSH_AUTH_SOCK",
}
_SENSITIVE_ENVIRONMENT_MARKERS = (
    "ACCESS_KEY",
    "API_KEY",
    "AUTHORIZATION",
    "COOKIE",
    "CREDENTIAL",
    "PASSWORD",
    "SECRET",
    "TOKEN",
)


def redact_text(value: str | None, *, max_chars: int = 20_000) -> str | None:
    if value is None:
        return None
    text = str(value)
    text = _PRIVATE_KEY_PATTERN.sub("[REDACTED]", text)
    text = _BEARER_PATTERN.sub("Bearer [REDACTED]", text)
    text = _JWT_PATTERN.sub("[REDACTED]", text)
    text = _CREDENTIAL_URI_PATTERN.sub(r"\1[REDACTED]@", text)
    text = _CONNECTION_ASSIGNMENT_PATTERN.sub(
        lambda match: f"{match.group(1)}=[REDACTED]",
        text,
    )
    text = _SECRET_PATTERN.sub(
        lambda match: f"{match.group(1)}=[REDACTED]",
        text,
    )
    text = _URL_QUERY_PATTERN.sub(r"\1?[REDACTED]", text)
    if len(text) > max_chars:
        return text[:max_chars] + "\n[truncated]"
    return text


def redact_diagnostic_text(
    value: str | None,
    *,
    max_chars: int = 20_000,
) -> str | None:
    text = redact_text(value, max_chars=max_chars)
    if text is None:
        return None
    text = _PROMPT_ASSIGNMENT_PATTERN.sub(
        lambda match: f"{match.group(1)}=[REDACTED]",
        text,
    )
    text = _WINDOWS_HOME_PATTERN.sub("[PRIVATE_PATH]", text)
    text = _POSIX_HOME_PATTERN.sub("[PRIVATE_PATH]", text)
    return _EMAIL_PATTERN.sub("[REDACTED]", text)


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


def sanitize_diagnostic_json(
    value: Any,
    *,
    key: str | None = None,
    depth: int = 0,
    max_chars: int = 20_000,
) -> Any:
    normalized_key = key.strip().lower().replace("-", "_") if key else ""
    if normalized_key in _DIAGNOSTIC_HIDDEN_KEYS:
        return "[REDACTED]"
    if key and is_sensitive_key(key):
        return "[REDACTED]"
    if depth > 12:
        return "[maximum depth reached]"
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return redact_diagnostic_text(value, max_chars=max_chars)
    if isinstance(value, dict):
        return {
            str(item_key): sanitize_diagnostic_json(
                item_value,
                key=str(item_key),
                depth=depth + 1,
                max_chars=max_chars,
            )
            for item_key, item_value in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [
            sanitize_diagnostic_json(
                item,
                depth=depth + 1,
                max_chars=max_chars,
            )
            for item in value
        ]
    if isinstance(value, set):
        return [
            sanitize_diagnostic_json(
                item,
                depth=depth + 1,
                max_chars=max_chars,
            )
            for item in sorted(value, key=repr)
        ]
    return redact_diagnostic_text(str(value), max_chars=max_chars)


def safe_endpoint_host(endpoint: str | None) -> str | None:
    if not endpoint:
        return None
    try:
        parsed = urlsplit(endpoint)
    except ValueError:
        return "[invalid endpoint]"
    return parsed.hostname or "[invalid endpoint]"


def safe_child_environment(
    environ: Mapping[str, str] | None = None,
) -> dict[str, str]:
    source = os.environ if environ is None else environ
    return {
        name: value
        for name, value in source.items()
        if not is_sensitive_environment_key(name)
    }


def is_sensitive_environment_key(name: str) -> bool:
    normalized = name.strip().upper().replace("-", "_")
    return normalized in _SENSITIVE_ENVIRONMENT_NAMES or any(
        marker in normalized for marker in _SENSITIVE_ENVIRONMENT_MARKERS
    )


def is_sensitive_key(key: str) -> bool:
    normalized = key.strip().lower().replace("-", "_")
    return normalized in _SENSITIVE_KEYS or any(
        normalized.endswith(f"_{suffix}")
        for suffix in (
            "api_key",
            "authorization",
            "connection_string",
            "password",
            "private_key",
            "secret",
            "token",
        )
    )
