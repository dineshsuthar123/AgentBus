from __future__ import annotations

import json
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

import pytest

from agentbus.agents.planner import PlannerOutput
from agentbus.config import AgentBusConfig
from agentbus.execution.cancellation import CancellationRequested
from agentbus.execution.cancellation_registry import CancellationRegistry
from agentbus.execution.models import (
    RunRecord,
    RunStatus,
    TaskSpec,
    TaskStatus,
)
from agentbus.execution.state_store import StateStore
from agentbus.mcp import McpServerConfig, mcp_server_capabilities
from agentbus.models.router import ModelRouter
from agentbus.models.types import ModelRole
from agentbus.policy import ToolApprovalDisposition
from agentbus.runtime.loop import AgentLoop, ManagedToolApprovalRequired
from agentbus.sandbox.platform import ExecutableCatalog
from agentbus.tools.protocol import ToolInvocationStatus, ToolResourceBudget
from agentbus.tools.runtime import ManagedToolRuntime, build_managed_tool_runtime


MCP_FIXTURE = Path(__file__).parent / "fixtures" / "mcp" / "fake_server.py"
DELETE_TARGET = "deterministic deletion target\n"


@dataclass
class ProfileHarness:
    workspace: Path
    config: AgentBusConfig
    store: StateStore
    cancellations: CancellationRegistry
    plan: dict
    profile: str

    def runtime(
        self,
        *,
        owned_worktree: bool = False,
        with_mcp: bool = False,
    ) -> ManagedToolRuntime:
        alias = "fake-mcp-profile"
        commands: dict[str, object] = {
            "python": sys.executable,
            "pytest": (sys.executable, "-m", "pytest"),
            "git": "git",
        }
        if with_mcp:
            commands[alias] = (
                sys.executable,
                "-u",
                str(MCP_FIXTURE),
                "--mode",
                "normal",
            )
        runtime = build_managed_tool_runtime(
            workspace=self.workspace,
            state_store=self.store,
            cancellation_registry=self.cancellations,
            executable_catalog=ExecutableCatalog(commands),
            owned_worktree=owned_worktree,
        )
        if with_mcp:
            runtime.import_mcp_server(
                McpServerConfig(
                    server_id="fixture",
                    transport="stdio",
                    executable_alias=alias,
                    capability_map={
                        name: mcp_server_capabilities("fixture")
                        for name in ("echo", "write_note")
                    },
                ),
                run_id="run-1",
            )
        return runtime

    def loop(self, runtime: ManagedToolRuntime) -> AgentLoop:
        model = ModelRouter(self.config).for_role(ModelRole.CODER)
        capabilities = self.plan["steps"][0]["required_capabilities"]
        return AgentLoop(
            config=self.config,
            model=model,
            cancellation=self.cancellations.get("run-1"),
            tool_runtime=runtime,
            state_store=self.store,
            cancellation_registry=self.cancellations,
            run_id="run-1",
            task_id="step-1",
            resource_budget=self.config.tool_resource_budget,
            policy_context={"planned_capabilities": capabilities},
        )


@pytest.mark.parametrize(
    ("profile", "tool_name"),
    [
        ("tool-safe-read", "filesystem.read"),
        ("tool-atomic-write", "filesystem.write"),
        ("tool-source-patch", "filesystem.patch"),
        ("tool-pytest", "test.execute"),
        ("tool-git-diff", "git.diff"),
    ],
)
def test_deterministic_profiles_execute_through_managed_runtime(
    tmp_path: Path,
    profile: str,
    tool_name: str,
) -> None:
    harness = _harness(tmp_path, profile)
    if profile == "tool-git-diff":
        (harness.workspace / "README.md").write_text(
            "deterministic profile changed\n",
            encoding="utf-8",
        )
    runtime = harness.runtime()
    try:
        summary = harness.loop(runtime).run("Execute the deterministic profile.")
    finally:
        runtime.close()

    records = harness.store.list_tool_invocations("run-1")
    audits = harness.store.list_tool_audits("run-1")
    assert summary == f"Completed deterministic profile {profile}."
    assert len(records) == len(audits) == 1
    assert records[0].tool_name == tool_name
    assert records[0].status == ToolInvocationStatus.SUCCEEDED
    assert audits[0].record.outcome == ToolInvocationStatus.SUCCEEDED
    if profile == "tool-atomic-write":
        assert (harness.workspace / "profile_result.txt").read_text(
            encoding="utf-8"
        ) == "deterministic atomic write\n"
    if profile == "tool-source-patch":
        assert (harness.workspace / "module.py").read_text(
            encoding="utf-8"
        ) == "VALUE = 2\n"
    if profile == "tool-pytest":
        result = records[0].safe_result
        assert result.exit_code == 0
        assert result.safe_diagnostic_metadata["structured_output_key_count"] > 0
        assert result.safe_diagnostic_metadata["stdout_retained_bytes"] > 0
    if profile == "tool-git-diff":
        assert "deterministic profile changed" in _git(
            harness.workspace,
            "diff",
            "--",
            "README.md",
        )
        assert records[0].safe_result.structured_output["persisted_summary"] is True


def test_control_acceptance_profile_executes_real_multi_tool_lifecycle(
    tmp_path: Path,
) -> None:
    harness = _harness(tmp_path, "tool-control-acceptance")
    runtime = harness.runtime()
    try:
        summary = harness.loop(runtime).run(
            "Read, write, and verify through managed tools."
        )
    finally:
        runtime.close()

    records = harness.store.list_tool_invocations("run-1")
    audits = harness.store.list_tool_audits("run-1")
    assert summary == "Completed deterministic profile tool-control-acceptance."
    assert [record.tool_name for record in records] == [
        "filesystem.read",
        "filesystem.write",
        "test.execute",
    ]
    assert all(
        record.status == ToolInvocationStatus.SUCCEEDED for record in records
    )
    assert len(audits) == 3
    assert all(
        audit.record.outcome == ToolInvocationStatus.SUCCEEDED for audit in audits
    )
    assert (harness.workspace / "acceptance_tool.py").read_text(
        encoding="utf-8"
    ) == (
        "def add(left: int, right: int) -> int:\n"
        "    return left + right\n"
    )
    assert records[-1].safe_result.exit_code == 0


@pytest.mark.parametrize(
    ("profile", "target", "rule_id"),
    [
        (
            "tool-deny-outside-read",
            "outside.txt",
            "deny.unsafe_path_syntax",
        ),
        (
            "tool-deny-credential-read",
            "workspace/.env",
            "deny.protected_file",
        ),
    ],
)
def test_deterministic_denial_profiles_never_read_protected_content(
    tmp_path: Path,
    profile: str,
    target: str,
    rule_id: str,
) -> None:
    secret = "profile-secret-must-not-leak"
    harness = _harness(tmp_path, profile)
    (tmp_path / target).write_text(secret, encoding="utf-8")
    runtime = harness.runtime()
    try:
        harness.loop(runtime).run("Attempt the denied deterministic profile.")
    finally:
        runtime.close()

    record = harness.store.list_tool_invocations("run-1")[0]
    audit = harness.store.list_tool_audits("run-1")[0]
    events = harness.store.list_events("run-1")
    assert record.status == ToolInvocationStatus.DENIED
    assert record.policy_decision.rule_id == rule_id
    assert record.safe_result is None
    persisted = record.model_dump_json() + audit.model_dump_json()
    persisted += json.dumps(events, sort_keys=True, default=str)
    assert secret not in persisted
    assert audit.record.outcome == ToolInvocationStatus.DENIED


def test_deterministic_timeout_profile_terminates_with_bounded_result(
    tmp_path: Path,
) -> None:
    harness = _harness(tmp_path, "tool-process-timeout")
    runtime = harness.runtime()
    try:
        harness.loop(runtime).run("Time out the managed process.")
        snapshot = runtime.dispatcher.budget_ledger.snapshot("run-1")
    finally:
        runtime.close()

    result = harness.store.list_tool_invocations("run-1")[0].safe_result
    assert result.status == ToolInvocationStatus.TIMED_OUT
    assert result.timed_out is True
    assert result.error.code == "wall_clock_timeout"
    assert result.structured_output["persisted_summary"] is True
    assert snapshot.active_processes == 0


def test_deterministic_excessive_output_is_truncated_and_resource_exhausted(
    tmp_path: Path,
) -> None:
    harness = _harness(tmp_path, "tool-excessive-output")
    runtime = harness.runtime()
    try:
        harness.loop(runtime).run("Bound excessive managed process output.")
    finally:
        runtime.close()

    result = harness.store.list_tool_invocations("run-1")[0].safe_result
    assert result.status == ToolInvocationStatus.FAILED
    assert result.error.code == "budget_stdout_bytes"
    assert result.stdout_truncated is True
    assert len(result.stdout.encode("utf-8")) <= (
        harness.config.tool_resource_budget.stdout_bytes
    )


def test_deterministic_budget_profile_rejects_the_second_invocation(
    tmp_path: Path,
) -> None:
    budget = ToolResourceBudget(
        invocations_per_task=1,
        invocations_per_run=1,
    )
    harness = _harness(tmp_path, "tool-budget-exhaustion", budget=budget)
    runtime = harness.runtime()
    try:
        summary = harness.loop(runtime).run("Exhaust the managed invocation budget.")
    finally:
        runtime.close()

    records = harness.store.list_tool_invocations("run-1")
    events = harness.store.list_events("run-1")
    assert summary == "Completed deterministic profile tool-budget-exhaustion."
    assert len(records) == 1
    assert records[0].status == ToolInvocationStatus.SUCCEEDED
    assert [
        event["event_type"]
        for event in events
        if event["event_type"] == "tool_budget_rejected"
    ] == ["tool_budget_rejected"]


def test_deterministic_loop_profile_stops_at_the_configured_bound(
    tmp_path: Path,
) -> None:
    harness = _harness(tmp_path, "tool-loop-limit")
    runtime = harness.runtime()
    try:
        result = harness.loop(runtime).run(
            "Bound the repeated tool loop.",
            max_steps=3,
        )
    finally:
        runtime.close()

    records = harness.store.list_tool_invocations("run-1")
    assert "max_steps was reached" in result
    assert len(records) == 3
    assert all(
        record.status == ToolInvocationStatus.SUCCEEDED for record in records
    )


@pytest.mark.parametrize(
    ("profile", "owned_worktree", "expected_tool"),
    [
        ("tool-delete-approval", False, "filesystem.delete"),
        ("tool-git-commit", True, "git.commit"),
    ],
)
def test_deterministic_approval_profiles_resume_from_persisted_exact_grant(
    tmp_path: Path,
    profile: str,
    owned_worktree: bool,
    expected_tool: str,
) -> None:
    harness = _harness(tmp_path, profile)
    first_runtime = harness.runtime(owned_worktree=owned_worktree)
    try:
        with pytest.raises(ManagedToolApprovalRequired) as captured:
            harness.loop(first_runtime).run("Suspend for exact tool approval.")
    finally:
        first_runtime.close()

    pending = harness.store.get_tool_approval(
        "run-1",
        captured.value.approval_id,
    )
    assert pending.disposition is None
    assert pending.request.tool_name == expected_tool
    harness.store.decide_tool_approval(
        "run-1",
        captured.value.approval_id,
        disposition=ToolApprovalDisposition.APPROVED,
        reason="Approve the deterministic offline profile.",
    )

    resumed_runtime = harness.runtime(owned_worktree=owned_worktree)
    try:
        summary = harness.loop(resumed_runtime).run("Resume exact tool approval.")
    finally:
        resumed_runtime.close()

    assert summary == f"Completed deterministic profile {profile}."
    records = harness.store.list_tool_invocations("run-1")
    matching = [record for record in records if record.tool_name == expected_tool]
    assert len(matching) == 1
    assert matching[0].status == ToolInvocationStatus.SUCCEEDED
    assert matching[0].approval_id == captured.value.approval_id
    if profile == "tool-delete-approval":
        assert not (harness.workspace / "delete_me.txt").exists()
    else:
        assert _git(harness.workspace, "log", "-1", "--format=%s") == (
            "test: deterministic managed commit"
        )


def test_deterministic_local_mcp_profile_uses_policy_and_restart_safe_approval(
    tmp_path: Path,
) -> None:
    harness = _harness(tmp_path, "tool-local-mcp")
    first_runtime = harness.runtime(with_mcp=True)
    try:
        with pytest.raises(ManagedToolApprovalRequired) as captured:
            harness.loop(first_runtime).run("Invoke the configured local MCP tool.")
        first_transport = first_runtime._mcp_sessions["fixture"].client.transport
        assert first_transport.is_running is True
    finally:
        first_runtime.close()
    assert first_transport.is_running is False

    harness.store.decide_tool_approval(
        "run-1",
        captured.value.approval_id,
        disposition=ToolApprovalDisposition.APPROVED,
        reason="Approve the configured offline MCP fixture.",
    )
    resumed_runtime = harness.runtime(with_mcp=True)
    try:
        summary = harness.loop(resumed_runtime).run("Resume the local MCP tool.")
        result = harness.store.list_tool_invocations("run-1")[0].safe_result
        resumed_transport = resumed_runtime._mcp_sessions["fixture"].client.transport
        assert resumed_transport.is_running is True
    finally:
        resumed_runtime.close()

    assert resumed_transport.is_running is False
    assert summary == "Completed deterministic profile tool-local-mcp."
    assert result.status == ToolInvocationStatus.SUCCEEDED
    assert result.structured_output["persisted_summary"] is True
    assert result.safe_diagnostic_metadata["structured_output_key_count"] > 0


def test_deterministic_cancel_profile_terminates_active_process(
    tmp_path: Path,
) -> None:
    harness = _harness(tmp_path, "tool-process-cancel")
    runtime = harness.runtime()
    loop = harness.loop(runtime)
    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(loop.run, "Cancel the active managed process.")
            _wait_for_running_invocation(harness.store)
            assert harness.cancellations.request("run-1", "deterministic cancel")
            with pytest.raises(CancellationRequested):
                future.result(timeout=10)
        snapshot = runtime.dispatcher.budget_ledger.snapshot("run-1")
    finally:
        runtime.close()

    result = harness.store.list_tool_invocations("run-1")[0].safe_result
    assert result.status == ToolInvocationStatus.CANCELLED
    assert result.cancellation.requested is True
    assert result.cancellation.process_terminated is True
    assert result.cancellation.cleanup_completed is True
    assert snapshot.active_processes == 0


def _harness(
    tmp_path: Path,
    profile: str,
    *,
    budget: ToolResourceBudget | None = None,
) -> ProfileHarness:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "README.md").write_text(
        "deterministic profile baseline\n",
        encoding="utf-8",
    )
    (workspace / "module.py").write_text("VALUE = 1\n", encoding="utf-8")
    (workspace / "test_profile.py").write_text(
        "from module import VALUE\n\n\ndef test_value():\n    assert VALUE in {1, 2}\n",
        encoding="utf-8",
    )
    if profile == "tool-control-acceptance":
        (workspace / "test_acceptance_tool.py").write_text(
            "from acceptance_tool import add\n\n\n"
            "def test_add():\n"
            "    assert add(2, 3) == 5\n",
            encoding="utf-8",
        )
    (workspace / "delete_me.txt").write_bytes(DELETE_TARGET.encode("utf-8"))
    _initialize_repository(workspace)

    config = AgentBusConfig(
        provider_name="deterministic",
        deterministic_profile=profile,
        model_max_retries=0,
        workspace_dir=str(workspace),
        runs_dir=str(tmp_path / "runs"),
        state_dir=str(tmp_path / "state"),
        tool_resource_budget=budget or ToolResourceBudget(),
    )
    planner = ModelRouter(config).generate_json(
        ModelRole.PLANNER,
        "Plan the deterministic managed-tool profile.",
        schema=PlannerOutput,
    )
    plan = planner.json_value()
    store = StateStore(tmp_path / "tool-state.db")
    required = plan["steps"][0]["required_capabilities"]
    store.create_run_with_tasks(
        RunRecord(
            run_id="run-1",
            original_task=f"Execute {profile}",
            workflow_type="durable",
            model="deterministic-coder",
            workspace=str(workspace.resolve()),
            planner_output=plan,
            graph_data={"version": 1, "tasks": ["step-1"]},
        ),
        [
            TaskSpec(
                task_id="step-1",
                title=f"Execute {profile}",
                description="Exercise the production managed-tool path.",
                metadata={"required_capabilities": required},
            )
        ],
    )
    store.update_run_status("run-1", RunStatus.RUNNING)
    store.update_task_status("run-1", "step-1", TaskStatus.READY)
    store.update_task_status("run-1", "step-1", TaskStatus.RUNNING)
    return ProfileHarness(
        workspace=workspace,
        config=config,
        store=store,
        cancellations=CancellationRegistry(store),
        plan=plan,
        profile=profile,
    )


def _initialize_repository(workspace: Path) -> None:
    _git(workspace, "init")
    _git(workspace, "config", "user.name", "AgentBus Tests")
    _git(workspace, "config", "user.email", "agentbus@example.invalid")
    _git(workspace, "add", "--all")
    _git(workspace, "commit", "-m", "test: initialize profile fixture")


def _git(workspace: Path, *arguments: str) -> str:
    result = subprocess.run(
        ["git", *arguments],
        cwd=workspace,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        shell=False,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


def _wait_for_running_invocation(store: StateStore) -> None:
    deadline = time.monotonic() + 10
    waiter = threading.Event()
    while time.monotonic() < deadline:
        records = store.list_tool_invocations("run-1")
        if records and records[0].status == ToolInvocationStatus.RUNNING:
            return
        waiter.wait(0.01)
    raise AssertionError("Managed process did not reach running state.")
