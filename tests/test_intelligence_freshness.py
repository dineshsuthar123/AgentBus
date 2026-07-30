from __future__ import annotations

from pathlib import Path

from agentbus.intelligence import (
    FreshnessLimits,
    IndexFreshnessChecker,
    IndexState,
    IndexStore,
    RepositoryIndexer,
    repository_identity,
    workspace_identity,
)
from agentbus.intelligence.parsers import ParserRegistry, PythonAstParser


def _components(
    tmp_path: Path,
    *,
    registry: ParserRegistry | None = None,
) -> tuple[RepositoryIndexer, IndexFreshnessChecker]:
    repository = repository_identity("fixtures/freshness")
    workspace = workspace_identity(repository.repository_id, [""])
    store = IndexStore(tmp_path / ".agentbus-test" / "index.sqlite3")
    active_registry = registry or ParserRegistry((PythonAstParser(),))
    return (
        RepositoryIndexer(
            tmp_path,
            repository,
            workspace,
            store,
            registry=active_registry,
        ),
        IndexFreshnessChecker(
            tmp_path,
            repository,
            workspace,
            store,
            registry=active_registry,
        ),
    )


def test_freshness_reports_absent_and_current_indexes(
    tmp_path: Path,
) -> None:
    (tmp_path / "service.py").write_text(
        "def service():\n    return True\n",
        encoding="utf-8",
    )
    indexer, checker = _components(tmp_path)

    assert checker.status().state == IndexState.ABSENT

    result = indexer.build()
    status = checker.status()

    assert status.state == IndexState.CURRENT
    assert status.snapshot_id == result.snapshot.snapshot_id
    assert status.stale_paths == ()
    assert status.indexed_files == 1
    assert status.total_files == 1


def test_freshness_detects_modified_added_and_deleted_sources(
    tmp_path: Path,
) -> None:
    source = tmp_path / "service.py"
    source.write_text("VALUE = 1\n", encoding="utf-8")
    indexer, checker = _components(tmp_path)
    indexer.build()

    source.write_text("VALUE = 2\n", encoding="utf-8")
    (tmp_path / "added.py").write_text("ADDED = True\n", encoding="utf-8")

    changed = checker.status()

    assert changed.state == IndexState.STALE
    assert changed.stale_paths == ("added.py", "service.py")

    indexer.update()
    source.unlink()

    deleted = checker.status()

    assert deleted.state == IndexState.STALE
    assert deleted.stale_paths == ("service.py",)


def test_freshness_excludes_protected_and_generated_files(
    tmp_path: Path,
) -> None:
    (tmp_path / "service.py").write_text("VALUE = 1\n", encoding="utf-8")
    indexer, checker = _components(tmp_path)
    indexer.build()

    (tmp_path / ".env").write_text("REAL_KEY=secret\n", encoding="utf-8")
    (tmp_path / "dist").mkdir()
    (tmp_path / "dist" / "generated.py").write_text(
        "GENERATED = True\n",
        encoding="utf-8",
    )

    status = checker.status()

    assert status.state == IndexState.CURRENT
    assert status.stale_paths == ()
    assert ".env" not in repr(status)
    assert "REAL_KEY" not in repr(status)


def test_freshness_rejects_changed_parser_versions(
    tmp_path: Path,
) -> None:
    (tmp_path / "service.py").write_text("VALUE = 1\n", encoding="utf-8")
    indexer, checker = _components(tmp_path)
    indexer.build()
    assert checker.status().state == IndexState.CURRENT

    changed_parser = PythonAstParser()
    changed_parser.descriptor = changed_parser.descriptor.model_copy(
        update={"version": "99.0.0"}
    )
    _, incompatible = _components(
        tmp_path,
        registry=ParserRegistry((changed_parser,)),
    )

    status = incompatible.status()

    assert status.state == IndexState.INCOMPATIBLE
    assert status.diagnostics[0].code == "index.parsers_incompatible"


def test_freshness_bounds_reported_stale_paths(
    tmp_path: Path,
) -> None:
    for name in ("a.py", "b.py"):
        (tmp_path / name).write_text("VALUE = 1\n", encoding="utf-8")
    indexer, checker = _components(tmp_path)
    indexer.build()
    for name in ("a.py", "b.py"):
        (tmp_path / name).write_text("VALUE = 2\n", encoding="utf-8")

    checker = IndexFreshnessChecker(
        checker.workspace,
        checker.repository,
        checker.workspace_identity,
        checker.store,
        registry=checker.registry,
        limits=FreshnessLimits(maximum_stale_paths=1),
    )
    status = checker.status()

    assert status.state == IndexState.STALE
    assert status.stale_paths == ("a.py",)
    assert any(
        item.code == "index.stale_paths_truncated"
        for item in status.diagnostics
    )
