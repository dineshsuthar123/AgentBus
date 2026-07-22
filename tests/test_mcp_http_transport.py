from __future__ import annotations

import json
import threading
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from agentbus.execution.cancellation import CancellationRequested, CancellationToken
from agentbus.mcp import McpHttpTransport, McpServerConfig, mcp_server_capabilities
from agentbus.mcp.errors import McpOutputLimitExceeded, McpTransportError


TOKEN = "offline-loopback-test-token"


class _LoopbackHandler(BaseHTTPRequestHandler):
    mode = "normal"
    requests: list[dict] = []
    entered = threading.Event()
    release = threading.Event()

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", "0"))
        message = json.loads(self.rfile.read(length))
        type(self).requests.append(
            {
                "message": message,
                "authorization": self.headers.get("Authorization"),
                "protocol": self.headers.get("MCP-Protocol-Version"),
                "session": self.headers.get("Mcp-Session-Id"),
            }
        )
        if self.mode == "redirect":
            self.send_response(302)
            self.send_header("Location", "https://example.com/mcp")
            self.end_headers()
            return
        if self.mode == "hang":
            type(self).entered.set()
            type(self).release.wait(timeout=10)
        result = {
            "jsonrpc": "2.0",
            "id": message.get("id"),
            "result": {"ok": True, "padding": "x" * (2_048 if self.mode == "large" else 0)},
        }
        body = json.dumps(result, separators=(",", ":")).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Mcp-Session-Id", "offline-session")
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def log_message(self, format: str, *args) -> None:
        return


def test_loopback_http_authenticates_and_propagates_protocol_session_headers() -> None:
    with _server("normal") as endpoint:
        transport = _transport(endpoint)
        with transport:
            first = transport.request(_request(1), timeout_seconds=5)
            transport.set_protocol_version("2025-11-25")
            second = transport.request(_request(2), timeout_seconds=5)

        assert first["result"]["ok"] is True
        assert second["result"]["ok"] is True
        assert _LoopbackHandler.requests[0]["authorization"] == f"Bearer {TOKEN}"
        assert _LoopbackHandler.requests[1]["protocol"] == "2025-11-25"
        assert _LoopbackHandler.requests[1]["session"] == "offline-session"
        assert transport.safe_diagnostics["redirects_allowed"] is False
        assert TOKEN not in repr(transport.safe_diagnostics)


@pytest.mark.parametrize(
    ("mode", "error_type"),
    [("redirect", McpTransportError), ("large", McpOutputLimitExceeded)],
)
def test_loopback_http_rejects_redirects_and_oversized_output(
    mode: str,
    error_type: type[Exception],
) -> None:
    with _server(mode) as endpoint:
        transport = _transport(endpoint, maximum_output=1_024)
        with transport, pytest.raises(error_type):
            transport.request(_request(1), timeout_seconds=5)


def test_loopback_http_propagates_cancellation_without_waiting_for_server() -> None:
    with _server("hang") as endpoint:
        transport = _transport(endpoint)
        cancellation = CancellationToken()
        captured: list[BaseException] = []

        def invoke() -> None:
            try:
                with transport:
                    transport.request(
                        _request(1),
                        timeout_seconds=5,
                        cancellation=cancellation,
                    )
            except BaseException as exc:
                captured.append(exc)

        worker = threading.Thread(target=invoke, daemon=True)
        worker.start()
        assert _LoopbackHandler.entered.wait(timeout=5)
        cancellation.request("operator cancelled loopback MCP")
        worker.join(timeout=5)
        _LoopbackHandler.release.set()

        assert len(captured) == 1
        assert isinstance(captured[0], CancellationRequested)
        assert cancellation.snapshot().acknowledged is True


def _request(request_id: int) -> dict:
    return {"jsonrpc": "2.0", "id": request_id, "method": "ping", "params": {}}


def _transport(endpoint: str, *, maximum_output: int = 262_144) -> McpHttpTransport:
    return McpHttpTransport(
        McpServerConfig(
            server_id="loopback",
            transport="loopback_http",
            endpoint_url=endpoint,
            authorization_token=TOKEN,
            explicit_loopback_http=True,
            maximum_server_output_bytes=maximum_output,
            capability_map={"status": mcp_server_capabilities("loopback")},
        )
    )


@contextmanager
def _server(mode: str):
    _LoopbackHandler.mode = mode
    _LoopbackHandler.requests = []
    _LoopbackHandler.entered = threading.Event()
    _LoopbackHandler.release = threading.Event()
    server = ThreadingHTTPServer(("127.0.0.1", 0), _LoopbackHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        host, port = server.server_address
        yield f"http://{host}:{port}/mcp"
    finally:
        _LoopbackHandler.release.set()
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
