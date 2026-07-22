from __future__ import annotations

import threading
import subprocess
from pathlib import Path

import pytest

from agentbus.config import AgentBusConfig
from agentbus.control.errors import (
    ControlPlaneConflictError,
    ControlPlaneUnavailableError,
)
from agentbus.control.models import RunCreateRequest
from agentbus.control.supervisor import AgentBusRunBackend, BackgroundRunSupervisor
from agentbus.execution.cancellation import CancellationState
from agentbus.execution.models import RunStatus, utc_now
from agentbus.tools.protocol import ToolResourceBudget


class BlockingBackend:
    def __init__(self) -> None:
        self.started = threading.Event()
        self.release = threading.Event()
        self.executions: list[str] = []
        self.workspaces: dict[str, str] = {}
        self.cancelled: list[str] = []
        self.cancellation = CancellationState()

    def execute_new(self, request: RunCreateRequest, run_id: str) -> None:
        self.executions.append(run_id)
        self.workspaces[run_id] = request.workspace
        self.started.set()
        self.release.wait(timeout=5)

    def resume(self, run_id: str) -> None:
        self.started.set()
        self.release.wait(timeout=5)

    def cancel(self, run_id: str, reason: str | None = None) -> RunStatus:
        self.cancelled.append(run_id)
        now = utc_now()
        self.cancellation = CancellationState(
            requested=True,
            requested_at=now,
            reason=reason,
            propagated_at=now,
            propagation_sources=["control-supervisor"],
            acknowledged=True,
            acknowledged_at=now,
            acknowledgement_source="blocking-backend",
            acknowledgement_stage="test",
            scheduling_stopped_at=now,
            cleanup_completed_at=now,
            resume_eligible=False,
            terminal_reason="Cancelled safely.",
            revision=1,
        )
        return RunStatus.CANCELLED

    def cancellation_state(self, run_id: str) -> CancellationState:
        return self.cancellation

    def workspace_for(self, run_id: str) -> str:
        return self.workspaces[run_id]


def _request(workspace: Path) -> RunCreateRequest:
    return RunCreateRequest(task="Task", workspace=str(workspace), workflow="single")


def _isolated_repository(path: Path) -> Path:
    path.mkdir(exist_ok=True)
    result = subprocess.run(
        ["git", "init"],
        cwd=path,
        capture_output=True,
        text=True,
        shell=False,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    return path


def test_submit_returns_immediately_and_fences_same_workspace(
    tmp_path: Path,
) -> None:
    backend = BlockingBackend()
    supervisor = BackgroundRunSupervisor(backend, max_background_runs=2)
    workspace = _isolated_repository(tmp_path / "repo")
    assert supervisor.has_active_runs() is False
    accepted = supervisor.submit(_request(workspace))
    assert backend.started.wait(timeout=2)

    with pytest.raises(ControlPlaneConflictError, match="active AgentBus run"):
        supervisor.submit(_request(workspace))

    assert accepted.run_id in backend.executions
    assert supervisor.is_active(accepted.run_id)
    assert supervisor.has_active_runs() is True
    backend.release.set()
    supervisor.shutdown()
    assert supervisor.has_active_runs() is False


def test_completed_run_releases_workspace_for_next_submission(
    tmp_path: Path,
) -> None:
    backend = BlockingBackend()
    backend.release.set()
    supervisor = BackgroundRunSupervisor(backend)
    workspace = _isolated_repository(tmp_path / "repo")
    first = supervisor.submit(_request(workspace))
    while supervisor.is_active(first.run_id):
        threading.Event().wait(0.01)

    second = supervisor.submit(_request(workspace))

    assert first.run_id != second.run_id
    supervisor.shutdown()


def test_resume_has_single_active_owner(tmp_path: Path) -> None:
    backend = BlockingBackend()
    backend.workspaces["run-1"] = str(tmp_path)
    supervisor = BackgroundRunSupervisor(backend)

    response = supervisor.resume("run-1")
    assert backend.started.wait(timeout=2)

    with pytest.raises(ControlPlaneConflictError, match="active owner"):
        supervisor.resume("run-1")

    assert response.resumed is True
    backend.release.set()
    supervisor.shutdown()


def test_cancel_delegates_to_persisted_backend(tmp_path: Path) -> None:
    backend = BlockingBackend()
    backend.workspaces["run-1"] = str(tmp_path)
    supervisor = BackgroundRunSupervisor(backend)

    response = supervisor.cancel("run-1", "User requested cancellation")

    assert response.status == "cancelled"
    assert backend.cancelled == ["run-1"]
    assert response.cancellation.requested is True
    assert response.cancellation.acknowledged is True
    assert response.cancellation.scheduling_stopped is True
    assert response.cancellation.cleanup_completed is True
    assert response.cancellation.resume_eligible is False
    supervisor.shutdown()


def test_shutdown_refuses_new_work(tmp_path: Path) -> None:
    backend = BlockingBackend()
    supervisor = BackgroundRunSupervisor(backend)
    supervisor.shutdown()

    with pytest.raises(ControlPlaneUnavailableError, match="shutting down"):
        supervisor.submit(_request(tmp_path))


def test_run_backend_propagates_the_request_tool_budget(tmp_path: Path) -> None:
    backend = AgentBusRunBackend(
        AgentBusConfig(state_dir=str(tmp_path / "state"))
    )
    budget = ToolResourceBudget(
        invocations_per_task=2,
        invocations_per_run=3,
    )
    request = RunCreateRequest(
        task="Execute a bounded deterministic profile.",
        workspace=str(tmp_path / "workspace"),
        provider="deterministic",
        deterministic={"profile": "tool-budget-exhaustion"},
        tool_budget=budget,
    )

    configured = backend._config_for(request)

    assert configured.tool_resource_budget == budget
    assert backend._persisted_tool_budget(
        {
            "tool_runtime": {
                "resource_budget": budget.model_dump(mode="json"),
            }
        }
    ) == budget
