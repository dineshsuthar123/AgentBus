from __future__ import annotations

import json
import threading
from dataclasses import dataclass
from typing import Any

from agentbus import __version__
from agentbus.execution.cancellation import CancellationToken
from agentbus.mcp.errors import (
    McpProtocolError,
    McpRemoteError,
    McpUnsupportedProtocolVersion,
)
from agentbus.mcp.models import McpServerConfig
from agentbus.mcp.transport import McpTransport
from agentbus.security.redaction import redact_text


MAX_MCP_REMOTE_METADATA_BYTES = 65_536
MAX_MCP_REMOTE_TEXT_CHARS = 4_096


@dataclass(frozen=True)
class McpConnectionInfo:
    server_id: str
    protocol_version: str
    server_name: str
    server_version: str
    capabilities: tuple[str, ...]


class McpClient:
    def __init__(
        self,
        config: McpServerConfig,
        transport: McpTransport,
    ) -> None:
        self.config = config
        self.transport = transport
        self._lock = threading.RLock()
        self._next_request_id = 0
        self._connection: McpConnectionInfo | None = None

    @property
    def connection_info(self) -> McpConnectionInfo:
        connection = self._connection
        if connection is None:
            raise McpProtocolError("MCP client is not initialized.")
        return connection

    @property
    def is_connected(self) -> bool:
        return self._connection is not None

    def connect(
        self,
        *,
        cancellation: CancellationToken | None = None,
    ) -> McpConnectionInfo:
        with self._lock:
            if self._connection is not None:
                raise McpProtocolError("MCP client is already initialized.")
            self.transport.start()
            try:
                result = self._request(
                    "initialize",
                    {
                        "protocolVersion": self.config.supported_protocol_versions[0],
                        "capabilities": {},
                        "clientInfo": {
                            "name": "agentbus",
                            "version": __version__,
                        },
                    },
                    timeout_seconds=self.config.startup_timeout_seconds,
                    cancellation=cancellation,
                    require_connection=False,
                )
                connection = self._parse_initialize_result(result)
                self.transport.set_protocol_version(connection.protocol_version)
                self.transport.notify(
                    {
                        "jsonrpc": "2.0",
                        "method": "notifications/initialized",
                    },
                    timeout_seconds=self.config.startup_timeout_seconds,
                    cancellation=cancellation,
                )
                self._connection = connection
                return connection
            except BaseException:
                self.transport.close()
                raise

    def ping(
        self,
        *,
        cancellation: CancellationToken | None = None,
    ) -> None:
        result = self._request(
            "ping",
            {},
            timeout_seconds=self.config.request_timeout_seconds,
            cancellation=cancellation,
        )
        if result != {}:
            raise McpProtocolError("MCP ping result must be an empty object.")

    def close(self) -> None:
        with self._lock:
            self._connection = None
            self.transport.close()

    def __enter__(self) -> "McpClient":
        self.connect()
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        self.close()

    def _request(
        self,
        method: str,
        params: dict[str, Any],
        *,
        timeout_seconds: float,
        cancellation: CancellationToken | None,
        require_connection: bool = True,
    ) -> dict[str, Any]:
        if require_connection and self._connection is None:
            raise McpProtocolError("MCP client must initialize before requests.")
        with self._lock:
            self._next_request_id += 1
            request_id = self._next_request_id
            response = self.transport.request(
                {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "method": method,
                    "params": params,
                },
                timeout_seconds=timeout_seconds,
                cancellation=cancellation,
            )
        return _response_result(response, request_id)

    def _parse_initialize_result(
        self,
        result: dict[str, Any],
    ) -> McpConnectionInfo:
        _require_bounded_json(result, "MCP initialize result")
        protocol_version = result.get("protocolVersion")
        if protocol_version not in self.config.supported_protocol_versions:
            raise McpUnsupportedProtocolVersion(
                "MCP server selected an unsupported protocol version."
            )
        capabilities = result.get("capabilities")
        if not isinstance(capabilities, dict) or not isinstance(
            capabilities.get("tools"),
            dict,
        ):
            raise McpProtocolError("MCP server did not declare the tools capability.")
        server_info = result.get("serverInfo")
        if not isinstance(server_info, dict):
            raise McpProtocolError("MCP server did not provide bounded serverInfo.")
        server_name = _bounded_remote_text(server_info.get("name"), "server name")
        server_version = _bounded_remote_text(
            server_info.get("version"),
            "server version",
        )
        instructions = result.get("instructions")
        if instructions is not None:
            _bounded_remote_text(instructions, "server instructions")
        return McpConnectionInfo(
            server_id=self.config.server_id,
            protocol_version=protocol_version,
            server_name=server_name,
            server_version=server_version,
            capabilities=tuple(sorted(str(name) for name in capabilities)),
        )


def _response_result(
    response: dict[str, Any],
    request_id: str | int,
) -> dict[str, Any]:
    _require_bounded_json(response, "MCP JSON-RPC response")
    response_id = response.get("id")
    ids_match = (
        not isinstance(response_id, bool)
        and type(response_id) is type(request_id)
        and response_id == request_id
    )
    if response.get("jsonrpc") != "2.0" or not ids_match:
        raise McpProtocolError("MCP response identity does not match its request.")
    has_result = "result" in response
    has_error = "error" in response
    if has_result == has_error:
        raise McpProtocolError(
            "MCP response must contain exactly one result or error."
        )
    if has_error:
        error = response["error"]
        error_code = error.get("code") if isinstance(error, dict) else None
        if (
            not isinstance(error, dict)
            or not isinstance(error_code, int)
            or isinstance(error_code, bool)
        ):
            raise McpProtocolError("MCP server returned a malformed JSON-RPC error.")
        message = _bounded_remote_text(error.get("message"), "error message")
        safe_message = redact_text(message, max_chars=512) or "Remote MCP error"
        raise McpRemoteError(
            f"MCP server returned error {error['code']}: {safe_message}"
        )
    result = response["result"]
    if not isinstance(result, dict):
        raise McpProtocolError("MCP response result must be an object.")
    return result


def _require_bounded_json(value: Any, label: str) -> None:
    try:
        encoded = json.dumps(
            value,
            ensure_ascii=False,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise McpProtocolError(f"{label} must contain finite JSON.") from exc
    if len(encoded) > MAX_MCP_REMOTE_METADATA_BYTES:
        raise McpProtocolError(f"{label} exceeds the bounded metadata size.")


def _bounded_remote_text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise McpProtocolError(f"MCP {label} must be a non-empty string.")
    text = value.strip()
    if len(text) > MAX_MCP_REMOTE_TEXT_CHARS or "\x00" in text:
        raise McpProtocolError(f"MCP {label} exceeds the bounded text size.")
    return text
