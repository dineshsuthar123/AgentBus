from __future__ import annotations

from agentbus.protocol_identity import (
    TOOL_PROTOCOL_MAJOR,
    TOOL_PROTOCOL_MINOR,
    TOOL_PROTOCOL_NAME,
    TOOL_PROTOCOL_VERSION,
)

SUPPORTED_TOOL_PROTOCOL_VERSIONS = frozenset({TOOL_PROTOCOL_VERSION})


def is_supported_protocol_version(version: str) -> bool:
    return version in SUPPORTED_TOOL_PROTOCOL_VERSIONS
