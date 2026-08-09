from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

from agentbus.agents.planner import PlannerOutput
from agentbus.config import AgentBusConfig
from agentbus.execution.cancellation_registry import CancellationRegistry
from agentbus.execution.models import (
    RunRecord,
    RunStatus,
    TaskSpec,
    TaskStatus,
)
from agentbus.execution.state_store import StateStore
from agentbus.models.router import ModelRouter
from agentbus.models.types import ModelRole
from agentbus.policy import ToolApprovalDisposition
from agentbus.runtime.loop import AgentLoop, ManagedToolApprovalRequired
from agentbus.sandbox.platform import ExecutableCatalog
from agentbus.security.redaction import redact_text
from agentbus.tools.protocol import ToolInvocationStatus, ToolResourceBudget
from agentbus.tools.runtime import build_managed_tool_runtime


_RUN_ID = "beta-managed-tool-run"
_TASK_ID = "step-1"


def run_managed_approval_probe(root: str | Path) -> dict[str, Any]:
    runtime_root = Path(root).expanduser().resolve()
    if runtime_root.exists() and any(runtime_root.iterdir()):
        raise ValueError("Managed-tool probe root must be empty.")
    runtime_root.mkdir(parents=True, exist_ok=True)
    workspace = runtime_root / "workspace"
    workspace.mkdir()
    (workspace / "README.md").write_text(
        "managed approval acceptance\n",
        encoding="utf-8",
        newline="\n",
    )
    target = workspace / "delete_me.txt"
    target.write_text(
        "deterministic deletion target\n",
        encoding="utf-8",
        newline="\n",
    )
    _initialize_repository(workspace)

    config = AgentBusConfig(
        provider_name="deterministic",
        deterministic_profile="tool-delete-approval",
        model_max_retries=0,
        workspace_dir=str(workspace),
        runs_dir=str(runtime_root / "runs"),
        state_dir=str(runtime_root / "state"),
        tool_resource_budget=ToolResourceBudget(),
    )
    planner = ModelRouter(config).generate_json(
        ModelRole.PLANNER,
        "Plan the deterministic managed-tool approval probe.",
        schema=PlannerOutput,
    )
    plan = planner.json_value()
    required = plan["steps"][0]["required_capabilities"]
    store = StateStore(runtime_root / "tool-state.db")
    store.create_run_with_tasks(
        RunRecord(
            run_id=_RUN_ID,
            original_task="Approve one deterministic managed tool.",
            workflow_type="durable",
            model="deterministic-coder",
            workspace=str(workspace),
            planner_output=plan,
            graph_data={"version": 1, "tasks": [_TASK_ID]},
        ),
        [
            TaskSpec(
                task_id=_TASK_ID,
                title="Delete the acceptance target",
                description="Exercise exact managed-tool approval and resume.",
                metadata={"required_capabilities": required},
            )
        ],
    )
    store.update_run_status(_RUN_ID, RunStatus.RUNNING)
    store.update_task_status(_RUN_ID, _TASK_ID, TaskStatus.READY)
    store.update_task_status(_RUN_ID, _TASK_ID, TaskStatus.RUNNING)
    cancellations = CancellationRegistry(store)

    first_runtime = _runtime(workspace, store, cancellations)
    try:
        try:
            _loop(config, plan, store, cancellations, first_runtime).run(
                "Suspend for exact managed-tool approval."
            )
        except ManagedToolApprovalRequired as pending:
            approval_id = pending.approval_id
            tool_name = pending.tool_name
        else:
            raise RuntimeError("Managed tool did not request approval.")
    finally:
        first_runtime.close()

    approval = store.get_tool_approval(_RUN_ID, approval_id)
    if approval.disposition is not None or approval.request.tool_name != tool_name:
        raise RuntimeError("Persisted tool approval request did not match the invocation.")
    store.decide_tool_approval(
        _RUN_ID,
        approval_id,
        disposition=ToolApprovalDisposition.APPROVED,
        reason="Approve the offline beta acceptance probe.",
    )

    resumed_runtime = _runtime(workspace, store, cancellations)
    try:
        summary = _loop(config, plan, store, cancellations, resumed_runtime).run(
            "Resume the exact approved managed tool."
        )
    finally:
        resumed_runtime.close()
    invocations = store.list_tool_invocations(_RUN_ID)
    audits = store.list_tool_audits(_RUN_ID)
    matching = [item for item in invocations if item.approval_id == approval_id]
    succeeded = (
        len(matching) == 1
        and matching[0].status == ToolInvocationStatus.SUCCEEDED
        and not target.exists()
    )
    return {
        "ok": succeeded,
        "summary": summary,
        "tool_name": tool_name,
        "approval_requested": True,
        "approval_approved": True,
        "tool_status": matching[0].status.value if len(matching) == 1 else "missing",
        "target_deleted": not target.exists(),
        "invocation_count": len(invocations),
        "audit_count": len(audits),
        "provider": "deterministic",
        "provider_calls": 0,
        "network_used": False,
    }


def _runtime(workspace: Path, store: StateStore, cancellations: CancellationRegistry):
    return build_managed_tool_runtime(
        workspace=workspace,
        state_store=store,
        cancellation_registry=cancellations,
        executable_catalog=ExecutableCatalog(
            {
                "python": sys.executable,
                "pytest": (sys.executable, "-m", "pytest"),
                "git": "git",
            }
        ),
    )


def _loop(config, plan, store, cancellations, runtime) -> AgentLoop:
    return AgentLoop(
        config=config,
        model=ModelRouter(config).for_role(ModelRole.CODER),
        cancellation=cancellations.get(_RUN_ID),
        tool_runtime=runtime,
        state_store=store,
        cancellation_registry=cancellations,
        run_id=_RUN_ID,
        task_id=_TASK_ID,
        resource_budget=config.tool_resource_budget,
        policy_context={
            "planned_capabilities": plan["steps"][0]["required_capabilities"]
        },
    )


def _initialize_repository(workspace: Path) -> None:
    hooks = workspace.parent / "empty-hooks"
    hooks.mkdir()
    for arguments in (
        ("init", "-q", "--initial-branch=main"),
        ("add", "--all"),
        (
            "-c",
            f"core.hooksPath={hooks}",
            "-c",
            "commit.gpgSign=false",
            "-c",
            "user.name=AgentBus Beta Acceptance",
            "-c",
            "user.email=acceptance@agentbus.invalid",
            "commit",
            "-q",
            "-m",
            "test: initialize managed approval probe",
        ),
    ):
        completed = subprocess.run(
            ["git", *arguments],
            cwd=workspace,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
            shell=False,
            check=False,
        )
        if completed.returncode != 0:
            raise RuntimeError("Managed-tool probe Git initialization failed.")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="agentbus beta managed-tool probe")
    parser.add_argument("--root", required=True)
    args = parser.parse_args(argv)
    try:
        payload = run_managed_approval_probe(args.root)
    except Exception as exc:
        payload = {
            "ok": False,
            "error": redact_text(str(exc), max_chars=500) or type(exc).__name__,
            "provider_calls": 0,
            "network_used": False,
        }
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0 if payload.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
