from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

import requests

from agentbus.execution.state_store import RunNotFoundError, StateStore
from agentbus.security.redaction import safe_child_environment

_TERMINAL_STATUSES = {"succeeded", "failed", "cancelled"}
_SUCCESS_EVENT = "integration_commit_published"
_CANCELLATION_EVENT = "cancellation_cleanup_completed"


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="agentbus-control-acceptance-") as temporary:
        root = Path(temporary)
        workspace = _initialize_repository(root / "repo")
        state_path = root / "state.db"
        registry = root / "daemons.json"
        process = _launch_daemon(
            workspace=workspace,
            state_path=state_path,
            runs_dir=root / "runs",
            registry=registry,
        )
        token = ""
        observed_payloads: list[Any] = []
        try:
            handshake = _read_handshake(process)
            token = handshake["bearer_token"]
            base = f"http://127.0.0.1:{handshake['port']}"
            headers = {"Authorization": f"Bearer {token}"}
            info = _request("GET", f"{base}/api/v1/info", headers=headers).json()
            assert info["daemon_id"] == handshake["daemon_id"]
            validated = _request(
                "POST",
                f"{base}/api/v1/workspaces/validate",
                headers=headers,
                json={"workspace": str(workspace), "require_git": True},
            ).json()
            assert validated["valid"] is True

            successful_run = _submit_run(
                base,
                headers,
                workspace,
                task="Create and verify the deterministic calculator.",
                profile="python-calculator",
                latency_seconds=0,
                latency_roles=[],
            )
            successful_summary = _wait_for_terminal_run(
                base,
                headers,
                successful_run,
            )
            assert successful_summary["status"] == "succeeded"
            success_events = _replay_events(
                base,
                headers,
                successful_run,
                until_event=_SUCCESS_EVENT,
            )
            observed_payloads.extend(success_events)
            _assert_successful_run(
                base,
                headers,
                workspace,
                successful_run,
                success_events,
            )

            cancellation_marker = "acceptance-private-prompt-marker"
            cancelled_run = _submit_run(
                base,
                headers,
                workspace,
                task=(
                    "Exercise cooperative cancellation during provider execution. "
                    + cancellation_marker
                ),
                profile="cancellation-two-task",
                latency_seconds=30,
                latency_roles=["coder"],
            )
            _wait_for_provider_operation(state_path, cancelled_run)
            cancellation_response = _request(
                "POST",
                f"{base}/api/v1/runs/{cancelled_run}/cancel",
                headers=headers,
                json={"reason": "Offline acceptance cancellation"},
            ).json()
            assert cancellation_response["cancellation_requested"] is True
            assert cancellation_response["cancellation"]["requested"] is True
            assert (
                cancellation_response["cancellation"][
                    "provider_cancellation_signalled"
                ]
                is True
            )
            cancelled_summary = _wait_for_terminal_run(
                base,
                headers,
                cancelled_run,
            )
            assert cancelled_summary["status"] == "cancelled"
            cancel_events = _replay_events(
                base,
                headers,
                cancelled_run,
                until_event=_CANCELLATION_EVENT,
            )
            observed_payloads.extend(cancel_events)
            _assert_cancelled_run(
                base,
                headers,
                cancelled_run,
                cancelled_summary,
                cancel_events,
            )

            serialized = json.dumps(observed_payloads, sort_keys=True)
            assert token not in serialized
            assert cancellation_marker not in serialized
            assert token not in registry.read_text(encoding="utf-8")
            assert process.wait(timeout=30) == 0
            assert process.stderr is not None
            stderr = process.stderr.read()
            assert token not in stderr
            registry_payload = json.loads(registry.read_text(encoding="utf-8"))
            assert registry_payload["daemons"] == []
        finally:
            if process.poll() is None:
                process.terminate()
                process.wait(timeout=10)
    print("AgentBus control-plane true offline acceptance: PASS")
    return 0


def _initialize_repository(workspace: Path) -> Path:
    workspace.mkdir()
    _git(workspace, "init")
    _git(workspace, "config", "user.email", "acceptance@agentbus.invalid")
    _git(workspace, "config", "user.name", "AgentBus Acceptance")
    (workspace / "README.md").write_text(
        "# Offline acceptance workspace\n",
        encoding="utf-8",
    )
    _git(workspace, "add", "README.md")
    _git(workspace, "commit", "-m", "initial")
    return workspace.resolve()


def _launch_daemon(
    *,
    workspace: Path,
    state_path: Path,
    runs_dir: Path,
    registry: Path,
) -> subprocess.Popen[str]:
    code = (
        "from agentbus.config import AgentBusConfig;"
        "from agentbus.control.server import serve;"
        "import sys;"
        "c=AgentBusConfig(workspace_dir=sys.argv[1],state_db=sys.argv[2],"
        "runs_dir=sys.argv[3]);"
        "raise SystemExit(serve(config=c,port=0,json_ready=True,"
        "idle_timeout=3.0,registry_path=sys.argv[4],log_level='error'))"
    )
    return subprocess.Popen(
        [
            sys.executable,
            "-c",
            code,
            str(workspace),
            str(state_path),
            str(runs_dir),
            str(registry),
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        shell=False,
        env=safe_child_environment(),
    )


def _read_handshake(process: subprocess.Popen[str]) -> dict[str, Any]:
    assert process.stdout is not None
    handshake_line = process.stdout.readline().strip()
    if not handshake_line:
        stderr = process.stderr.read() if process.stderr is not None else ""
        raise RuntimeError(f"Control daemon did not become ready: {stderr}")
    return json.loads(handshake_line)


def _submit_run(
    base: str,
    headers: dict[str, str],
    workspace: Path,
    *,
    task: str,
    profile: str,
    latency_seconds: float,
    latency_roles: list[str],
) -> str:
    response = _request(
        "POST",
        f"{base}/api/v1/runs",
        headers=headers,
        json={
            "task": task,
            "workspace": str(workspace),
            "provider": "deterministic",
            "workflow": "multi",
            "durable": True,
            "parallel": True,
            "max_workers": 1,
            "commit_changes": True,
            "keep_worktrees": True,
            "retry_limit": 1,
            "deterministic": {
                "profile": profile,
                "latency_seconds": latency_seconds,
                "latency_roles": latency_roles,
            },
        },
    ).json()
    assert response["status"] == "pending"
    assert Path(response["workspace"]).resolve() == workspace
    return str(response["run_id"])


def _wait_for_terminal_run(
    base: str,
    headers: dict[str, str],
    run_id: str,
    *,
    timeout_seconds: float = 60,
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        response = requests.get(
            f"{base}/api/v1/runs/{run_id}",
            headers=headers,
            timeout=10,
        )
        if response.status_code == 404:
            time.sleep(0.05)
            continue
        response.raise_for_status()
        summary = response.json()
        if summary["status"] in _TERMINAL_STATUSES:
            return summary
        time.sleep(0.05)
    raise TimeoutError(f"Run {run_id} did not reach a terminal state.")


def _wait_for_provider_operation(
    state_path: Path,
    run_id: str,
    *,
    timeout_seconds: float = 20,
) -> None:
    store = StateStore(state_path)
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        try:
            state = store.get_cancellation_state(run_id)
        except RunNotFoundError:
            time.sleep(0.02)
            continue
        if any(
            operation.provider == "deterministic"
            and operation.name == "deterministic.coder.generate"
            for operation in state.active_operations
        ):
            return
        time.sleep(0.02)
    raise TimeoutError("The deterministic coder provider did not become active.")


def _replay_events(
    base: str,
    headers: dict[str, str],
    run_id: str,
    *,
    until_event: str,
) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    with requests.get(
        f"{base}/api/v1/runs/{run_id}/events",
        headers=headers,
        params={"after": 0},
        stream=True,
        timeout=(5, 20),
    ) as response:
        response.raise_for_status()
        for line in response.iter_lines(decode_unicode=True, chunk_size=1):
            if not line or not line.startswith("data: "):
                continue
            event = json.loads(line[6:])
            events.append(event)
            if event["event_type"] == until_event:
                return events
    raise AssertionError(f"Event replay did not include {until_event}.")


def _assert_successful_run(
    base: str,
    headers: dict[str, str],
    workspace: Path,
    run_id: str,
    events: list[dict[str, Any]],
) -> None:
    report = _request(
        "GET",
        f"{base}/api/v1/runs/{run_id}/report",
        headers=headers,
    ).json()
    payload = report["report"]
    assert report["status"] == "succeeded"
    assert payload["graph_progress"]["succeeded"] == 1
    assert payload["verifier_status"] == "passed"
    assert payload["reviewer_status"] == "approved"
    assert payload["task_commits"]["step-1"]
    assert payload["integration_commit"]
    assert payload["commit_identifier"] == payload["integration_commit"]
    assert payload["changed_files"] == [
        "agentbus_result.py",
        "test_agentbus_result.py",
    ]
    required_events = {
        "durable_run_created",
        "task_attempt_started",
        "task_commit_created",
        "durable_task_integrated",
        "parallel_execution_completed",
        "final_integration_verification_started",
        "final_integration_review_completed",
        "durable_run_succeeded",
        _SUCCESS_EVENT,
    }
    observed_events = {event["event_type"] for event in events}
    assert required_events <= observed_events, (
        f"Missing successful-run events: "
        f"{sorted(required_events - observed_events)}; "
        f"observed={sorted(observed_events)}"
    )

    changes = _request(
        "GET",
        f"{base}/api/v1/runs/{run_id}/changes",
        headers=headers,
    ).json()
    assert {item["path"] for item in changes["changes"]} == {
        "agentbus_result.py",
        "test_agentbus_result.py",
    }
    assert all(item["status"] == "committed" for item in changes["changes"])
    diff = _request(
        "GET",
        f"{base}/api/v1/runs/{run_id}/diff",
        headers=headers,
    ).json()
    assert "def add(left: int, right: int) -> int:" in diff["diff"]
    assert diff["truncated"] is False
    after = _request(
        "GET",
        f"{base}/api/v1/runs/{run_id}/changes/agentbus_result.py",
        headers=headers,
        params={"revision": "after"},
    ).json()
    assert "return left + right" in after["content"]
    assert not (workspace / "agentbus_result.py").exists()


def _assert_cancelled_run(
    base: str,
    headers: dict[str, str],
    run_id: str,
    summary: dict[str, Any],
    events: list[dict[str, Any]],
) -> None:
    cancellation = summary["cancellation"]
    assert cancellation["requested"] is True
    assert cancellation["acknowledged"] is True
    assert cancellation["provider_cancellation_signalled"] is True
    assert cancellation["provider_cancellation_acknowledged"] is True
    assert cancellation["scheduling_stopped"] is True
    assert cancellation["cleanup_completed"] is True

    tasks = _request(
        "GET",
        f"{base}/api/v1/runs/{run_id}/tasks",
        headers=headers,
    ).json()["tasks"]
    by_id = {task["task_id"]: task for task in tasks}
    assert by_id["step-1"]["status"] == "cancelled"
    assert by_id["step-1"]["attempts"] == 1
    assert by_id["step-2"]["status"] == "cancelled"
    assert by_id["step-2"]["attempts"] == 0

    event_types = [event["event_type"] for event in events]
    expected_order = [
        "cancellation_requested",
        "cancellation_propagated",
        "provider_cancellation_requested",
        "provider_cancellation_acknowledged",
        "scheduling_stopped",
        "run_cancelled",
        _CANCELLATION_EVENT,
    ]
    positions = [event_types.index(event_type) for event_type in expected_order]
    assert positions == sorted(positions)
    assert event_types.count("run_cancelled") == 1
    assert event_types.count(_CANCELLATION_EVENT) == 1
    assert not any(
        event["event_type"] == "worker_started"
        and event.get("task_id") == "step-2"
        for event in events
    )

    report = _request(
        "GET",
        f"{base}/api/v1/runs/{run_id}/report",
        headers=headers,
    ).json()
    assert report["status"] == "cancelled"
    assert report["cancellation"]["provider_cancellation_acknowledged"] is True
    assert report["report"]["current_leases"] == []


def _request(method: str, url: str, **kwargs):
    deadline = time.monotonic() + 5
    while True:
        try:
            response = requests.request(method, url, timeout=15, **kwargs)
            response.raise_for_status()
            return response
        except requests.ConnectionError:
            if time.monotonic() >= deadline:
                raise
            time.sleep(0.05)


def _git(workspace: Path, *arguments: str) -> None:
    result = subprocess.run(
        ["git", *arguments],
        cwd=workspace,
        capture_output=True,
        text=True,
        shell=False,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip())


if __name__ == "__main__":
    raise SystemExit(main())
