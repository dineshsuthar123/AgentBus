from __future__ import annotations

from pathlib import Path

from agentbus.intelligence import (
    ArchitectureAnalyzer,
    DependencyEdge,
    DependencyGraph,
    DependencyKind,
    IndexStore,
    Project,
    ProjectKind,
    RepositoryIndexer,
    SourceFile,
    SourceLanguage,
    Symbol,
    SymbolKind,
    SymbolLocation,
    edge_id,
    file_id,
    project_id,
    repository_identity,
    stable_id,
    workspace_identity,
)
from agentbus.intelligence.parsers import ParserRegistry, PythonAstParser


def _records():
    repository = repository_identity("fixtures/architecture")
    service_id = project_id(
        repository.repository_id,
        "services/api",
        ProjectKind.PYTHON,
        name="api-service",
    )
    shared_id = project_id(
        repository.repository_id,
        "packages/shared",
        ProjectKind.PYTHON,
        name="shared",
    )
    service = Project(
        project_id=service_id,
        repository_id=repository.repository_id,
        name="api-service",
        kind=ProjectKind.PYTHON,
        root="services/api",
        source_roots=("services/api/src",),
        manifest_paths=("services/api/pyproject.toml",),
    )
    shared = Project(
        project_id=shared_id,
        repository_id=repository.repository_id,
        name="shared",
        kind=ProjectKind.PYTHON,
        root="packages/shared",
        source_roots=("packages/shared/src",),
        manifest_paths=("packages/shared/pyproject.toml",),
    )
    service_file = SourceFile(
        file_id=file_id(
            repository.repository_id,
            "services/api/src/security/routes.py",
        ),
        repository_id=repository.repository_id,
        project_id=service_id,
        relative_path="services/api/src/security/routes.py",
        language=SourceLanguage.PYTHON,
        content_hash="1" * 64,
        size_bytes=10,
        parser_name="fixture",
        parser_version="1.0.0",
    )
    shared_file = SourceFile(
        file_id=file_id(
            repository.repository_id,
            "packages/shared/src/domain/model.py",
        ),
        repository_id=repository.repository_id,
        project_id=shared_id,
        relative_path="packages/shared/src/domain/model.py",
        language=SourceLanguage.PYTHON,
        content_hash="2" * 64,
        size_bytes=10,
        parser_name="fixture",
        parser_version="1.0.0",
    )
    endpoint = Symbol(
        symbol_id=stable_id("symbol", "architecture", "endpoint"),
        file_id=service_file.file_id,
        project_id=service_id,
        name="route",
        qualified_name="service.route",
        kind=SymbolKind.ENDPOINT,
        language=SourceLanguage.PYTHON,
        location=SymbolLocation(
            relative_path=service_file.relative_path,
            start_line=1,
            start_column=0,
            end_line=1,
            end_column=1,
        ),
        endpoint="/health",
    )
    model = Symbol(
        symbol_id=stable_id("symbol", "architecture", "model"),
        file_id=shared_file.file_id,
        project_id=shared_id,
        name="Model",
        qualified_name="shared.Model",
        kind=SymbolKind.CLASS,
        language=SourceLanguage.PYTHON,
        location=SymbolLocation(
            relative_path=shared_file.relative_path,
            start_line=1,
            start_column=0,
            end_line=1,
            end_column=1,
        ),
        exported=True,
    )
    crossing = DependencyEdge(
        edge_id=edge_id(
            endpoint.symbol_id,
            model.symbol_id,
            DependencyKind.REFERENCES.value,
        ),
        kind=DependencyKind.REFERENCES,
        source_id=endpoint.symbol_id,
        target_id=model.symbol_id,
        confidence=1.0,
        parser_name="fixture",
        parser_version="1.0.0",
        explanation="Explicit fixture dependency.",
    )
    return (service, shared), (service_file, shared_file), (endpoint, model), crossing


def test_infers_evidence_backed_project_layers_and_sensitive_areas() -> None:
    projects, files, symbols, crossing = _records()
    graph = DependencyGraph(
        (crossing,),
        symbols=symbols,
        files=files,
    )

    result = ArchitectureAnalyzer().analyze(
        projects,
        files,
        symbols,
        graph,
        generated_roots=("dist",),
    )

    boundary_types = {
        item.boundary_type for item in result.boundaries
    }
    assert {
        "component",
        "generated",
        "layer",
        "security_sensitive",
        "service",
        "shared_library",
    }.issuperset(boundary_types)
    assert "service" in boundary_types
    assert "shared_library" in boundary_types
    assert "layer" in boundary_types
    assert "security_sensitive" in boundary_types
    assert "generated" in boundary_types
    assert all(item.source_evidence for item in result.boundaries)
    assert all(item.explanation for item in result.boundaries)
    assert files[0].relative_path in result.high_risk_paths
    assert result.project_crossing_edge_ids == (crossing.edge_id,)
    assert not any(
        item.boundary_type == "forbidden_dependency"
        for item in result.boundaries
    )


def test_reports_cycles_without_inventing_forbidden_rules() -> None:
    projects, files, symbols, crossing = _records()
    reverse = crossing.model_copy(
        update={
            "edge_id": edge_id(
                crossing.target_id,
                crossing.source_id,
                crossing.kind.value,
            ),
            "source_id": crossing.target_id,
            "target_id": crossing.source_id,
        }
    )
    graph = DependencyGraph(
        (crossing, reverse),
        symbols=symbols,
        files=files,
    )

    result = ArchitectureAnalyzer().analyze(
        projects,
        files,
        symbols,
        graph,
    )

    assert result.dependency_cycles
    assert any(
        item.code == "architecture.dependency_cycles"
        for item in result.diagnostics
    )
    assert not any(
        item.boundary_type == "forbidden_dependency"
        for item in result.boundaries
    )


def test_indexer_persists_inferred_architecture_boundaries(
    tmp_path: Path,
) -> None:
    (tmp_path / "pyproject.toml").write_text(
        "[project]\nname = \"service\"\nversion = \"1.0.0\"\n",
        encoding="utf-8",
    )
    (tmp_path / "app.py").write_text(
        "from fastapi import FastAPI\n\n"
        "app = FastAPI()\n\n"
        "@app.get('/health')\n"
        "def health():\n"
        "    return {'ok': True}\n",
        encoding="utf-8",
    )
    repository = repository_identity("fixtures/architecture-index")
    workspace = workspace_identity(repository.repository_id, [""])
    store = IndexStore(tmp_path / ".agentbus-test" / "index.sqlite3")

    result = RepositoryIndexer(
        tmp_path,
        repository,
        workspace,
        store,
        registry=ParserRegistry((PythonAstParser(),)),
    ).build()
    boundaries = store.list_architecture_boundaries(
        result.snapshot.snapshot_id
    )

    assert boundaries
    assert any(item.boundary_type == "service" for item in boundaries)
