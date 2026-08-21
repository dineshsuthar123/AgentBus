from __future__ import annotations

import json
import sys
from typing import Any


_MAX_MESSAGE_BYTES = 64 * 1024


def main() -> int:
    while True:
        raw = sys.stdin.buffer.readline(_MAX_MESSAGE_BYTES + 2)
        if not raw:
            return 0
        if len(raw) > _MAX_MESSAGE_BYTES or not raw.endswith(b"\n"):
            return 2
        try:
            request = json.loads(raw)
        except (UnicodeDecodeError, json.JSONDecodeError):
            return 2
        if not isinstance(request, dict) or "id" not in request:
            continue
        response = {
            "jsonrpc": "2.0",
            "id": request["id"],
            "result": _result(request),
        }
        encoded = json.dumps(
            response,
            ensure_ascii=False,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
        sys.stdout.buffer.write(encoded + b"\n")
        sys.stdout.buffer.flush()


def _result(request: dict[str, Any]) -> dict[str, Any]:
    method = request.get("method")
    if method == "initialize":
        params = request.get("params")
        version = (
            params.get("protocolVersion") if isinstance(params, dict) else None
        )
        return {
            "protocolVersion": version,
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": "agentbus-soak-peer", "version": "1.0.0"},
        }
    if method == "ping":
        return {}
    if method == "tools/list":
        return {
            "tools": [
                {
                    "name": "echo",
                    "description": "Echo one bounded offline soak message.",
                    "inputSchema": {
                        "type": "object",
                        "properties": {
                            "message": {"type": "string", "maxLength": 256}
                        },
                        "required": ["message"],
                        "additionalProperties": False,
                    },
                    "outputSchema": {
                        "type": "object",
                        "properties": {"echo": {"type": "string"}},
                        "required": ["echo"],
                        "additionalProperties": False,
                    },
                    "annotations": {"readOnlyHint": True},
                }
            ]
        }
    if method == "tools/call":
        raw_params = request.get("params")
        params = raw_params if isinstance(raw_params, dict) else {}
        raw_arguments = params.get("arguments")
        arguments = raw_arguments if isinstance(raw_arguments, dict) else {}
        raw_message = arguments.get("message", "")
        message = raw_message if isinstance(raw_message, str) else ""
        return {
            "content": [{"type": "text", "text": message}],
            "structuredContent": {"echo": message},
            "isError": False,
        }
    return {}


if __name__ == "__main__":
    raise SystemExit(main())
