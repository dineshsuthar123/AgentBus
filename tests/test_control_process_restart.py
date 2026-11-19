"""Kill and restart an owned daemon against real durable state."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
from collections import deque
from pathlib import Path
from queue import Empty, Queue
from types import SimpleNamespace

import pytest
import requests

from agentbus.control.acceptance import (
    _git, _initialize_repository, _resume_run, _wait_for_terminal_run,
)
from agentbus.execution.models import RunStatus, TaskStatus
from agentbus.execution.state_store import RunNotFoundError, StateStore
from agentbus.security.redaction import redact_diagnostic_text, safe_child_environment


def _approval_snapshot(store: StateStore, run_id: str) -> dict:
    try:
        run = store.get_run(run_id)
    except RunNotFoundError:
        return {"run_status": "not_yet_persisted"}
    return {
        "run": run.model_dump(mode="json", include={"status", "created_at", "updated_at"}),
        "tasks": [{"id": task.task_id, **task.model_dump(mode="json", include={
            "status", "current_attempt_count", "updated_at",
        })} for task in store.list_tasks(run_id)[:16]],
        "attempts": [attempt.model_dump(mode="json", include={
            "task_id", "attempt_number", "status", "started_at", "completed_at", "error_category",
        }) for attempt in store.list_attempts(run_id)[-16:]],
        "approvals": [approval.model_dump(mode="json", include={
            "approval_id", "disposition", "created_at", "decided_at",
        }) for approval in store.list_tool_approvals(run_id, limit=16)],
        "active_operations": [item.model_dump(mode="json") for item in
                              store.get_cancellation_state(run_id).active_operations[:16]],
        "events": [{key: value for key, value in event.items() if key != "payload"}
                   for event in store.list_events(run_id, limit=256)[-16:]],
    }


def _wait_for_restart_approval(base, headers, run_id, store, child, output, *, phase):
    """Wait on the durable event cursor, then confirm the approval through the daemon."""
    deadline = time.monotonic() + 30
    cursor = 0
    state = None
    request_errors = deque(maxlen=8)
    requests_sent = retries = 0
    try:
        while time.monotonic() < deadline:
            if child.poll() is not None:
                raise RuntimeError("Owned daemon exited before exposing the pending approval.")
            try:
                events = store.list_events(run_id, after_event_id=cursor, limit=64)
            except RunNotFoundError:
                events = []
            if events:
                cursor = events[-1]["event_id"]
                state = store.get_run(run_id).status
                if state in {RunStatus.SUCCEEDED, RunStatus.FAILED, RunStatus.CANCELLED}:
                    raise AssertionError("Run reached a terminal state before requesting approval.")
            if state == RunStatus.WAITING_FOR_APPROVAL:
                try:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        break
                    url = f"{base}/api/v1/runs/{run_id}"
                    requests_sent += 1
                    response = requests.get(f"{url}/approvals", headers=headers,
                                            timeout=min(1.0, remaining))
                    response.raise_for_status()
                    pending = [item for item in response.json()["approvals"]
                               if item["approval_kind"] == "tool" and item["state"] == "pending"]
                    if pending:
                        assert len(pending) == 1
                        remaining = deadline - time.monotonic()
                        if remaining <= 0:
                            break
                        requests_sent += 1
                        summary = requests.get(url, headers=headers, timeout=min(1.0, remaining))
                        summary.raise_for_status()
                        if summary.json()["status"] == RunStatus.WAITING_FOR_APPROVAL:
                            return pending[0]
                except (requests.ConnectionError, requests.Timeout) as exc:
                    retries += 1
                    request_errors.append(type(exc).__name__)
                except requests.HTTPError as exc:
                    request_errors.append(f"HTTP {exc.response.status_code}")
                    raise
            time.sleep(0.05)
        raise TimeoutError("Owned run did not expose its pending approval in 30 seconds.")
    except Exception as exc:
        status = child.poll()
        details = {"phase": phase, "last_observed_status": state, "event_cursor": cursor, "http_requests": requests_sent,
                   "request_retries": retries, "request_errors": list(request_errors),
                   "daemon_alive": status is None, "daemon_exit_code": status,
                   "daemon_output": ["".join(lines.copy()) for lines in output]}
        try:
            details["persisted_state"] = _approval_snapshot(store, run_id)
        except Exception as snapshot_error:
            details["persisted_state_error"] = type(snapshot_error).__name__
        token = headers["Authorization"].removeprefix("Bearer ")
        text = json.dumps(details, default=str).replace(token, "[REDACTED]")
        exc.add_note(redact_diagnostic_text(text, max_chars=12_000) or "No approval diagnostics.")
        raise


def test_approval_wait_reports_child_exit_with_redacted_output(tmp_path):
    store = StateStore(tmp_path / "state.db")
    token = "private-approval-diagnostic-token"
    with pytest.raises(RuntimeError, match="Owned daemon exited") as failure:
        _wait_for_restart_approval("http://127.0.0.1", {"Authorization": f"Bearer {token}"},
                                   "owned-run", store, SimpleNamespace(poll=lambda: 17),
                                   (deque([f"failure mentioning {token}"]),), phase="before_restart")
    diagnostic, = failure.value.__notes__
    assert token not in diagnostic
    assert "[REDACTED]" in diagnostic
    assert json.loads(diagnostic)["daemon_exit_code"] == 17
    assert json.loads(diagnostic)["persisted_state"]["run_status"] == "not_yet_persisted"


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
            stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="replace", shell=False,
        )
        children.append(child)
        first_line = Queue(maxsize=1)
        output = (deque(maxlen=4), deque(maxlen=4))

        def drain(stream, lines, *, handshake=False):
            if handshake:
                first_line.put(stream.readline(4096))
            while line := stream.readline(1024):
                lines.append(line)

        threading.Thread(target=drain, args=(child.stdout, output[0]),
                         kwargs={"handshake": True}, daemon=True).start()
        threading.Thread(target=drain, args=(child.stderr, output[1]), daemon=True).start()
        try:
            ready = json.loads(first_line.get(timeout=15))
        except (Empty, json.JSONDecodeError):
            raise RuntimeError(f"Owned daemon did not report readiness, exit code {child.poll()}.") from None
        return f"http://127.0.0.1:{ready['port']}", {
            "Authorization": f"Bearer {ready['bearer_token']}"
        }, output

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
        base, headers, output = launch()
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
            approval = _wait_for_restart_approval(base, headers, run_id, store, children[-1], output, phase="before_restart")
            assert (workspace / "delete_me.txt").exists()
        stop(children[-1])
        if boundary == "drifted_task":
            (workspace / "README.md").write_text("External change after crash.\n", encoding="utf-8")
        base, headers, output = launch()
        if boundary == "pending_approval":
            restored = _wait_for_restart_approval(base, headers, run_id, store, children[-1], output, phase="after_restart")
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
