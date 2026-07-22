from __future__ import annotations

import json
import threading
from dataclasses import dataclass
from typing import Any

import jsonschema

from agentbus import __version__
from agentbus.execution.cancellation import CancellationToken
from agentbus.mcp.errors import (
    McpProtocolError,
    McpRemoteError,
    McpUnsupportedProtocolVersion,
)
from agentbus.mcp.models import McpServerConfig, namespace_mcp_tool
from agentbus.mcp.transport import McpTransport
from agentbus.security.redaction import redact_text


MAX_MCP_REMOTE_METADATA_BYTES = 65_536
MAX_MCP_REMOTE_TEXT_CHARS = 4_096
MAX_MCP_TOOL_PAGES = 16
MAX_MCP_TOOLS = 256
MAX_MCP_CONTENT_ITEMS = 256


@dataclass(frozen=True)
class McpConnectionInfo:
    server_id: str
    protocol_version: str
    server_name: str
    server_version: str
    capabilities: tuple[str, ...]


@dataclass(frozen=True)
class McpRemoteTool:
    name: str
    namespaced_name: str
    description: str
    input_schema: dict[str, Any]
    output_schema: dict[str, Any] | None
    annotations: dict[str, Any]


@dataclass(frozen=True)
class McpToolCallResult:
    content: tuple[dict[str, Any], ...]
    structured_content: dict[str, Any] | None
    is_error: bool


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
        self._tools: dict[str, McpRemoteTool] = {}

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

    def list_tools(
        self,
        *,
        cancellation: CancellationToken | None = None,
    ) -> tuple[McpRemoteTool, ...]:
        tools: dict[str, McpRemoteTool] = {}
        namespaces: set[str] = set()
        seen_cursors: set[str] = set()
        cursor: str | None = None
        for _ in range(MAX_MCP_TOOL_PAGES):
            params = {"cursor": cursor} if cursor is not None else {}
            result = self._request(
                "tools/list",
                params,
                timeout_seconds=self.config.request_timeout_seconds,
                cancellation=cancellation,
            )
            _require_bounded_json(result, "MCP tools/list result")
            raw_tools = result.get("tools")
            if not isinstance(raw_tools, list):
                raise McpProtocolError("MCP tools/list result requires a tools array.")
            for raw_tool in raw_tools:
                tool = self._parse_remote_tool(raw_tool)
                if tool.name in tools or tool.namespaced_name in namespaces:
                    raise McpProtocolError("MCP server returned colliding tool names.")
                tools[tool.name] = tool
                namespaces.add(tool.namespaced_name)
                if len(tools) > MAX_MCP_TOOLS:
                    raise McpProtocolError("MCP server returned too many tools.")
            next_cursor = result.get("nextCursor")
            if next_cursor is None:
                self._tools = tools
                return tuple(tools[name] for name in sorted(tools))
            if (
                not isinstance(next_cursor, str)
                or not next_cursor
                or len(next_cursor) > 1_024
                or next_cursor in seen_cursors
            ):
                raise McpProtocolError("MCP server returned an invalid tools cursor.")
            seen_cursors.add(next_cursor)
            cursor = next_cursor
        raise McpProtocolError("MCP tools/list exceeded the pagination limit.")

    def call_tool(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        *,
        timeout_seconds: float | None = None,
        cancellation: CancellationToken | None = None,
    ) -> McpToolCallResult:
        try:
            tool = self._tools[tool_name]
        except KeyError as exc:
            raise McpProtocolError(
                "MCP tools must be discovered and capability-mapped before invocation."
            ) from exc
        _validate_schema_instance(
            arguments,
            tool.input_schema,
            "MCP tool arguments",
        )
        result = self._request(
            "tools/call",
            {"name": tool.name, "arguments": arguments},
            timeout_seconds=min(
                timeout_seconds or self.config.request_timeout_seconds,
                self.config.request_timeout_seconds,
            ),
            cancellation=cancellation,
        )
        _require_bounded_json(
            result,
            "MCP tools/call result",
            maximum_bytes=self.config.maximum_tool_output_bytes,
        )
        content = result.get("content", [])
        if not isinstance(content, list) or len(content) > MAX_MCP_CONTENT_ITEMS:
            raise McpProtocolError("MCP tool result content must be a bounded array.")
        if any(not isinstance(item, dict) for item in content):
            raise McpProtocolError("MCP tool result content items must be objects.")
        structured = result.get("structuredContent")
        if structured is not None and not isinstance(structured, dict):
            raise McpProtocolError("MCP structured tool output must be an object.")
        is_error = result.get("isError", False)
        if not isinstance(is_error, bool):
            raise McpProtocolError("MCP tool isError must be a boolean.")
        if tool.output_schema is not None and not is_error:
            if structured is None:
                raise McpProtocolError(
                    "MCP tool declared outputSchema but omitted structuredContent."
                )
            _validate_schema_instance(
                structured,
                tool.output_schema,
                "MCP structured tool output",
            )
        return McpToolCallResult(
            content=tuple(content),
            structured_content=structured,
            is_error=is_error,
        )

    def close(self) -> None:
        with self._lock:
            self._connection = None
            self._tools = {}
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

    def _parse_remote_tool(self, value: Any) -> McpRemoteTool:
        if not isinstance(value, dict):
            raise McpProtocolError("MCP tool definitions must be objects.")
        _require_bounded_json(value, "MCP tool definition")
        name = value.get("name")
        if not isinstance(name, str):
            raise McpProtocolError("MCP tool definitions require a name.")
        try:
            namespaced = namespace_mcp_tool(self.config.server_id, name)
            self.config.capabilities_for(name)
        except ValueError as exc:
            raise McpProtocolError(str(exc)) from exc
        description_value = value.get("description")
        description = (
            _bounded_remote_text(description_value, "tool description")
            if description_value is not None
            else f"Imported MCP tool {name} from {self.config.server_id}."
        )
        input_schema = value.get("inputSchema")
        _validate_remote_schema(input_schema, "MCP tool inputSchema")
        output_schema = value.get("outputSchema")
        if output_schema is not None:
            _validate_remote_schema(output_schema, "MCP tool outputSchema")
        annotations = value.get("annotations", {})
        if not isinstance(annotations, dict):
            raise McpProtocolError("MCP tool annotations must be an object.")
        _require_bounded_json(annotations, "MCP tool annotations")
        return McpRemoteTool(
            name=name,
            namespaced_name=namespaced,
            description=description,
            input_schema=input_schema,
            output_schema=output_schema,
            annotations=annotations,
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


def _require_bounded_json(
    value: Any,
    label: str,
    *,
    maximum_bytes: int = MAX_MCP_REMOTE_METADATA_BYTES,
) -> None:
    try:
        encoded = json.dumps(
            value,
            ensure_ascii=False,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise McpProtocolError(f"{label} must contain finite JSON.") from exc
    if len(encoded) > maximum_bytes:
        raise McpProtocolError(f"{label} exceeds the bounded metadata size.")


def _bounded_remote_text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise McpProtocolError(f"MCP {label} must be a non-empty string.")
    text = value.strip()
    if len(text) > MAX_MCP_REMOTE_TEXT_CHARS or "\x00" in text:
        raise McpProtocolError(f"MCP {label} exceeds the bounded text size.")
    return text


def _validate_remote_schema(value: Any, label: str) -> None:
    if not isinstance(value, dict) or value.get("type") != "object":
        raise McpProtocolError(f"{label} must be an object JSON Schema.")
    _require_bounded_json(value, label)
    _reject_schema_references(value)
    try:
        validator = jsonschema.validators.validator_for(value)
        validator.check_schema(value)
    except jsonschema.SchemaError as exc:
        raise McpProtocolError(f"{label} is not a valid JSON Schema.") from exc


def _reject_schema_references(value: Any, *, depth: int = 0) -> None:
    if depth > 32:
        raise McpProtocolError("MCP tool schema exceeds the maximum depth.")
    if isinstance(value, dict):
        if "$ref" in value or "$dynamicRef" in value:
            raise McpProtocolError("MCP tool schemas cannot use remote references.")
        for item in value.values():
            _reject_schema_references(item, depth=depth + 1)
    elif isinstance(value, list):
        for item in value:
            _reject_schema_references(item, depth=depth + 1)


def _validate_schema_instance(
    value: Any,
    schema: dict[str, Any],
    label: str,
) -> None:
    _require_bounded_json(value, label, maximum_bytes=1_048_576)
    validator = jsonschema.validators.validator_for(schema)(
        schema,
        format_checker=jsonschema.FormatChecker(),
    )
    if next(validator.iter_errors(value), None) is not None:
        raise McpProtocolError(f"{label} failed local JSON Schema validation.")
