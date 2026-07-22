from __future__ import annotations


TOOL_PROTOCOL_NAME = "agentbus.tool"
TOOL_PROTOCOL_MAJOR = 1
TOOL_PROTOCOL_MINOR = 0
TOOL_PROTOCOL_VERSION = f"{TOOL_PROTOCOL_MAJOR}.{TOOL_PROTOCOL_MINOR}"
SUPPORTED_TOOL_PROTOCOL_VERSIONS = frozenset({TOOL_PROTOCOL_VERSION})


def is_supported_protocol_version(version: str) -> bool:
    return version in SUPPORTED_TOOL_PROTOCOL_VERSIONS
