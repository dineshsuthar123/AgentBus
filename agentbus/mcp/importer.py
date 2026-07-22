from __future__ import annotations

import json
import time
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from agentbus.execution.cancellation import CancellationToken
from agentbus.mcp.client import McpClient, McpRemoteTool
from agentbus.mcp.errors import McpRemoteError, McpToolImportError
from agentbus.mcp.http_transport import McpHttpTransport
from agentbus.mcp.models import McpServerConfig, McpTransportKind
from agentbus.mcp.transport import McpStdioTransport
from agentbus.sandbox.platform import ExecutableCatalog
from agentbus.tools.interfaces import ManagedTool, ToolExecutionOutput
from agentbus.tools.protocol import (
    ToolCapabilityName,
    ToolDescriptor,
    ToolInvocation,
    ToolResourceUsage,
    ToolSafetyClassification,
    ToolVersion,
)
from agentbus.tools.registry import ToolRegistry


_DANGEROUS_CAPABILITIES = frozenset(
    {
        ToolCapabilityName.FILESYSTEM_DELETE,
        ToolCapabilityName.GIT_COMMIT,
        ToolCapabilityName.GIT_WORKTREE,
        ToolCapabilityName.PACKAGE_INSTALL,
        ToolCapabilityName.PROCESS_NETWORK,
    }
)
_RISKY_CAPABILITIES = frozenset(
    {
        ToolCapabilityName.FILESYSTEM_WRITE,
        ToolCapabilityName.FILESYSTEM_CREATE,
        ToolCapabilityName.FILESYSTEM_RENAME,
        ToolCapabilityName.GIT_WRITE,
        ToolCapabilityName.GIT_BRANCH,
        ToolCapabilityName.PROCESS_EXECUTE,
        ToolCapabilityName.TEST_EXECUTE,
    }
)


@dataclass
class McpImportSession:
    client: McpClient
    descriptors: tuple[ToolDescriptor, ...]
    _closed: bool = False

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self.client.close()

    def __enter__(self) -> "McpImportSession":
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        self.close()


class McpManagedTool(ManagedTool):
    def __init__(
        self,
        client: McpClient,
        remote_tool: McpRemoteTool,
    ) -> None:
        self.client = client
        self.remote_tool = remote_tool
        self._descriptor = _descriptor(client.config, remote_tool)

    @property
    def descriptor(self) -> ToolDescriptor:
        return self._descriptor

    def execute(
        self,
        invocation: ToolInvocation,
        *,
        cancellation: CancellationToken | None = None,
    ) -> ToolExecutionOutput:
        started = time.monotonic()
        result = self.client.call_tool(
            self.remote_tool.name,
            invocation.arguments,
            timeout_seconds=invocation.timeout_seconds,
            cancellation=cancellation,
        )
        elapsed = max(0.0, time.monotonic() - started)
        if result.is_error:
            raise McpRemoteError(
                "Configured MCP tool reported a remote execution failure."
            )
        structured_output = {
            "content": list(result.content),
            "structured_content": result.structured_content,
            "is_error": False,
        }
        encoded_size = len(
            json.dumps(
                structured_output,
                ensure_ascii=False,
                separators=(",", ":"),
                allow_nan=False,
            ).encode("utf-8")
        )
        return ToolExecutionOutput(
            structured_output=structured_output,
            resource_usage=ToolResourceUsage(
                wall_clock_seconds=elapsed,
                stdout_bytes=encoded_size,
            ),
            safe_diagnostic_metadata={
                "mcp_server_id": self.client.config.server_id,
                "mcp_tool_name": self.remote_tool.name,
                "mcp_protocol_version": self.client.connection_info.protocol_version,
                "transport": self.client.transport.safe_diagnostics,
            },
        )


def build_mcp_client(
    config: McpServerConfig,
    *,
    worktree: str | Path,
    executable_catalog: ExecutableCatalog | None = None,
    source_environment: Mapping[str, str] | None = None,
) -> McpClient:
    if config.transport == McpTransportKind.STDIO:
        if executable_catalog is None:
            raise McpToolImportError(
                "stdio MCP import requires an explicit executable catalog."
            )
        transport = McpStdioTransport(
            config,
            worktree=worktree,
            executable_catalog=executable_catalog,
            source_environment=source_environment,
        )
    else:
        transport = McpHttpTransport(config)
    return McpClient(config, transport)


def import_mcp_server(
    registry: ToolRegistry,
    config: McpServerConfig,
    *,
    worktree: str | Path,
    executable_catalog: ExecutableCatalog | None = None,
    source_environment: Mapping[str, str] | None = None,
    cancellation: CancellationToken | None = None,
    client: McpClient | None = None,
) -> McpImportSession:
    active_client = client or build_mcp_client(
        config,
        worktree=worktree,
        executable_catalog=executable_catalog,
        source_environment=source_environment,
    )
    if active_client.config != config:
        raise McpToolImportError("MCP import client configuration does not match.")
    try:
        active_client.connect(cancellation=cancellation)
        remote_tools = active_client.list_tools(cancellation=cancellation)
        registrations = []
        descriptors = []
        for remote_tool in remote_tools:
            descriptor = _descriptor(config, remote_tool)
            registrations.append(
                (
                    descriptor,
                    lambda remote=remote_tool: McpManagedTool(
                        active_client,
                        remote,
                    ),
                )
            )
            descriptors.append(descriptor)
        registry.register_many(registrations)
    except BaseException:
        active_client.close()
        raise
    return McpImportSession(
        client=active_client,
        descriptors=tuple(descriptors),
    )


def _descriptor(
    config: McpServerConfig,
    remote_tool: McpRemoteTool,
) -> ToolDescriptor:
    capabilities = config.capabilities_for(remote_tool.name)
    names = {capability.name for capability in capabilities}
    if names & _DANGEROUS_CAPABILITIES:
        safety = ToolSafetyClassification.DANGEROUS
    elif names & _RISKY_CAPABILITIES:
        safety = ToolSafetyClassification.RISKY
    else:
        safety = ToolSafetyClassification.SENSITIVE
    return ToolDescriptor(
        name=remote_tool.namespaced_name,
        version=ToolVersion(major=1),
        description=remote_tool.description,
        capabilities=capabilities,
        argument_schema=remote_tool.input_schema,
        output_schema={
            "type": "object",
            "properties": {
                "content": {
                    "type": "array",
                    "maxItems": 256,
                    "items": {"type": "object"},
                },
                "structured_content": {"type": ["object", "null"]},
                "is_error": {"const": False},
            },
            "required": ["content", "structured_content", "is_error"],
            "additionalProperties": False,
        },
        safety=safety,
        idempotent=False,
        supports_cancellation=True,
        maximum_timeout_seconds=config.request_timeout_seconds,
    )
