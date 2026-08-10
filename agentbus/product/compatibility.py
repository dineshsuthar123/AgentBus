from __future__ import annotations

import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from agentbus import __version__
from agentbus.control.version import CONTROL_PROTOCOL_VERSION
from agentbus.execution.schema import SCHEMA_VERSION
from agentbus.intelligence.version import (
    INTELLIGENCE_PROTOCOL_VERSION,
    INTELLIGENCE_SCHEMA_VERSION,
)
from agentbus.tools.protocol.version import TOOL_PROTOCOL_VERSION
from agentbus.trace.version import TRACE_SCHEMA_VERSION


SUPPORTED_PYTHON_VERSIONS = ("3.11", "3.12", "3.13", "3.14")
EXTENSION_COMPATIBILITY_RANGE = ">=0.6.0-beta.1 <0.7.0"
PYTHON_COMPATIBILITY_RANGE = ">=0.6.0b1,<0.7.0"


@dataclass(frozen=True)
class CompatibilityManifest:
    package_version: str = __version__
    control_protocol: str = CONTROL_PROTOCOL_VERSION
    tool_protocol: str = TOOL_PROTOCOL_VERSION
    trace_schema: int = TRACE_SCHEMA_VERSION
    state_schema: int = SCHEMA_VERSION
    intelligence_protocol: str = INTELLIGENCE_PROTOCOL_VERSION
    intelligence_schema: int = INTELLIGENCE_SCHEMA_VERSION
    extension_range: str = EXTENSION_COMPATIBILITY_RANGE
    supported_python: tuple[str, ...] = SUPPORTED_PYTHON_VERSIONS

    def to_dict(self) -> dict[str, Any]:
        return {
            "package": "agentbus",
            "version": self.package_version,
            "protocols": {
                "control": self.control_protocol,
                "tool": self.tool_protocol,
                "repository_intelligence": self.intelligence_protocol,
            },
            "schemas": {
                "state": self.state_schema,
                "trace": self.trace_schema,
                "repository_intelligence": self.intelligence_schema,
            },
            "extension_compatibility": self.extension_range,
            "supported_python": list(self.supported_python),
            "running_python": f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}",
            "running_python_supported": current_python_supported(),
        }


def compatibility_manifest() -> CompatibilityManifest:
    return CompatibilityManifest()


def current_python_supported() -> bool:
    current = f"{sys.version_info.major}.{sys.version_info.minor}"
    return current in SUPPORTED_PYTHON_VERSIONS


def extension_package_metadata(path: str | Path) -> dict[str, Any]:
    package_path = Path(path)
    try:
        document = json.loads(package_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"Unable to read VS Code extension metadata: {package_path}") from exc
    if not isinstance(document, dict):
        raise ValueError("VS Code extension metadata must be a JSON object")
    compatibility = document.get("agentbusCompatibility")
    if not isinstance(compatibility, dict):
        raise ValueError("VS Code extension metadata is missing agentbusCompatibility")
    return {
        "version": str(document.get("version", "")),
        "python": str(compatibility.get("python", "")),
        "control_protocol": str(compatibility.get("controlProtocol", "")),
        "state_schema": compatibility.get("stateSchema"),
    }


def validate_extension_package(path: str | Path) -> list[str]:
    metadata = extension_package_metadata(path)
    issues: list[str] = []
    if metadata["python"] != PYTHON_COMPATIBILITY_RANGE:
        issues.append("Python package compatibility range is stale")
    if metadata["control_protocol"] != CONTROL_PROTOCOL_VERSION:
        issues.append("Control protocol compatibility is stale")
    if metadata["state_schema"] != SCHEMA_VERSION:
        issues.append("State schema compatibility is stale")
    if _minor_line(metadata["version"]) != _minor_line(__version__):
        issues.append("Extension and Python package minor versions differ")
    return issues


def _minor_line(version: str) -> tuple[int, int] | None:
    match = re.match(r"^(\d+)\.(\d+)", version)
    if not match:
        return None
    return int(match.group(1)), int(match.group(2))
