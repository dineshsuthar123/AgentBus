from __future__ import annotations

import sys
from pathlib import Path

import pytest

from agentbus.execution.cancellation import CancellationRequested, CancellationToken
from agentbus.mcp import McpServerConfig, McpStdioTransport, mcp_server_capabilities
from agentbus.mcp.errors import McpOutputLimitExceeded, McpProtocolError
from agentbus.sandbox.platform import ExecutableCatalog


FIXTURE = Path(__file__).parent / "fixtures" / "mcp" / "fake_server.py"


def test_stdio_transport_uses_allowlist_sanitized_environment_and_json_lines(
    tmp_path: Path,
) -> None:
    transport = _transport(
        tmp_path,
        source_environment={
            "LANG": "C",
            "AZURE_OPENAI_API_KEY": "must-not-leak",
            "PATH": "untrusted-path",
        },
    )

    with transport:
        transport.send(
            {"jsonrpc": "2.0", "id": 7, "method": "ping", "params": {}}
        )
        response = transport.receive(timeout_seconds=5)
        diagnostics = transport.safe_diagnostics

        assert response["id"] == 7
        assert "AZURE_OPENAI_API_KEY" not in response["result"]["environment_names"]
        assert diagnostics["shell"] is False
        assert diagnostics["environment"]["sensitive_variables_present"] is False
        assert diagnostics["process_tree"]["tree_termination_supported"] is True
        assert transport.pid is not None

    assert transport.is_running is False


@pytest.mark.parametrize(
    ("mode", "error_type"),
    [
        ("oversized", McpOutputLimitExceeded),
        ("malformed", McpProtocolError),
    ],
)
def test_stdio_transport_terminates_invalid_or_excessive_servers(
    tmp_path: Path,
    mode: str,
    error_type: type[Exception],
) -> None:
    transport = _transport(tmp_path, mode=mode, maximum_output=1_024)
    transport.start()

    with pytest.raises(error_type):
        transport.receive(timeout_seconds=5)
    transport.close()

    assert transport.is_running is False


def test_stdio_transport_propagates_cancellation_and_cleans_process_tree(
    tmp_path: Path,
) -> None:
    transport = _transport(tmp_path, mode="hang")
    cancellation = CancellationToken()
    transport.start()
    transport.send({"jsonrpc": "2.0", "id": 1, "method": "hang"})
    cancellation.request("operator cancelled MCP")

    with pytest.raises(CancellationRequested):
        transport.receive(timeout_seconds=5, cancellation=cancellation)

    state = cancellation.snapshot()
    assert state.acknowledged is True
    assert "mcp-stdio" in state.propagation_sources
    assert transport.is_running is False


def _transport(
    root: Path,
    *,
    mode: str = "normal",
    maximum_output: int = 262_144,
    source_environment: dict[str, str] | None = None,
) -> McpStdioTransport:
    alias = f"fake-mcp-{mode}"
    catalog = ExecutableCatalog(
        {alias: (sys.executable, "-u", str(FIXTURE), "--mode", mode)}
    )
    config = McpServerConfig(
        server_id="fixture",
        transport="stdio",
        executable_alias=alias,
        maximum_server_output_bytes=maximum_output,
        capability_map={"echo": mcp_server_capabilities("fixture")},
    )
    return McpStdioTransport(
        config,
        worktree=root,
        executable_catalog=catalog,
        source_environment=source_environment,
        shutdown_grace_seconds=0.2,
    )
