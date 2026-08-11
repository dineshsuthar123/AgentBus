from __future__ import annotations

import os
import sys
import time
from pathlib import Path

import pytest

from agentbus.execution.cancellation import (
    CancellationRequested,
    CancellationToken,
)
from agentbus.mcp import (
    McpClient,
    McpServerConfig,
    McpStdioTransport,
    import_mcp_server,
    mcp_server_capabilities,
)
from agentbus.mcp.errors import (
    McpOutputLimitExceeded,
    McpProtocolError,
    McpRequestTimeout,
    McpTransportError,
    McpUnsupportedProtocolVersion,
)
from agentbus.sandbox.platform import ExecutableCatalog
from agentbus.tools.protocol import ToolCapabilityName
from agentbus.tools.registry import ToolRegistry


FIXTURE = Path(__file__).parent / "fixtures" / "mcp" / "fake_server.py"


def client_for_mode(
    root: Path,
    mode: str,
    *,
    lifecycle: Path | None = None,
    maximum_tool_output: int = 131_072,
) -> tuple[McpClient, McpStdioTransport, McpServerConfig]:
    alias = f"adversarial-mcp-{mode}"
    command = [sys.executable, "-u", str(FIXTURE), "--mode", mode]
    if lifecycle is not None:
        command.extend(["--lifecycle-dir", str(lifecycle)])
    catalog = ExecutableCatalog({alias: tuple(command)})
    config = McpServerConfig(
        server_id="adversarial",
        transport="stdio",
        executable_alias=alias,
        startup_timeout_seconds=0.25,
        request_timeout_seconds=0.5,
        maximum_server_output_bytes=131_072,
        maximum_tool_output_bytes=maximum_tool_output,
        capability_map={
            name: mcp_server_capabilities("adversarial")
            for name in ("echo", "write_note")
        },
    )
    transport = McpStdioTransport(
        config,
        worktree=root,
        executable_catalog=catalog,
        shutdown_grace_seconds=0.1,
    )
    return McpClient(config, transport), transport, config


def wait_for_pid(marker: Path, *, timeout_seconds: float = 3.0) -> int:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if marker.is_file():
            value = marker.read_text(encoding="utf-8").strip()
            if value:
                return int(value)
        time.sleep(0.02)
    raise AssertionError("controlled MCP child did not publish its PID")


def process_exists(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def assert_process_exits(pid: int, *, timeout_seconds: float = 3.0) -> None:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if not process_exists(pid):
            return
        time.sleep(0.02)
    raise AssertionError(f"controlled MCP process remained alive: {pid}")


@pytest.mark.parametrize(
    ("mode", "error_type"),
    (
        ("hang", McpRequestTimeout),
        ("malformed", McpProtocolError),
        ("unsupported", McpUnsupportedProtocolVersion),
        ("boolean-id", McpProtocolError),
        ("wrong-id", McpProtocolError),
        ("out-of-order", McpProtocolError),
    ),
)
def test_adversarial_handshake_is_bounded_and_cleans_up(
    tmp_path: Path,
    mode: str,
    error_type: type[Exception],
) -> None:
    client, transport, _ = client_for_mode(tmp_path, mode)

    with pytest.raises(error_type):
        client.connect()

    assert client.is_connected is False
    assert transport.is_running is False


def test_unexpected_peer_termination_redacts_stderr(tmp_path: Path) -> None:
    client, transport, _ = client_for_mode(tmp_path, "terminate")

    with pytest.raises(McpTransportError) as captured:
        client.connect()

    diagnostic = str(captured.value)
    assert "peer-secret-must-not-leak" not in diagnostic
    assert "API_KEY=[REDACTED]" in diagnostic
    assert len(diagnostic) < 2_200
    assert transport.is_running is False


@pytest.mark.parametrize(
    ("mode", "message"),
    (
        ("malformed-schema", "object JSON Schema"),
        ("oversized-declaration", "bounded metadata size"),
        ("duplicate-tools", "colliding tool names"),
    ),
)
def test_adversarial_tool_declarations_are_rejected(
    tmp_path: Path,
    mode: str,
    message: str,
) -> None:
    client, transport, _ = client_for_mode(tmp_path, mode)

    with client, pytest.raises(McpProtocolError, match=message):
        client.list_tools()

    assert transport.is_running is False


def test_oversized_tool_output_is_bounded_and_terminates_peer(
    tmp_path: Path,
) -> None:
    client, transport, _ = client_for_mode(
        tmp_path,
        "oversized-tool",
        maximum_tool_output=1_024,
    )

    with client:
        client.list_tools()
        with pytest.raises(McpOutputLimitExceeded):
            client.call_tool("echo", {"message": "bounded input"})

    assert transport.is_running is False


def test_secret_shaped_peer_output_is_redacted_at_import_boundary(
    tmp_path: Path,
) -> None:
    client, _, _ = client_for_mode(tmp_path, "secret-output")

    with client:
        client.list_tools()
        result = client.call_tool("echo", {"message": "safe input"})

    assert result.content[0]["text"] == "API_KEY=[REDACTED]"
    assert result.structured_content == {"echo": "API_KEY=[REDACTED]"}


def test_ignored_cancellation_terminates_peer_process_tree(
    tmp_path: Path,
) -> None:
    lifecycle = tmp_path / "lifecycle"
    lifecycle.mkdir()
    client, transport, _ = client_for_mode(
        tmp_path,
        "ignore-cancel-child",
        lifecycle=lifecycle,
    )
    client.connect()
    client.list_tools()
    parent_pid = transport.pid
    assert parent_pid is not None
    child_pid = wait_for_pid(lifecycle / "child.pid")
    cancellation = CancellationToken()
    cancellation.request("cancel controlled adversarial MCP")

    with pytest.raises(CancellationRequested):
        client.call_tool(
            "echo",
            {"message": "cancel me"},
            cancellation=cancellation,
        )

    state = cancellation.snapshot()
    assert state.acknowledged is True
    assert "mcp-stdio" in state.propagation_sources
    assert transport.is_running is False
    assert_process_exits(parent_pid)
    assert_process_exits(child_pid)
    client.close()


def test_remote_metadata_cannot_broaden_imported_capabilities(
    tmp_path: Path,
) -> None:
    client, _, config = client_for_mode(tmp_path, "capability-escalation")
    registry = ToolRegistry()

    with import_mcp_server(
        registry,
        config,
        worktree=tmp_path,
        client=client,
    ) as session:
        echo = next(
            descriptor
            for descriptor in session.descriptors
            if descriptor.name == "mcp.adversarial.echo"
        )

        assert echo.capabilities == config.capabilities_for("echo")
        assert {capability.name for capability in echo.capabilities} == {
            ToolCapabilityName.MCP_CONNECT,
            ToolCapabilityName.MCP_INVOKE,
        }
