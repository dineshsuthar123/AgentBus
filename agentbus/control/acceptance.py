from __future__ import annotations

import base64
import hashlib
import io
import json
import subprocess
import sys
import tempfile
import time
import zipfile
from pathlib import Path
from typing import Any

import requests

from agentbus.config import AgentBusConfig
from agentbus.execution.state_store import RunNotFoundError, StateStore
from agentbus.replay.service import TraceReplayService
from agentbus.replay.session import ReplayRequest, ReplaySessionStatus
from agentbus.security.redaction import safe_child_environment
from agentbus.trace.errors import TraceIntegrityError
from agentbus.trace.models import ReplayMode

_TERMINAL_STATUSES = {"succeeded", "failed", "cancelled"}
_SUCCESS_EVENT = "integration_commit_published"
_COMMIT_EVENT = "commit_created"
_CANCELLATION_EVENT = "cancellation_cleanup_completed"
_DELETE_TARGET = "deterministic deletion target\n"
_DELETE_TARGET_SHA256 = hashlib.sha256(_DELETE_TARGET.encode("utf-8")).hexdigest()
_MCP_ECHO_MARKER = "deterministic MCP hello"


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="agentbus-control-acceptance-") as temporary:
        root = Path(temporary)
        workspace = _initialize_repository(root / "repo")
        state_path = root / "state.db"
        runs_dir = root / "runs"
        registry = root / "daemons.json"
        mcp_fixture = (
            Path(__file__).resolve().parents[2]
            / "tests"
            / "fixtures"
            / "mcp"
            / "fake_server.py"
        )
        assert mcp_fixture.is_file()
        mcp_lifecycle_dir = root / "mcp-lifecycle"
        mcp_lifecycle_dir.mkdir()
        mcp_environment_marker = "acceptance-mcp-private-marker"
        traversal_marker = "acceptance-outside-secret-marker"
        (root / "outside.txt").write_text(traversal_marker, encoding="utf-8")
        process = _launch_daemon(
            workspace=workspace,
            state_path=state_path,
            runs_dir=runs_dir,
            registry=registry,
            mcp_fixture=mcp_fixture,
            mcp_lifecycle_dir=mcp_lifecycle_dir,
            mcp_environment_marker=mcp_environment_marker,
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
            resumed = _resume_run(
                base,
                headers,
                approval_run,
            )
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

            timeout_run = _submit_run(
                base,
                headers,
                workspace,
                task="Bound a slow managed process with its tool timeout.",
                profile="tool-process-timeout",
                latency_seconds=0,
                latency_roles=[],
                parallel=False,
                commit_changes=False,
            )
            timeout_summary = _wait_for_terminal_run(base, headers, timeout_run)
            assert timeout_summary["status"] == "succeeded"
            timeout_events = _replay_events(
                base,
                headers,
                timeout_run,
                until_event="durable_run_succeeded",
            )
            observed_payloads.extend(timeout_events)
            observed_payloads.extend(
                _assert_timed_out_process(base, headers, timeout_run)
            )
            print("acceptance: process timeout passed", flush=True)

            process_cancel_run = _submit_run(
                base,
                headers,
                workspace,
                task="Cancel a running managed process and clean up its process tree.",
                profile="tool-process-cancel",
                latency_seconds=0,
                latency_roles=[],
                parallel=False,
                commit_changes=False,
            )
            running_invocation = _wait_for_running_tool_invocation(
                base,
                headers,
                process_cancel_run,
                tool_name="process.execute",
            )
            observed_payloads.append(running_invocation)
            process_cancel_response = _request(
                "POST",
                (
                    f"{base}/api/v1/runs/{process_cancel_run}/tool-invocations/"
                    f"{running_invocation['invocation_id']}/cancel"
                ),
                headers=headers,
                json={"reason": "Offline acceptance managed process cancellation"},
            ).json()
            observed_payloads.append(process_cancel_response)
            assert process_cancel_response["invocation_status"] == "running"
            assert process_cancel_response["run_cancellation_requested"] is True
            assert process_cancel_response["cancellation"]["requested"] is True
            process_cancel_summary = _wait_for_terminal_run(
                base,
                headers,
                process_cancel_run,
            )
            assert process_cancel_summary["status"] == "cancelled"
            process_cancel_events = _replay_events(
                base,
                headers,
                process_cancel_run,
                until_event=_CANCELLATION_EVENT,
            )
            observed_payloads.extend(process_cancel_events)
            observed_payloads.extend(
                _assert_cancelled_process(
                    base,
                    headers,
                    process_cancel_run,
                    running_invocation["invocation_id"],
                    process_cancel_events,
                )
            )
            print("acceptance: process cancellation passed", flush=True)

            mcp_servers = _request(
                "GET",
                f"{base}/api/v1/mcp/servers",
                headers=headers,
            ).json()
            mcp_check = _request(
                "POST",
                f"{base}/api/v1/mcp/servers/fixture/check",
                headers=headers,
            ).json()
            observed_payloads.extend([mcp_servers, mcp_check])
            _assert_mcp_diagnostics(mcp_servers, mcp_check)
            before_mcp_run = _wait_for_mcp_cleanup(mcp_lifecycle_dir)
            observed_payloads.append(before_mcp_run)

            mcp_run = _submit_run(
                base,
                headers,
                workspace,
                task="Invoke the configured local MCP echo tool through normal policy.",
                profile="tool-local-mcp",
                latency_seconds=0,
                latency_roles=[],
                parallel=False,
                commit_changes=False,
            )
            mcp_approval = _wait_for_pending_tool_approval(
                base,
                headers,
                mcp_run,
            )
            observed_payloads.append(mcp_approval)
            _assert_mcp_approval(mcp_approval)
            mcp_approved = _request(
                "POST",
                (
                    f"{base}/api/v1/runs/{mcp_run}/approvals/"
                    f"{mcp_approval['approval_id']}/approve"
                ),
                headers=headers,
                json={
                    "revision": mcp_approval["revision"],
                    "reason": "Offline acceptance local MCP approval.",
                },
            ).json()
            observed_payloads.append(mcp_approved)
            assert mcp_approved["approval"]["state"] == "approved"
            mcp_resumed = _resume_run(
                base,
                headers,
                mcp_run,
            )
            assert mcp_resumed["resumed"] is True
            mcp_summary = _wait_for_terminal_run(base, headers, mcp_run)
            assert mcp_summary["status"] == "succeeded"
            mcp_events = _replay_events(
                base,
                headers,
                mcp_run,
                until_event="durable_run_succeeded",
            )
            observed_payloads.extend(mcp_events)
            observed_payloads.extend(
                _assert_mcp_invocation(
                    base,
                    headers,
                    mcp_run,
                    mcp_approval,
                    mcp_events,
                )
            )
            after_mcp_run = _wait_for_mcp_cleanup(
                mcp_lifecycle_dir,
                minimum_sessions=before_mcp_run["started_sessions"] + 2,
            )
            observed_payloads.append(after_mcp_run)
            print("acceptance: local MCP invocation passed", flush=True)

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

            mcp_cleanup = _wait_for_mcp_cleanup(mcp_lifecycle_dir)
            observed_payloads.append(mcp_cleanup)
            observed_payloads.extend(
                _assert_trace_replay_lifecycle(
                    base,
                    headers,
                    workspace=workspace,
                    state_path=state_path,
                    runs_dir=runs_dir,
                    run_id=tool_run,
                    root=root,
                    token=token,
                    mcp_fixture=mcp_fixture,
                    mcp_environment_marker=mcp_environment_marker,
                )
            )
            print("acceptance: deterministic replay lifecycle passed", flush=True)

            serialized = json.dumps(observed_payloads, sort_keys=True)
            assert token not in serialized
            assert cancellation_marker not in serialized
            assert traversal_marker not in serialized
            assert mcp_environment_marker not in serialized
            assert str(mcp_fixture) not in serialized
            assert (
                json.dumps(str(mcp_fixture))[1:-1]
                not in serialized
            )
            assert _MCP_ECHO_MARKER not in serialized
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
    mcp_fixture: Path,
    mcp_lifecycle_dir: Path,
    mcp_environment_marker: str,
) -> subprocess.Popen[str]:
    code = (
        "from agentbus.config import AgentBusConfig;"
        "from agentbus.control.server import serve;"
        "from agentbus.mcp import McpServerConfig,mcp_server_capabilities;"
        "import os,sys;"
        "m=McpServerConfig(server_id='fixture',transport='stdio',"
        "executable_alias='python',arguments=('-u',sys.argv[5],'--mode',"
        "'normal','--lifecycle-dir',sys.argv[6]),"
        "environment={'CI':os.environ['AGENTBUS_ACCEPTANCE_MCP_MARKER']},"
        "capability_map={'echo':mcp_server_capabilities('fixture'),"
        "'write_note':mcp_server_capabilities('fixture')});"
        "c=AgentBusConfig(workspace_dir=sys.argv[1],state_db=sys.argv[2],"
        "runs_dir=sys.argv[3],mcp_server_configs=(m,));"
        "raise SystemExit(serve(config=c,port=0,json_ready=True,"
        "idle_timeout=3.0,registry_path=sys.argv[4],log_level='error'))"
    )
    environment = safe_child_environment()
    environment["AGENTBUS_ACCEPTANCE_MCP_MARKER"] = mcp_environment_marker
    return subprocess.Popen(
        [
            sys.executable,
            "-c",
            code,
            str(workspace),
            str(state_path),
            str(runs_dir),
            str(registry),
            str(mcp_fixture),
            str(mcp_lifecycle_dir),
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        shell=False,
        env=environment,
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
    request = {
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
    }
    deadline = time.monotonic() + 5
    while True:
        response = requests.post(
            f"{base}/api/v1/runs",
            headers=headers,
            json=request,
            timeout=15,
        )
        if response.status_code != 409:
            response.raise_for_status()
            accepted = response.json()
            assert accepted["status"] == "pending"
            assert Path(accepted["workspace"]).resolve() == workspace
            return str(accepted["run_id"])
        payload = response.json()
        message = str(payload.get("error", {}).get("message", ""))
        if not message.startswith(
            "Workspace already has an active AgentBus run:"
        ):
            response.raise_for_status()
        if time.monotonic() >= deadline:
            raise TimeoutError(
                "The previous run did not release its workspace ownership."
            )
        time.sleep(0.02)


def _resume_run(
    base: str,
    headers: dict[str, str],
    run_id: str,
    *,
    clock=time.monotonic,
    sleeper=time.sleep,
) -> dict[str, Any]:
    deadline = clock() + 5
    while True:
        try:
            response = requests.post(
                f"{base}/api/v1/runs/{run_id}/resume",
                headers=headers,
                timeout=15,
            )
        except requests.ConnectionError:
            if clock() >= deadline:
                raise
            sleeper(0.05)
            continue
        if response.status_code != 409:
            response.raise_for_status()
            return response.json()
        payload = response.json()
        message = str(payload.get("error", {}).get("message", ""))
        if message != "The run already has an active owner.":
            response.raise_for_status()
        if clock() >= deadline:
            raise TimeoutError(
                "The previous run operation did not release its active owner."
            )
        sleeper(0.02)


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


def _wait_for_terminal_replay(
    base: str,
    headers: dict[str, str],
    replay_id: str,
    *,
    timeout_seconds: float = 30,
) -> dict[str, Any]:
    terminal = {
        ReplaySessionStatus.SUCCEEDED.value,
        ReplaySessionStatus.FAILED.value,
        ReplaySessionStatus.CANCELLED.value,
        ReplaySessionStatus.INCOMPATIBLE.value,
        ReplaySessionStatus.AWAITING_INPUT.value,
    }
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        response = requests.get(
            f"{base}/api/v1/replays/{replay_id}",
            headers=headers,
            timeout=10,
        )
        if response.status_code == 404:
            time.sleep(0.02)
            continue
        response.raise_for_status()
        replay = response.json()
        if replay["status"] in terminal:
            return replay
        time.sleep(0.02)
    raise TimeoutError(f"Replay {replay_id} did not reach a terminal state.")


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


def _wait_for_running_tool_invocation(
    base: str,
    headers: dict[str, str],
    run_id: str,
    *,
    tool_name: str,
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
                f"Run {run_id} terminated before {tool_name} started."
            )
        invocations = _request(
            "GET",
            f"{base}/api/v1/runs/{run_id}/tool-invocations",
            headers=headers,
        ).json()["invocations"]
        running = [
            item
            for item in invocations
            if item["tool_name"] == tool_name and item["status"] == "running"
        ]
        if running:
            assert len(running) == 1
            return running[0]
        time.sleep(0.05)
    raise TimeoutError(f"Run {run_id} did not start {tool_name}.")


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


def _assert_timed_out_process(
    base: str,
    headers: dict[str, str],
    run_id: str,
) -> list[Any]:
    listed = _request(
        "GET",
        f"{base}/api/v1/runs/{run_id}/tool-invocations",
        headers=headers,
    ).json()
    invocation = next(
        item
        for item in listed["invocations"]
        if item["tool_name"] == "process.execute"
    )
    assert invocation["status"] == "timed_out"
    detail = _request(
        "GET",
        (
            f"{base}/api/v1/runs/{run_id}/tool-invocations/"
            f"{invocation['invocation_id']}"
        ),
        headers=headers,
    ).json()
    result = detail["result"]
    assert result["status"] == "timed_out"
    assert result["timed_out"] is True
    assert result["error"]["code"] == "wall_clock_timeout"
    wall_clock_limit = result["resource_usage"]["limits"]["wall_clock_seconds"]
    assert wall_clock_limit["enforced"] is True
    report = _request(
        "GET",
        f"{base}/api/v1/runs/{run_id}/report",
        headers=headers,
    ).json()
    runtime = report["report"]["tool_runtime"]
    assert runtime["status_counts"]["timed_out"] == 1
    assert runtime["timeout_count"] == 1
    return [listed, detail, report]


def _assert_cancelled_process(
    base: str,
    headers: dict[str, str],
    run_id: str,
    invocation_id: str,
    events: list[dict[str, Any]],
) -> list[Any]:
    detail = _request(
        "GET",
        f"{base}/api/v1/runs/{run_id}/tool-invocations/{invocation_id}",
        headers=headers,
    ).json()
    assert detail["status"] == "cancelled"
    result = detail["result"]
    assert result["status"] == "cancelled"
    cancellation = result["cancellation"]
    assert cancellation["requested"] is True
    assert cancellation["signal_sent"] is True
    assert cancellation["acknowledged"] is True
    assert cancellation["process_terminated"] is True
    assert cancellation["cleanup_completed"] is True

    audit = _request(
        "GET",
        f"{base}/api/v1/runs/{run_id}/tool-audit",
        headers=headers,
    ).json()
    record = next(
        item["record"]
        for item in audit["records"]
        if item["record"]["invocation_id"] == invocation_id
    )
    assert record["outcome"] == "cancelled"
    event_types = [event["event_type"] for event in events]
    assert {
        "tool_cancel_requested",
        "tool_cancel_acknowledged",
        "tool_cancelled",
        "tool_cleanup_completed",
    } <= set(event_types)
    assert event_types.index("tool_cancel_acknowledged") < event_types.index(
        "tool_cancelled"
    )
    assert event_types.index("tool_cancelled") < event_types.index(
        "tool_cleanup_completed"
    )

    report = _request(
        "GET",
        f"{base}/api/v1/runs/{run_id}/report",
        headers=headers,
    ).json()
    runtime = report["report"]["tool_runtime"]
    assert report["status"] == "cancelled"
    assert report["report"]["current_leases"] == []
    assert runtime["status_counts"]["cancelled"] == 1
    assert runtime["cancellation_count"] == 1
    return [detail, audit, report]


def _assert_mcp_diagnostics(
    listed: dict[str, Any],
    checked: dict[str, Any],
) -> None:
    assert listed["total"] == 1
    server = listed["servers"][0]
    assert server["server_id"] == "fixture"
    assert server["transport"] == "stdio"
    assert [
        item["namespaced_name"] for item in server["configured_tools"]
    ] == ["mcp.fixture.echo", "mcp.fixture.write_note"]
    assert "arguments" not in server
    assert "environment" not in server
    assert checked["ready"] is True, checked["message"]
    assert checked["protocol_version"] == "2025-11-25"
    assert checked["advertised_tools"] == [
        "mcp.fixture.echo",
        "mcp.fixture.write_note",
    ]
    assert checked["cleanup_completed"] is True


def _assert_mcp_approval(approval: dict[str, Any]) -> None:
    assert approval["approval_kind"] == "tool"
    assert approval["tool_name"] == "mcp.fixture.echo"
    assert approval["affected_paths"] == []
    capabilities = approval["capabilities"]
    assert [item["name"] for item in capabilities] == [
        "mcp.connect",
        "mcp.invoke",
    ]
    assert all(
        item["scope"]["mcp_servers"] == ["fixture"] for item in capabilities
    )


def _assert_mcp_invocation(
    base: str,
    headers: dict[str, str],
    run_id: str,
    approval: dict[str, Any],
    events: list[dict[str, Any]],
) -> list[Any]:
    listed = _request(
        "GET",
        f"{base}/api/v1/runs/{run_id}/tool-invocations",
        headers=headers,
    ).json()
    invocation = next(
        item
        for item in listed["invocations"]
        if item["tool_name"] == "mcp.fixture.echo"
    )
    assert invocation["status"] == "succeeded"
    assert invocation["approval_id"] == approval["approval_id"]
    assert invocation["policy_decision"]["outcome"] == "allow_with_constraints"
    detail = _request(
        "GET",
        (
            f"{base}/api/v1/runs/{run_id}/tool-invocations/"
            f"{invocation['invocation_id']}"
        ),
        headers=headers,
    ).json()
    result = detail["result"]
    assert result["status"] == "succeeded"
    assert result["approval_id"] == approval["approval_id"]
    assert result["structured_output"]["persisted_summary"] is True
    assert result["safe_diagnostic_metadata"]["structured_output_key_count"] > 0
    assert _MCP_ECHO_MARKER not in json.dumps(detail, sort_keys=True)

    audit = _request(
        "GET",
        f"{base}/api/v1/runs/{run_id}/tool-audit",
        headers=headers,
    ).json()
    record = next(
        item["record"]
        for item in audit["records"]
        if item["record"]["invocation_id"] == invocation["invocation_id"]
    )
    assert record["outcome"] == "succeeded"
    assert record["approval_id"] == approval["approval_id"]
    event_types = {event["event_type"] for event in events}
    assert {
        "tool_approval_required",
        "tool_approval_approved",
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
    assert runtime["mcp_usage"] == {
        "servers": ["fixture"],
        "invocation_count": 1,
    }
    assert runtime["approvals"]["states"] == {"approved": 1}
    return [listed, detail, audit, report]


def _assert_trace_replay_lifecycle(
    base: str,
    headers: dict[str, str],
    *,
    workspace: Path,
    state_path: Path,
    runs_dir: Path,
    run_id: str,
    root: Path,
    token: str,
    mcp_fixture: Path,
    mcp_environment_marker: str,
) -> list[Any]:
    observed: list[Any] = []
    trace = _request(
        "GET",
        f"{base}/api/v1/runs/{run_id}/trace",
        headers=headers,
    ).json()
    assert trace["status"] == "succeeded"
    assert trace["completed_at"] is not None
    assert trace["span_count"] > 0
    assert trace["checkpoint_count"] > 0
    trace_id = str(trace["trace_id"])

    span_page = _request(
        "GET",
        f"{base}/api/v1/runs/{run_id}/trace/spans",
        headers=headers,
        params={"limit": 500},
    ).json()
    assert span_page["truncated"] is False
    spans = span_page["spans"]
    span_types = {item["span_type"] for item in spans}
    required_span_types = {
        "model_parse",
        "tool_policy",
        "tool_invocation",
        "verifier",
        "reviewer",
    }
    assert required_span_types <= span_types

    provenance = _request(
        "GET",
        f"{base}/api/v1/runs/{run_id}/provenance",
        headers=headers,
    ).json()
    assert provenance["trace_id"] == trace_id
    assert provenance["run_id"] == run_id
    assert len(provenance["integrity_root"]) == 64
    replayability = _request(
        "GET",
        f"{base}/api/v1/runs/{run_id}/replayability",
        headers=headers,
        params={"limit": 500},
    ).json()
    assert replayability["trace_id"] == trace_id
    assert replayability["replayable_offline"] is True
    assert replayability["missing_input_hashes"] == []
    assert replayability["truncated"] is False

    config = AgentBusConfig(
        workspace_dir=str(workspace),
        state_db=str(state_path),
        runs_dir=str(runs_dir),
    )
    store = StateStore(state_path)
    service = TraceReplayService(config, state_store=store)
    verified = service.verify(run_id)
    assert verified.valid is True
    assert verified.provenance_root == provenance["integrity_root"]

    report_before = _request(
        "GET",
        f"{base}/api/v1/runs/{run_id}/report",
        headers=headers,
    ).json()
    diff_before = _request(
        "GET",
        f"{base}/api/v1/runs/{run_id}/diff",
        headers=headers,
    ).json()
    head_before = _git_output(workspace, "rev-parse", "HEAD")
    status_before = _git_output(workspace, "status", "--short")

    accepted = _request(
        "POST",
        f"{base}/api/v1/runs/{run_id}/replays",
        headers=headers,
        json={"mode": "offline"},
    ).json()
    assert accepted["source_trace_id"] == trace_id
    replay = _wait_for_terminal_replay(
        base,
        headers,
        accepted["replay_id"],
    )
    _assert_providerless_replay(replay, isolated=False)
    _assert_replayed_span_contracts(base, headers, run_id, spans, replay)

    report_after = _request(
        "GET",
        f"{base}/api/v1/runs/{run_id}/report",
        headers=headers,
    ).json()
    diff_after = _request(
        "GET",
        f"{base}/api/v1/runs/{run_id}/diff",
        headers=headers,
    ).json()
    assert report_after == report_before
    assert diff_after == diff_before
    assert _git_output(workspace, "rev-parse", "HEAD") == head_before
    assert _git_output(workspace, "status", "--short") == status_before

    checkpoint = next(
        (
            item
            for item in trace["checkpoints"]
            if item["replayable"] and item["label"] == "task-graph-persisted"
        ),
        next(
            item for item in trace["checkpoints"] if item["replayable"]
        ),
    )
    checkpoint_accepted = _request(
        "POST",
        f"{base}/api/v1/runs/{run_id}/replays",
        headers=headers,
        json={
            "mode": "offline",
            "from_checkpoint_id": checkpoint["checkpoint_id"],
        },
    ).json()
    checkpoint_replay = _wait_for_terminal_replay(
        base,
        headers,
        checkpoint_accepted["replay_id"],
    )
    _assert_providerless_replay(checkpoint_replay, isolated=True)
    assert (
        checkpoint_replay["from_checkpoint_id"]
        == checkpoint["checkpoint_id"]
    )

    fork_accepted = _request(
        "POST",
        f"{base}/api/v1/runs/{run_id}/replays",
        headers=headers,
        json={
            "mode": "offline",
            "fork": True,
            "changed_inputs": {
                "resource_budgets": {"invocations_per_run": 64}
            },
            "live_provider_consent": False,
        },
    ).json()
    fork_replay = _wait_for_terminal_replay(
        base,
        headers,
        fork_accepted["replay_id"],
    )
    _assert_providerless_replay(fork_replay, isolated=False)
    assert fork_replay["fork"] is True
    assert fork_replay["changed_input_names"] == ["resource_budgets"]
    assert fork_replay["result_trace_id"]
    assert fork_replay["comparison_id"]

    comparison = _request(
        "GET",
        f"{base}/api/v1/comparisons/{fork_replay['comparison_id']}",
        headers=headers,
        params={"limit": 500},
    ).json()
    _assert_expected_fork_comparison(
        comparison,
        source_trace_id=trace_id,
        result_trace_id=fork_replay["result_trace_id"],
        source_provenance_root=provenance["integrity_root"],
    )
    explicit_comparison = _request(
        "POST",
        f"{base}/api/v1/comparisons",
        headers=headers,
        params={"limit": 500},
        json={
            "left": trace_id,
            "right": fork_replay["result_trace_id"],
        },
    ).json()
    assert explicit_comparison["comparison_id"] == comparison["comparison_id"]
    assert explicit_comparison["summary"] == comparison["summary"]

    fork_trace = store.get_trace(fork_replay["result_trace_id"])
    fork_trace_response = _request(
        "GET",
        f"{base}/api/v1/runs/{fork_trace.run_id}/trace",
        headers=headers,
    ).json()
    assert fork_trace_response["source_trace_id"] == trace_id
    assert fork_trace_response["providerless"] is True
    fork_provenance = _request(
        "GET",
        f"{base}/api/v1/runs/{fork_trace.run_id}/provenance",
        headers=headers,
    ).json()
    assert (
        fork_provenance["integrity_root"]
        == comparison["right_provenance_root"]
    )

    export_payload = _request(
        "GET",
        f"{base}/api/v1/traces/{trace_id}/export",
        headers=headers,
        params={"include_source_content": True},
    ).json()
    archive_bytes = _decode_control_archive(export_payload)
    assert export_payload["trace_id"] == trace_id
    assert export_payload["source_content_included"] is True
    _assert_archive_excludes_private_values(
        archive_bytes,
        token,
        str(workspace),
        str(mcp_fixture),
        mcp_environment_marker,
    )

    fixture_denied = _request_expect_status(
        "POST",
        f"{base}/api/v1/runs/{run_id}/fixtures",
        403,
        headers=headers,
        json={},
    ).json()
    fixture_payload = _request(
        "POST",
        f"{base}/api/v1/runs/{run_id}/fixtures",
        headers=headers,
        json={"include_source_content": True},
    ).json()
    fixture_bytes = _decode_control_archive(fixture_payload)
    assert fixture_payload["trace_id"] == trace_id
    assert fixture_payload["source_content_included"] is True
    assert fixture_payload["source_warning"]
    assert fixture_payload["license_warning"]
    assert fixture_payload["assertions_validated"] is True
    assert fixture_payload["replay_started"] is False
    _assert_archive_excludes_private_values(
        fixture_bytes,
        token,
        str(workspace),
        str(mcp_fixture),
        mcp_environment_marker,
    )

    cancelled_replay = _assert_cooperative_replay_cancellation(
        config,
        store,
        trace_id=trace_id,
        run_id=run_id,
    )
    cancelled_api = _request(
        "GET",
        f"{base}/api/v1/replays/{cancelled_replay['replay_id']}",
        headers=headers,
    ).json()
    assert cancelled_api["status"] == ReplaySessionStatus.CANCELLED.value

    listed_replays = _request(
        "GET",
        f"{base}/api/v1/replays",
        headers=headers,
        params={"source_trace_id": trace_id, "limit": 500},
    ).json()
    assert listed_replays["truncated"] is False
    assert all(
        item["status"]
        in {
            ReplaySessionStatus.SUCCEEDED.value,
            ReplaySessionStatus.CANCELLED.value,
        }
        for item in listed_replays["replays"]
    )
    assert _git_output(workspace, "rev-parse", "HEAD") == head_before
    assert _git_output(workspace, "status", "--short") == status_before

    _assert_trace_blob_tamper_detected(service, store, run_id)
    fixture_execution = _execute_regression_fixture(
        root,
        fixture_bytes,
    )
    imported_observed = _assert_isolated_archive_import(
        root,
        archive_base64=export_payload["archive_base64"],
        expected_trace_id=trace_id,
        expected_provenance_root=provenance["integrity_root"],
        mcp_fixture=mcp_fixture,
    )

    observed.extend(
        [
            trace,
            span_page,
            provenance,
            replayability,
            verified.model_dump(mode="json"),
            report_before,
            diff_before,
            accepted,
            replay,
            checkpoint_accepted,
            checkpoint_replay,
            fork_accepted,
            fork_replay,
            comparison,
            explicit_comparison,
            fork_trace_response,
            fork_provenance,
            _without_archive(export_payload),
            fixture_denied,
            _without_archive(fixture_payload),
            cancelled_replay,
            cancelled_api,
            listed_replays,
            fixture_execution,
            *imported_observed,
        ]
    )
    return observed


def _assert_providerless_replay(
    replay: dict[str, Any],
    *,
    isolated: bool,
) -> None:
    assert replay["status"] == ReplaySessionStatus.SUCCEEDED.value, replay
    assert replay["provider_calls"] == 0
    assert replay["network_calls"] == 0
    assert replay["missing_inputs"] == []
    assert replay["isolated"] is isolated
    assert "isolated_workspace" not in replay
    if isolated:
        assert (
            replay["isolation_scope"]
            == "daemon_managed_temporary_workspace"
        )
    else:
        assert replay["isolation_scope"] is None


def _assert_replayed_span_contracts(
    base: str,
    headers: dict[str, str],
    run_id: str,
    spans: list[dict[str, Any]],
    replay: dict[str, Any],
) -> None:
    assert replay["span_results_truncated"] is False
    by_span_id = {item["span_id"]: item for item in spans}
    actions_by_type: dict[str, set[str]] = {}
    for result in replay["span_results"]:
        source = by_span_id[result["span_id"]]
        actions_by_type.setdefault(source["span_type"], set()).add(
            result["action"]
        )
        assert result["succeeded"] is True
    for span_type in {"model_parse", "tool_policy", "verifier", "reviewer"}:
        assert actions_by_type[span_type] == {"replayed"}
    assert actions_by_type["tool_invocation"] <= {"reused", "simulated"}
    assert actions_by_type["tool_invocation"]
    assert replay["policy_drift"] == []

    results_by_span = {
        item["span_id"]: item for item in replay["span_results"]
    }
    for source in spans:
        if source["span_type"] not in {
            "tool_policy",
            "verifier",
            "reviewer",
        }:
            continue
        result = results_by_span[source["span_id"]]
        detail = _request(
            "GET",
            (
                f"{base}/api/v1/runs/{run_id}/trace/spans/"
                f"{source['span_id']}"
            ),
            headers=headers,
        ).json()
        output_hashes = {item["sha256"] for item in detail["outputs"]}
        assert result["output_sha256"] in output_hashes


def _assert_expected_fork_comparison(
    comparison: dict[str, Any],
    *,
    source_trace_id: str,
    result_trace_id: str,
    source_provenance_root: str,
) -> None:
    assert comparison["left_trace_id"] == source_trace_id
    assert comparison["right_trace_id"] == result_trace_id
    assert comparison["left_provenance_root"] == source_provenance_root
    assert comparison["right_provenance_root"] != source_provenance_root
    assert comparison["summary"]["provenance_root_changed"] is True
    changed = sum(
        comparison["summary"][name]
        for name in ("changed_spans", "added_spans", "removed_spans")
    )
    assert changed > 0
    assert "expected" in comparison["categories"]
    assert comparison["truncated"] is False


def _assert_cooperative_replay_cancellation(
    config: AgentBusConfig,
    store: StateStore,
    *,
    trace_id: str,
    run_id: str,
) -> dict[str, Any]:
    checks = 0

    def cancelled() -> bool:
        nonlocal checks
        checks += 1
        return checks >= 3

    replay_id = "acceptance-cancelled-replay"
    service = TraceReplayService(
        config,
        state_store=store,
        cancelled=cancelled,
    )
    result = service.replay(
        trace_id,
        ReplayRequest(
            replay_id=replay_id,
            source_trace_id=trace_id,
            source_run_id=run_id,
            mode=ReplayMode.OFFLINE,
        ),
    )
    assert result.session.status == ReplaySessionStatus.CANCELLED
    assert result.session.provider_calls == 0
    assert result.session.network_calls == 0
    assert len(result.session.span_results) == 2
    return result.session.model_dump(mode="json")


def _assert_trace_blob_tamper_detected(
    service: TraceReplayService,
    store: StateStore,
    run_id: str,
) -> None:
    manifest = store.get_run_provenance_manifest(run_id)
    digest = next(
        item.identifier
        for item in manifest.integrity_entries
        if item.kind == "blob"
    )
    blob_path = service.object_store.blob_directory / digest[:2] / digest
    original = blob_path.read_bytes()
    detected = False
    try:
        blob_path.write_bytes(original + b"\0tampered")
        try:
            service.verify(run_id)
        except TraceIntegrityError:
            detected = True
    finally:
        blob_path.write_bytes(original)
    assert detected is True
    assert service.verify(run_id).valid is True


def _execute_regression_fixture(
    root: Path,
    fixture_bytes: bytes,
) -> dict[str, Any]:
    fixture_root = root / "fixture-runtime"
    workspace = fixture_root / "workspace"
    workspace.mkdir(parents=True)
    fixture_path = fixture_root / "captured.agentbus-trace"
    fixture_path.write_bytes(fixture_bytes)
    config = AgentBusConfig(
        workspace_dir=str(workspace),
        state_db=str(fixture_root / "state.db"),
        runs_dir=str(fixture_root / "runs"),
    )
    result = TraceReplayService(config).replay_archive(
        fixture_path,
        allow_source_content=True,
    )
    assert result.replay.session.status == ReplaySessionStatus.SUCCEEDED
    assert result.replay.session.provider_calls == 0
    assert result.replay.session.network_calls == 0
    assert result.fixture_assertions is not None
    assert result.fixture_assertions.passed is True
    assert result.fixture_assertions.failures == []
    return {
        "trace_id": result.imported.trace.trace_id,
        "run_id": result.imported.trace.run_id,
        "status": result.replay.session.status.value,
        "provider_calls": result.replay.session.provider_calls,
        "network_calls": result.replay.session.network_calls,
        "fixture_assertions": result.fixture_assertions.model_dump(
            mode="json"
        ),
    }


def _assert_isolated_archive_import(
    root: Path,
    *,
    archive_base64: str,
    expected_trace_id: str,
    expected_provenance_root: str,
    mcp_fixture: Path,
) -> list[Any]:
    workspace = _initialize_repository(root / "import-repo")
    state_path = root / "import-state.db"
    runs_dir = root / "import-runs"
    registry = root / "import-daemons.json"
    lifecycle_dir = root / "import-mcp-lifecycle"
    lifecycle_dir.mkdir()
    environment_marker = "acceptance-import-mcp-private-marker"
    process = _launch_daemon(
        workspace=workspace,
        state_path=state_path,
        runs_dir=runs_dir,
        registry=registry,
        mcp_fixture=mcp_fixture,
        mcp_lifecycle_dir=lifecycle_dir,
        mcp_environment_marker=environment_marker,
    )
    daemon_token = ""
    try:
        handshake = _read_handshake(process)
        daemon_token = handshake["bearer_token"]
        base = f"http://127.0.0.1:{handshake['port']}"
        headers = {"Authorization": f"Bearer {daemon_token}"}
        denied = _request_expect_status(
            "POST",
            f"{base}/api/v1/traces/import",
            403,
            headers=headers,
            json={
                "archive_base64": archive_base64,
                "allow_source_content": False,
            },
        ).json()
        imported = _request(
            "POST",
            f"{base}/api/v1/traces/import",
            headers=headers,
            json={
                "archive_base64": archive_base64,
                "allow_source_content": True,
            },
        ).json()
        assert imported["trace_id"] == expected_trace_id
        assert imported["provenance_root"] == expected_provenance_root
        assert imported["replay_started"] is False

        accepted = _request(
            "POST",
            f"{base}/api/v1/runs/{imported['run_id']}/replays",
            headers=headers,
            json={"mode": "offline"},
        ).json()
        replay = _wait_for_terminal_replay(
            base,
            headers,
            accepted["replay_id"],
        )
        _assert_providerless_replay(replay, isolated=False)
        imported_trace = _request(
            "GET",
            f"{base}/api/v1/runs/{imported['run_id']}/trace",
            headers=headers,
        ).json()
        assert imported_trace["trace_id"] == expected_trace_id

        assert process.wait(timeout=30) == 0
        assert process.stderr is not None
        stderr = process.stderr.read()
        assert daemon_token not in stderr
        assert environment_marker not in stderr
        registry_payload = json.loads(registry.read_text(encoding="utf-8"))
        assert registry_payload["daemons"] == []
        return [denied, imported, accepted, replay, imported_trace]
    finally:
        if process.poll() is None:
            process.terminate()
            process.wait(timeout=10)


def _decode_control_archive(payload: dict[str, Any]) -> bytes:
    archive = base64.b64decode(payload["archive_base64"], validate=True)
    assert 0 < len(archive) <= 650_000
    assert base64.b64encode(archive).decode("ascii") == payload["archive_base64"]
    assert hashlib.sha256(archive).hexdigest() == payload["archive_sha256"]
    return archive


def _without_archive(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in payload.items()
        if key != "archive_base64"
    }


def _assert_archive_excludes_private_values(
    archive: bytes,
    *values: str,
) -> None:
    with zipfile.ZipFile(io.BytesIO(archive), mode="r") as bundle:
        content = b"\n".join(bundle.read(name) for name in bundle.namelist())
    for value in values:
        encodings = {
            value,
            value.replace("\\", "/"),
            json.dumps(value)[1:-1],
        }
        assert all(item.encode("utf-8") not in content for item in encodings)


def _wait_for_mcp_cleanup(
    lifecycle_dir: Path,
    *,
    minimum_sessions: int = 1,
    timeout_seconds: float = 30,
) -> dict[str, int]:
    deadline = time.monotonic() + timeout_seconds
    started: set[str] = set()
    stopped: set[str] = set()
    while time.monotonic() < deadline:
        started = {
            path.name.removesuffix(".started")
            for path in lifecycle_dir.glob("*.started")
        }
        stopped = {
            path.name.removesuffix(".stopped")
            for path in lifecycle_dir.glob("*.stopped")
        }
        if len(started) >= minimum_sessions and started == stopped:
            return {
                "started_sessions": len(started),
                "stopped_sessions": len(stopped),
            }
        time.sleep(0.05)
    raise TimeoutError(
        "Configured MCP processes did not complete cleanup "
        f"(started={len(started)}, stopped={len(stopped)})."
    )


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


def _request_expect_status(
    method: str,
    url: str,
    expected_status: int,
    **kwargs,
):
    deadline = time.monotonic() + 5
    while True:
        try:
            response = requests.request(method, url, timeout=15, **kwargs)
            if response.status_code != expected_status:
                raise AssertionError(
                    f"Expected HTTP {expected_status}, "
                    f"received HTTP {response.status_code}."
                )
            return response
        except requests.ConnectionError:
            if time.monotonic() >= deadline:
                raise
            time.sleep(0.05)


def _git_output(workspace: Path, *arguments: str) -> str:
    result = subprocess.run(
        ["git", *arguments],
        cwd=workspace,
        capture_output=True,
        text=True,
        shell=False,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip())
    return result.stdout.strip()


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
