from __future__ import annotations

from dataclasses import dataclass

from agentbus.intelligence import (
    ArchitectureBoundary,
    ArchitectureInference,
    ChangeImpactAnalyzer,
    DependencyEdge,
    DependencyGraph,
    DependencyKind,
    EvidenceBackedRiskAssessor,
    ImpactRequest,
    ImpactRisk,
    OwnershipExtraction,
    OwnershipRule,
    Project,
    ProjectKind,
    RiskSignals,
    SourceFile,
    SourceLanguage,
    Symbol,
    SymbolKind,
    SymbolLocation,
    content_hash,
    edge_id,
    file_id,
    project_id,
    repository_identity,
    stable_hash,
    stable_id,
)


@dataclass(frozen=True)
class ImpactFixture:
    analyzer: ChangeImpactAnalyzer
    files: dict[str, SourceFile]
    symbols: dict[str, Symbol]
    edges: dict[str, DependencyEdge]


def _fixture() -> ImpactFixture:
    repository = repository_identity("fixtures/change-impact")
    api_id = project_id(
        repository.repository_id,
        "services/api",
        ProjectKind.PYTHON,
        name="api",
    )
    shared_id = project_id(
        repository.repository_id,
        "shared",
        ProjectKind.PYTHON,
        name="shared",
    )
    projects = (
        Project(
            project_id=api_id,
            repository_id=repository.repository_id,
            name="api",
            kind=ProjectKind.PYTHON,
            root="services/api",
            source_roots=("services/api/src",),
            test_roots=("services/api/tests",),
        ),
        Project(
            project_id=shared_id,
            repository_id=repository.repository_id,
            name="shared",
            kind=ProjectKind.PYTHON,
            root="shared",
            source_roots=("shared",),
        ),
    )
    file_specs = (
        ("shared/config.py", shared_id, False, False),
        ("services/api/src/service.py", api_id, False, False),
        ("services/api/src/endpoint.py", api_id, False, False),
        ("services/api/tests/test_endpoint.py", api_id, True, False),
        (".env", None, False, True),
    )
    files = {
        path: SourceFile(
            file_id=file_id(repository.repository_id, path),
            repository_id=repository.repository_id,
            project_id=owner,
            relative_path=path,
            language=SourceLanguage.PYTHON,
            content_hash=content_hash(f"fixture:{path}"),
            size_bytes=len(path.encode("utf-8")),
            parser_name="fixture",
            parser_version="1.0.0",
            test=test,
            protected=protected,
        )
        for path, owner, test, protected in file_specs
    }

    def symbol(
        key: str,
        path: str,
        name: str,
        kind: SymbolKind,
        *,
        exported: bool = False,
        test: bool = False,
        endpoint: str | None = None,
        confidence: float = 1.0,
    ) -> Symbol:
        source = files[path]
        return Symbol(
            symbol_id=stable_id("symbol", "impact", key),
            file_id=source.file_id,
            project_id=source.project_id,
            name=name,
            qualified_name=f"fixture.{name}",
            kind=kind,
            language=SourceLanguage.PYTHON,
            location=SymbolLocation(
                relative_path=path,
                start_line=1,
                start_column=0,
                end_line=2,
                end_column=1,
            ),
            exported=exported,
            test=test,
            endpoint=endpoint,
            confidence=confidence,
        )

    symbols = {
        "config": symbol(
            "config",
            "shared/config.py",
            "settings",
            SymbolKind.CONFIGURATION_UNIT,
        ),
        "service": symbol(
            "service",
            "services/api/src/service.py",
            "load_settings",
            SymbolKind.FUNCTION,
            exported=True,
        ),
        "endpoint": symbol(
            "endpoint",
            "services/api/src/endpoint.py",
            "get_health",
            SymbolKind.ENDPOINT,
            exported=True,
            endpoint="GET /health",
            confidence=0.9,
        ),
        "test": symbol(
            "test",
            "services/api/tests/test_endpoint.py",
            "test_health",
            SymbolKind.TEST,
            test=True,
        ),
        "secret": symbol(
            "secret",
            ".env",
            "REAL_KEY",
            SymbolKind.CONFIGURATION_UNIT,
        ),
    }

    def dependency(
        key: str,
        source: Symbol,
        target: Symbol,
        kind: DependencyKind,
    ) -> DependencyEdge:
        return DependencyEdge(
            edge_id=edge_id(source.symbol_id, target.symbol_id, key),
            kind=kind,
            source_id=source.symbol_id,
            target_id=target.symbol_id,
            confidence=0.95,
            parser_name="fixture",
            parser_version="1.0.0",
            explanation=f"{source.name} depends on {target.name}.",
        )

    edges = {
        "service_config": dependency(
            "service_config",
            symbols["service"],
            symbols["config"],
            DependencyKind.READS,
        ),
        "endpoint_service": dependency(
            "endpoint_service",
            symbols["endpoint"],
            symbols["service"],
            DependencyKind.CALLS,
        ),
        "test_endpoint": dependency(
            "test_endpoint",
            symbols["test"],
            symbols["endpoint"],
            DependencyKind.TESTS,
        ),
    }
    graph = DependencyGraph(
        edges.values(),
        files=files.values(),
        symbols=symbols.values(),
    )
    boundaries = (
        ArchitectureBoundary(
            boundary_id="boundary_" + stable_hash("api"),
            name="API service",
            scope=("services/api/**",),
            boundary_type="service",
            source_evidence=("services/api",),
            confidence=0.9,
            explanation="The API is a discovered service project.",
        ),
        ArchitectureBoundary(
            boundary_id="boundary_" + stable_hash("shared"),
            name="Shared library",
            scope=("shared/**",),
            boundary_type="shared_library",
            source_evidence=("shared",),
            confidence=0.9,
            explanation="The shared project is imported by a service.",
        ),
    )
    architecture = ArchitectureInference(
        boundaries=boundaries,
        diagnostics=(),
        high_risk_paths=(),
        project_crossing_edge_ids=(edges["service_config"].edge_id,),
        dependency_cycles=(),
    )
    rule = OwnershipRule(
        rule_id="owner_" + stable_hash("shared"),
        pattern="shared/**",
        owners=("@platform",),
        source_path=".github/CODEOWNERS",
        confidence=1.0,
        explanation="Shared code is owned by platform.",
    )
    analyzer = ChangeImpactAnalyzer(
        graph,
        projects=projects,
        architecture=architecture,
        ownership=OwnershipExtraction(rules=(rule,), diagnostics=()),
    )
    return ImpactFixture(
        analyzer=analyzer,
        files=files,
        symbols=symbols,
        edges=edges,
    )


def test_change_impact_propagates_across_projects_and_selects_tests() -> None:
    fixture = _fixture()
    result = fixture.analyzer.analyze(
        ImpactRequest(paths=("shared/config.py",), max_depth=4)
    )
    repeated = fixture.analyzer.analyze(
        ImpactRequest(paths=("shared/config.py",), max_depth=4)
    )

    assert result == repeated
    assert result.direct_dependents == (
        fixture.symbols["service"].symbol_id,
    )
    assert set(result.transitive_dependents) == {
        fixture.symbols["service"].symbol_id,
        fixture.symbols["endpoint"].symbol_id,
        fixture.symbols["test"].symbol_id,
    }
    assert len(result.affected_projects) == 2
    assert set(result.affected_public_apis) == {
        fixture.symbols["service"].symbol_id,
        fixture.symbols["endpoint"].symbol_id,
    }
    assert result.affected_endpoints == (
        fixture.symbols["endpoint"].symbol_id,
    )
    assert result.affected_configurations == (
        fixture.symbols["config"].symbol_id,
    )
    assert fixture.edges["service_config"].edge_id in (
        result.architecture_crossings
    )
    assert result.ownership_rules
    assert result.integration_hotspots
    assert result.tests.selected_tests == (
        "services/api/tests/test_endpoint.py",
    )
    assert result.risk == ImpactRisk.HIGH
    assert result.confidence > 0.5
    assert result.truncated is False


def test_proposed_dependency_cycle_is_reported_as_high_risk() -> None:
    fixture = _fixture()
    proposed = DependencyEdge(
        edge_id=edge_id(
            fixture.symbols["config"].symbol_id,
            fixture.symbols["endpoint"].symbol_id,
            "proposed_cycle",
        ),
        kind=DependencyKind.REFERENCES,
        source_id=fixture.symbols["config"].symbol_id,
        target_id=fixture.symbols["endpoint"].symbol_id,
        confidence=0.8,
        parser_name="proposal",
        parser_version="1.0.0",
        explanation="A proposed config dependency reaches the endpoint.",
    )

    result = fixture.analyzer.analyze(
        ImpactRequest(symbol_ids=(fixture.symbols["config"].symbol_id,)),
        proposed_edges=(proposed,),
    )

    assert result.risk == ImpactRisk.HIGH
    assert result.tests.full_suite_recommended is True
    assert any(
        item.startswith("dependency.introduced_cycle:")
        for item in result.evidence
    )
    assert "risk.high.dependency_cycle_introduced" in result.evidence


def test_forbidden_crossing_is_critical_and_direct_test_is_mandatory() -> None:
    fixture = _fixture()
    forbidden = ArchitectureBoundary(
        boundary_id="boundary_" + stable_hash("forbidden-api-shared"),
        name="API must not reach shared configuration",
        scope=("services/api/**",),
        boundary_type="forbidden_dependency",
        source_evidence=("architecture/policy.json",),
        confidence=1.0,
        explanation="An explicit repository policy forbids this dependency.",
        forbidden_targets=("shared/**",),
    )
    analyzer = ChangeImpactAnalyzer(
        fixture.analyzer.graph,
        projects=fixture.analyzer.projects,
        boundaries=(*fixture.analyzer.boundaries, forbidden),
        ownership=fixture.analyzer.ownership,
    )

    critical = analyzer.analyze(
        ImpactRequest(paths=("shared/config.py",), max_depth=4)
    )
    direct_test = analyzer.analyze(
        ImpactRequest(
            symbol_ids=(fixture.symbols["endpoint"].symbol_id,),
            max_depth=1,
        )
    )

    assert critical.risk == ImpactRisk.CRITICAL
    assert "risk.critical.explicit_forbidden_architecture_crossing" in (
        critical.evidence
    )
    assert direct_test.tests.mandatory_tests == (
        "services/api/tests/test_endpoint.py",
    )
    assert direct_test.tests.optional_tests == ()


def test_protected_and_unknown_subjects_are_safe_and_explicit() -> None:
    fixture = _fixture()
    unknown_symbol = stable_id("symbol", "impact", "unknown")

    result = fixture.analyzer.analyze(
        ImpactRequest(
            paths=(".env", "new/module.py"),
            symbol_ids=(unknown_symbol,),
        )
    )

    assert result.changed_paths == ("new/module.py",)
    assert ".env" not in repr(result)
    assert "REAL_KEY" not in repr(result)
    assert "protected_changed_path_omitted" in result.uncertainty
    assert "changed_path_not_indexed:new/module.py" in result.uncertainty
    assert f"changed_symbol_not_indexed:{unknown_symbol}" in result.uncertainty
    assert result.risk == ImpactRisk.LOW
    assert result.confidence < 0.5

    proposed = DependencyEdge(
        edge_id=edge_id(
            fixture.symbols["service"].symbol_id,
            fixture.symbols["secret"].symbol_id,
            "protected_proposal",
        ),
        kind=DependencyKind.READS,
        source_id=fixture.symbols["service"].symbol_id,
        target_id=fixture.symbols["secret"].symbol_id,
        parser_name="proposal",
        parser_version="1.0.0",
        explanation="This protected proposal must be omitted.",
    )
    protected_proposal = fixture.analyzer.analyze(
        ImpactRequest(symbol_ids=(fixture.symbols["service"].symbol_id,)),
        proposed_edges=(proposed,),
    )
    assert "protected_proposed_dependency_omitted" in (
        protected_proposal.uncertainty
    )
    assert fixture.symbols["secret"].symbol_id not in repr(protected_proposal)


def test_risk_classification_uses_semantic_evidence_not_change_size() -> None:
    assessor = EvidenceBackedRiskAssessor()

    assert assessor.assess(RiskSignals()).risk == ImpactRisk.LOW
    assert assessor.assess(
        RiskSignals(affected_public_apis=1)
    ).risk == ImpactRisk.MEDIUM
    assert assessor.assess(
        RiskSignals(introduced_cycles=1)
    ).risk == ImpactRisk.HIGH
    critical = assessor.assess(
        RiskSignals(forbidden_crossings=1),
        evidence_confidences=(1.0,),
    )
    assert critical.risk == ImpactRisk.CRITICAL
    assert critical.confidence == 1.0
