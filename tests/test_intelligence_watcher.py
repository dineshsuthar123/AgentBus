from __future__ import annotations

from pathlib import Path
from threading import Event

from agentbus.intelligence import (
    FileChangeKind,
    IndexFreshnessChecker,
    IndexState,
    IndexStore,
    RepositoryChangeBuffer,
    RepositoryIndexer,
    RepositoryWatchUpdater,
    WatchLimits,
    repository_identity,
    workspace_identity,
)
from agentbus.intelligence.parsers import ParserRegistry, PythonAstParser


class _Clock:
    def __init__(self) -> None:
        self.value = 0.0

    def __call__(self) -> float:
        return self.value


def _runtime(
    tmp_path: Path,
) -> tuple[RepositoryIndexer, IndexFreshnessChecker]:
    repository = repository_identity("fixtures/watcher")
    workspace = workspace_identity(repository.repository_id, [""])
    store = IndexStore(tmp_path / ".agentbus-test" / "index.sqlite3")
    registry = ParserRegistry((PythonAstParser(),))
    return (
        RepositoryIndexer(
            tmp_path,
            repository,
            workspace,
            store,
            registry=registry,
        ),
        IndexFreshnessChecker(
            tmp_path,
            repository,
            workspace,
            store,
            registry=registry,
        ),
    )


def test_change_buffer_debounces_and_coalesces_paths(
    tmp_path: Path,
) -> None:
    clock = _Clock()
    changes = RepositoryChangeBuffer(
        tmp_path,
        limits=WatchLimits(debounce_seconds=2),
        monotonic=clock,
    )

    assert changes.observe("service.py")
    assert changes.observe("service.py")
    assert changes.observe("temporary.py", FileChangeKind.CREATED)
    assert changes.observe("temporary.py", FileChangeKind.DELETED)
    assert changes.drain() is None

    clock.value = 2
    batch = changes.drain()

    assert batch is not None
    assert batch.changed_paths == ("service.py",)
    assert batch.full_rescan_required is False


def test_change_buffer_ignores_unowned_protected_and_generated_paths(
    tmp_path: Path,
) -> None:
    outside = tmp_path.parent / "outside.py"
    outside.write_text("SECRET = True\n", encoding="utf-8")
    (tmp_path / ".env").write_text("KEY=secret\n", encoding="utf-8")
    (tmp_path / "dist").mkdir()
    (tmp_path / "dist" / "output.py").write_text(
        "GENERATED = True\n",
        encoding="utf-8",
    )
    changes = RepositoryChangeBuffer(tmp_path)

    assert changes.observe(outside) is False
    assert changes.observe("../outside.py") is False
    assert changes.observe(".env") is False
    assert changes.observe("dist/output.py") is False
    assert changes.drain(force=True) is None


def test_change_buffer_recovers_overflow_with_full_rescan(
    tmp_path: Path,
) -> None:
    changes = RepositoryChangeBuffer(
        tmp_path,
        limits=WatchLimits(maximum_pending_paths=2),
    )

    assert changes.observe("a.py")
    assert changes.observe("b.py")
    assert changes.observe("c.py")

    batch = changes.drain(force=True)

    assert batch is not None
    assert batch.changed_paths == ()
    assert batch.overflowed is True
    assert batch.full_rescan_required is True


def test_change_buffer_pauses_during_git_transitions(
    tmp_path: Path,
) -> None:
    changes = RepositoryChangeBuffer(tmp_path)

    assert changes.observe(".git/index.lock", FileChangeKind.CREATED)
    paused = changes.drain(force=True)

    assert paused is not None
    assert paused.paused is True
    assert paused.full_rescan_required is True

    assert changes.observe(".git/index.lock", FileChangeKind.DELETED)
    resumed = changes.drain(force=True)

    assert resumed is not None
    assert resumed.paused is False
    assert resumed.full_rescan_required is True

    assert changes.observe(
        ".git/rebase-merge/head-name",
        FileChangeKind.CREATED,
    )
    rebase_paused = changes.drain(force=True)
    assert rebase_paused is not None
    assert rebase_paused.paused is True
    assert changes.observe(
        ".git/rebase-merge",
        FileChangeKind.DELETED,
    )
    recovered = changes.drain(force=True)
    assert recovered is not None
    assert recovered.paused is False


def test_watcher_update_rescans_and_refreshes_index(
    tmp_path: Path,
) -> None:
    source = tmp_path / "service.py"
    source.write_text("VALUE = 1\n", encoding="utf-8")
    indexer, freshness = _runtime(tmp_path)
    indexer.build()
    source.write_text("VALUE = 2\n", encoding="utf-8")
    changes = RepositoryChangeBuffer(tmp_path)
    assert changes.observe(source)

    update = RepositoryWatchUpdater(changes, indexer).process_ready(
        force=True
    )

    assert update is not None
    assert update.indexing_result is not None
    assert update.indexing_result.indexed_paths == ("service.py",)
    assert freshness.status().state == IndexState.CURRENT


def test_watcher_shutdown_is_cooperative(
    tmp_path: Path,
) -> None:
    cancellation = Event()
    changes = RepositoryChangeBuffer(tmp_path)
    assert changes.observe("service.py")
    cancellation.set()

    batch = changes.drain(force=True, cancellation=cancellation)

    assert batch is not None
    assert batch.paused is True
    assert batch.changed_paths == ()
