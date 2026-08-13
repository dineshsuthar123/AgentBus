from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--mode",
        required=True,
        choices=(
            "ignore-termination",
            "tree",
            "child",
            "grandchild",
            "leaf",
            "continuous-output",
            "spawn-repeatedly",
            "wait",
            "exit",
            "unicode",
            "close-pipes",
        ),
    )
    parser.add_argument("--lifecycle-dir")
    parser.add_argument("--exit-code", type=int, default=0)
    parser.add_argument("--child-count", type=int, default=4)
    parser.add_argument("--expected-active-children", type=int)
    parser.add_argument("--output-iterations", type=int)
    args = parser.parse_args()
    lifecycle = (
        Path(args.lifecycle_dir).resolve(strict=True)
        if args.lifecycle_dir is not None
        else None
    )

    if args.mode == "ignore-termination":
        _write_pid(lifecycle, "ignore")
        if hasattr(signal, "SIGTERM"):
            signal.signal(signal.SIGTERM, lambda *_: None)
        print("ready", flush=True)
        _wait_forever()
    if args.mode == "tree":
        _write_pid(lifecycle, "parent")
        _spawn("child", lifecycle)
        print("tree-ready", flush=True)
        _wait_forever()
    if args.mode == "child":
        _write_pid(lifecycle, "child")
        _spawn("grandchild", lifecycle)
        _wait_forever()
    if args.mode == "grandchild":
        _write_pid(lifecycle, "grandchild")
        _wait_forever()
    if args.mode == "leaf":
        _write_pid(lifecycle, "leaf")
        _wait_forever()
    if args.mode == "continuous-output":
        _write_pid(lifecycle, "continuous")
        chunk = ("stdout-" + ("x" * 120) + "\n").encode("utf-8")
        error = ("stderr-" + ("y" * 120) + "\n").encode("utf-8")
        remaining = (
            None
            if args.output_iterations is None
            else max(0, args.output_iterations)
        )
        try:
            while remaining is None or remaining > 0:
                os.write(1, chunk)
                os.write(2, error)
                if remaining is not None:
                    remaining -= 1
        except OSError:
            return 0
        return 0
    if args.mode == "spawn-repeatedly":
        _write_pid(lifecycle, "spawner")
        children = []
        blocked = 0
        for _ in range(max(0, min(args.child_count, 16))):
            try:
                children.append(_spawn("leaf", lifecycle))
            except OSError:
                blocked += 1
        active_children = _settled_active_children(
            children,
            expected=args.expected_active_children,
        )
        print(
            json.dumps(
                {
                    "children": [child.pid for child in children],
                    "active_children": [child.pid for child in active_children],
                    "blocked": blocked,
                }
            ),
            flush=True,
        )
        return 0
    if args.mode == "wait":
        _write_pid(lifecycle, "wait")
        _wait_forever()
    if args.mode == "exit":
        return args.exit_code
    if args.mode == "unicode":
        print("AgentBus unicode: caf\u00e9 \u6e2c\u8a66 \U0001f680")
        sys.stderr.write("stderr unicode: na\u00efve \u03bb\n")
        return 0
    if args.mode == "close-pipes":
        sys.stdout.flush()
        sys.stderr.flush()
        os.close(1)
        os.close(2)
        time.sleep(0.05)
        os._exit(0)
    return 0


def _spawn(mode: str, lifecycle: Path | None) -> subprocess.Popen[bytes]:
    command = [sys.executable, "-u", str(Path(__file__).resolve()), "--mode", mode]
    if lifecycle is not None:
        command.extend(["--lifecycle-dir", str(lifecycle)])
    return subprocess.Popen(
        command,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        shell=False,
        close_fds=True,
    )


def _write_pid(lifecycle: Path | None, role: str) -> None:
    if lifecycle is None:
        return
    marker = lifecycle / f"{role}-{os.getpid()}.pid"
    marker.write_text(str(os.getpid()), encoding="utf-8")


def _settled_active_children(
    children: list[subprocess.Popen[bytes]],
    *,
    expected: int | None,
) -> list[subprocess.Popen[bytes]]:
    active = [child for child in children if child.poll() is None]
    if os.name != "nt" or expected is None or len(active) <= expected:
        return active
    deadline = time.monotonic() + 2.0
    while len(active) > expected and time.monotonic() < deadline:
        time.sleep(0.01)
        active = [child for child in active if child.poll() is None]
    return active


def _wait_forever() -> None:
    while True:
        time.sleep(60)


if __name__ == "__main__":
    raise SystemExit(main())
