from __future__ import annotations

import argparse
import json
import os
import sys
import time


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--mode",
        choices=(
            "normal",
            "oversized",
            "malformed",
            "hang",
            "unsupported",
            "boolean-id",
            "malformed-schema",
            "oversized-description",
            "oversized-tool",
            "tool-error",
        ),
        default="normal",
    )
    args = parser.parse_args()
    if args.mode == "oversized":
        sys.stdout.write("x" * 4_096 + "\n")
        sys.stdout.flush()
        time.sleep(30)
        return 0
    if args.mode == "malformed":
        sys.stdout.write("not-json\n")
        sys.stdout.flush()
        time.sleep(30)
        return 0

    for raw in sys.stdin:
        message = json.loads(raw)
        if "id" not in message:
            continue
        if args.mode == "hang":
            time.sleep(30)
            continue
        method = message.get("method")
        if method == "initialize":
            requested = message.get("params", {}).get("protocolVersion")
            result = {
                "protocolVersion": (
                    "2099-01-01" if args.mode == "unsupported" else requested
                ),
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": "offline-fixture", "version": "1.0.0"},
            }
        elif method == "ping":
            result = {}
        elif method == "tools/list":
            input_schema = {
                "type": "object",
                "properties": {"message": {"type": "string", "maxLength": 256}},
                "required": ["message"],
                "additionalProperties": False,
            }
            if args.mode == "malformed-schema":
                input_schema = {"type": "not-a-json-schema-type"}
            result = {
                "tools": [
                    {
                        "name": "echo",
                        "description": (
                            "x" * 5_000
                            if args.mode == "oversized-description"
                            else "Echo a bounded message."
                        ),
                        "inputSchema": input_schema,
                        "outputSchema": {
                            "type": "object",
                            "properties": {"echo": {"type": "string"}},
                            "required": ["echo"],
                            "additionalProperties": False,
                        },
                        "annotations": {"readOnlyHint": True},
                    },
                    {
                        "name": "write_note",
                        "description": "Pretend to write a note for offline tests.",
                        "inputSchema": {
                            "type": "object",
                            "properties": {"content": {"type": "string"}},
                            "required": ["content"],
                            "additionalProperties": False,
                        },
                        "annotations": {"destructiveHint": True},
                    },
                ]
            }
        elif method == "tools/call":
            params = message.get("params", {})
            tool_name = params.get("name")
            arguments = params.get("arguments", {})
            if args.mode == "tool-error":
                result = {
                    "content": [{"type": "text", "text": "offline failure"}],
                    "isError": True,
                }
            elif tool_name == "echo":
                echoed = arguments.get("message", "")
                result = {
                    "content": [
                        {
                            "type": "text",
                            "text": (
                                "x" * 4_096
                                if args.mode == "oversized-tool"
                                else echoed
                            ),
                        }
                    ],
                    "structuredContent": {"echo": echoed},
                    "isError": False,
                }
            else:
                result = {
                    "content": [{"type": "text", "text": "note accepted"}],
                    "isError": False,
                }
        else:
            result = {
                "echo": message.get("params", {}),
                "environment_names": sorted(os.environ),
            }
        sys.stdout.write(
            json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": True if args.mode == "boolean-id" else message["id"],
                    "result": result,
                },
                separators=(",", ":"),
            )
            + "\n"
        )
        sys.stdout.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
