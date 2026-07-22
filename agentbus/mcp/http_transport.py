from __future__ import annotations

import json
import queue
import threading
import time
from typing import Any
from urllib.parse import urlsplit

from agentbus.execution.cancellation import CancellationToken
from agentbus.mcp.errors import (
    McpOutputLimitExceeded,
    McpProtocolError,
    McpRequestTimeout,
    McpTransportError,
)
from agentbus.mcp.models import McpServerConfig, McpTransportKind
from agentbus.security.redaction import safe_endpoint_host


MAX_HTTP_RESPONSE_MESSAGES = 256


class McpHttpTransport:
    def __init__(self, config: McpServerConfig) -> None:
        if config.transport != McpTransportKind.LOOPBACK_HTTP:
            raise ValueError(
                "McpHttpTransport requires loopback HTTP server configuration"
            )
        self.config = config
        self._started = False
        self._closed = False
        self._state_lock = threading.RLock()
        self._active_clients: set[Any] = set()
        self._protocol_version: str | None = None
        self._session_id: str | None = None

    @property
    def safe_diagnostics(self) -> dict[str, Any]:
        return {
            "server_id": self.config.server_id,
            "transport": "loopback_http",
            "endpoint_host": safe_endpoint_host(self.config.endpoint_url),
            "authenticated": True,
            "redirects_allowed": False,
            "proxy_environment_allowed": False,
            "protocol_version": self._protocol_version,
            "session_established": self._session_id is not None,
        }

    def start(self) -> None:
        with self._state_lock:
            if self._started:
                raise McpTransportError("MCP HTTP transport was already started.")
            if self._closed:
                raise McpTransportError("MCP HTTP transport is closed.")
            _require_httpx()
            self._started = True

    def set_protocol_version(self, protocol_version: str) -> None:
        if protocol_version not in self.config.supported_protocol_versions:
            raise McpProtocolError("Cannot set an unsupported MCP protocol version.")
        self._protocol_version = protocol_version

    def request(
        self,
        message: dict[str, Any],
        *,
        timeout_seconds: float,
        cancellation: CancellationToken | None = None,
    ) -> dict[str, Any]:
        request_id = message.get("id")
        if request_id is None:
            raise McpProtocolError("MCP HTTP requests require a JSON-RPC ID.")
        responses = self._post(
            message,
            timeout_seconds=timeout_seconds,
            cancellation=cancellation,
            notification=False,
        )
        matches = [
            item
            for item in responses
            if _request_ids_match(item.get("id"), request_id)
        ]
        if len(matches) != 1:
            raise McpProtocolError(
                "MCP HTTP response did not contain exactly one matching request ID."
            )
        return matches[0]

    def notify(
        self,
        message: dict[str, Any],
        *,
        timeout_seconds: float,
        cancellation: CancellationToken | None = None,
    ) -> None:
        if "id" in message:
            raise McpProtocolError("MCP notifications must not contain an ID.")
        self._post(
            message,
            timeout_seconds=timeout_seconds,
            cancellation=cancellation,
            notification=True,
        )

    def close(self) -> None:
        with self._state_lock:
            if self._closed:
                return
            self._closed = True
            self._started = False
            clients = tuple(self._active_clients)
        for client in clients:
            try:
                client.close()
            except Exception:
                pass

    def __enter__(self) -> "McpHttpTransport":
        self.start()
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        self.close()

    def _post(
        self,
        message: dict[str, Any],
        *,
        timeout_seconds: float,
        cancellation: CancellationToken | None,
        notification: bool,
    ) -> tuple[dict[str, Any], ...]:
        if timeout_seconds <= 0:
            raise ValueError("MCP HTTP timeout must be positive")
        self._require_started()
        effective_timeout = min(
            timeout_seconds,
            self.config.request_timeout_seconds,
        )
        encoded = _encode_message(message)
        results: queue.Queue[tuple[dict[str, Any], ...] | BaseException] = (
            queue.Queue(maxsize=1)
        )
        worker = threading.Thread(
            target=self._post_worker,
            args=(encoded, effective_timeout, notification, results),
            name=f"agentbus-mcp-http-{self.config.server_id}",
            daemon=True,
        )
        worker.start()
        deadline = time.monotonic() + effective_timeout
        while True:
            if cancellation is not None and cancellation.is_requested:
                cancellation.mark_propagated("mcp-loopback-http")
                self._interrupt_active_clients()
                worker.join(timeout=1)
                cancellation.checkpoint(
                    "mcp-loopback-http",
                    stage="http-request-closed",
                )
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                self._interrupt_active_clients()
                worker.join(timeout=1)
                raise McpRequestTimeout(
                    f"MCP HTTP request timed out: {self.config.server_id}."
                )
            try:
                result = results.get(timeout=min(remaining, 0.05))
            except queue.Empty:
                continue
            if isinstance(result, BaseException):
                raise result
            return result

    def _post_worker(
        self,
        encoded: bytes,
        timeout_seconds: float,
        notification: bool,
        results: queue.Queue[tuple[dict[str, Any], ...] | BaseException],
    ) -> None:
        httpx = _require_httpx()
        client = httpx.Client(
            follow_redirects=False,
            trust_env=False,
            timeout=timeout_seconds,
        )
        with self._state_lock:
            if self._closed:
                results.put(McpTransportError("MCP HTTP transport is closed."))
                client.close()
                return
            self._active_clients.add(client)
        try:
            messages = self._perform_post(client, encoded, notification)
            results.put(messages)
        except (McpProtocolError, McpTransportError) as exc:
            results.put(exc)
        except Exception:
            results.put(
                McpTransportError(
                    f"MCP loopback HTTP request failed: {self.config.server_id}."
                )
            )
        finally:
            with self._state_lock:
                self._active_clients.discard(client)
            try:
                client.close()
            except Exception:
                pass

    def _perform_post(
        self,
        client,
        encoded: bytes,
        notification: bool,
    ) -> tuple[dict[str, Any], ...]:
        endpoint = self.config.endpoint_url
        token = self.config.authorization_token
        if endpoint is None or token is None:
            raise McpTransportError("MCP HTTP endpoint is not fully configured.")
        with client.stream(
            "POST",
            endpoint,
            content=encoded,
            headers=self._headers(token.get_secret_value()),
        ) as response:
            if response.is_redirect:
                raise McpTransportError("MCP HTTP redirects are not allowed.")
            if response.status_code not in ({200, 202, 204} if notification else {200}):
                raise McpTransportError(
                    f"MCP HTTP server returned status {response.status_code}."
                )
            self._capture_session(response.headers.get("Mcp-Session-Id"))
            body = bytearray()
            for chunk in response.iter_bytes():
                body.extend(chunk)
                if len(body) > self.config.maximum_server_output_bytes:
                    raise McpOutputLimitExceeded(
                        "MCP HTTP server exceeded its response output limit."
                    )
            if not body:
                if notification:
                    return ()
                raise McpProtocolError("MCP HTTP request returned no response message.")
            return _decode_http_messages(bytes(body), response.headers.get("content-type"))

    def _headers(self, token: str) -> dict[str, str]:
        endpoint = self.config.endpoint_url
        if endpoint is None:
            raise McpTransportError("MCP HTTP endpoint is unavailable.")
        parsed = urlsplit(endpoint)
        headers = {
            "Accept": "application/json, text/event-stream",
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Origin": f"{parsed.scheme}://{parsed.netloc}",
        }
        if self._protocol_version is not None:
            headers["MCP-Protocol-Version"] = self._protocol_version
        if self._session_id is not None:
            headers["Mcp-Session-Id"] = self._session_id
        return headers

    def _capture_session(self, session_id: str | None) -> None:
        if session_id is None:
            return
        if not 1 <= len(session_id) <= 256 or any(
            not 0x21 <= ord(character) <= 0x7E for character in session_id
        ):
            raise McpProtocolError("MCP HTTP server returned an invalid session ID.")
        with self._state_lock:
            if self._session_id is not None and self._session_id != session_id:
                raise McpProtocolError("MCP HTTP server changed its session ID.")
            self._session_id = session_id

    def _interrupt_active_clients(self) -> None:
        with self._state_lock:
            clients = tuple(self._active_clients)
        for client in clients:
            try:
                client.close()
            except Exception:
                pass

    def _require_started(self) -> None:
        with self._state_lock:
            if not self._started or self._closed:
                raise McpTransportError("MCP HTTP transport is not running.")


def _require_httpx():
    try:
        import httpx
    except ImportError as exc:
        raise McpTransportError(
            "Loopback HTTP MCP requires the AgentBus 'mcp' optional extra."
        ) from exc
    return httpx


def _encode_message(message: dict[str, Any]) -> bytes:
    if not isinstance(message, dict) or message.get("jsonrpc") != "2.0":
        raise McpProtocolError("MCP HTTP messages require a JSON-RPC 2.0 object.")
    try:
        encoded = json.dumps(
            message,
            ensure_ascii=False,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise McpProtocolError("MCP HTTP messages must contain finite JSON.") from exc
    if len(encoded) > 1_048_576:
        raise McpProtocolError("MCP HTTP request exceeds the bounded size.")
    return encoded


def _decode_http_messages(
    body: bytes,
    content_type: str | None,
) -> tuple[dict[str, Any], ...]:
    media_type = (content_type or "").split(";", 1)[0].strip().lower()
    if media_type == "application/json":
        values = [_decode_json(body)]
    elif media_type == "text/event-stream":
        values = _decode_sse(body)
    else:
        raise McpProtocolError("MCP HTTP server returned an unsupported content type.")
    messages: list[dict[str, Any]] = []
    for value in values:
        batch = value if isinstance(value, list) else [value]
        for message in batch:
            if not isinstance(message, dict) or message.get("jsonrpc") != "2.0":
                raise McpProtocolError(
                    "MCP HTTP response messages require JSON-RPC 2.0 objects."
                )
            messages.append(message)
            if len(messages) > MAX_HTTP_RESPONSE_MESSAGES:
                raise McpOutputLimitExceeded(
                    "MCP HTTP response exceeded the bounded message count."
                )
    if not messages:
        raise McpProtocolError("MCP HTTP response contained no messages.")
    return tuple(messages)


def _decode_json(raw: bytes) -> Any:
    try:
        return json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise McpProtocolError("MCP HTTP server returned invalid UTF-8 JSON.") from exc


def _decode_sse(raw: bytes) -> list[Any]:
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise McpProtocolError("MCP HTTP server returned invalid UTF-8 SSE.") from exc
    values: list[Any] = []
    data_lines: list[str] = []
    for line in text.replace("\r\n", "\n").split("\n"):
        if not line:
            if data_lines:
                values.append(_decode_json("\n".join(data_lines).encode("utf-8")))
                data_lines = []
            continue
        if line.startswith("data:"):
            data_lines.append(line[5:].lstrip(" "))
    if data_lines:
        values.append(_decode_json("\n".join(data_lines).encode("utf-8")))
    if not values:
        raise McpProtocolError("MCP HTTP SSE response contained no data events.")
    return values


def _request_ids_match(candidate: Any, expected: str | int) -> bool:
    if isinstance(candidate, bool) or isinstance(expected, bool):
        return False
    return type(candidate) is type(expected) and candidate == expected
