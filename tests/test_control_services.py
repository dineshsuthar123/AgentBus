from __future__ import annotations

import subprocess
from datetime import timedelta
from pathlib import Path

import pytest

from agentbus.config import AgentBusConfig
from agentbus.control.errors import ControlPlaneForbiddenError
from agentbus.control.models import WorkspaceValidationRequest
from agentbus.control.services import ControlQueryService, WorkspaceService
from agentbus.execution.cancellation import (
    CancellationOperation,
    CancellationState,
)
from agentbus.execution.models import RunRecord, utc_now
from agentbus.execution.state_store import StateStore
from agentbus.git.repository import WorkspaceRepositoryMismatch


def _git(workspace: Path, *arguments: str) -> None:
    result = subprocess.run(
        ["git", *arguments],
        cwd=workspace,
        capture_output=True,
        text=True,
        shell=False,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def _git_output(workspace: Path, *arguments: str) -> str:
    result = subprocess.run(
        ["git", *arguments],
        cwd=workspace,
        capture_output=True,
        text=True,
        shell=False,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


def _repository(path: Path) -> Path:
    path.mkdir()
    _git(path, "init")
    _git(path, "config", "user.email", "agentbus@example.invalid")
    _git(path, "config", "user.name", "AgentBus Tests")
    (path / "tracked.txt").write_text("before\n", encoding="utf-8")
    _git(path, "add", "tracked.txt")
    _git(path, "commit", "-m", "initial")
    return path.resolve()


def _service(tmp_path: Path, workspace: Path) -> tuple[ControlQueryService, RunRecord]:
    store = StateStore(tmp_path / "state.db")
    run = RunRecord(
        run_id="run-1",
        original_task="Change tracked file",
        model="fake",
        workspace=str(workspace),
    )
    store.create_run(run)
    config = AgentBusConfig(
        workspace_dir=str(workspace),
        state_db=str(tmp_path / "state.db"),
    )
    return ControlQueryService(config, store), run


def test_isolated_workspace_repository_is_accepted(tmp_path: Path) -> None:
    workspace = _repository(tmp_path / "repo")

    result = WorkspaceService().validate(
        WorkspaceValidationRequest(workspace=str(workspace), require_git=True)
    )

    assert result.valid is True
    assert result.git_top_level == str(workspace)


def test_nested_directory_in_parent_repository_is_rejected(tmp_path: Path) -> None:
    parent = _repository(tmp_path / "parent")
    nested = parent / "nested"
    nested.mkdir()

    with pytest.raises(WorkspaceRepositoryMismatch):
        WorkspaceService().validate(
            WorkspaceValidationRequest(workspace=str(nested), require_git=True)
        )


def test_changes_and_diff_are_scoped_to_selected_repository(tmp_path: Path) -> None:
    workspace = _repository(tmp_path / "repo")
    unrelated = tmp_path / "unrelated.txt"
    unrelated.write_text("outside\n", encoding="utf-8")
    (workspace / "tracked.txt").write_text("after\n", encoding="utf-8")
    (workspace / "new.txt").write_text("new\n", encoding="utf-8")
    service, run = _service(tmp_path, workspace)

    changes = service.repository.list_changes(run)
    diff = service.repository.diff(run)

    assert {change.path for change in changes.changes} == {
        "new.txt",
        "tracked.txt",
    }
    assert str(unrelated) not in diff.diff
    assert "after" in diff.diff


@pytest.mark.parametrize("path", ["../outside.txt", "/absolute.txt", "C:/outside.txt"])
def test_file_api_rejects_traversal_and_absolute_paths(
    tmp_path: Path,
    path: str,
) -> None:
    workspace = _repository(tmp_path / "repo")
    service, run = _service(tmp_path, workspace)

    with pytest.raises(ControlPlaneForbiddenError):
        service.repository.file_content(run, path, revision="after")


@pytest.mark.parametrize("name", [".env", "private.pem", ".agentbus/state.db"])
def test_file_api_rejects_secret_and_control_metadata(
    tmp_path: Path,
    name: str,
) -> None:
    workspace = _repository(tmp_path / "repo")
    path = workspace / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("secret\n", encoding="utf-8")
    service, run = _service(tmp_path, workspace)

    with pytest.raises(ControlPlaneForbiddenError, match="not available"):
        service.repository.file_content(run, name, revision="after")


def test_file_api_rejects_binary_content(tmp_path: Path) -> None:
    workspace = _repository(tmp_path / "repo")
    (workspace / "binary.dat").write_bytes(b"prefix\x00suffix")
    service, run = _service(tmp_path, workspace)

    with pytest.raises(ControlPlaneForbiddenError, match="Binary"):
        service.repository.file_content(run, "binary.dat", revision="after")


def test_before_and_after_content_are_repository_contained(tmp_path: Path) -> None:
    workspace = _repository(tmp_path / "repo")
    (workspace / "tracked.txt").write_text("after\n", encoding="utf-8")
    service, run = _service(tmp_path, workspace)

    before = service.repository.file_content(run, "tracked.txt", revision="before")
    after = service.repository.file_content(run, "tracked.txt", revision="after")

    assert before.content.splitlines() == ["before"]
    assert after.content.splitlines() == ["after"]


def test_query_responses_expose_persisted_cancellation_lifecycle(
    tmp_path: Path,
) -> None:
    workspace = _repository(tmp_path / "repo")
    service, run = _service(tmp_path, workspace)
    requested_at = utc_now()
    state = CancellationState(
        requested=True,
        requested_at=requested_at,
        reason="Stop safely",
        propagated_at=requested_at,
        propagation_sources=["control", "scheduler"],
        provider_cancellation_requested_at=requested_at,
        provider_names=["deterministic"],
        acknowledged=True,
        acknowledged_at=requested_at + timedelta(milliseconds=1),
        acknowledgement_source="deterministic-provider",
        acknowledgement_stage="provider-wait",
        provider_cancellation_acknowledged_at=(
            requested_at + timedelta(milliseconds=1)
        ),
        provider_acknowledgement_source="deterministic-provider",
        active_operations=[
            CancellationOperation(
                operation_id="operation-1",
                name="verification-command",
                source="verifier",
                interruptible=False,
                task_id="step-1",
                started_at=requested_at,
            )
        ],
        operations_completed_after_request=["task-commit"],
        tasks_prevented_from_starting=["step-2"],
        tasks_completed_after_request=["step-1"],
        scheduling_stopped_at=requested_at + timedelta(milliseconds=2),
        cleanup_completed_at=requested_at + timedelta(milliseconds=3),
        resume_eligible=False,
        terminal_reason="Cancelled safely.",
        revision=1,
    )
    service.store.persist_cancellation_state(run.run_id, state)

    summary = service.run_summary(service.get_run(run.run_id))
    report = service.report(run.run_id)
    scheduler = service.scheduler(run.run_id)

    assert summary.cancellation.requested is True
    assert summary.cancellation.provider_cancellation_signalled is True
    assert summary.cancellation.provider_cancellation_acknowledged is True
    assert (
        summary.cancellation.active_non_interruptible_operation
        == "verification-command"
    )
    assert summary.cancellation.completed_after_cancellation_request is True
    assert summary.cancellation.tasks_prevented_from_starting == ["step-2"]
    assert summary.cancellation.resume_eligible is False
    assert report.cancellation == summary.cancellation
    assert scheduler.cancellation == summary.cancellation


def test_review_api_reads_parallel_integration_commit_without_checkout(
    tmp_path: Path,
) -> None:
    workspace = _repository(tmp_path / "repo")
    base_commit = _git_output(workspace, "rev-parse", "HEAD")
    original_branch = _git_output(workspace, "branch", "--show-current")
    _git(workspace, "switch", "-c", "agentbus/integration-test")
    (workspace / "tracked.txt").write_text("integrated\n", encoding="utf-8")
    (workspace / "created.py").write_text("VALUE = 42\n", encoding="utf-8")
    _git(workspace, "add", "tracked.txt", "created.py")
    _git(workspace, "commit", "-m", "integrated result")
    integration_commit = _git_output(workspace, "rev-parse", "HEAD")
    _git(workspace, "switch", original_branch)
    service, run = _service(tmp_path, workspace)
    service.store.update_run_details(
        run.run_id,
        metadata_updates={
            "parallel_execution": {
                "enabled": True,
                "base_commit": base_commit,
                "integration_commit": integration_commit,
            }
        },
    )
    persisted = service.get_run(run.run_id)

    changes = service.repository.list_changes(persisted)
    diff = service.repository.diff(persisted)
    before = service.repository.file_content(
        persisted,
        "tracked.txt",
        revision="before",
    )
    after = service.repository.file_content(
        persisted,
        "created.py",
        revision="after",
    )

    assert not (workspace / "created.py").exists()
    assert {item.path for item in changes.changes} == {
        "created.py",
        "tracked.txt",
    }
    assert all(item.status == "committed" for item in changes.changes)
    assert "VALUE = 42" in diff.diff
    assert before.content == "before\n"
    assert after.content == "VALUE = 42\n"
