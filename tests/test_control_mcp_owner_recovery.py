"""Real MCP and durable state around a failed supervisor completion callback."""
from __future__ import annotations

import queue
from pathlib import Path

from agentbus.config import AgentBusConfig
from agentbus.control.acceptance import _git, _initialize_repository
from agentbus.control.models import ApprovalDecisionRequest, RunCreateRequest
from agentbus.control.services import ControlQueryService
from agentbus.control.supervisor import AgentBusRunBackend, BackgroundRunSupervisor
from agentbus.execution.models import ApprovalOutcome, RunStatus
from agentbus.mcp import McpServerConfig, mcp_server_capabilities
from agentbus.tools.protocol import ToolInvocationStatus


def test_real_mcp_approval_can_resume_after_completion_report_failure(tmp_path: Path) -> None:
    workspace = _initialize_repository(tmp_path / "repo", mixed_language=False)
    (workspace / "acceptance_tool.py").write_text(
        "def add(left, right):\n    return left + right\n", encoding="utf-8"
    )
    _git(workspace, "add", "acceptance_tool.py")
    _git(workspace, "commit", "-m", "Complete verification fixture")
    lifecycle = tmp_path / "peer-lifecycle"
    lifecycle.mkdir()
    fixture = Path(__file__).parent / "fixtures" / "mcp" / "fake_server.py"
    config = AgentBusConfig(
        workspace_dir=str(workspace), state_dir=str(tmp_path / "state"),
        provider_name="deterministic", enable_provider_fallback=False,
        mcp_server_configs=(McpServerConfig(
            server_id="fixture", transport="stdio", executable_alias="python",
            arguments=("-u", str(fixture.resolve()), "--lifecycle-dir", str(lifecycle)),
            capability_map={"echo": mcp_server_capabilities("fixture"),
                            "write_note": mcp_server_capabilities("fixture")},
        ),),
    )

    class Backend(AgentBusRunBackend):
        fail_once = True

        def finalize_cancellation(self, run_id: str) -> RunStatus:
            if self.fail_once:
                self.fail_once = False
                raise OSError("controlled completion report failure")
            return super().finalize_cancellation(run_id)

    completed: queue.Queue[Exception | None] = queue.Queue()

    class ObservedSupervisor(BackgroundRunSupervisor):
        def _completed(self, active) -> None:
            error = None
            try:
                super()._completed(active)
            except OSError as exc:
                error = exc
            finally:
                completed.put(error)

    backend = Backend(config)
    supervisor = ObservedSupervisor(backend)
    query = ControlQueryService(config, backend.store)
    try:
        accepted = supervisor.submit(RunCreateRequest(
            task="Invoke the configured local MCP echo tool through normal policy.",
            workspace=str(workspace), workflow="multi", durable=True,
            provider="deterministic", deterministic={"profile": "tool-local-mcp"},
            commit_changes=False,
        ))
        assert isinstance(completed.get(timeout=30), OSError)
        run_id = accepted.run_id
        assert backend.store.get_run(run_id).status == RunStatus.WAITING_FOR_APPROVAL
        assert not supervisor.is_active(run_id)
        approval, = query.approvals(run_id).approvals
        query.decide_approval(run_id, approval.approval_id,
                             ApprovalDecisionRequest(revision=approval.revision), ApprovalOutcome.APPROVED)
        assert supervisor.resume(run_id).resumed
        assert completed.get(timeout=30) is None
        assert backend.store.get_run(run_id).status == RunStatus.SUCCEEDED
        records = backend.store.list_tool_invocations(run_id)
        assert len([r for r in records if r.tool_name == "mcp.fixture.echo"
                    and r.status == ToolInvocationStatus.SUCCEEDED]) == 1
        attempts_before = backend.store.list_attempts(run_id)
        assert supervisor.resume(run_id).resumed
        assert completed.get(timeout=30) is None
        assert backend.store.list_attempts(run_id) == attempts_before
        assert backend.store.list_tool_invocations(run_id) == records
        assert not supervisor.has_active_runs()
        assert not [r for r in backend.store.list_worker_lease_rows(run_id) if r["status"] == "active"]
        started = {p.stem for p in lifecycle.glob("*.started")}
        stopped = {p.stem for p in lifecycle.glob("*.stopped")}
        assert started and started == stopped
    finally:
        supervisor.shutdown()
