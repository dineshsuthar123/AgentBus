from __future__ import annotations

import sys
from pathlib import Path

import pytest

from agentbus.mcp import (
    McpClient,
    McpServerConfig,
    McpStdioTransport,
    mcp_server_capabilities,
)
from agentbus.mcp.errors import McpOutputLimitExceeded, McpProtocolError
from agentbus.sandbox.platform import ExecutableCatalog


FIXTURE = Path(__file__).parent / "fixtures" / "mcp" / "fake_server.py"


class SecretOutputTransport:
    def start(self) -> None:
        return None

    def request(self, message, *, timeout_seconds, cancellation=None):
        del timeout_seconds, cancellation
        method = message["method"]
        if method == "initialize":
            result = {
                "protocolVersion": "2025-11-25",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "unit-peer", "version": "1"},
            }
        elif method == "tools/list":
            result = {
                "tools": [
                    {
                        "name": "echo",
                        "inputSchema": {
                            "type": "object",
                            "additionalProperties": False,
                        },
                    }
                ]
            }
        else:
            result = {
                "content": [
                    {
                        "type": "text",
                        "text": "API_KEY=unit-secret-must-not-leak",
                    }
                ],
                "structuredContent": {
                    "echo": "TOKEN=structured-secret-must-not-leak",
                },
                "isError": False,
            }
        return {"jsonrpc": "2.0", "id": message["id"], "result": result}

    def notify(self, message, *, timeout_seconds, cancellation=None) -> None:
        del message, timeout_seconds, cancellation

    def set_protocol_version(self, protocol_version: str) -> None:
        del protocol_version

    def close(self) -> None:
        return None


def test_client_discovers_capability_mapped_tools_and_validates_call(
    tmp_path: Path,
) -> None:
    client = _client(tmp_path)
    with client:
        tools = client.list_tools()
        result = client.call_tool("echo", {"message": "bounded hello"})

    assert [tool.name for tool in tools] == ["echo", "write_note"]
    assert tools[0].namespaced_name == "mcp.fixture.echo"
    assert tools[0].annotations["readOnlyHint"] is True
    assert result.structured_content == {"echo": "bounded hello"}
    assert result.content[0]["text"] == "bounded hello"
    assert result.is_error is False


def test_client_redacts_untrusted_remote_tool_output() -> None:
    config = McpServerConfig(
        server_id="fixture",
        transport="stdio",
        executable_alias="unit-peer",
        capability_map={"echo": mcp_server_capabilities("fixture")},
    )
    client = McpClient(config, SecretOutputTransport())

    with client:
        client.list_tools()
        result = client.call_tool("echo", {})

    assert result.content[0]["text"] == "API_KEY=[REDACTED]"
    assert result.structured_content == {"echo": "TOKEN=[REDACTED]"}


def test_client_rejects_unmapped_remote_tool(tmp_path: Path) -> None:
    client = _client(tmp_path, mapped_tools=("echo",))
    with client, pytest.raises(McpProtocolError, match="no explicit capability"):
        client.list_tools()


def test_client_rejects_configured_tool_missing_from_server(tmp_path: Path) -> None:
    client = _client(tmp_path, mapped_tools=("echo", "write_note", "ghost"))
    with client, pytest.raises(McpProtocolError, match="not advertised"):
        client.list_tools()


@pytest.mark.parametrize(
    ("mode", "message"),
    [
        ("malformed-schema", "object JSON Schema"),
        ("oversized-description", "bounded text"),
    ],
)
def test_client_rejects_malformed_or_unbounded_tool_metadata(
    tmp_path: Path,
    mode: str,
    message: str,
) -> None:
    client = _client(tmp_path, mode=mode)
    with client, pytest.raises(McpProtocolError, match=message):
        client.list_tools()


def test_client_validates_arguments_before_remote_invocation(tmp_path: Path) -> None:
    client = _client(tmp_path)
    with client:
        client.list_tools()
        with pytest.raises(McpProtocolError, match="local JSON Schema"):
            client.call_tool("echo", {"unexpected": "value"})


def test_client_bounds_tool_output_and_preserves_remote_error_state(
    tmp_path: Path,
) -> None:
    oversized = _client(tmp_path, mode="oversized-tool", maximum_output=1_024)
    with oversized:
        oversized.list_tools()
        with pytest.raises(McpOutputLimitExceeded, match="oversized"):
            oversized.call_tool("echo", {"message": "hello"})

    failed = _client(tmp_path, mode="tool-error")
    with failed:
        failed.list_tools()
        result = failed.call_tool("echo", {"message": "hello"})
    assert result.is_error is True
    assert result.content[0]["text"] == "offline failure"


def _client(
    root: Path,
    *,
    mode: str = "normal",
    mapped_tools: tuple[str, ...] = ("echo", "write_note"),
    maximum_output: int = 1_048_576,
) -> McpClient:
    alias = f"fake-mcp-{mode}"
    catalog = ExecutableCatalog(
        {alias: (sys.executable, "-u", str(FIXTURE), "--mode", mode)}
    )
    config = McpServerConfig(
        server_id="fixture",
        transport="stdio",
        executable_alias=alias,
        maximum_tool_output_bytes=maximum_output,
        capability_map={
            name: mcp_server_capabilities("fixture") for name in mapped_tools
        },
    )
    return McpClient(
        config,
        McpStdioTransport(
            config,
            worktree=root,
            executable_catalog=catalog,
            shutdown_grace_seconds=0.2,
        ),
    )
