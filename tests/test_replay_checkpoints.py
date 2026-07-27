import subprocess
from pathlib import Path

import pytest

from agentbus.execution.models import RunRecord
from agentbus.execution.state_store import StateStore
from agentbus.replay import (
    CheckpointKind,
    CheckpointManager,
    ReplayIncompatibleError,
    ReplayIsolationError,
    ReplayIsolationManager,
)
from agentbus.trace import ContentAddressedStore, TraceRecorder


def _run(run_id="run-1", workspace="workspace") -> RunRecord:
    return RunRecord(
        run_id=run_id,
        original_task="Replay safely",
        model="deterministic",
        workspace=str(workspace),
    )


def _git(cwd: Path, *arguments: str) -> str:
    result = subprocess.run(
        ["git", *arguments],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
        shell=False,
    )
    return result.stdout.strip()


def _repository(path: Path) -> str:
    path.mkdir()
    _git(path, "init", "-q")
    _git(path, "config", "user.email", "replay@example.invalid")
    _git(path, "config", "user.name", "Replay Test")
    (path / "app.py").write_text("VALUE = 1\n", encoding="utf-8")
    _git(path, "add", "app.py")
    _git(path, "commit", "-q", "-m", "initial")
    return _git(path, "rev-parse", "HEAD")


def test_checkpoint_state_validates_ancestry_and_dependencies(
    tmp_path: Path,
) -> None:
    objects = ContentAddressedStore(tmp_path / "objects")
    manager = CheckpointManager(objects)
    recorder = TraceRecorder("run-1")
    recorder.start_trace()
    first = manager.capture(
        recorder,
        kind=CheckpointKind.GRAPH_PERSISTED,
        label="graph",
        completed_task_ids=[],
    )
    second = manager.capture(
        recorder,
        kind=CheckpointKind.TASK_COMPLETED,
        label="task complete",
        parent_checkpoint_id=first.checkpoint_id,
        completed_task_ids=["step-1"],
        required_task_ids=["step-1"],
    )
    trace = recorder.snapshot()

    ancestry = manager.validate_ancestry(trace, second.checkpoint_id)

    assert [item.checkpoint_id for item in ancestry] == [
        first.checkpoint_id,
        second.checkpoint_id,
    ]


def test_checkpoint_rejects_missing_dependency_and_ancestor(tmp_path: Path) -> None:
    objects = ContentAddressedStore(tmp_path / "objects")
    manager = CheckpointManager(objects)
    recorder = TraceRecorder("run-1")
    recorder.start_trace()
    missing_dependency = manager.capture(
        recorder,
        kind=CheckpointKind.TASK_COMPLETED,
        label="invalid",
        completed_task_ids=[],
        required_task_ids=["step-1"],
    )

    with pytest.raises(ReplayIncompatibleError, match="dependencies"):
        manager.load_state(missing_dependency)

    valid = manager.capture(
        recorder,
        kind=CheckpointKind.TASK_COMPLETED,
        label="missing parent",
        parent_checkpoint_id="not-captured",
    )
    trace = recorder.snapshot()
    with pytest.raises(ReplayIncompatibleError, match="ancestor"):
        manager.validate_ancestry(trace, valid.checkpoint_id)


def test_replay_database_and_worktree_are_isolated(tmp_path: Path) -> None:
    repository = tmp_path / "source"
    base_commit = _repository(repository)
    source_store = StateStore(tmp_path / "source-state.db")
    source_store.create_run(_run(workspace=repository))
    isolation = ReplayIsolationManager(
        tmp_path / "replays",
        source_store,
        repository_root=repository,
    )

    report = isolation.reconstruct(
        "replay-1",
        run_id="run-1",
        base_commit=base_commit,
    )
    replay_database = isolation.actual_database_path("replay-1")
    replay_worktree = isolation.actual_worktree_path("replay-1")

    assert report.root == "[ISOLATED_REPLAY_ROOT]"
    assert replay_database != source_store.database_path
    assert StateStore(replay_database).get_run("run-1").run_id == "run-1"
    assert replay_worktree is not None
    assert repository not in replay_worktree.parents
    (replay_worktree / "app.py").write_text("VALUE = 2\n", encoding="utf-8")
    assert (repository / "app.py").read_text(encoding="utf-8") == "VALUE = 1\n"


def test_replay_root_cannot_overlap_source_repository(tmp_path: Path) -> None:
    repository = tmp_path / "source"
    _repository(repository)
    store = StateStore(tmp_path / "state.db")
    store.create_run(_run(workspace=repository))

    with pytest.raises(ReplayIsolationError, match="outside"):
        ReplayIsolationManager(
            repository / ".agentbus-replays",
            store,
            repository_root=repository,
        )
