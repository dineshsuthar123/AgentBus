from __future__ import annotations

import hashlib
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from fastapi.testclient import TestClient

from agentbus.config import AgentBusConfig
from agentbus.control.app import ControlAppContext, create_app
from agentbus.control.models import (
    CancellationLifecycle,
    CancelResponse,
    ResumeResponse,
    RunAcceptedResponse,
)
from agentbus.control.services import ControlQueryService
from agentbus.execution.models import RunRecord, TaskSpec
from agentbus.execution.state_store import StateStore
from agentbus.tools.protocol import ToolCapabilityName, ToolApprovalRequest
from agentbus.tools.runtime import build_managed_tool_runtime


TOKEN = "multi-root-control-token-with-thirty-two-bytes"


class _Supervisor:
    def submit(self, request):
        return RunAcceptedResponse(
            run_id="unused",
            status="pending",
            workspace=request.workspace,
            created_at=datetime.now(timezone.utc),
        )

    def resume(self, run_id):
        return ResumeResponse(run_id=run_id, status="running", resumed=True)

    def cancel(self, run_id, reason=None):
        return CancelResponse(
            run_id=run_id,
            status="cancelled",
            cancellation_requested=True,
            cancellation=CancellationLifecycle(requested=True, reason=reason),
        )

    def shutdown(self, *, wait=True):
        return None


def _git(repository: Path, *arguments: str) -> str:
    result = subprocess.run(
        ["git", *arguments],
        cwd=repository,
        check=True,
        capture_output=True,
        text=True,
        shell=False,
    )
    return result.stdout


def _repository(path: Path, label: str, source_path: str) -> Path:
    path.mkdir(parents=True)
    _git(path, "init", "-q")
    _git(path, "config", "user.name", "AgentBus Multi-Root Test")
    _git(path, "config", "user.email", "multi-root@agentbus.invalid")
    _git(path, "config", "core.autocrlf", "false")
    (path / "pyproject.toml").write_text(
        f'[project]\nname = "{label}"\nversion = "0.1.0"\n',
        encoding="utf-8",
    )
    source = path / source_path
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_text(
        "def shared_identity() -> str:\n"
        f'    return "{label}"\n\n'
        f"def {label.replace('-', '_')}_only() -> str:\n"
        f'    return "{label}"\n',
        encoding="utf-8",
    )
    (path / "delete_me.txt").write_text(
        f"delete only inside {label}\n",
        encoding="utf-8",
    )
    _git(path, "add", "--all")
    _git(path, "commit", "-q", "-m", "initial")
    return path.resolve()


def _client(tmp_path: Path, workspace: Path) -> tuple[TestClient, StateStore]:
    state_path = tmp_path / "state" / "state.db"
    store = StateStore(state_path)
    config = AgentBusConfig(
        workspace_dir=str(workspace),
        state_db=str(state_path),
        provider_name="deterministic",
    )
    app = create_app(
        token=TOKEN,
        query_service=ControlQueryService(config, store),
        supervisor=_Supervisor(),
        context=ControlAppContext(
            daemon_id="multi-root-isolation",
            host="127.0.0.1",
            port=0,
            started_at=datetime.now(timezone.utc),
            state_database=str(state_path),
        ),
        shutdown_supervisor=False,
    )
    return TestClient(app, raise_server_exceptions=False), store


def _auth() -> dict[str, str]:
    return {"Authorization": f"Bearer {TOKEN}"}


def _build(client: TestClient, workspace: Path) -> dict:
    response = client.post(
        "/api/v1/workspaces/index",
        headers=_auth(),
        json={"workspace": str(workspace), "workspace_trusted": True},
    )
    assert response.status_code == 201, response.text
    return response.json()


def _symbols(client: TestClient, workspace_id: str, query: str) -> list[dict]:
    response = client.post(
        f"/api/v1/workspaces/{workspace_id}/search",
        headers=_auth(),
        json={"query": query, "limit": 50, "include_evidence": True},
    )
    assert response.status_code == 200, response.text
    return [
        item["symbol"]
        for item in response.json()["report"]["results"]
        if item["symbol"] is not None
    ]


def _create_run(
    store: StateStore,
    *,
    run_id: str,
    workspace: Path,
    changed_file: str,
) -> None:
    store.create_run_with_tasks(
        RunRecord(
            run_id=run_id,
            original_task="Exercise one isolated repository root.",
            workflow_type="multi",
            model="deterministic-v1",
            workspace=str(workspace),
            changed_files=[changed_file],
        ),
        [
            TaskSpec(
                task_id="task-1",
                title="Exercise isolated tools",
                description="Request exact deletion approval without executing it.",
            )
        ],
    )


def _pending_delete(
    store: StateStore,
    *,
    run_id: str,
    workspace: Path,
) -> ToolApprovalRequest:
    target = workspace / "delete_me.txt"
    runtime = build_managed_tool_runtime(workspace=workspace, state_store=store)
    try:
        call = runtime.prepare_model_call(
            tool_name="filesystem.delete",
            arguments={
                "path": "delete_me.txt",
                "expected_sha256": hashlib.sha256(target.read_bytes()).hexdigest(),
            },
            expected_capabilities=(ToolCapabilityName.FILESYSTEM_DELETE,),
            run_id=run_id,
            task_id="task-1",
            caller_role="coder",
            workspace_trusted=True,
            provider_consented=True,
            idempotency_key="same-relative-delete-across-roots",
        )
        pending = runtime.invoke(
            call,
            run_id=run_id,
            task_id="task-1",
            caller_role="coder",
            workspace_trusted=True,
            provider_consented=True,
            invocation_id=f"{run_id}-delete",
        )
    finally:
        runtime.close()
    assert pending.awaiting_approval is True
    assert pending.approval_request is not None
    return pending.approval_request


def test_multi_root_indexes_runs_approvals_and_repository_views_are_isolated(
    tmp_path: Path,
) -> None:
    monorepo = _repository(
        tmp_path / "monorepo",
        "monorepo-root",
        "services/api/shared.py",
    )
    nested = _repository(
        monorepo / "plugins" / "nested",
        "nested-root",
        "src/shared.py",
    )
    independent = _repository(
        tmp_path / "independent",
        "independent-root",
        "src/shared.py",
    )
    invalid = tmp_path / "invalid-root"
    invalid.mkdir()
    (invalid / "shared.py").write_text(
        "def shared_identity(): return 'invalid'\n",
        encoding="utf-8",
    )
    client, store = _client(tmp_path, monorepo)

    built = {
        "monorepo": _build(client, monorepo),
        "nested": _build(client, nested),
        "independent": _build(client, independent),
    }
    workspace_ids = {item["workspace_id"] for item in built.values()}
    repository_ids = {item["repository_id"] for item in built.values()}
    assert len(workspace_ids) == 3
    assert len(repository_ids) == 3
    for name, workspace in (
        ("monorepo", monorepo),
        ("nested", nested),
        ("independent", independent),
    ):
        attached = client.post(
            "/api/v1/workspaces/index/attach",
            headers=_auth(),
            json={"workspace": str(workspace)},
        )
        assert attached.status_code == 200
        assert attached.json()["workspace_id"] == built[name]["workspace_id"]

    parent_symbols = _symbols(
        client,
        built["monorepo"]["workspace_id"],
        "shared_identity",
    )
    nested_symbols = _symbols(
        client,
        built["nested"]["workspace_id"],
        "shared_identity",
    )
    independent_symbols = _symbols(
        client,
        built["independent"]["workspace_id"],
        "shared_identity",
    )
    shared = [
        next(symbol for symbol in symbols if symbol["name"] == "shared_identity")
        for symbols in (parent_symbols, nested_symbols, independent_symbols)
    ]
    assert [symbol["relative_path"] for symbol in shared] == [
        "services/api/shared.py",
        "src/shared.py",
        "src/shared.py",
    ]
    assert len({symbol["symbol_id"] for symbol in shared}) == 3
    assert not any(
        symbol["name"] == "nested_root_only"
        for symbol in _symbols(
            client,
            built["monorepo"]["workspace_id"],
            "nested_root_only",
        )
    )
    parent_diagnostics = built["monorepo"]["result"]["snapshot"]["diagnostics"]
    boundary = next(
        item
        for item in parent_diagnostics
        if item["code"] == "discovery.nested_repository_boundary"
    )
    assert boundary["relative_path"] == "plugins/nested"

    for rejected_root in (invalid, monorepo / "services"):
        rejected = client.post(
            "/api/v1/workspaces/index",
            headers=_auth(),
            json={"workspace": str(rejected_root), "workspace_trusted": True},
        )
        assert rejected.status_code == 403
        assert str(rejected_root) not in rejected.text

    run_roots = {
        "run-nested": (nested, "nested_change.py"),
        "run-independent": (independent, "independent_change.py"),
    }
    for run_id, (workspace, changed_file) in run_roots.items():
        _create_run(
            store,
            run_id=run_id,
            workspace=workspace,
            changed_file=changed_file,
        )
        (workspace / changed_file).write_text(
            f'RUN_ROOT = "{run_id}"\n',
            encoding="utf-8",
        )

    nested_approval = _pending_delete(
        store,
        run_id="run-nested",
        workspace=nested,
    )
    independent_approval = _pending_delete(
        store,
        run_id="run-independent",
        workspace=independent,
    )
    assert nested_approval.approval_id != independent_approval.approval_id
    assert nested_approval.workspace_identity != independent_approval.workspace_identity
    assert nested_approval.affected_paths == independent_approval.affected_paths

    approved = client.post(
        (
            "/api/v1/runs/run-nested/approvals/"
            f"{nested_approval.approval_id}/approve"
        ),
        headers=_auth(),
        json={"revision": 1, "reason": "Approve only the nested repository."},
    )
    cross_root = client.post(
        (
            "/api/v1/runs/run-independent/approvals/"
            f"{nested_approval.approval_id}/approve"
        ),
        headers=_auth(),
        json={"revision": 1},
    )
    independent_approvals = client.get(
        "/api/v1/runs/run-independent/approvals",
        headers=_auth(),
    )
    assert approved.status_code == 200
    assert cross_root.status_code == 404
    assert [
        (item["approval_id"], item["state"])
        for item in independent_approvals.json()["approvals"]
    ] == [(independent_approval.approval_id, "pending")]

    for run_id, (workspace, changed_file) in run_roots.items():
        other_file = next(
            candidate
            for candidate in ("nested_change.py", "independent_change.py")
            if candidate != changed_file
        )
        changes = client.get(
            f"/api/v1/runs/{run_id}/changes",
            headers=_auth(),
        )
        diff = client.get(
            f"/api/v1/runs/{run_id}/diff",
            headers=_auth(),
        )
        report = client.get(
            f"/api/v1/runs/{run_id}/report",
            headers=_auth(),
        )
        assert changes.status_code == 200, changes.text
        assert changes.json()["workspace"] == str(workspace)
        assert [item["path"] for item in changes.json()["changes"]] == [changed_file]
        assert diff.status_code == 200, diff.text
        assert changed_file in diff.json()["diff"]
        assert other_file not in diff.json()["diff"]
        assert report.status_code == 200, report.text
        assert report.json()["report"]["changed_files"] == [changed_file]
        assert report.json()["report"]["workspace"] == str(workspace)
