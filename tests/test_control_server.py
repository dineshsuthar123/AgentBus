from __future__ import annotations

import json
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

from agentbus.control.lifecycle import bind_loopback_socket


def test_port_zero_uses_os_selected_loopback_port() -> None:
    listener = bind_loopback_socket("127.0.0.1", 0)
    try:
        host, port = listener.getsockname()[:2]
        assert host == "127.0.0.1"
        assert 0 < port <= 65535
    finally:
        listener.close()


def test_json_ready_daemon_authenticates_and_cleans_registry(
    tmp_path: Path,
) -> None:
    registry = tmp_path / "daemons.json"
    state = tmp_path / "state.db"
    code = (
        "from agentbus.config import AgentBusConfig;"
        "from agentbus.control.server import serve;"
        "import sys;"
        "config=AgentBusConfig(workspace_dir=sys.argv[1],"
        "state_db=sys.argv[2],runs_dir=sys.argv[3]);"
        "raise SystemExit(serve(config=config,port=0,json_ready=True,"
        "idle_timeout=1.0,registry_path=sys.argv[4],log_level='error'))"
    )
    process = subprocess.Popen(
        [
            sys.executable,
            "-c",
            code,
            str(tmp_path),
            str(state),
            str(tmp_path / "runs"),
            str(registry),
        ],
        cwd=Path.cwd(),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        shell=False,
    )
    assert process.stdout is not None
    assert process.stderr is not None
    try:
        line = process.stdout.readline().strip()
        handshake = json.loads(line)
        token = handshake["bearer_token"]
        assert handshake["port"] > 0
        assert handshake["token_delivery"] == "parent_process_stdout"

        registry_text = registry.read_text(encoding="utf-8")
        assert token not in registry_text
        assert "bearer_token" not in registry_text

        url = f"http://127.0.0.1:{handshake['port']}/api/v1/info"
        response = _request_with_retry(
            url,
            headers={"Authorization": f"Bearer {token}"},
        )
        info = json.loads(response)
        assert info["daemon_id"] == handshake["daemon_id"]
        assert token not in response

        try:
            urllib.request.urlopen(url, timeout=1)
        except urllib.error.HTTPError as exc:
            assert exc.code == 403
        else:
            raise AssertionError("unauthenticated API request unexpectedly succeeded")

        assert process.wait(timeout=8) == 0
        payload = json.loads(registry.read_text(encoding="utf-8"))
        assert payload["daemons"] == []
    finally:
        if process.poll() is None:
            process.terminate()
            process.wait(timeout=5)


def _request_with_retry(url: str, *, headers: dict[str, str]) -> str:
    deadline = time.monotonic() + 5
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        request = urllib.request.Request(url, headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=1) as response:
                return response.read().decode("utf-8")
        except (OSError, urllib.error.URLError) as exc:
            last_error = exc
            time.sleep(0.05)
    raise AssertionError(f"daemon did not become ready: {last_error}")
