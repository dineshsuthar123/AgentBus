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
from agentbus.mcp.errors import (
    McpProtocolError,
    McpUnsupportedProtocolVersion,
)
from agentbus.sandbox.platform import ExecutableCatalog


FIXTURE = Path(__file__).parent / "fixtures" / "mcp" / "fake_server.py"


def test_client_negotiates_version_initializes_and_pings(tmp_path: Path) -> None:
    client, transport = _client(tmp_path)

    connection = client.connect()
    client.ping()

    assert connection.protocol_version == "2025-11-25"
    assert connection.server_name == "offline-fixture"
    assert connection.capabilities == ("tools",)
    assert transport.is_running is True
    client.close()
    assert transport.is_running is False


def test_client_rejects_unsupported_negotiation_and_cleans_server(
    tmp_path: Path,
) -> None:
    client, transport = _client(tmp_path, mode="unsupported")

    with pytest.raises(McpUnsupportedProtocolVersion, match="unsupported"):
        client.connect()

    assert client.is_connected is False
    assert transport.is_running is False


def test_client_rejects_requests_before_initialization(tmp_path: Path) -> None:
    client, _ = _client(tmp_path)

    with pytest.raises(McpProtocolError, match="initialize before"):
        client.ping()


def test_client_rejects_boolean_response_id_for_numeric_request(
    tmp_path: Path,
) -> None:
    client, transport = _client(tmp_path, mode="boolean-id")

    with pytest.raises(McpProtocolError, match="unexpected request ID"):
        client.connect()

    assert transport.is_running is False


def _client(root: Path, *, mode: str = "normal"):
    alias = f"fake-mcp-{mode}"
    catalog = ExecutableCatalog(
        {alias: (sys.executable, "-u", str(FIXTURE), "--mode", mode)}
    )
    config = McpServerConfig(
        server_id="fixture",
        transport="stdio",
        executable_alias=alias,
        capability_map={"echo": mcp_server_capabilities("fixture")},
    )
    transport = McpStdioTransport(
        config,
        worktree=root,
        executable_catalog=catalog,
        shutdown_grace_seconds=0.2,
    )
    return McpClient(config, transport), transport
