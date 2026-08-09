from __future__ import annotations

import traceback
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from agentbus.security.redaction import redact_text, sanitize_json


class ProductErrorCategory(StrEnum):
    INSTALLATION_ERROR = "INSTALLATION_ERROR"
    CONFIGURATION_ERROR = "CONFIGURATION_ERROR"
    PROVIDER_CONFIGURATION_ERROR = "PROVIDER_CONFIGURATION_ERROR"
    PROVIDER_UNAVAILABLE = "PROVIDER_UNAVAILABLE"
    WORKSPACE_ERROR = "WORKSPACE_ERROR"
    GIT_ERROR = "GIT_ERROR"
    INDEX_ERROR = "INDEX_ERROR"
    TOOL_POLICY_DENIED = "TOOL_POLICY_DENIED"
    APPROVAL_REQUIRED = "APPROVAL_REQUIRED"
    DAEMON_ERROR = "DAEMON_ERROR"
    PROTOCOL_MISMATCH = "PROTOCOL_MISMATCH"
    MIGRATION_REQUIRED = "MIGRATION_REQUIRED"
    STORAGE_ERROR = "STORAGE_ERROR"
    REPLAY_ERROR = "REPLAY_ERROR"
    MCP_ERROR = "MCP_ERROR"
    INTERNAL_ERROR = "INTERNAL_ERROR"


_DEFAULT_CODES = {
    ProductErrorCategory.INSTALLATION_ERROR: "AGENTBUS-E1001",
    ProductErrorCategory.CONFIGURATION_ERROR: "AGENTBUS-E2001",
    ProductErrorCategory.PROVIDER_CONFIGURATION_ERROR: "AGENTBUS-E2101",
    ProductErrorCategory.PROVIDER_UNAVAILABLE: "AGENTBUS-E2201",
    ProductErrorCategory.WORKSPACE_ERROR: "AGENTBUS-E3001",
    ProductErrorCategory.GIT_ERROR: "AGENTBUS-E3101",
    ProductErrorCategory.INDEX_ERROR: "AGENTBUS-E4001",
    ProductErrorCategory.TOOL_POLICY_DENIED: "AGENTBUS-E5001",
    ProductErrorCategory.APPROVAL_REQUIRED: "AGENTBUS-E5101",
    ProductErrorCategory.DAEMON_ERROR: "AGENTBUS-E6001",
    ProductErrorCategory.PROTOCOL_MISMATCH: "AGENTBUS-E6101",
    ProductErrorCategory.MIGRATION_REQUIRED: "AGENTBUS-E6201",
    ProductErrorCategory.STORAGE_ERROR: "AGENTBUS-E6301",
    ProductErrorCategory.REPLAY_ERROR: "AGENTBUS-E7001",
    ProductErrorCategory.MCP_ERROR: "AGENTBUS-E7101",
    ProductErrorCategory.INTERNAL_ERROR: "AGENTBUS-E9001",
}


@dataclass(frozen=True)
class ProductError(RuntimeError):
    category: ProductErrorCategory
    message: str
    likely_cause: str
    recommended_action: str
    docs_topic: str | None = None
    safe_detail: str | None = None
    retryable: bool = False
    code: str | None = None

    def __post_init__(self) -> None:
        code = self.code or _DEFAULT_CODES[self.category]
        if not code.startswith("AGENTBUS-E") or not code[10:].isdigit():
            raise ValueError("Product error codes must use AGENTBUS-E####")
        object.__setattr__(self, "code", code)
        RuntimeError.__init__(self, self.message)

    def to_dict(self, *, debug_detail: str | None = None) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "code": self.code,
            "category": self.category.value,
            "message": _safe(self.message, 500),
            "likely_cause": _safe(self.likely_cause, 1_000),
            "recommended_action": _safe(self.recommended_action, 1_000),
            "docs_topic": _safe(self.docs_topic, 200),
            "safe_detail": _safe(self.safe_detail, 2_000),
            "retryable": self.retryable,
        }
        if debug_detail:
            payload["debug_detail"] = _safe(debug_detail, 8_000)
        return sanitize_json(payload, max_chars=8_000)


def as_product_error(
    error: BaseException,
    *,
    category: ProductErrorCategory = ProductErrorCategory.INTERNAL_ERROR,
    message: str | None = None,
    likely_cause: str = "AgentBus could not complete the requested operation.",
    recommended_action: str = "Run `agentbus doctor` and retry the operation.",
    docs_topic: str | None = "troubleshooting",
    retryable: bool = False,
) -> ProductError:
    if isinstance(error, ProductError):
        return error
    return ProductError(
        category=category,
        message=message or "AgentBus encountered an unexpected error.",
        likely_cause=likely_cause,
        recommended_action=recommended_action,
        docs_topic=docs_topic,
        safe_detail=type(error).__name__,
        retryable=retryable,
    )


def render_product_error(error: ProductError, *, debug: bool = False) -> str:
    payload = error.to_dict()
    lines = [
        f"{payload['code']} {payload['category']}",
        str(payload["message"]),
        "",
        f"Likely cause: {payload['likely_cause']}",
        f"Recommended action: {payload['recommended_action']}",
        f"Retryable: {'yes' if payload['retryable'] else 'no'}",
    ]
    if payload["docs_topic"]:
        lines.append(f"Documentation: {payload['docs_topic']}")
    if payload["safe_detail"]:
        lines.append(f"Detail: {payload['safe_detail']}")
    if debug:
        rendered = "".join(traceback.format_exception(error))
        lines.extend(("", "Debug detail:", _safe(rendered, 8_000) or ""))
    return "\n".join(lines)


def _safe(value: str | None, limit: int) -> str | None:
    return redact_text(value, max_chars=limit) if value is not None else None
