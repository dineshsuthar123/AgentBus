from __future__ import annotations

from pathlib import Path

from agentbus.execution.cancellation import CancellationToken
from agentbus.intelligence import (
    IndexState,
    IndexStore,
    RepositoryIndexer,
    repository_identity,
    workspace_identity,
)


def _indexer(tmp_path: Path) -> tuple[RepositoryIndexer, IndexStore]:
    repository = repository_identity("fixtures/incremental-indexer")
    workspace = workspace_identity(repository.repository_id, [""])
    store = IndexStore(tmp_path / ".agentbus-test" / "index.sqlite3")
    return (
        RepositoryIndexer(
            tmp_path,
            repository,
            workspace,
            store,
        ),
        store,
    )


def test_builds_and_persists_an_initial_repository_snapshot(
    tmp_path: Path,
) -> None:
    (tmp_path / "pyproject.toml").write_text(
        """
[project]
name = "indexer-fixture"
version = "0.1.0"
""".lstrip(),
        encoding="utf-8",
    )
    source_root = tmp_path / "src" / "fixture"
    source_root.mkdir(parents=True)
    source = """
def helper():
    return "source-only-marker-4cba"

def run():
    return helper()
""".lstrip()
    (source_root / "service.py").write_text(source, encoding="utf-8")
    tests_root = tmp_path / "tests"
    tests_root.mkdir()
    (tests_root / "test_service.py").write_text(
        """
from fixture.service import run

def test_run():
    assert run()
""".lstrip(),
        encoding="utf-8",
    )
    (tmp_path / ".env").write_text(
        "REAL_SECRET_MUST_NOT_BE_INDEXED=abc\n",
        encoding="utf-8",
    )
    indexer, store = _indexer(tmp_path)

    result = indexer.build()
    snapshot = result.snapshot

    assert snapshot.state == IndexState.CURRENT
    assert snapshot.file_count == 2
    assert snapshot.symbol_count >= 5
    assert snapshot.reference_count >= 2
    assert result.indexed_paths == (
        "src/fixture/service.py",
        "tests/test_service.py",
    )
    files = store.list_files(snapshot.snapshot_id)
    assert {item.relative_path for item in files} == set(result.indexed_paths)
    assert next(
        item
        for item in files
        if item.relative_path == "tests/test_service.py"
    ).test is True
    assert all(item.project_id for item in files)
    symbols = store.list_symbols(snapshot.snapshot_id)
    assert any(item.qualified_name.endswith(".run") for item in symbols)
    references = store.list_references(snapshot.snapshot_id)
    assert any(item.target_symbol_id for item in references)
    database_bytes = store.database_path.read_bytes()
    assert source.encode("utf-8") not in database_bytes
    assert b"REAL_SECRET_MUST_NOT_BE_INDEXED" not in database_bytes


def test_repeated_build_reuses_the_content_addressed_snapshot(
    tmp_path: Path,
) -> None:
    (tmp_path / "go.mod").write_text(
        "module example.com/indexer\n",
        encoding="utf-8",
    )
    (tmp_path / "service.go").write_text(
        "package service\nfunc Run() {}\n",
        encoding="utf-8",
    )
    indexer, store = _indexer(tmp_path)

    first = indexer.build()
    second = indexer.build()

    assert second.snapshot == first.snapshot
    assert second.unchanged is True
    assert second.indexed_paths == ()
    assert second.reused_paths == ("service.go",)
    assert len(
        store.list_snapshots(first.snapshot.repository_id)
    ) == 1


def test_failed_source_decode_produces_a_partial_snapshot(
    tmp_path: Path,
) -> None:
    (tmp_path / "broken.py").write_bytes(b"\xff\xfe\x00")
    indexer, store = _indexer(tmp_path)

    result = indexer.build()

    assert result.snapshot.state == IndexState.PARTIALLY_CURRENT
    assert result.skipped_paths == ("broken.py",)
    assert result.snapshot.file_count == 0
    assert store.list_files(result.snapshot.snapshot_id) == ()
    assert any(
        item.code == "index.file_failed"
        for item in result.snapshot.diagnostics
    )


def test_cancelled_initial_build_publishes_a_paused_checkpoint(
    tmp_path: Path,
) -> None:
    (tmp_path / "service.py").write_text(
        "def run():\n    return True\n",
        encoding="utf-8",
    )
    indexer, _ = _indexer(tmp_path)
    cancellation = CancellationToken()
    cancellation.request("test")

    result = indexer.build(cancellation=cancellation)

    assert result.snapshot.state == IndexState.PAUSED
    assert result.snapshot.completed_at is None
    assert result.snapshot.file_count == 0


def test_partial_and_paused_snapshots_cannot_alias(
    tmp_path: Path,
) -> None:
    (tmp_path / "broken.py").write_bytes(b"\xff\xfe\x00")
    indexer, store = _indexer(tmp_path)
    partial = indexer.build()
    cancellation = CancellationToken()
    cancellation.request("test")

    paused = indexer.build(cancellation=cancellation)

    assert partial.snapshot.state == IndexState.PARTIALLY_CURRENT
    assert paused.snapshot.state == IndexState.PAUSED
    assert paused.snapshot.snapshot_id != partial.snapshot.snapshot_id
    assert len(
        store.list_snapshots(partial.snapshot.repository_id)
    ) == 2
