from __future__ import annotations

import os
import sqlite3
import subprocess
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier
from typing import Any

import pytest

from agentbus.execution.engine import DurableExecutionEngine
from agentbus.execution.leases import LeaseService
from agentbus.execution.models import (
    FailureCategory,
    RunStatus,
    TaskExecutionResult,
    TaskStatus,
)
from agentbus.execution.state_store import StateStore
from agentbus.execution.worker import LocalTaskWorker, WorkerStatus
from agentbus.worktrees.errors import (
    WorktreeAlreadyExistsError,
    WorktreeDirtyError,
    WorktreeError,
    WorktreeOwnershipError,
)
from agentbus.worktrees.manager import GitWorktreeManager
from agentbus.worktrees.models import WorktreeRecord, WorktreeStatus


def test_repeated_and_concurrent_worktree_lifecycle_preserves_unrelated_state(
    tmp_path: Path,
) -> None:
    source, base = _repository(tmp_path / "repo")
    task_ids = ["repeat", *(f"parallel-{index}" for index in range(6))]
    store = _state(tmp_path / "state.db", source, task_ids)
    worktree_root = tmp_path / "worktrees"
    manager = GitWorktreeManager(source, worktree_root, store)
    unrelated = worktree_root / "human-worktree"
    _git(source, "worktree", "add", "-b", "human/unrelated", str(unrelated), base)
    (unrelated / "human-draft.txt").write_text("preserve me\n", encoding="utf-8")

    repeated: list[WorktreeRecord] = []
    for index in range(5):
        record = manager.create_task_worktree(
            "run-1",
            "repeat",
            base,
            f"repeat-worker-{index}",
        )
        repeated.append(record)
        manager.mark_cleanup_pending(record.worktree_id)
        assert manager.remove(record.worktree_id).status == WorktreeStatus.REMOVED

    parallel = _run_concurrently(
        [
            lambda task_id=task_id: manager.create_task_worktree(
                "run-1",
                task_id,
                base,
                f"worker-{task_id}",
            )
            for task_id in task_ids
            if task_id != "repeat"
        ]
    )

    assert len({record.worktree_id for record in repeated + parallel}) == 11
    assert all(manager.validate(record).is_dir() for record in parallel)
    assert all(record.base_commit == base for record in parallel)
    for record in parallel:
        manager.mark_cleanup_pending(record.worktree_id)
    removed = _run_concurrently(
        [
            lambda record=record: manager.remove(record.worktree_id)
            for record in parallel
        ]
    )

    assert all(record.status == WorktreeStatus.REMOVED for record in removed)
    assert unrelated.is_dir()
    assert (unrelated / "human-draft.txt").read_text(encoding="utf-8") == (
        "preserve me\n"
    )
    assert _git(unrelated, "status", "--porcelain") == "?? human-draft.txt"
    _assert_repository_ok(source, base)


def test_concurrent_creators_cannot_claim_the_same_task_twice(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source, base = _repository(tmp_path / "repo")
    store = _state(tmp_path / "state.db", source, ["same-task"])
    root = tmp_path / "worktrees"
    first = GitWorktreeManager(source, root, store)
    second = GitWorktreeManager(source, root, store)
    original_list = store.list_worktrees
    preflight = Barrier(2)

    def synchronized_list(
        run_id: str | None = None,
        *,
        task_id: str | None = None,
    ):
        records = original_list(run_id, task_id=task_id)
        if run_id == "run-1" and task_id == "same-task":
            preflight.wait(timeout=15)
        return records

    monkeypatch.setattr(store, "list_worktrees", synchronized_list)

    def create(manager: GitWorktreeManager, worker_id: str):
        try:
            return manager.create_task_worktree(
                "run-1",
                "same-task",
                base,
                worker_id,
            )
        except WorktreeAlreadyExistsError as exc:
            return exc

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = [
            future.result(timeout=30)
            for future in (
                executor.submit(create, first, "worker-1"),
                executor.submit(create, second, "worker-2"),
            )
        ]

    records = [item for item in outcomes if isinstance(item, WorktreeRecord)]
    conflicts = [
        item for item in outcomes if isinstance(item, WorktreeAlreadyExistsError)
    ]
    assert len(records) == 1
    assert len(conflicts) == 1
    assert len(original_list("run-1", task_id="same-task")) == 1
    assert first.validate(records[0]).is_dir()
    _assert_repository_ok(source, base)


def test_legacy_duplicate_ownership_metadata_is_refused_without_cleanup(
    tmp_path: Path,
) -> None:
    source, base = _repository(tmp_path / "repo")
    store = _state(tmp_path / "state.db", source, ["duplicate-task"])
    manager = GitWorktreeManager(source, tmp_path / "worktrees", store)
    original = manager.create_task_worktree(
        "run-1",
        "duplicate-task",
        base,
        "worker-1",
    )
    with sqlite3.connect(store.database_path) as connection:
        connection.execute(
            """INSERT INTO worktrees(
                worktree_id, run_id, task_id, path, repository_root, base_commit,
                branch_ref, purpose, status, worker_id, result_commit, created_at,
                updated_at, metadata_json
            )
            SELECT ?, run_id, task_id, path || ?, repository_root, base_commit,
                branch_ref || ?, purpose, status, worker_id, result_commit,
                created_at, updated_at, metadata_json
            FROM worktrees WHERE worktree_id = ?""",
            ("legacy-duplicate", "-duplicate", "-duplicate", original.worktree_id),
        )

    with pytest.raises(WorktreeOwnershipError, match="Multiple non-removed"):
        manager.create_task_worktree(
            "run-1",
            "duplicate-task",
            base,
            "worker-2",
        )

    assert len(store.list_worktrees("run-1", task_id="duplicate-task")) == 2
    assert manager.validate(original).is_dir()
    _assert_repository_ok(source, base)


def test_failure_and_cancellation_preserve_dirty_task_worktrees(
    tmp_path: Path,
) -> None:
    source, base = _repository(tmp_path / "repo")
    store = _state(
        tmp_path / "state.db",
        source,
        ["failed-task", "cancelled-task"],
    )
    store.update_run_status("run-1", RunStatus.RUNNING)
    manager = GitWorktreeManager(source, tmp_path / "worktrees", store)
    leases = LeaseService(store)

    failed = _execute_worker(
        store,
        manager,
        leases,
        base,
        task_id="failed-task",
        worker_id="failure-worker",
        filename="failed-side-effect.txt",
        result=TaskExecutionResult(
            succeeded=False,
            summary="controlled task failure",
            failure_category=FailureCategory.VERIFIER_FAILURE,
            error_message="controlled failure",
            retryable=False,
        ),
    )
    cancelled = _execute_worker(
        store,
        manager,
        leases,
        base,
        task_id="cancelled-task",
        worker_id="cancellation-worker",
        filename="cancelled-side-effect.txt",
        result=TaskExecutionResult(
            succeeded=False,
            summary="controlled cancellation",
            failure_category=FailureCategory.CANCELLED,
            error_message="controlled cancellation",
            retryable=False,
        ),
    )

    failed_worktree = store.list_worktrees(
        "run-1", task_id="failed-task"
    )[-1]
    cancelled_worktree = store.list_worktrees(
        "run-1", task_id="cancelled-task"
    )[-1]
    assert failed.status == WorkerStatus.FAILED
    assert cancelled.status == WorkerStatus.CANCELLED
    assert failed_worktree.status == WorktreeStatus.ACTIVE
    assert cancelled_worktree.status == WorktreeStatus.CLEANUP_PENDING
    assert (Path(failed_worktree.path) / "failed-side-effect.txt").is_file()
    assert (Path(cancelled_worktree.path) / "cancelled-side-effect.txt").is_file()
    assert store.get_task("run-1", "failed-task").status == TaskStatus.FAILED
    assert store.get_task("run-1", "cancelled-task").status == TaskStatus.CANCELLED
    with pytest.raises(WorktreeDirtyError):
        manager.remove(cancelled_worktree.worktree_id)
    assert Path(cancelled_worktree.path).is_dir()
    _assert_repository_ok(source, base)


def test_stale_record_cannot_remove_replacement_branch_worktree(
    tmp_path: Path,
) -> None:
    source, base = _repository(tmp_path / "repo")
    store = _state(tmp_path / "state.db", source, ["owned-task"])
    manager = GitWorktreeManager(source, tmp_path / "worktrees", store)
    owned = manager.create_task_worktree(
        "run-1",
        "owned-task",
        base,
        "worker-1",
    )
    owned_path = Path(owned.path)
    _git(source, "worktree", "remove", str(owned_path))
    _git(
        source,
        "worktree",
        "add",
        "-b",
        "human/replacement",
        str(owned_path),
        base,
    )
    (owned_path / "replacement-note.txt").write_text(
        "not AgentBus owned\n",
        encoding="utf-8",
    )

    with pytest.raises(WorktreeOwnershipError, match="branch does not match"):
        manager.mark_cleanup_pending(owned.worktree_id)

    assert owned_path.is_dir()
    assert _git(owned_path, "branch", "--show-current") == "human/replacement"
    assert (owned_path / "replacement-note.txt").is_file()
    assert store.get_worktree(owned.worktree_id).status == WorktreeStatus.ACTIVE
    _assert_repository_ok(source, base)


def test_controlled_git_failure_is_bounded_and_preserves_ambiguous_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source, base = _repository(tmp_path / "repo")
    store = _state(tmp_path / "state.db", source, ["failed-create"])
    manager = GitWorktreeManager(source, tmp_path / "worktrees", store)
    original_run_git = manager._run_git

    def fail_worktree_add(arguments: list[str], *, cwd: Path) -> str:
        if arguments[:2] == ["worktree", "add"]:
            raise WorktreeError("controlled worktree add failure")
        return original_run_git(arguments, cwd=cwd)

    monkeypatch.setattr(manager, "_run_git", fail_worktree_add)
    with pytest.raises(WorktreeError, match="controlled worktree add failure"):
        manager.create_task_worktree(
            "run-1",
            "failed-create",
            base,
            "worker-1",
        )

    orphan = store.list_worktrees("run-1", task_id="failed-create")[-1]
    assert orphan.status == WorktreeStatus.ORPHANED
    assert not Path(orphan.path).exists()
    with pytest.raises(WorktreeAlreadyExistsError, match="unresolved status"):
        manager.create_task_worktree(
            "run-1",
            "failed-create",
            base,
            "worker-2",
        )

    monkeypatch.setattr(manager, "_run_git", original_run_git)
    monkeypatch.setenv("GIT_DIR", str(tmp_path / "must-not-be-used"))
    captured: dict[str, Any] = {}
    original_subprocess_run = subprocess.run

    def failed_command(command, **kwargs):
        captured["command"] = command
        captured.update(kwargs)
        return subprocess.CompletedProcess(
            command,
            1,
            stdout="",
            stderr="token=real-secret\n" + "x" * 20_000,
        )

    monkeypatch.setattr(subprocess, "run", failed_command)
    with pytest.raises(WorktreeError) as failure:
        manager._run_git(["status", "--porcelain"], cwd=source)

    assert "real-secret" not in str(failure.value)
    assert len(str(failure.value)) < 2_200
    assert captured["shell"] is False
    assert "GIT_DIR" not in captured["env"]
    assert captured["env"]["GIT_CONFIG_GLOBAL"] == os.devnull
    monkeypatch.setattr(subprocess, "run", original_subprocess_run)
    _assert_repository_ok(source, base, run_git=original_run_git)


@pytest.mark.skipif(os.name != "nt", reason="Windows sharing semantics only")
def test_windows_locked_file_refuses_removal_without_marking_removed(
    tmp_path: Path,
) -> None:
    import ctypes
    from ctypes import wintypes

    source, base = _repository(tmp_path / "repo")
    store = _state(tmp_path / "state.db", source, ["locked-task"])
    manager = GitWorktreeManager(source, tmp_path / "worktrees", store)
    record = manager.create_task_worktree(
        "run-1",
        "locked-task",
        base,
        "worker-1",
    )
    manager.mark_cleanup_pending(record.worktree_id)
    locked_path = Path(record.path) / "shared.txt"
    create_file = ctypes.windll.kernel32.CreateFileW
    create_file.argtypes = (
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.LPVOID,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    )
    create_file.restype = wintypes.HANDLE
    handle = create_file(
        str(locked_path),
        0x80000000,
        0,
        None,
        3,
        0x80,
        None,
    )
    invalid_handle = ctypes.c_void_p(-1).value
    assert handle not in (None, invalid_handle)
    try:
        with pytest.raises(WorktreeError):
            manager.remove(record.worktree_id)
        assert store.get_worktree(record.worktree_id).status == (
            WorktreeStatus.CLEANUP_PENDING
        )
        assert Path(record.path).exists()
    finally:
        ctypes.windll.kernel32.CloseHandle(handle)

    _assert_repository_ok(source, base)


def _repository(path: Path) -> tuple[Path, str]:
    path.mkdir()
    _git(path, "init", "-q")
    _git(path, "config", "user.name", "AgentBus Test")
    _git(path, "config", "user.email", "agentbus@example.invalid")
    (path / "shared.txt").write_text("baseline\n", encoding="utf-8")
    _git(path, "add", "shared.txt")
    _git(path, "commit", "-q", "-m", "baseline")
    return path.resolve(), _git(path, "rev-parse", "HEAD")


def _state(
    database: Path,
    source: Path,
    task_ids: list[str],
) -> StateStore:
    store = StateStore(database)
    plan = {
        "goal": "Stress worktree lifecycle",
        "steps": [
            {
                "id": task_id,
                "title": task_id,
                "description": f"Exercise {task_id}",
                "dependencies": [],
                "risk": "low",
                "maximum_attempts": 2,
            }
            for task_id in task_ids
        ],
        "test_strategy": "offline",
        "done_criteria": ["worktrees remain isolated"],
    }
    DurableExecutionEngine(store).create_run(
        "Stress worktree lifecycle",
        plan,
        model="deterministic",
        workspace=str(source),
        run_id="run-1",
    )
    return store


def _execute_worker(
    store: StateStore,
    manager: GitWorktreeManager,
    leases: LeaseService,
    base_commit: str,
    *,
    task_id: str,
    worker_id: str,
    filename: str,
    result: TaskExecutionResult,
):
    store.update_task_status("run-1", task_id, TaskStatus.READY)
    lease = leases.acquire_lease(
        "run-1",
        task_id,
        worker_id,
        activate_task=True,
    )

    class Executor:
        def __init__(self, workspace: Path):
            self.workspace = workspace

        def execute(self, _context):
            (self.workspace / filename).write_text("preserve me\n", encoding="utf-8")
            return result

    return LocalTaskWorker(
        worker_id=worker_id,
        store=store,
        lease_service=leases,
        worktree_manager=manager,
        executor_factory=Executor,
        heartbeat_seconds=60,
    ).execute(
        store.get_run("run-1"),
        store.get_task("run-1", task_id),
        lease,
        base_commit,
    )


def _run_concurrently(operations: list[Callable[[], Any]]) -> list[Any]:
    barrier = Barrier(len(operations))

    def invoke(operation: Callable[[], Any]) -> Any:
        barrier.wait(timeout=15)
        return operation()

    with ThreadPoolExecutor(max_workers=len(operations)) as executor:
        futures = [executor.submit(invoke, operation) for operation in operations]
        return [future.result(timeout=60) for future in futures]


def _git(path: Path, *arguments: str) -> str:
    return subprocess.run(
        ["git", *arguments],
        cwd=path,
        check=True,
        capture_output=True,
        text=True,
        shell=False,
        env=_git_environment(),
    ).stdout.strip()


def _git_environment() -> dict[str, str]:
    environment = {
        name: value
        for name, value in os.environ.items()
        if not name.upper().startswith("GIT_")
    }
    environment.update(
        {
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_SYSTEM": os.devnull,
            "GIT_TERMINAL_PROMPT": "0",
        }
    )
    return environment


def _assert_repository_ok(
    source: Path,
    base_commit: str,
    *,
    run_git=None,
) -> None:
    if run_git is None:
        assert _git(source, "status", "--porcelain") == ""
        assert _git(source, "rev-parse", "HEAD") == base_commit
        _git(source, "fsck", "--full", "--no-dangling")
        return
    assert run_git(["status", "--porcelain"], cwd=source) == ""
    assert run_git(["rev-parse", "HEAD"], cwd=source) == base_commit
    run_git(["fsck", "--full", "--no-dangling"], cwd=source)
