from __future__ import annotations

from typing import Any

from agentbus.protocol_identity import (
    CONTROL_PROTOCOL_VERSION,
    TOOL_PROTOCOL_NAME,
    TOOL_PROTOCOL_VERSION,
)
from agentbus.trace.version import TRACE_SCHEMA_NAME, TRACE_SCHEMA_VERSION


def provenance_protocol_documents() -> dict[str, dict[str, Any]]:
    return {
        "control": {
            "name": "agentbus.control",
            "version": CONTROL_PROTOCOL_VERSION,
        },
        "replay_checkpoint": {
            "name": "agentbus.replay.checkpoint",
            "version": 1,
        },
        "tool": {
            "name": TOOL_PROTOCOL_NAME,
            "version": TOOL_PROTOCOL_VERSION,
        },
        "trace": {
            "name": TRACE_SCHEMA_NAME,
            "version": TRACE_SCHEMA_VERSION,
        },
    }


__all__ = ["provenance_protocol_documents"]
