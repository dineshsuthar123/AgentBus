from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from pydantic import ValidationError

from agentbus.intelligence import (
    ArchitectureBoundary,
    DependencyEdge,
    DependencyKind,
    IndexDiagnostic,
    IndexPersistenceError,
    IndexSnapshot,
    IndexState,
    IndexStore,
    Module,
    OwnershipRule,
    Project,
    ProjectKind,
    SourceFile,
    SourceLanguage,
    Symbol,
    SymbolKind,
    SymbolLocation,
    SymbolReference,
    content_hash,
    edge_id,
    file_id,
    module_id,
    project_id,
    reference_id,
    repository_identity,
    snapshot_id,
    symbol_id,
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


def test_symbol_graph_and_architecture_metadata_round_trip(
    tmp_path: Path,
) -> None:
    store = IndexStore(tmp_path / "repository.sqlite3")
    bundle = _graph_bundle()

    stored = store.publish_snapshot(
        bundle["repository"],
        bundle["workspace"],
        bundle["snapshot"],
        projects=bundle["projects"],
        files=bundle["files"],
        modules=bundle["modules"],
        symbols=bundle["symbols"],
        references=bundle["references"],
        edges=bundle["edges"],
        ownership_rules=bundle["ownership_rules"],
        architecture_boundaries=bundle["boundaries"],
    )

    assert stored == bundle["snapshot"]
    assert store.list_modules(stored.snapshot_id) == tuple(
        sorted(bundle["modules"], key=lambda item: item.module_id)
    )
    assert store.list_symbols(stored.snapshot_id) == tuple(
        sorted(bundle["symbols"], key=lambda item: item.symbol_id)
    )
    assert store.list_references(stored.snapshot_id) == bundle["references"]
    assert store.list_edges(stored.snapshot_id) == bundle["edges"]
    assert store.list_ownership_rules(stored.snapshot_id) == bundle["ownership_rules"]
    assert (
        store.list_architecture_boundaries(stored.snapshot_id)
        == bundle["boundaries"]
    )
    store.verify()


def test_graph_reference_cannot_resolve_through_an_older_snapshot(
    tmp_path: Path,
) -> None:
    store = IndexStore(tmp_path / "repository.sqlite3")
    bundle = _graph_bundle()
    store.publish_snapshot(
        bundle["repository"],
        bundle["workspace"],
        bundle["snapshot"],
        projects=bundle["projects"],
        files=bundle["files"],
        modules=bundle["modules"],
        symbols=bundle["symbols"],
        references=bundle["references"],
        edges=bundle["edges"],
        ownership_rules=bundle["ownership_rules"],
        architecture_boundaries=bundle["boundaries"],
    )
    reference = bundle["references"][0]
    retained_symbol = next(
        symbol
        for symbol in bundle["symbols"]
        if symbol.symbol_id == reference.source_symbol_id
    ).model_copy(update={"parent_symbol_id": None})
    snapshot = bundle["snapshot"].model_copy(
        update={
            "snapshot_id": "snapshot_" + "9" * 64,
            "symbol_count": 1,
        }
    )

    with pytest.raises(IndexPersistenceError, match="target symbol"):
        store.publish_snapshot(
            bundle["repository"],
            bundle["workspace"],
            snapshot,
            projects=bundle["projects"],
            files=bundle["files"],
            modules=bundle["modules"],
            symbols=(retained_symbol,),
            references=bundle["references"],
            edges=bundle["edges"],
            ownership_rules=bundle["ownership_rules"],
            architecture_boundaries=bundle["boundaries"],
        )


def test_snapshot_pruning_retains_latest_and_resumable_records(
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
    second_file = files[0].model_copy(
        update={
            "content_hash": content_hash("second source"),
            "size_bytes": len("second source"),
        }
    )
    second = _snapshot_for_file(
        first,
        second_file,
        suffix="second",
        completed_at=first.completed_at + timedelta(minutes=1),
    )
    store.publish_snapshot(
        repository,
        workspace,
        second,
        projects=projects,
        files=(second_file,),
    )
    building_file = files[0].model_copy(
        update={
            "content_hash": content_hash("building source"),
            "size_bytes": len("building source"),
        }
    )
    building = _snapshot_for_file(
        first,
        building_file,
        suffix="building",
        completed_at=first.completed_at - timedelta(minutes=1),
    ).model_copy(update={"state": IndexState.BUILDING})
    store.publish_snapshot(
        repository,
        workspace,
        building,
        projects=projects,
        files=(building_file,),
    )

    deleted = store.prune_snapshots(repository.repository_id, retain=1)

    assert deleted == (first.snapshot_id,)
    retained = {
        snapshot.snapshot_id
        for snapshot in store.list_snapshots(repository.repository_id)
    }
    assert retained == {second.snapshot_id, building.snapshot_id}
    with sqlite3.connect(store.database_path) as connection:
        hashes = {
            row[0]
            for row in connection.execute(
                "SELECT content_hash FROM content_hashes"
            ).fetchall()
        }
    assert hashes == {second_file.content_hash, building_file.content_hash}
    store.verify()


def test_store_revalidates_copied_models_before_writing(tmp_path: Path) -> None:
    store = IndexStore(tmp_path / "repository.sqlite3")
    repository, workspace, snapshot, projects, files = _bundle()
    unsafe = files[0].model_copy(
        update={"relative_path": "../../outside.py"}
    )

    with pytest.raises(ValidationError, match="traverse"):
        store.publish_snapshot(
            repository,
            workspace,
            snapshot,
            projects=projects,
            files=(unsafe,),
        )

    with sqlite3.connect(store.database_path) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM repositories"
        ).fetchone()[0] == 0


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


def _graph_bundle() -> dict[str, object]:
    repository, workspace, base, projects, files = _bundle()
    project = projects[0]
    source = files[0]
    module_identity = module_id(
        project.project_id,
        source.relative_path,
        "agentbus.example",
    )
    module = Module(
        module_id=module_identity,
        project_id=project.project_id,
        name="example",
        qualified_name="agentbus.example",
        relative_path=source.relative_path,
        language=SourceLanguage.PYTHON,
        public=True,
    )
    class_identity = symbol_id(
        source.file_id,
        "agentbus.example.Calculator",
        SymbolKind.CLASS,
    )
    method_identity = symbol_id(
        source.file_id,
        "agentbus.example.Calculator.answer",
        SymbolKind.METHOD,
        signature="answer(self) -> int",
    )
    parent = Symbol(
        symbol_id=class_identity,
        file_id=source.file_id,
        project_id=project.project_id,
        module_id=module.module_id,
        name="Calculator",
        qualified_name="agentbus.example.Calculator",
        kind=SymbolKind.CLASS,
        language=SourceLanguage.PYTHON,
        location=SymbolLocation(
            relative_path=source.relative_path,
            start_line=1,
            start_column=0,
            end_line=2,
            end_column=13,
        ),
        exported=True,
    )
    child = Symbol(
        symbol_id=method_identity,
        file_id=source.file_id,
        project_id=project.project_id,
        module_id=module.module_id,
        name="answer",
        qualified_name="agentbus.example.Calculator.answer",
        kind=SymbolKind.METHOD,
        language=SourceLanguage.PYTHON,
        location=SymbolLocation(
            relative_path=source.relative_path,
            start_line=2,
            start_column=4,
            end_line=2,
            end_column=13,
        ),
        signature="answer(self) -> int",
        documentation="Returns the deterministic answer.",
        parent_symbol_id=parent.symbol_id,
        attributes={"decorators": []},
    )
    location = SymbolLocation(
        relative_path=source.relative_path,
        start_line=2,
        start_column=4,
        end_line=2,
        end_column=10,
    )
    reference = SymbolReference(
        reference_id=reference_id(
            source.file_id,
            source.relative_path,
            location.start_line,
            location.start_column,
            parent.qualified_name,
            DependencyKind.REFERENCES.value,
        ),
        source_symbol_id=child.symbol_id,
        source_file_id=source.file_id,
        target_symbol_id=parent.symbol_id,
        kind=DependencyKind.REFERENCES,
        location=location,
        explanation="Static class reference.",
    )
    edge = DependencyEdge(
        edge_id=edge_id(
            child.symbol_id,
            parent.symbol_id,
            DependencyKind.CALLS.value,
            location_key=f"{source.relative_path}:2:4",
        ),
        kind=DependencyKind.CALLS,
        source_id=child.symbol_id,
        target_id=parent.symbol_id,
        location=location,
        confidence=0.9,
        parser_name="python-ast",
        parser_version="1",
        explanation="Statically resolved call target.",
    )
    ownership = OwnershipRule(
        rule_id="owner-agentbus",
        pattern="agentbus/**",
        owners=("@agentbus-maintainers",),
        source_path="CODEOWNERS",
        confidence=1,
        explanation="Explicit CODEOWNERS rule.",
    )
    boundary = ArchitectureBoundary(
        boundary_id="boundary-agentbus",
        name="AgentBus package",
        scope=("agentbus/*",),
        boundary_type="security_sensitive",
        source_evidence=("CODEOWNERS",),
        confidence=0.8,
        explanation="Owned package with runtime safety responsibilities.",
    )
    graph_hash = graph_fingerprint((edge,))
    identity = snapshot_id(
        repository.repository_id,
        base.source_fingerprint,
        parser_versions_fingerprint(base.parser_versions),
        base.project_map_hash,
        graph_hash,
    )
    snapshot = base.model_copy(
        update={
            "snapshot_id": identity,
            "symbol_count": 2,
            "reference_count": 1,
            "edge_count": 1,
            "graph_hash": graph_hash,
        }
    )
    return {
        "repository": repository,
        "workspace": workspace,
        "snapshot": snapshot,
        "projects": projects,
        "files": files,
        "modules": (module,),
        "symbols": tuple(sorted((parent, child), key=lambda item: item.symbol_id)),
        "references": (reference,),
        "edges": (edge,),
        "ownership_rules": (ownership,),
        "boundaries": (boundary,),
    }


def _snapshot_for_file(
    base: IndexSnapshot,
    source: SourceFile,
    *,
    suffix: str,
    completed_at: datetime,
) -> IndexSnapshot:
    source_fingerprint = file_set_fingerprint((source,))
    identity = snapshot_id(
        base.repository_id,
        content_hash(f"{source_fingerprint}:{suffix}"),
        parser_versions_fingerprint(base.parser_versions),
        base.project_map_hash,
        base.graph_hash,
    )
    return base.model_copy(
        update={
            "snapshot_id": identity,
            "created_at": completed_at - timedelta(seconds=1),
            "completed_at": completed_at,
            "source_fingerprint": source_fingerprint,
        }
    )
