from __future__ import annotations

import json
import threading
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Any

import jsonschema

from agentbus.control.errors import ControlPlaneError
from agentbus.control.models import RunCreateRequest
from agentbus.control.services import ControlQueryService
from agentbus.control.supervisor import BackgroundRunSupervisor
from agentbus.mcp.models import (
    LATEST_MCP_PROTOCOL_VERSION,
    SUPPORTED_MCP_PROTOCOL_VERSIONS,
)
from agentbus.security.redaction import sanitize_json
from agentbus.tools.protocol import ToolDescriptor, safe_protocol_dict


MAX_AGENTBUS_MCP_RESPONSE_BYTES = 1_000_000
MAX_AGENTBUS_MCP_BATCH = 64


@dataclass(frozen=True)
class _ServerTool:
    name: str
    description: str
    input_schema: dict[str, Any]
    handler: Callable[[dict[str, Any]], Any]
    read_only: bool
    destructive: bool = False

    def definition(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "inputSchema": self.input_schema,
            "annotations": {
                "readOnlyHint": self.read_only,
                "destructiveHint": self.destructive,
            },
        }


class AgentBusMcpServer:
    def __init__(
        self,
        query_service: ControlQueryService,
        supervisor: BackgroundRunSupervisor,
        *,
        descriptor_provider: Callable[[], Iterable[ToolDescriptor]] | None = None,
    ) -> None:
        self.query = query_service
        self.supervisor = supervisor
        self.descriptor_provider = descriptor_provider or (lambda: ())
        self._lock = threading.RLock()
        self._initialized = False
        self._ready = False
        self._protocol_version: str | None = None
        self._tools = self._build_tools()

    def handle(
        self,
        payload: Any,
        *,
        protocol_version: str | None = None,
        require_protocol_header: bool = False,
    ) -> dict[str, Any] | list[dict[str, Any]] | None:
        if isinstance(payload, list):
            if not payload or len(payload) > MAX_AGENTBUS_MCP_BATCH:
                return _error(None, -32600, "Invalid JSON-RPC batch")
            if _has_duplicate_request_ids(payload):
                return _error(None, -32600, "Duplicate JSON-RPC request IDs")
            responses = [
                response
                for item in payload
                if (
                    response := self._handle_message(
                        item,
                        protocol_version=protocol_version,
                        require_protocol_header=require_protocol_header,
                    )
                )
                is not None
            ]
            return responses or None
        return self._handle_message(
            payload,
            protocol_version=protocol_version,
            require_protocol_header=require_protocol_header,
        )

    def _handle_message(
        self,
        message: Any,
        *,
        protocol_version: str | None,
        require_protocol_header: bool,
    ) -> dict[str, Any] | None:
        if not isinstance(message, dict) or message.get("jsonrpc") != "2.0":
            return _error(None, -32600, "Invalid JSON-RPC request")
        request_id = message.get("id")
        if isinstance(request_id, bool) or not (
            request_id is None or isinstance(request_id, (str, int))
        ):
            return _error(None, -32600, "Invalid JSON-RPC request ID")
        method = message.get("method")
        params = message.get("params", {})
        if not isinstance(method, str) or not isinstance(params, dict):
            return _error(request_id, -32600, "Invalid JSON-RPC request")
        if method != "initialize":
            if require_protocol_header and protocol_version is None:
                return _error(
                    request_id,
                    -32000,
                    "MCP protocol version header is required",
                )
            if (
                protocol_version is not None
                and protocol_version != self._protocol_version
            ):
                return _error(
                    request_id,
                    -32000,
                    "MCP protocol version header does not match the session",
                )
        if method == "initialize":
            return self._initialize(request_id, params)
        if method == "notifications/initialized":
            if self._initialized:
                self._ready = True
            return None
        if method == "notifications/cancelled":
            return None
        if not self._initialized or not self._ready:
            return _error(request_id, -32000, "MCP server is not initialized")
        if method == "ping":
            return _result(request_id, {})
        if method == "tools/list":
            if params.get("cursor") not in {None, ""}:
                return _error(request_id, -32602, "Pagination cursor is not supported")
            return _result(
                request_id,
                {"tools": [self._tools[name].definition() for name in sorted(self._tools)]},
            )
        if method == "tools/call":
            return self._call_tool(request_id, params)
        return _error(request_id, -32601, "Method not found")

    def _initialize(
        self,
        request_id: str | int | None,
        params: dict[str, Any],
    ) -> dict[str, Any]:
        requested = params.get("protocolVersion")
        if not isinstance(requested, str):
            return _error(request_id, -32602, "protocolVersion is required")
        negotiated = (
            requested
            if requested in SUPPORTED_MCP_PROTOCOL_VERSIONS
            else LATEST_MCP_PROTOCOL_VERSION
        )
        with self._lock:
            self._initialized = True
            self._ready = False
            self._protocol_version = negotiated
        return _result(
            request_id,
            {
                "protocolVersion": negotiated,
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {
                    "name": "agentbus-control",
                    "version": "0.3",
                    "description": "Constrained authenticated AgentBus control tools.",
                },
            },
        )

    def _call_tool(
        self,
        request_id: str | int | None,
        params: dict[str, Any],
    ) -> dict[str, Any]:
        name = params.get("name")
        arguments = params.get("arguments", {})
        if not isinstance(name, str) or not isinstance(arguments, dict):
            return _error(request_id, -32602, "Invalid tools/call parameters")
        tool = self._tools.get(name)
        if tool is None:
            return _result(
                request_id,
                _tool_result(
                    {"error": "The requested AgentBus MCP tool is not exposed."},
                    is_error=True,
                ),
            )
        validator = jsonschema.validators.validator_for(tool.input_schema)(
            tool.input_schema,
            format_checker=jsonschema.FormatChecker(),
        )
        if next(validator.iter_errors(arguments), None) is not None:
            return _error(request_id, -32602, "Tool arguments failed local validation")
        try:
            payload = sanitize_json(tool.handler(arguments))
            result = _tool_result(payload, is_error=False)
        except Exception as exc:
            message = (
                exc.safe_message
                if isinstance(exc, ControlPlaneError)
                else "The AgentBus control operation failed safely."
            )
            result = _tool_result(
                {"error": message},
                is_error=True,
            )
        if _encoded_size(result) > MAX_AGENTBUS_MCP_RESPONSE_BYTES:
            result = _tool_result(
                {"error": "The AgentBus MCP tool result exceeded its output limit."},
                is_error=True,
            )
        return _result(request_id, result)

    def _build_tools(self) -> dict[str, _ServerTool]:
        run_schema = _object_schema(
            {"run_id": {"type": "string", "minLength": 1, "maxLength": 128}},
            required=("run_id",),
        )
        return {
            "agentbus.run.inspect": _ServerTool(
                "agentbus.run.inspect",
                "Inspect bounded metadata for one AgentBus run.",
                run_schema,
                lambda value: self.query.run_summary(
                    self.query.get_run(value["run_id"])
                ).model_dump(mode="json"),
                True,
            ),
            "agentbus.run.tasks": _ServerTool(
                "agentbus.run.tasks",
                "List bounded task metadata for one AgentBus run.",
                run_schema,
                lambda value: self.query.tasks(value["run_id"]).model_dump(mode="json"),
                True,
            ),
            "agentbus.run.report": _ServerTool(
                "agentbus.run.report",
                "Read the safe bounded durable report for one AgentBus run.",
                run_schema,
                lambda value: self.query.report(value["run_id"]).model_dump(mode="json"),
                True,
            ),
            "agentbus.run.approvals": _ServerTool(
                "agentbus.run.approvals",
                "List approval metadata without deciding approvals.",
                run_schema,
                lambda value: self.query.approvals(value["run_id"]).model_dump(mode="json"),
                True,
            ),
            "agentbus.run.changes": _ServerTool(
                "agentbus.run.changes",
                "Inspect changed-file metadata scoped to the run repository.",
                run_schema,
                lambda value: self.query.repository.list_changes(
                    self.query.get_run(value["run_id"])
                ).model_dump(mode="json"),
                True,
            ),
            "agentbus.run.diff": _ServerTool(
                "agentbus.run.diff",
                "Inspect a bounded non-secret repository diff for one run.",
                _object_schema(
                    {
                        "run_id": {
                            "type": "string",
                            "minLength": 1,
                            "maxLength": 128,
                        },
                        "path": {"type": "string", "minLength": 1, "maxLength": 2048},
                        "byte_limit": {
                            "type": "integer",
                            "minimum": 1,
                            "maximum": 30_000,
                        },
                    },
                    required=("run_id",),
                ),
                self._diff,
                True,
            ),
            "agentbus.tools.inspect": _ServerTool(
                "agentbus.tools.inspect",
                "Inspect configured managed tool descriptors without implementations.",
                _object_schema({}),
                self._descriptors,
                True,
            ),
            "agentbus.run.cancel": _ServerTool(
                "agentbus.run.cancel",
                "Request cooperative cancellation for one AgentBus run.",
                _object_schema(
                    {
                        "run_id": {
                            "type": "string",
                            "minLength": 1,
                            "maxLength": 128,
                        },
                        "reason": {"type": "string", "minLength": 1, "maxLength": 1000},
                    },
                    required=("run_id",),
                ),
                lambda value: self.supervisor.cancel(
                    value["run_id"],
                    value.get("reason"),
                ).model_dump(mode="json"),
                False,
                True,
            ),
            "agentbus.run.submit": _ServerTool(
                "agentbus.run.submit",
                "Submit a deterministic durable managed task without commit or PR side effects.",
                _object_schema(
                    {
                        "task": {"type": "string", "minLength": 1, "maxLength": 10_000},
                        "workspace": {
                            "type": "string",
                            "minLength": 1,
                            "maxLength": 4096,
                        },
                        "profile": {
                            "enum": ["python-calculator", "cancellation-two-task"]
                        },
                    },
                    required=("task", "workspace"),
                ),
                self._submit,
                False,
                True,
            ),
        }

    def _diff(self, value: dict[str, Any]) -> dict[str, Any]:
        return self.query.repository.diff(
            self.query.get_run(value["run_id"]),
            path=value.get("path"),
            byte_limit=value.get("byte_limit", 30_000),
        ).model_dump(mode="json")

    def _descriptors(self, _value: dict[str, Any]) -> dict[str, Any]:
        descriptors = tuple(self.descriptor_provider())
        if len(descriptors) > 256:
            raise ValueError("Too many configured tool descriptors.")
        return {
            "tools": [safe_protocol_dict(descriptor) for descriptor in descriptors]
        }

    def _submit(self, value: dict[str, Any]) -> dict[str, Any]:
        request = RunCreateRequest(
            task=value["task"],
            workspace=value["workspace"],
            provider="deterministic",
            workflow="multi",
            durable=True,
            parallel=False,
            max_workers=1,
            deterministic={"profile": value.get("profile", "python-calculator")},
            fallback_enabled=False,
            live_provider_consent=False,
            create_pr=False,
            commit_changes=False,
            keep_worktrees=True,
            metadata={"submission_source": "authenticated_mcp"},
        )
        return self.supervisor.submit(request).model_dump(mode="json")


def _object_schema(
    properties: dict[str, Any],
    *,
    required: tuple[str, ...] = (),
) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": properties,
        "required": list(required),
        "additionalProperties": False,
    }


def _tool_result(payload: Any, *, is_error: bool) -> dict[str, Any]:
    return {
        "content": [
            {
                "type": "text",
                "text": (
                    "AgentBus operation failed safely."
                    if is_error
                    else "AgentBus operation completed."
                ),
            }
        ],
        "structuredContent": payload if isinstance(payload, dict) else {"value": payload},
        "isError": is_error,
    }


def _result(request_id: Any, result: dict[str, Any]) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def _error(request_id: Any, code: int, message: str) -> dict[str, Any]:
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "error": {"code": code, "message": message},
    }


def _has_duplicate_request_ids(messages: list[Any]) -> bool:
    seen: set[tuple[type[Any], str | int | None]] = set()
    for message in messages:
        if not isinstance(message, dict) or "id" not in message:
            continue
        request_id = message.get("id")
        if isinstance(request_id, bool) or not (
            request_id is None or isinstance(request_id, (str, int))
        ):
            continue
        identity = (type(request_id), request_id)
        if identity in seen:
            return True
        seen.add(identity)
    return False


def _encoded_size(value: Any) -> int:
    return len(
        json.dumps(
            value,
            ensure_ascii=False,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    )
