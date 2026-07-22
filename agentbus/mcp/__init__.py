from __future__ import annotations

from importlib import import_module


_EXPORT_MODULES = {
    "AgentBusMcpServer": "agentbus.mcp.server",
    "LATEST_MCP_PROTOCOL_VERSION": "agentbus.mcp.models",
    "SUPPORTED_MCP_PROTOCOL_VERSIONS": "agentbus.mcp.models",
    "McpClient": "agentbus.mcp.client",
    "McpConnectionInfo": "agentbus.mcp.client",
    "McpHttpTransport": "agentbus.mcp.http_transport",
    "McpImportSession": "agentbus.mcp.importer",
    "McpManagedTool": "agentbus.mcp.importer",
    "McpRemoteTool": "agentbus.mcp.client",
    "McpServerConfig": "agentbus.mcp.models",
    "McpStdioTransport": "agentbus.mcp.transport",
    "McpToolCallResult": "agentbus.mcp.client",
    "McpTransport": "agentbus.mcp.transport",
    "McpTransportKind": "agentbus.mcp.models",
    "build_mcp_client": "agentbus.mcp.importer",
    "import_mcp_server": "agentbus.mcp.importer",
    "mcp_server_capabilities": "agentbus.mcp.models",
    "namespace_mcp_tool": "agentbus.mcp.models",
}


def __getattr__(name: str):
    module_name = _EXPORT_MODULES.get(name)
    if module_name is None:
        raise AttributeError(f"module 'agentbus.mcp' has no attribute {name!r}")
    return getattr(import_module(module_name), name)


__all__ = sorted(_EXPORT_MODULES)
