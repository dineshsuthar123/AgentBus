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
