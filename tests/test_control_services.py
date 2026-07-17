from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from agentbus.config import AgentBusConfig
from agentbus.control.errors import ControlPlaneForbiddenError
from agentbus.control.models import WorkspaceValidationRequest
from agentbus.control.services import ControlQueryService, WorkspaceService
from agentbus.execution.models import RunRecord
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
