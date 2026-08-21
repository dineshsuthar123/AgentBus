from __future__ import annotations

import atexit
import argparse
import json
import os
import subprocess
import sys
import time
import uuid
from pathlib import Path


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
            "oversized-declaration",
            "duplicate-tools",
            "oversized-tool",
            "secret-output",
            "tool-error",
            "ignore-cancel-child",
            "terminate",
            "wrong-id",
            "out-of-order",
            "capability-escalation",
        ),
        default="normal",
    )
    parser.add_argument("--lifecycle-dir")
    args = parser.parse_args()
    _track_lifecycle(args.lifecycle_dir)
    if args.mode == "ignore-cancel-child":
        _spawn_tracked_child(args.lifecycle_dir)
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
        if args.mode == "terminate" and method == "initialize":
            sys.stderr.write("API_KEY=peer-secret-must-not-leak\n")
            sys.stderr.flush()
            return 19
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
                            "x" * 70_000
                            if args.mode == "oversized-declaration"
                            else "x" * 5_000
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
            if args.mode == "duplicate-tools":
                result["tools"].append(dict(result["tools"][0]))
            if args.mode == "capability-escalation":
                result["tools"][0]["annotations"] = {
                    "destructiveHint": False,
                    "requestedCapabilities": [
                        "filesystem.write",
                        "process.execute",
                    ],
                }
                result["tools"][0]["capabilities"] = {
                    "filesystem": {"roots": ["/"]},
                }
        elif method == "tools/call":
            if args.mode == "ignore-cancel-child":
                time.sleep(30)
                continue
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
                output_text = (
                    "API_KEY=peer-secret-must-not-leak"
                    if args.mode == "secret-output"
                    else "x" * 4_096
                    if args.mode == "oversized-tool"
                    else echoed
                )
                result = {
                    "content": [
                        {
                            "type": "text",
                            "text": output_text,
                        }
                    ],
                    "structuredContent": {"echo": output_text},
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
        response_id = (
            True
            if args.mode == "boolean-id"
            else "invalid-correlation"
            if args.mode == "wrong-id"
            else message["id"]
        )
        response = {
            "jsonrpc": "2.0",
            "id": response_id,
            "result": result,
        }
        if args.mode == "out-of-order":
            unexpected = dict(response)
            unexpected["id"] = message["id"] + 10_000
            sys.stdout.write(json.dumps(unexpected, separators=(",", ":")) + "\n")
        sys.stdout.write(json.dumps(response, separators=(",", ":")) + "\n")
        sys.stdout.flush()
    return 0


def _spawn_tracked_child(directory_value: str | None) -> None:
    if directory_value is None:
        raise RuntimeError("child mode requires a lifecycle directory")
    directory = Path(directory_value).resolve(strict=True)
    marker = directory / "child.pid"
    child_code = (
        "import os, sys, time\n"
        "from pathlib import Path\n"
        "Path(sys.argv[1]).write_text(str(os.getpid()), encoding='utf-8')\n"
        "time.sleep(60)\n"
    )
    subprocess.Popen(
        [sys.executable, "-c", child_code, str(marker)],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        shell=False,
        close_fds=True,
    )


def _track_lifecycle(directory_value: str | None) -> None:
    if directory_value is None:
        return
    directory = Path(directory_value).resolve(strict=True)
    token = f"{os.getpid()}-{uuid.uuid4().hex}"
    pid = f"{os.getpid()}\n"
    (directory / f"{token}.started").write_text(pid, encoding="utf-8")
    atexit.register(
        (directory / f"{token}.stopped").write_text,
        pid,
        encoding="utf-8",
    )


if __name__ == "__main__":
    raise SystemExit(main())
