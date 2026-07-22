from __future__ import annotations

import json
import os
import queue
import subprocess
import tempfile
import threading
import time
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Protocol

from agentbus.execution.cancellation import CancellationToken
from agentbus.mcp.errors import (
    McpOutputLimitExceeded,
    McpProtocolError,
    McpRequestTimeout,
    McpTransportError,
)
from agentbus.mcp.models import McpServerConfig, McpTransportKind
from agentbus.sandbox.environment import (
    environment_diagnostics,
    sanitized_process_environment,
)
from agentbus.sandbox.platform import ExecutableCatalog, validate_working_directory
from agentbus.sandbox.process_tree import ManagedProcessTree
from agentbus.security.redaction import redact_text
from agentbus.tools.protocol import ToolResourceBudget


MAX_MCP_CLIENT_MESSAGE_BYTES = 1_048_576
MAX_MCP_MESSAGE_BATCH = 256
_END_OF_STREAM = object()


class McpTransport(Protocol):
    def start(self) -> None: ...

    def request(
        self,
        message: dict[str, Any],
        *,
        timeout_seconds: float,
        cancellation: CancellationToken | None = None,
    ) -> dict[str, Any]: ...

    def notify(
        self,
        message: dict[str, Any],
        *,
        timeout_seconds: float,
        cancellation: CancellationToken | None = None,
    ) -> None: ...

    def set_protocol_version(self, protocol_version: str) -> None: ...

    def close(self) -> None: ...


class McpStdioTransport:
    def __init__(
        self,
        config: McpServerConfig,
        *,
        worktree: str | Path,
        executable_catalog: ExecutableCatalog,
        source_environment: Mapping[str, str] | None = None,
        shutdown_grace_seconds: float = 1.0,
    ) -> None:
        if config.transport != McpTransportKind.STDIO:
            raise ValueError("McpStdioTransport requires stdio server configuration")
        if shutdown_grace_seconds <= 0 or shutdown_grace_seconds > 10:
            raise ValueError("MCP shutdown grace must be between 0 and 10 seconds")
        self.config = config
        self.worktree = validate_working_directory(worktree)
        self.catalog = executable_catalog
        self.source_environment = source_environment
        self.shutdown_grace_seconds = shutdown_grace_seconds
        self._process: subprocess.Popen[bytes] | None = None
        self._tree: ManagedProcessTree | None = None
        self._isolated_home: tempfile.TemporaryDirectory[str] | None = None
        self._messages: queue.Queue[dict[str, Any] | BaseException | object] = (
            queue.Queue(maxsize=MAX_MCP_MESSAGE_BATCH)
        )
        self._write_lock = threading.Lock()
        self._request_lock = threading.Lock()
        self._state_lock = threading.RLock()
        self._output_lock = threading.Lock()
        self._closing = threading.Event()
        self._readers: tuple[threading.Thread, threading.Thread] = ()
        self._output_bytes = 0
        self._stderr = bytearray()
        self._safe_diagnostics: dict[str, Any] = {}

    @property
    def pid(self) -> int | None:
        process = self._process
        return process.pid if process is not None else None

    @property
    def is_running(self) -> bool:
        process = self._process
        return process is not None and process.poll() is None

    @property
    def safe_diagnostics(self) -> dict[str, Any]:
        with self._state_lock:
            return dict(self._safe_diagnostics)

    def start(self) -> None:
        with self._state_lock:
            if self._process is not None:
                raise McpTransportError("MCP stdio transport was already started.")
            if self._closing.is_set():
                raise McpTransportError("MCP stdio transport cannot be restarted.")
            alias = self.config.executable_alias
            if alias is None:
                raise McpTransportError("MCP stdio executable is not configured.")
            identity = self.catalog.resolve(alias)
            if os.name == "nt" and identity.path.suffix.lower() in {".bat", ".cmd"}:
                raise McpTransportError(
                    "MCP stdio requires a native executable or allowlisted interpreter, "
                    "not a Windows command script."
                )
            cwd = validate_working_directory(
                self.worktree,
                self.config.working_directory,
            )
            isolated_home = tempfile.TemporaryDirectory(prefix="agentbus-mcp-")
            environment = sanitized_process_environment(
                source=self.source_environment,
                executable_directories=self.catalog.executable_directories,
                overrides=self.config.environment,
                isolated_home=isolated_home.name,
            )
            command = identity.command(self.config.arguments)
            try:
                process = subprocess.Popen(
                    command,
                    cwd=cwd,
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    shell=False,
                    env=environment,
                    close_fds=True,
                    **ManagedProcessTree.launch_options(),
                )
            except OSError as exc:
                isolated_home.cleanup()
                raise McpTransportError(
                    f"Could not launch configured MCP server: {self.config.server_id}."
                ) from exc
            budget = ToolResourceBudget(
                wall_clock_seconds=self.config.request_timeout_seconds,
                stdout_bytes=self.config.maximum_server_output_bytes,
                stderr_bytes=self.config.maximum_server_output_bytes,
                combined_output_bytes=(
                    self.config.maximum_server_output_bytes * 2
                ),
            )
            try:
                tree = ManagedProcessTree.attach(process, budget)
            except Exception:
                process.kill()
                process.wait(timeout=2)
                isolated_home.cleanup()
                raise
            self._process = process
            self._tree = tree
            self._isolated_home = isolated_home
            self._safe_diagnostics = {
                "server_id": self.config.server_id,
                "transport": "stdio",
                "executable": identity.safe_metadata(),
                "working_directory": str(cwd),
                "pid": process.pid,
                "environment": environment_diagnostics(environment),
                "process_tree": tree.metadata.safe_metadata(),
                "isolated_home": True,
                "shell": False,
            }
            self._readers = self._start_readers(process)

    def send(self, message: dict[str, Any]) -> None:
        process = self._require_running()
        if process.stdin is None:
            raise McpTransportError("MCP server input stream is unavailable.")
        try:
            encoded = json.dumps(
                message,
                ensure_ascii=False,
                separators=(",", ":"),
                allow_nan=False,
            ).encode("utf-8")
        except (TypeError, ValueError) as exc:
            raise McpProtocolError("MCP client message must be finite JSON.") from exc
        if len(encoded) > MAX_MCP_CLIENT_MESSAGE_BYTES:
            raise McpProtocolError("MCP client message exceeds the bounded size.")
        with self._write_lock:
            try:
                process.stdin.write(encoded + b"\n")
                process.stdin.flush()
            except (BrokenPipeError, OSError, ValueError) as exc:
                raise McpTransportError(
                    "MCP server closed its input stream unexpectedly."
                ) from exc

    def request(
        self,
        message: dict[str, Any],
        *,
        timeout_seconds: float,
        cancellation: CancellationToken | None = None,
    ) -> dict[str, Any]:
        request_id = message.get("id")
        if request_id is None:
            raise McpProtocolError("MCP stdio requests require a JSON-RPC ID.")
        effective_timeout = min(
            timeout_seconds,
            self.config.request_timeout_seconds,
        )
        deadline = time.monotonic() + effective_timeout
        with self._request_lock:
            self.send(message)
            while True:
                if cancellation is not None and cancellation.is_requested:
                    self._best_effort_cancel(request_id, "AgentBus run cancelled")
                    cancellation.mark_propagated("mcp-stdio")
                    self.close()
                    cancellation.checkpoint(
                        "mcp-stdio",
                        stage="process-tree-terminated",
                    )
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    self._best_effort_cancel(request_id, "AgentBus request timed out")
                    raise McpRequestTimeout(
                        f"MCP server request timed out: {self.config.server_id}."
                    )
                try:
                    response = self.receive(
                        timeout_seconds=min(remaining, 0.05),
                    )
                except McpRequestTimeout:
                    continue
                if _request_ids_match(response.get("id"), request_id):
                    return response
                if "id" in response and "method" in response:
                    self.send(
                        {
                            "jsonrpc": "2.0",
                            "id": response["id"],
                            "error": {
                                "code": -32601,
                                "message": "Server-initiated requests are not supported",
                            },
                        }
                    )
                    continue
                if "method" in response and "id" not in response:
                    continue
                raise McpProtocolError(
                    "MCP server returned a response for an unexpected request ID."
                )

    def notify(
        self,
        message: dict[str, Any],
        *,
        timeout_seconds: float,
        cancellation: CancellationToken | None = None,
    ) -> None:
        del timeout_seconds
        if "id" in message:
            raise McpProtocolError("MCP notifications must not contain an ID.")
        if cancellation is not None:
            cancellation.checkpoint("mcp-stdio", stage="before-notification")
        self.send(message)

    def set_protocol_version(self, protocol_version: str) -> None:
        if protocol_version not in self.config.supported_protocol_versions:
            raise McpProtocolError("Cannot set an unsupported MCP protocol version.")

    def receive(
        self,
        *,
        timeout_seconds: float,
        cancellation: CancellationToken | None = None,
    ) -> dict[str, Any]:
        if timeout_seconds <= 0:
            raise ValueError("MCP receive timeout must be positive")
        self._require_started()
        deadline = time.monotonic() + timeout_seconds
        while True:
            if cancellation is not None and cancellation.is_requested:
                cancellation.mark_propagated("mcp-stdio")
                self.close()
                cancellation.checkpoint(
                    "mcp-stdio",
                    stage="process-tree-terminated",
                )
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise McpRequestTimeout(
                    f"MCP server request timed out: {self.config.server_id}."
                )
            try:
                item = self._messages.get(timeout=min(remaining, 0.05))
            except queue.Empty:
                continue
            if item is _END_OF_STREAM:
                raise McpTransportError(self._closed_server_message())
            if isinstance(item, BaseException):
                raise item
            return item

    def close(self) -> None:
        with self._state_lock:
            process = self._process
            tree = self._tree
            isolated_home = self._isolated_home
            if process is None:
                return
            self._closing.set()
            self._process = None
            self._tree = None
            self._isolated_home = None
        if process.stdin is not None:
            try:
                process.stdin.close()
            except OSError:
                pass
        try:
            process.wait(timeout=self.shutdown_grace_seconds)
        except subprocess.TimeoutExpired:
            if tree is not None:
                tree.terminate(grace_seconds=self.shutdown_grace_seconds)
            else:
                process.kill()
                process.wait(timeout=self.shutdown_grace_seconds)
        finally:
            if tree is not None:
                tree.close(grace_seconds=self.shutdown_grace_seconds)
            for stream in (process.stdout, process.stderr):
                if stream is not None:
                    try:
                        stream.close()
                    except OSError:
                        pass
            current = threading.current_thread()
            for reader in self._readers:
                if reader is not current:
                    reader.join(timeout=self.shutdown_grace_seconds)
            if isolated_home is not None:
                isolated_home.cleanup()

    def __enter__(self) -> "McpStdioTransport":
        self.start()
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        self.close()

    def _start_readers(
        self,
        process: subprocess.Popen[bytes],
    ) -> tuple[threading.Thread, threading.Thread]:
        if process.stdout is None or process.stderr is None:
            raise McpTransportError("MCP server output streams are unavailable.")
        stdout = threading.Thread(
            target=self._read_stdout,
            args=(process.stdout,),
            name=f"agentbus-mcp-stdout-{process.pid}",
            daemon=True,
        )
        stderr = threading.Thread(
            target=self._read_stderr,
            args=(process.stderr,),
            name=f"agentbus-mcp-stderr-{process.pid}",
            daemon=True,
        )
        stdout.start()
        stderr.start()
        return stdout, stderr

    def _read_stdout(self, stream) -> None:
        maximum_line = min(
            self.config.maximum_server_output_bytes,
            self.config.maximum_tool_output_bytes,
        )
        try:
            while not self._closing.is_set():
                line = stream.readline(maximum_line + 2)
                if not line:
                    self._offer(_END_OF_STREAM)
                    return
                self._account_output(len(line))
                if len(line) > maximum_line or not line.endswith(b"\n"):
                    raise McpOutputLimitExceeded(
                        "MCP server emitted an oversized or unterminated message."
                    )
                self._decode_and_offer(line[:-1])
        except BaseException as exc:
            if not self._closing.is_set():
                self._fail(exc)

    def _read_stderr(self, stream) -> None:
        try:
            for chunk in iter(lambda: stream.read(16_384), b""):
                if self._closing.is_set():
                    return
                self._account_output(len(chunk))
                remaining = min(
                    65_536,
                    self.config.maximum_server_output_bytes,
                ) - len(self._stderr)
                if remaining > 0:
                    self._stderr.extend(chunk[:remaining])
        except BaseException as exc:
            if not self._closing.is_set():
                self._fail(exc)

    def _decode_and_offer(self, raw: bytes) -> None:
        try:
            value = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise McpProtocolError("MCP server emitted invalid UTF-8 JSON.") from exc
        messages = value if isinstance(value, list) else [value]
        if not messages or len(messages) > MAX_MCP_MESSAGE_BATCH:
            raise McpProtocolError("MCP server emitted an invalid JSON-RPC batch.")
        for message in messages:
            if not isinstance(message, dict):
                raise McpProtocolError("MCP server messages must be JSON objects.")
            if message.get("jsonrpc") != "2.0":
                raise McpProtocolError("MCP server messages require jsonrpc 2.0.")
            self._offer(message)

    def _account_output(self, size: int) -> None:
        with self._output_lock:
            self._output_bytes += size
            if self._output_bytes > self.config.maximum_server_output_bytes:
                raise McpOutputLimitExceeded(
                    "MCP server exceeded its cumulative output limit."
                )

    def _offer(self, item: dict[str, Any] | BaseException | object) -> None:
        try:
            self._messages.put_nowait(item)
        except queue.Full as exc:
            raise McpOutputLimitExceeded(
                "MCP server exceeded the bounded pending-message queue."
            ) from exc

    def _fail(self, error: BaseException) -> None:
        safe_error: BaseException
        if isinstance(error, (McpProtocolError, McpTransportError)):
            safe_error = error
        else:
            safe_error = McpTransportError("MCP server output reader failed.")
        try:
            self._offer(safe_error)
        except McpOutputLimitExceeded:
            pass
        tree = self._tree
        if tree is not None:
            tree.terminate(grace_seconds=self.shutdown_grace_seconds)

    def _require_started(self) -> subprocess.Popen[bytes]:
        process = self._process
        if process is None:
            raise McpTransportError("MCP stdio transport is not running.")
        return process

    def _require_running(self) -> subprocess.Popen[bytes]:
        process = self._require_started()
        if process.poll() is not None:
            raise McpTransportError(self._closed_server_message())
        return process

    def _closed_server_message(self) -> str:
        stderr = redact_text(
            self._stderr.decode("utf-8", errors="replace"),
            max_chars=2_000,
        )
        suffix = f" Diagnostic: {stderr}" if stderr else ""
        return f"MCP server exited unexpectedly: {self.config.server_id}.{suffix}"

    def _best_effort_cancel(self, request_id: str | int, reason: str) -> None:
        try:
            self.send(
                {
                    "jsonrpc": "2.0",
                    "method": "notifications/cancelled",
                    "params": {"requestId": request_id, "reason": reason},
                }
            )
        except McpTransportError:
            pass


def _request_ids_match(candidate: Any, expected: str | int) -> bool:
    if isinstance(candidate, bool) or isinstance(expected, bool):
        return False
    return type(candidate) is type(expected) and candidate == expected
