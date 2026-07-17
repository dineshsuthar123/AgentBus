from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import requests

from agentbus.config import AgentBusConfig
from agentbus.execution.models import RiskLevel, RunRecord, RunStatus, TaskSpec, TaskStatus
from agentbus.execution.state_store import StateStore


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="agentbus-control-acceptance-") as temporary:
        root = Path(temporary)
        workspace = root / "repo"
        workspace.mkdir()
        _git(workspace, "init")
        _git(workspace, "config", "user.email", "acceptance@agentbus.invalid")
        _git(workspace, "config", "user.name", "AgentBus Acceptance")
        (workspace / "app.txt").write_text("before\n", encoding="utf-8")
        _git(workspace, "add", "app.txt")
        _git(workspace, "commit", "-m", "initial")
        state_path = root / "state.db"
        store = StateStore(state_path)
        approval_run = RunRecord(
            run_id="acceptance-approval",
            original_task="Offline approval acceptance",
            model="deterministic-fake",
            workspace=str(workspace.resolve()),
        )
        store.create_run_with_tasks(
            approval_run,
            [
                TaskSpec(
                    task_id="approve-me",
                    title="Approve deterministic edit",
                    description="Approve a local fake-provider artifact.",
                    risk=RiskLevel.HIGH,
                    expected_outputs=["app.txt"],
                )
            ],
        )
        store.update_run_status(approval_run.run_id, RunStatus.RUNNING)
        store.update_task_status(
            approval_run.run_id, "approve-me", TaskStatus.READY
        )
        store.update_task_status(
            approval_run.run_id,
            "approve-me",
            TaskStatus.WAITING_FOR_APPROVAL,
        )
        store.update_run_status(
            approval_run.run_id, RunStatus.WAITING_FOR_APPROVAL
        )
        cancel_run = RunRecord(
            run_id="acceptance-cancel",
            original_task="Offline cancellation acceptance",
            model="deterministic-fake",
            workspace=str(workspace.resolve()),
        )
        store.create_run_with_tasks(
            cancel_run,
            [
                TaskSpec(
                    task_id="cancel-me",
                    title="Cancel deterministic task",
                    description="Remain pending until cancellation.",
                )
            ],
        )
        registry = root / "daemons.json"
        code = (
            "from agentbus.config import AgentBusConfig;"
            "from agentbus.control.server import serve;"
            "import sys;"
            "c=AgentBusConfig(workspace_dir=sys.argv[1],state_db=sys.argv[2],"
            "runs_dir=sys.argv[3]);"
            "raise SystemExit(serve(config=c,port=0,json_ready=True,"
            "idle_timeout=15.0,registry_path=sys.argv[4],log_level='error'))"
        )
        process = subprocess.Popen(
            [
                sys.executable,
                "-c",
                code,
                str(workspace),
                str(state_path),
                str(root / "runs"),
                str(registry),
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            shell=False,
        )
        try:
            assert process.stdout is not None
            handshake_line = process.stdout.readline().strip()
            handshake = json.loads(handshake_line)
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
            approvals = _request(
                "GET",
                f"{base}/api/v1/runs/{approval_run.run_id}/approvals",
                headers=headers,
            ).json()["approvals"]
            approval = approvals[0]
            _request(
                "POST",
                f"{base}/api/v1/runs/{approval_run.run_id}/approvals/"
                f"{approval['approval_id']}/approve",
                headers=headers,
                json={"revision": approval["revision"], "reason": "offline acceptance"},
            )
            cancelled = _request(
                "POST",
                f"{base}/api/v1/runs/{cancel_run.run_id}/cancel",
                headers=headers,
                json={"reason": "offline acceptance"},
            ).json()
            assert cancelled["status"] == "cancelled"
            (workspace / "app.txt").write_text("after\n", encoding="utf-8")
            changes = _request(
                "GET",
                f"{base}/api/v1/runs/{approval_run.run_id}/changes",
                headers=headers,
            ).json()
            assert any(item["path"] == "app.txt" for item in changes["changes"])
            diff = _request(
                "GET",
                f"{base}/api/v1/runs/{approval_run.run_id}/diff",
                headers=headers,
            ).json()
            assert "after" in diff["diff"]
            assert token not in registry.read_text(encoding="utf-8")
            assert process.wait(timeout=25) == 0
            assert process.stderr is not None
            assert token not in process.stderr.read()
            registry_payload = json.loads(registry.read_text(encoding="utf-8"))
            assert registry_payload["daemons"] == []
        finally:
            if process.poll() is None:
                process.terminate()
                process.wait(timeout=5)
    print("AgentBus control-plane offline acceptance: PASS")
    return 0


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
