from __future__ import annotations

from dataclasses import dataclass

import pytest

from agentbus.intelligence import (
    DependencyEdge,
    DependencyGraph,
    DependencyKind,
    HistoricalTestFixture,
    ImpactRequest,
    ImpactRisk,
    Project,
    ProjectKind,
    SourceFile,
    SourceLanguage,
    Symbol,
    SymbolKind,
    SymbolLocation,
    TestImpactSelector as ImpactTestSelector,
    TestSelectionLimits as SelectionLimits,
    content_hash,
    edge_id,
    file_id,
    project_id,
    repository_identity,
    stable_id,
)


@dataclass(frozen=True)
class SelectionFixture:
    selector: ImpactTestSelector
    files: dict[str, SourceFile]
    symbols: dict[str, Symbol]


def _fixture() -> SelectionFixture:
    repository = repository_identity("fixtures/test-impact")
    owner = project_id(
        repository.repository_id,
        "app",
        ProjectKind.PYTHON,
        name="app",
    )
    project = Project(
        project_id=owner,
        repository_id=repository.repository_id,
        name="app",
        kind=ProjectKind.PYTHON,
        root="app",
        source_roots=("app/src",),
        test_roots=("app/tests",),
    )
    file_specs = (
        ("app/src/core.py", False, False, owner),
        ("app/src/service.py", False, False, owner),
        ("app/tests/test_core.py", True, False, owner),
        ("app/tests/test_import.py", True, False, owner),
        ("app/tests/test_service.py", True, False, owner),
        ("app/tests/test_misc.py", True, False, owner),
        ("integration/regression.py", True, False, None),
        ("checks/secret_test.py", True, True, owner),
    )
    files = {
        path: SourceFile(
            file_id=file_id(repository.repository_id, path),
            repository_id=repository.repository_id,
            project_id=project_owner,
            relative_path=path,
            language=SourceLanguage.PYTHON,
            content_hash=content_hash(f"test-impact:{path}"),
            size_bytes=len(path.encode("utf-8")),
            parser_name="fixture",
            parser_version="1.0.0",
            test=test,
            protected=protected,
        )
        for path, test, protected, project_owner in file_specs
    }

    def symbol(
        key: str,
        path: str,
        *,
        test: bool = False,
    ) -> Symbol:
        source = files[path]
        return Symbol(
            symbol_id=stable_id("symbol", "test-impact", key),
            file_id=source.file_id,
            project_id=source.project_id,
            name=key,
            qualified_name=f"fixture.{key}",
            kind=SymbolKind.TEST if test else SymbolKind.FUNCTION,
            language=SourceLanguage.PYTHON,
            location=SymbolLocation(
                relative_path=path,
                start_line=1,
                start_column=0,
                end_line=2,
                end_column=1,
            ),
            test=test,
        )

    symbols = {
        "core": symbol("core", "app/src/core.py"),
        "service": symbol("service", "app/src/service.py"),
        "direct": symbol(
            "test_core",
            "app/tests/test_core.py",
            test=True,
        ),
        "imported": symbol(
            "test_import",
            "app/tests/test_import.py",
            test=True,
        ),
        "transitive": symbol(
            "test_service",
            "app/tests/test_service.py",
            test=True,
        ),
        "misc": symbol(
            "test_misc",
            "app/tests/test_misc.py",
            test=True,
        ),
        "historical": symbol(
            "regression",
            "integration/regression.py",
            test=True,
        ),
        "secret": symbol(
            "secret_test",
            "checks/secret_test.py",
            test=True,
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
            explanation=f"{source.name} exercises {target.name}.",
        )

    edges = (
        dependency(
            "service_core",
            symbols["service"],
            symbols["core"],
            DependencyKind.CALLS,
        ),
        dependency(
            "direct_core",
            symbols["direct"],
            symbols["core"],
            DependencyKind.TESTS,
        ),
        dependency(
            "import_core",
            symbols["imported"],
            symbols["core"],
            DependencyKind.IMPORTS,
        ),
        dependency(
            "transitive_service",
            symbols["transitive"],
            symbols["service"],
            DependencyKind.TESTS,
        ),
    )
    graph = DependencyGraph(
        edges,
        files=files.values(),
        symbols=symbols.values(),
    )
    return SelectionFixture(
        selector=ImpactTestSelector(graph, projects=(project,)),
        files=files,
        symbols=symbols,
    )


def test_selects_direct_import_transitive_and_project_tests() -> None:
    fixture = _fixture()
    request = ImpactRequest(
        symbol_ids=(fixture.symbols["core"].symbol_id,),
        max_depth=4,
    )

    selection_options = {
        "affected_configuration_ids": (
            fixture.symbols["core"].symbol_id,
        )
    }
    result = fixture.selector.select(request, **selection_options)
    repeated = fixture.selector.select(request, **selection_options)

    assert result == repeated
    assert result.mandatory_tests == (
        "app/tests/test_core.py",
        "app/tests/test_import.py",
    )
    assert set(result.optional_tests) == {
        "app/tests/test_misc.py",
        "app/tests/test_service.py",
    }
    assert result.selected_tests == tuple(
        sorted((*result.mandatory_tests, *result.optional_tests))
    )
    assert result.full_suite_recommended is False
    assert result.confidence >= 0.65
    assert "test.safety.verification_policy_authoritative" in result.evidence
    assert any("direct_test_reference" in item for item in result.evidence)
    assert any("direct_test_import" in item for item in result.evidence)
    assert any("transitive_dependency_path" in item for item in result.evidence)
    assert any("configuration_relationship" in item for item in result.evidence)


def test_historical_fixture_and_configured_mandatory_tests_are_preserved() -> None:
    fixture = _fixture()
    request = ImpactRequest(paths=("app/src/core.py",))
    historical = HistoricalTestFixture(
        fixture_id="regression-core-v1",
        test_paths=("integration/regression.py",),
        related_paths=("app/src/core.py",),
        confidence=0.85,
    )
    ignored = HistoricalTestFixture(
        fixture_id="nondeterministic",
        test_paths=("integration/ignored.py",),
        related_paths=("app/src/core.py",),
        deterministic=False,
    )

    result = fixture.selector.select(
        request,
        configured_mandatory_tests=("checks/release_gate.py",),
        historical_fixtures=(ignored, historical),
    )

    assert "checks/release_gate.py" in result.mandatory_tests
    assert "integration/regression.py" in result.selected_tests
    assert "integration/ignored.py" not in result.selected_tests
    assert "mandatory_test_not_indexed" in result.escalation_reasons
    assert result.full_suite_recommended is True
    assert any("historical_fixture:regression-core-v1" in item for item in result.evidence)


def test_low_confidence_and_missing_tests_recommend_full_suite() -> None:
    repository = repository_identity("fixtures/test-impact-fallback")
    source = SourceFile(
        file_id=file_id(repository.repository_id, "src/Widget.java"),
        repository_id=repository.repository_id,
        relative_path="src/Widget.java",
        language=SourceLanguage.JAVA,
        content_hash=content_hash("class Widget {}"),
        size_bytes=15,
        parser_name="fixture",
        parser_version="1.0.0",
    )
    test = SourceFile(
        file_id=file_id(repository.repository_id, "checks/WidgetTest.java"),
        repository_id=repository.repository_id,
        relative_path="checks/WidgetTest.java",
        language=SourceLanguage.JAVA,
        content_hash=content_hash("class WidgetTest {}"),
        size_bytes=19,
        parser_name="fixture",
        parser_version="1.0.0",
        test=True,
    )
    selector = ImpactTestSelector(DependencyGraph((), files=(source, test)))

    fallback = selector.select(ImpactRequest(paths=(source.relative_path,)))
    missing = selector.select(ImpactRequest(paths=("src/Missing.java",)))

    assert fallback.selected_tests == (test.relative_path,)
    assert fallback.full_suite_recommended is True
    assert "only_fallback_test_evidence" in fallback.escalation_reasons
    assert any("naming_convention_fallback" in item for item in fallback.evidence)
    assert missing.selected_tests == ()
    assert missing.full_suite_recommended is True
    assert "changed_subject_not_indexed" in missing.escalation_reasons
    assert "no_relevant_tests_identified" in missing.escalation_reasons


def test_truncation_keeps_mandatory_tests_and_protected_tests_fail_closed() -> None:
    fixture = _fixture()
    limited = ImpactTestSelector(
        fixture.selector.graph,
        projects=fixture.selector.projects,
        limits=SelectionLimits(maximum_tests=1),
    )
    request = ImpactRequest(paths=("app/src/core.py",))

    truncated = limited.select(request)
    assert len(truncated.mandatory_tests) == 1
    assert truncated.full_suite_recommended is True
    assert "mandatory_test_evidence_truncated" in (
        truncated.escalation_reasons
    )
    with pytest.raises(ValueError, match="protected path"):
        fixture.selector.select(
            request,
            configured_mandatory_tests=("checks/secret_test.py",),
        )
    protected_subject = fixture.selector.select(
        ImpactRequest(paths=("checks/secret_test.py",))
    )
    assert protected_subject.selected_tests == ()
    assert "checks/secret_test.py" not in repr(protected_subject)
    assert "protected_test_subject_omitted" in (
        protected_subject.escalation_reasons
    )

    low_risk = fixture.selector.select(
        request,
        risk=ImpactRisk.LOW,
        impact_confidence=0.2,
        impact_truncated=True,
    )
    assert low_risk.full_suite_recommended is True
    assert "impact_analysis_truncated" in low_risk.escalation_reasons
    assert "low_test_selection_confidence" in low_risk.escalation_reasons
