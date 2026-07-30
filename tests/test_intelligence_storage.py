from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pytest

from agentbus.intelligence import (
    IndexDiagnostic,
    IndexPersistenceError,
    IndexSnapshot,
    IndexState,
    IndexStore,
    Project,
    ProjectKind,
    SourceFile,
    SourceLanguage,
    content_hash,
    file_id,
    project_id,
    repository_identity,
    snapshot_id,
    workspace_identity,
)
from agentbus.intelligence.fingerprints import (
    file_set_fingerprint,
    graph_fingerprint,
    parser_versions_fingerprint,
    project_map_fingerprint,
)
from agentbus.intelligence.models import DiagnosticSeverity
from agentbus.intelligence.schema import LATEST_SCHEMA_VERSION


def test_store_initializes_canonical_wal_database(tmp_path: Path) -> None:
    path = tmp_path / "nested" / ".." / "index" / "repository.sqlite3"

    store = IndexStore(path)

    assert store.database_path == path.resolve()
    assert store.schema_version == LATEST_SCHEMA_VERSION
    assert store.journal_mode == "wal"


def test_snapshot_round_trip_is_portable_and_idempotent(tmp_path: Path) -> None:
    store = IndexStore(tmp_path / "repository.sqlite3")
    repository, workspace, snapshot, projects, files = _bundle()

    first = store.publish_snapshot(
        repository,
        workspace,
        snapshot,
        projects=projects,
        files=files,
    )
    second = store.publish_snapshot(
        repository,
        workspace,
        snapshot,
        projects=projects,
        files=files,
    )

    assert first == snapshot
    assert second == snapshot
    assert store.get_repository(repository.repository_id) == repository
    assert store.get_workspace(workspace.workspace_id) == workspace
    assert store.list_projects(snapshot.snapshot_id) == projects
    assert store.list_files(snapshot.snapshot_id) == files
    assert store.latest_snapshot(repository.repository_id) == snapshot
    with sqlite3.connect(store.database_path) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM index_snapshots"
        ).fetchone()[0] == 1
        assert connection.execute(
            "SELECT COUNT(*) FROM content_hashes"
        ).fetchone()[0] == 1
        stored = connection.execute(
            "SELECT roots_json FROM workspaces"
        ).fetchone()[0]
    assert str(tmp_path) not in stored


def test_content_hash_metadata_is_deduplicated_across_snapshots(
    tmp_path: Path,
) -> None:
    store = IndexStore(tmp_path / "repository.sqlite3")
    repository, workspace, first, projects, files = _bundle(snapshot_suffix="first")
    _, _, second, _, second_files = _bundle(snapshot_suffix="second")

    store.publish_snapshot(
        repository,
        workspace,
        first,
        projects=projects,
        files=files,
    )
    store.publish_snapshot(
        repository,
        workspace,
        second,
        projects=projects,
        files=second_files,
    )

    assert len(store.list_snapshots(repository.repository_id)) == 2
    with sqlite3.connect(store.database_path) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM content_hashes"
        ).fetchone()[0] == 1


def test_failed_snapshot_rolls_back_without_replacing_previous_snapshot(
    tmp_path: Path,
) -> None:
    store = IndexStore(tmp_path / "repository.sqlite3")
    repository, workspace, first, projects, files = _bundle(snapshot_suffix="first")
    store.publish_snapshot(
        repository,
        workspace,
        first,
        projects=projects,
        files=files,
    )
    _, _, failed, _, _ = _bundle(snapshot_suffix="failed", file_count=2)
    conflicting_hash_metadata = files[0].model_copy(
        update={
            "file_id": "file_" + "f" * 64,
            "relative_path": "agentbus/other.py",
            "size_bytes": files[0].size_bytes + 1,
        }
    )

    with pytest.raises(IndexPersistenceError, match="Content hash metadata"):
        store.publish_snapshot(
            repository,
            workspace,
            failed,
            projects=projects,
            files=(files[0], conflicting_hash_metadata),
        )

    assert store.latest_snapshot(repository.repository_id) == first
    with sqlite3.connect(store.database_path) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM index_snapshots"
        ).fetchone()[0] == 1
        assert connection.execute(
            "SELECT COUNT(*) FROM indexed_files"
        ).fetchone()[0] == 1
    store.verify()


def test_snapshot_rejects_cross_repository_records(tmp_path: Path) -> None:
    store = IndexStore(tmp_path / "repository.sqlite3")
    repository, workspace, snapshot, projects, files = _bundle()
    foreign = repository_identity("example/foreign")
    foreign_file = files[0].model_copy(
        update={"repository_id": foreign.repository_id}
    )

    with pytest.raises(IndexPersistenceError, match="another repository"):
        store.publish_snapshot(
            repository,
            workspace,
            snapshot,
            projects=projects,
            files=(foreign_file,),
        )

    with sqlite3.connect(store.database_path) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM repositories"
        ).fetchone()[0] == 0


def test_snapshot_retry_rejects_conflicting_content(tmp_path: Path) -> None:
    store = IndexStore(tmp_path / "repository.sqlite3")
    repository, workspace, snapshot, projects, files = _bundle()
    store.publish_snapshot(
        repository,
        workspace,
        snapshot,
        projects=projects,
        files=files,
    )
    conflicting = files[0].model_copy(
        update={
            "content_hash": content_hash("different"),
            "size_bytes": len("different"),
        }
    )

    with pytest.raises(IndexPersistenceError, match="conflicts with stored content"):
        store.publish_snapshot(
            repository,
            workspace,
            snapshot,
            projects=projects,
            files=(conflicting,),
        )


def _bundle(
    *,
    snapshot_suffix: str = "default",
    file_count: int = 1,
):
    repository = repository_identity("example/agentbus", display_name="AgentBus")
    workspace = workspace_identity(repository.repository_id, [""])
    project_identity = project_id(
        repository.repository_id,
        "",
        ProjectKind.PYTHON,
        name="agentbus",
    )
    project = Project(
        project_id=project_identity,
        repository_id=repository.repository_id,
        name="agentbus",
        kind=ProjectKind.PYTHON,
        root="",
        source_roots=("agentbus",),
        test_roots=("tests",),
        generated_roots=(),
        manifest_paths=("pyproject.toml",),
    )
    source = b"def answer():\n    return 42\n"
    source_file = SourceFile(
        file_id=file_id(repository.repository_id, "agentbus/example.py"),
        repository_id=repository.repository_id,
        project_id=project.project_id,
        relative_path="agentbus/example.py",
        language=SourceLanguage.PYTHON,
        content_hash=content_hash(source),
        size_bytes=len(source),
        parser_name="python-ast",
        parser_version="1",
    )
    parser_versions = {"python-ast": "1"}
    project_hash = project_map_fingerprint((project,))
    graph_hash = graph_fingerprint(())
    source_fingerprint = file_set_fingerprint((source_file,))
    identity = snapshot_id(
        repository.repository_id,
        content_hash(f"{source_fingerprint}:{snapshot_suffix}"),
        parser_versions_fingerprint(parser_versions),
        project_hash,
        graph_hash,
    )
    snapshot = IndexSnapshot(
        snapshot_id=identity,
        repository_id=repository.repository_id,
        workspace_id=workspace.workspace_id,
        state=IndexState.CURRENT,
        created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        completed_at=datetime(2026, 1, 1, 0, 0, 1, tzinfo=timezone.utc),
        file_count=file_count,
        project_map_hash=project_hash,
        graph_hash=graph_hash,
        parser_versions=parser_versions,
        source_fingerprint=source_fingerprint,
        diagnostics=(
            IndexDiagnostic(
                code="index.complete",
                severity=DiagnosticSeverity.INFO,
                message="Index completed without source persistence.",
            ),
        ),
    )
    return repository, workspace, snapshot, (project,), (source_file,)
