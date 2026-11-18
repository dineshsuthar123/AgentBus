"""Kill and restart an owned daemon against real durable state."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest
import requests

from agentbus.control.acceptance import (
    _git, _initialize_repository, _read_handshake, _resume_run,
    _wait_for_pending_tool_approval, _wait_for_terminal_run,
)
from agentbus.execution.models import RunStatus, TaskStatus
from agentbus.execution.state_store import StateStore
from agentbus.security.redaction import safe_child_environment


@pytest.mark.parametrize("boundary", ["active_task", "drifted_task", "exhausted_task", "pending_approval"])
def test_owned_daemon_survives_process_loss(tmp_path: Path, boundary: str) -> None:
    workspace = _initialize_repository(tmp_path / "repo", mixed_language=False)
    (workspace / "acceptance_tool.py").write_text(
        "def add(left, right):\n    return left + right\n", encoding="utf-8"
    )
    _git(workspace, "add", "acceptance_tool.py")
    _git(workspace, "commit", "-m", "Complete independent verification fixture")
    database = tmp_path / "state.db"
    code = (
        "import sys; from agentbus.config import AgentBusConfig;"
        "from agentbus.control.server import serve;"
        "c=AgentBusConfig(provider_name='deterministic',enable_provider_fallback=False,"
        "workspace_dir=sys.argv[1],state_db=sys.argv[2],runs_dir=sys.argv[3]);"
        "raise SystemExit(serve(config=c,port=0,json_ready=True,idle_timeout=120,"
        "registry_path=sys.argv[4],log_level='error'))"
    )
    children: list[subprocess.Popen] = []

    def launch():
        # The optional interpreter selects an independently installed wheel.
        executable = os.environ.get("SYNDRA_ACCEPTANCE_PYTHON", sys.executable)
        child = subprocess.Popen(
            [executable, "-u", "-c", code, str(workspace), str(database),
             str(tmp_path / "runs"), str(tmp_path / "registry.json")],
            cwd=workspace, env=safe_child_environment(), stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=True, shell=False,
        )
        children.append(child)
        ready = _read_handshake(child)
        return f"http://127.0.0.1:{ready['port']}", {
            "Authorization": f"Bearer {ready['bearer_token']}"
        }

    def stop(child):
        if child.poll() is None:
            if os.name == "nt":
                # Only the process tree returned by our own Popen is targeted.
                result = subprocess.run(["taskkill", "/PID", str(child.pid), "/T", "/F"],
                                        capture_output=True, timeout=30)
                # A worker can finish while taskkill enumerates descendants.
                # Verify the owned parent actually died even if enumeration raced.
                if result.returncode:
                    child.wait(timeout=10)
            else:
                child.kill()
            child.wait(timeout=30)

    try:
        base, headers = launch()
        accepted = requests.post(f"{base}/api/v1/runs", headers=headers, timeout=15, json={
            "workspace": str(workspace), "task": "Build a deterministic calculator.",
            "provider": "deterministic", "durable": True, "workflow": "multi",
            "parallel": False, "commit_changes": False, "retry_limit": 2,
            "deterministic": {
                "profile": {"active_task": "python-calculator", "drifted_task": "python-calculator", "exhausted_task": "cancellation-two-task",
                            "pending_approval": "tool-delete-approval"}[boundary],
                "latency_seconds": 1 if boundary != "pending_approval" else 0,
                "latency_roles": ["coder"] if boundary != "pending_approval" else [],
            },
        })
        assert accepted.status_code == 202
        run_id = accepted.json()["run_id"]
        store = StateStore(database)
        if boundary != "pending_approval":
            deadline = time.monotonic() + 60
            while time.monotonic() < deadline:
                tasks = store.list_tasks(run_id)
                active = tasks[-1] if tasks else None
                if (active and active.status == TaskStatus.RUNNING
                        and store.get_cancellation_state(run_id).active_operations
                        and (boundary != "exhausted_task" or tasks[0].status == TaskStatus.SUCCEEDED)):
                    break
                time.sleep(0.02)
            else:
                pytest.fail("The expected task never became active in a provider operation.")
            if boundary == "exhausted_task":
                completed_attempts = store.list_attempts(run_id, "step-1")
                assert len(completed_attempts) == 1
        else:
            approval = _wait_for_pending_tool_approval(base, headers, run_id)
            assert (workspace / "delete_me.txt").exists()
        stop(children[-1])
        if boundary == "drifted_task":
            (workspace / "README.md").write_text("External change after crash.\n", encoding="utf-8")
        base, headers = launch()
        if boundary == "pending_approval":
            restored = _wait_for_pending_tool_approval(base, headers, run_id)
            assert restored["approval_id"] == approval["approval_id"]
            assert restored["revision"] == approval["revision"]
            assert (workspace / "delete_me.txt").exists()
            response = requests.post(
                f"{base}/api/v1/runs/{run_id}/approvals/{approval['approval_id']}/approve",
                headers=headers, json={"revision": approval["revision"]}, timeout=10,
            )
            assert response.status_code == 200
        _resume_run(base, headers, run_id)
        expected = "failed" if boundary in {"exhausted_task", "drifted_task"} else "succeeded"
        assert _wait_for_terminal_run(base, headers, run_id)["status"] == expected
        if boundary == "exhausted_task":
            assert store.list_attempts(run_id, "step-1") == completed_attempts
            assert not (workspace / "syndra_secondary.py").exists()
        elif boundary == "pending_approval":
            assert not (workspace / "delete_me.txt").exists()
        elif boundary == "drifted_task":
            assert store.list_tool_invocations(run_id) == []
            assert (workspace / "README.md").read_text(encoding="utf-8") == "External change after crash.\n"
        attempts, invocations = store.list_attempts(run_id), store.list_tool_invocations(run_id)
        _resume_run(base, headers, run_id)
        assert _wait_for_terminal_run(base, headers, run_id)["status"] == expected
        assert store.list_attempts(run_id) == attempts
        assert store.list_tool_invocations(run_id) == invocations
        assert store.get_run(run_id).status == RunStatus(expected)
        assert not [r for r in store.list_worker_lease_rows(run_id) if r["status"] == "active"]
        print(json.dumps({"boundary": boundary, "status": expected,
                          "attempts": len(attempts), "invocations": len(invocations),
                          "completed_work_repeated": False}))
    finally:
        for child in children:
            stop(child)
            if child.stdout:
                child.stdout.close()
            if child.stderr:
                child.stderr.close()
