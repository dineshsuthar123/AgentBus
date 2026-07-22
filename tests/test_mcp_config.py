from __future__ import annotations

import tomllib
from pathlib import Path

import pytest
from pydantic import ValidationError

from agentbus.config import AgentBusConfig
from agentbus.mcp import (
    McpServerConfig,
    McpTransportKind,
    mcp_server_capabilities,
    namespace_mcp_tool,
)
from agentbus.tools.protocol import CapabilityScope, ToolCapability, ToolCapabilityName


def test_stdio_server_requires_explicit_command_and_scoped_capabilities() -> None:
    config = McpServerConfig(
        server_id="local-tools",
        transport=McpTransportKind.STDIO,
        executable_alias="python",
        arguments=("-m", "fake_mcp_server"),
        environment={"NO_COLOR": "1"},
        capability_map={"read_file": mcp_server_capabilities("local-tools")},
    )

    assert config.executable_alias == "python"
    assert config.capabilities_for("read_file")[0].scope.mcp_servers == (
        "local-tools",
    )
    assert namespace_mcp_tool("local-tools", "read_file") == (
        "mcp.local-tools.read_file"
    )


def test_agentbus_config_rejects_duplicate_mcp_server_ids() -> None:
    server = McpServerConfig(
        server_id="local",
        transport=McpTransportKind.STDIO,
        executable_alias="python",
        capability_map={"read": mcp_server_capabilities("local")},
    )

    with pytest.raises(ValueError, match="server IDs must be unique"):
        AgentBusConfig(mcp_server_configs=(server, server))


@pytest.mark.parametrize(
    ("updates", "message"),
    [
        ({"executable_alias": None}, "executable alias"),
        ({"inherit_environment": True}, "unrestricted environment"),
        ({"environment": {"API_KEY": "secret"}}, "Sensitive MCP"),
        ({"environment": {"CUSTOM": "value"}}, "not allowlisted"),
        ({"arguments": ("line\nbreak",)}, "single-line"),
        ({"supported_protocol_versions": ("2099-01-01",)}, "Unsupported MCP"),
    ],
)
def test_stdio_server_rejects_unsafe_configuration(updates, message: str) -> None:
    values = {
        "server_id": "local",
        "transport": McpTransportKind.STDIO,
        "executable_alias": "python",
        "capability_map": {"read": mcp_server_capabilities("local")},
        **updates,
    }

    with pytest.raises(ValidationError, match=message):
        McpServerConfig(**values)


def test_capability_map_requires_transport_capabilities_and_exact_server() -> None:
    read_only = ToolCapability(
        name=ToolCapabilityName.FILESYSTEM_READ,
        scope=CapabilityScope(roots=("C:/workspace",)),
    )
    with pytest.raises(ValidationError, match="mcp.connect and mcp.invoke"):
        McpServerConfig(
            server_id="local",
            transport="stdio",
            executable_alias="python",
            capability_map={"read": (read_only,)},
        )

    with pytest.raises(ValidationError, match="configured server ID"):
        McpServerConfig(
            server_id="local",
            transport="stdio",
            executable_alias="python",
            capability_map={"read": mcp_server_capabilities("different")},
        )

    expanded_connect = ToolCapability(
        name=ToolCapabilityName.MCP_CONNECT,
        scope=CapabilityScope(
            roots=("C:/workspace",),
            mcp_servers=("local",),
        ),
    )
    with pytest.raises(ValidationError, match="configured server ID"):
        McpServerConfig(
            server_id="local",
            transport="stdio",
            executable_alias="python",
            capability_map={
                "read": (
                    expanded_connect,
                    ToolCapability(
                        name=ToolCapabilityName.MCP_INVOKE,
                        scope=CapabilityScope(mcp_servers=("local",)),
                    ),
                )
            },
        )


def test_capability_map_rejects_normalized_namespace_collisions() -> None:
    capabilities = mcp_server_capabilities("local")
    with pytest.raises(ValidationError, match="collide"):
        McpServerConfig(
            server_id="local",
            transport="stdio",
            executable_alias="python",
            capability_map={"Read": capabilities, "read": capabilities},
        )


def test_http_server_must_be_explicit_authenticated_and_loopback_only() -> None:
    config = McpServerConfig(
        server_id="dashboard",
        transport="loopback_http",
        endpoint_url="http://127.0.0.1:8765/mcp",
        authorization_token="test-token-with-entropy",
        explicit_loopback_http=True,
        capability_map={"status": mcp_server_capabilities("dashboard")},
    )

    assert config.transport == McpTransportKind.LOOPBACK_HTTP
    assert "test-token-with-entropy" not in repr(config)
    assert "test-token-with-entropy" not in config.model_dump_json()

    for endpoint in (
        "https://example.com/mcp",
        "http://192.168.1.10/mcp",
        "file:///tmp/mcp.sock",
    ):
        with pytest.raises(ValidationError, match="loopback|http or https"):
            McpServerConfig(
                server_id="dashboard",
                transport="loopback_http",
                endpoint_url=endpoint,
                authorization_token="test-token-with-entropy",
                explicit_loopback_http=True,
                capability_map={"status": mcp_server_capabilities("dashboard")},
            )


def test_mcp_http_dependency_is_an_optional_extra() -> None:
    project = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))[
        "project"
    ]

    assert project["optional-dependencies"]["mcp"] == ["httpx>=0.28,<1"]
