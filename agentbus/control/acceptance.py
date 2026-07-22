from __future__ import annotations

import hashlib
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
_COMMIT_EVENT = "commit_created"
_CANCELLATION_EVENT = "cancellation_cleanup_completed"
_DELETE_TARGET = "deterministic deletion target\n"
_DELETE_TARGET_SHA256 = hashlib.sha256(_DELETE_TARGET.encode("utf-8")).hexdigest()


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="agentbus-control-acceptance-") as temporary:
        root = Path(temporary)
        workspace = _initialize_repository(root / "repo")
        state_path = root / "state.db"
        registry = root / "daemons.json"
        traversal_marker = "acceptance-outside-secret-marker"
        (root / "outside.txt").write_text(traversal_marker, encoding="utf-8")
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
            print("acceptance: daemon ready", flush=True)

            tool_run = _submit_run(
                base,
                headers,
                workspace,
                task="Exercise the deterministic managed-tool lifecycle.",
                profile="tool-control-acceptance",
                latency_seconds=0,
                latency_roles=[],
                parallel=False,
            )
            tool_summary = _wait_for_terminal_run(base, headers, tool_run)
            assert tool_summary["status"] == "succeeded"
            tool_events = _replay_events(
                base,
                headers,
                tool_run,
                until_event=_COMMIT_EVENT,
            )
            observed_payloads.extend(tool_events)
            observed_payloads.extend(
                _assert_tool_lifecycle(base, headers, workspace, tool_run, tool_events)
            )
            print("acceptance: tool lifecycle passed", flush=True)

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
            print("acceptance: durable calculator passed", flush=True)

            approval_run = _submit_run(
                base,
                headers,
                workspace,
                task="Delete the deterministic target only after exact approval.",
                profile="tool-delete-approval",
                latency_seconds=0,
                latency_roles=[],
                parallel=False,
                commit_changes=False,
            )
            approval = _wait_for_pending_tool_approval(
                base,
                headers,
                approval_run,
            )
            observed_payloads.append(approval)
            observed_payloads.append(
                _assert_capability_escalation_rejected(
                    base,
                    headers,
                    approval_run,
                    approval,
                )
            )
            approved = _request(
                "POST",
                (
                    f"{base}/api/v1/runs/{approval_run}/approvals/"
                    f"{approval['approval_id']}/approve"
                ),
                headers=headers,
                json={
                    "revision": approval["revision"],
                    "reason": "Offline acceptance exact deletion approval.",
                },
            ).json()
            observed_payloads.append(approved)
            assert approved["approval"]["state"] == "approved"
            resumed = _request(
                "POST",
                f"{base}/api/v1/runs/{approval_run}/resume",
                headers=headers,
            ).json()
            assert resumed["resumed"] is True
            approval_summary = _wait_for_terminal_run(base, headers, approval_run)
            assert approval_summary["status"] == "succeeded"
            approval_events = _replay_events(
                base,
                headers,
                approval_run,
                until_event="durable_run_succeeded",
            )
            observed_payloads.extend(approval_events)
            observed_payloads.extend(
                _assert_approved_deletion(
                    base,
                    headers,
                    workspace,
                    approval_run,
                    approval,
                    approval_events,
                )
            )
            print("acceptance: exact tool approval passed", flush=True)

            traversal_run = _submit_run(
                base,
                headers,
                workspace,
                task="Attempt a traversal read and preserve the policy denial.",
                profile="tool-deny-outside-read",
                latency_seconds=0,
                latency_roles=[],
                parallel=False,
                commit_changes=False,
            )
            traversal_summary = _wait_for_terminal_run(
                base,
                headers,
                traversal_run,
            )
            assert traversal_summary["status"] == "succeeded"
            traversal_events = _replay_events(
                base,
                headers,
                traversal_run,
                until_event="durable_run_succeeded",
            )
            observed_payloads.extend(traversal_events)
            observed_payloads.extend(
                _assert_traversal_denied(base, headers, traversal_run)
            )
            print("acceptance: traversal denial passed", flush=True)

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
                cancel_events,
            )
            print("acceptance: provider cancellation passed", flush=True)

            serialized = json.dumps(observed_payloads, sort_keys=True)
            assert token not in serialized
            assert cancellation_marker not in serialized
            assert traversal_marker not in serialized
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
    (workspace / "test_acceptance_tool.py").write_text(
        "from acceptance_tool import add\n\n\n"
        "def test_add():\n"
        "    assert add(2, 3) == 5\n",
        encoding="utf-8",
    )
    (workspace / "delete_me.txt").write_bytes(_DELETE_TARGET.encode("utf-8"))
    _git(
        workspace,
        "add",
        "README.md",
        "test_acceptance_tool.py",
        "delete_me.txt",
    )
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
    parallel: bool = True,
    commit_changes: bool = True,
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
            "parallel": parallel,
            "max_workers": 1,
            "commit_changes": commit_changes,
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


def _wait_for_pending_tool_approval(
    base: str,
    headers: dict[str, str],
    run_id: str,
    *,
    timeout_seconds: float = 30,
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        response = requests.get(
            f"{base}/api/v1/runs/{run_id}",
            headers=headers,
            timeout=15,
        )
        if response.status_code == 404:
            time.sleep(0.05)
            continue
        response.raise_for_status()
        summary = response.json()
        if summary["status"] in _TERMINAL_STATUSES:
            raise AssertionError(
                f"Run {run_id} terminated before requesting tool approval."
            )
        approvals = _request(
            "GET",
            f"{base}/api/v1/runs/{run_id}/approvals",
            headers=headers,
        ).json()["approvals"]
        pending = [
            item
            for item in approvals
            if item["approval_kind"] == "tool" and item["state"] == "pending"
        ]
        if summary["status"] == "waiting_for_approval" and pending:
            assert len(pending) == 1
            return pending[0]
        time.sleep(0.05)
    raise TimeoutError(f"Run {run_id} did not request tool approval.")


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
    timeout_seconds: float = 30,
) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    deadline = time.monotonic() + timeout_seconds
    with requests.get(
        f"{base}/api/v1/runs/{run_id}/events",
        headers=headers,
        params={"after": 0},
        stream=True,
        timeout=(5, 20),
    ) as response:
        response.raise_for_status()
        for line in response.iter_lines(decode_unicode=True, chunk_size=1):
            if time.monotonic() >= deadline:
                raise TimeoutError(
                    f"Event replay for {run_id} did not include {until_event}."
                )
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
    events: list[dict[str, Any]],
) -> None:
    summary = _request(
        "GET",
        f"{base}/api/v1/runs/{run_id}",
        headers=headers,
    ).json()
    assert summary["status"] == "cancelled"
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


def _assert_tool_lifecycle(
    base: str,
    headers: dict[str, str],
    workspace: Path,
    run_id: str,
    events: list[dict[str, Any]],
) -> list[Any]:
    listed = _request(
        "GET",
        f"{base}/api/v1/runs/{run_id}/tool-invocations",
        headers=headers,
    ).json()
    invocations = listed["invocations"]
    tool_names = [item["tool_name"] for item in invocations]
    assert tool_names[:3] == [
        "filesystem.read",
        "filesystem.write",
        "test.execute",
    ]
    assert all(item["status"] == "succeeded" for item in invocations)
    assert all(
        item["policy_decision"]["outcome"]
        in {"allow", "allow_with_constraints"}
        for item in invocations
    )

    details = [
        _request(
            "GET",
            (
                f"{base}/api/v1/runs/{run_id}/tool-invocations/"
                f"{item['invocation_id']}"
            ),
            headers=headers,
        ).json()
        for item in invocations
    ]
    write = next(item for item in details if item["tool_name"] == "filesystem.write")
    pytest_call = next(
        item
        for item in details
        if item["tool_name"] == "test.execute"
        and item["caller_role"] == "coder"
    )
    assert write["result"]["status"] == "succeeded"
    assert any(
        artifact["relative_path"] == "acceptance_tool.py"
        for artifact in write["result"]["artifacts"]
    )
    assert pytest_call["result"]["exit_code"] == 0
    assert pytest_call["result"]["structured_output"]["persisted_summary"] is True
    assert (
        pytest_call["result"]["safe_diagnostic_metadata"][
            "structured_output_key_count"
        ]
        > 0
    )

    audit = _request(
        "GET",
        f"{base}/api/v1/runs/{run_id}/tool-audit",
        headers=headers,
    ).json()
    assert len(audit["records"]) == len(invocations)
    assert all(
        item["record"]["outcome"] == "succeeded" for item in audit["records"]
    )
    event_types = {event["event_type"] for event in events}
    assert {
        "tool_invocation_requested",
        "tool_policy_allowed",
        "tool_invocation_started",
        "tool_succeeded",
    } <= event_types

    report = _request(
        "GET",
        f"{base}/api/v1/runs/{run_id}/report",
        headers=headers,
    ).json()
    runtime = report["report"]["tool_runtime"]
    assert runtime["status_counts"] == {"succeeded": len(invocations)}
    assert runtime["audit_record_count"] == len(invocations)
    assert runtime["artifacts"][0]["relative_path"] == "acceptance_tool.py"
    after = _request(
        "GET",
        f"{base}/api/v1/runs/{run_id}/changes/acceptance_tool.py",
        headers=headers,
        params={"revision": "after"},
    ).json()
    assert "return left + right" in after["content"]
    assert (workspace / "acceptance_tool.py").is_file()
    return [listed, *details, audit, report, after]


def _assert_capability_escalation_rejected(
    base: str,
    headers: dict[str, str],
    run_id: str,
    approval: dict[str, Any],
) -> dict[str, Any]:
    assert approval["approval_kind"] == "tool"
    assert approval["tool_name"] == "filesystem.delete"
    assert approval["affected_paths"] == ["delete_me.txt"]
    assert [item["name"] for item in approval["capabilities"]] == [
        "filesystem.delete"
    ]
    assert approval["resource_budget"]["invocations_per_task"] > 0
    response = requests.post(
        f"{base}/api/v1/policy/evaluate",
        headers=headers,
        json={
            "run_id": run_id,
            "task_id": approval["task_id"],
            "tool_name": "filesystem.delete",
            "arguments": {
                "path": "delete_me.txt",
                "expected_sha256": _DELETE_TARGET_SHA256,
            },
            "expected_capabilities": ["filesystem.read"],
            "caller_role": "coder",
            "workspace_trusted": True,
            "provider_consented": True,
        },
        timeout=15,
    )
    assert response.status_code == 403
    payload = response.json()
    assert payload["error"]["code"] == "forbidden"
    assert "do not match runtime derivation" in payload["error"]["message"]
    return payload


def _assert_approved_deletion(
    base: str,
    headers: dict[str, str],
    workspace: Path,
    run_id: str,
    approval: dict[str, Any],
    events: list[dict[str, Any]],
) -> list[Any]:
    listed = _request(
        "GET",
        f"{base}/api/v1/runs/{run_id}/tool-invocations",
        headers=headers,
    ).json()
    deletion = next(
        item
        for item in listed["invocations"]
        if item["tool_name"] == "filesystem.delete"
    )
    assert deletion["status"] == "succeeded"
    assert deletion["approval_id"] == approval["approval_id"]
    assert deletion["policy_decision"]["outcome"] == "allow_with_constraints"
    detail = _request(
        "GET",
        (
            f"{base}/api/v1/runs/{run_id}/tool-invocations/"
            f"{deletion['invocation_id']}"
        ),
        headers=headers,
    ).json()
    assert detail["result"]["status"] == "succeeded"
    assert detail["result"]["approval_id"] == approval["approval_id"]
    assert not (workspace / "delete_me.txt").exists()

    audit = _request(
        "GET",
        f"{base}/api/v1/runs/{run_id}/tool-audit",
        headers=headers,
    ).json()
    deletion_audit = next(
        item["record"]
        for item in audit["records"]
        if item["record"]["invocation_id"] == deletion["invocation_id"]
    )
    assert deletion_audit["outcome"] == "succeeded"
    assert deletion_audit["approval_id"] == approval["approval_id"]
    event_types = {event["event_type"] for event in events}
    assert {
        "tool_approval_required",
        "tool_approval_approved",
        "tool_succeeded",
    } <= event_types
    report = _request(
        "GET",
        f"{base}/api/v1/runs/{run_id}/report",
        headers=headers,
    ).json()
    assert report["report"]["tool_runtime"]["approvals"]["states"] == {
        "approved": 1
    }
    return [listed, detail, audit, report]


def _assert_traversal_denied(
    base: str,
    headers: dict[str, str],
    run_id: str,
) -> list[Any]:
    listed = _request(
        "GET",
        f"{base}/api/v1/runs/{run_id}/tool-invocations",
        headers=headers,
    ).json()
    denied = next(
        item
        for item in listed["invocations"]
        if item["tool_name"] == "filesystem.read"
    )
    assert denied["status"] == "denied"
    assert denied["policy_decision"]["outcome"] == "deny"
    assert denied["policy_decision"]["rule_id"] == "deny.unsafe_path_syntax"
    detail = _request(
        "GET",
        (
            f"{base}/api/v1/runs/{run_id}/tool-invocations/"
            f"{denied['invocation_id']}"
        ),
        headers=headers,
    ).json()
    assert detail["result"] is None
    audit = _request(
        "GET",
        f"{base}/api/v1/runs/{run_id}/tool-audit",
        headers=headers,
    ).json()
    denied_audit = next(
        item["record"]
        for item in audit["records"]
        if item["record"]["invocation_id"] == denied["invocation_id"]
    )
    assert denied_audit["outcome"] == "denied"
    report = _request(
        "GET",
        f"{base}/api/v1/runs/{run_id}/report",
        headers=headers,
    ).json()
    runtime = report["report"]["tool_runtime"]
    assert runtime["status_counts"]["denied"] == 1
    assert runtime["denied_operations"] == [
        {
            "invocation_id": denied["invocation_id"],
            "tool_name": "filesystem.read",
            "rule_id": "deny.unsafe_path_syntax",
            "error_category": "policy_denied",
        }
    ]
    return [listed, detail, audit, report]


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
