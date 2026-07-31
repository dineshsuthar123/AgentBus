from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, replace
from datetime import datetime, timezone

import pytest

from agentbus.intelligence import (
    ArchitectureBoundary,
    ContextCandidate,
    ContextPlan,
    ContextRole,
    DiagnosticSeverity,
    ImpactResult,
    ImpactRisk,
    IndexDiagnostic,
    IndexSnapshot,
    IndexState,
    Project,
    ProjectKind,
    SourceFile,
    SourceLanguage,
    Symbol,
    SymbolKind,
    SymbolLocation,
    TestImpactResult as IntelligenceTestImpactResult,
    content_hash,
    file_id,
    project_id,
    repository_identity,
    stable_hash,
    stable_id,
    workspace_identity,
)
from agentbus.runtime.intelligence import (
    PlannerDependencyEvidence,
    PlannerIntelligenceContext,
    PlannerScopeValidationError,
    PlannerScopeValidator,
    append_planner_intelligence,
)
from agentbus.runtime.intelligence_guidance import (
    build_coder_intelligence,
    build_reviewer_intelligence,
)


@dataclass(frozen=True)
class PlannerFixture:
    context: PlannerIntelligenceContext
    project: Project
    source: SourceFile
    test: SourceFile
    protected: SourceFile
    symbol: Symbol
    protected_symbol: Symbol
    boundary: ArchitectureBoundary


def _fixture() -> PlannerFixture:
    repository = repository_identity("fixtures/runtime-planner")
    workspace = workspace_identity(repository.repository_id, [""])
    owner = project_id(
        repository.repository_id,
        "services/api",
        ProjectKind.PYTHON,
        name="api",
    )
    project = Project(
        project_id=owner,
        repository_id=repository.repository_id,
        name="api",
        kind=ProjectKind.PYTHON,
        root="services/api",
        source_roots=("services/api/src",),
        test_roots=("services/api/tests",),
    )

    def source_file(
        path: str,
        *,
        test: bool = False,
        protected: bool = False,
    ) -> SourceFile:
        return SourceFile(
            file_id=file_id(repository.repository_id, path),
            repository_id=repository.repository_id,
            project_id=None if protected else owner,
            relative_path=path,
            language=SourceLanguage.PYTHON,
            content_hash=content_hash(f"fixture:{path}"),
            size_bytes=100,
            parser_name="fixture",
            parser_version="1.0.0",
            test=test,
            protected=protected,
        )

    source = source_file("services/api/src/service.py")
    test = source_file("services/api/tests/test_service.py", test=True)
    protected = source_file(".env", protected=True)

    def symbol(
        key: str,
        file: SourceFile,
        name: str,
        kind: SymbolKind,
    ) -> Symbol:
        return Symbol(
            symbol_id=stable_id("symbol", "runtime-planner", key),
            file_id=file.file_id,
            project_id=file.project_id,
            name=name,
            qualified_name=f"fixture.{name}",
            kind=kind,
            language=SourceLanguage.PYTHON,
            location=SymbolLocation(
                relative_path=file.relative_path,
                start_line=1,
                start_column=0,
                end_line=2,
                end_column=1,
            ),
            exported=not file.protected,
        )

    public_symbol = symbol(
        "service",
        source,
        "calculate_total",
        SymbolKind.FUNCTION,
    )
    protected_symbol = symbol(
        "secret",
        protected,
        "REAL_KEY",
        SymbolKind.CONSTANT,
    )
    boundary = ArchitectureBoundary(
        boundary_id="boundary_" + stable_hash("api-service"),
        name="API service",
        scope=("services/api/**",),
        boundary_type="service",
        source_evidence=("services/api",),
        confidence=0.95,
        explanation="Keep API changes inside the service boundary.",
    )
    snapshot = IndexSnapshot(
        snapshot_id=stable_id("snapshot", "runtime-planner"),
        repository_id=repository.repository_id,
        workspace_id=workspace.workspace_id,
        state=IndexState.STALE,
        created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        project_map_hash=content_hash("project-map"),
        graph_hash=content_hash("graph"),
        parser_versions={"python": "1.0.0"},
        source_fingerprint=content_hash("source"),
    )
    safe_candidate = ContextCandidate(
        candidate_id="candidate-service",
        relative_path=source.relative_path,
        source_hash=source.content_hash,
        symbol_id=public_symbol.symbol_id,
        role=ContextRole.PLANNER,
        score=1.0,
        byte_count=50,
        estimated_tokens=12,
        selected=True,
        reasons=("lexical_match",),
        content="def calculate_total(): return 42",
    )
    protected_candidate = ContextCandidate(
        candidate_id="candidate-secret",
        relative_path=protected.relative_path,
        source_hash=protected.content_hash,
        symbol_id=protected_symbol.symbol_id,
        role=ContextRole.PLANNER,
        score=1.0,
        byte_count=50,
        estimated_tokens=12,
        selected=True,
        reasons=("must_be_filtered",),
        content="REAL_KEY=must-not-appear",
    )
    context_plan = ContextPlan(
        plan_id=stable_id("plan", "runtime-planner"),
        snapshot_id=snapshot.snapshot_id,
        role=ContextRole.PLANNER,
        task_hash=content_hash("update total"),
        byte_budget=1_000,
        token_budget=250,
        selected_bytes=100,
        selected_tokens=24,
        candidates=(safe_candidate, protected_candidate),
        stale_warning="Planner context was built from a stale index.",
        plan_hash=content_hash("context-plan"),
    )
    tests = IntelligenceTestImpactResult(
        result_id=stable_id("testimpact", "runtime-planner"),
        selected_tests=(test.relative_path,),
        mandatory_tests=(test.relative_path,),
        confidence=0.9,
        evidence=("dependency.tests",),
    )
    impact = ImpactResult(
        result_id=stable_id("impact", "runtime-planner"),
        snapshot_id=snapshot.snapshot_id,
        changed_paths=(source.relative_path,),
        changed_symbols=(public_symbol.symbol_id,),
        affected_projects=(owner,),
        affected_public_apis=(public_symbol.symbol_id,),
        affected_endpoints=(public_symbol.symbol_id,),
        integration_hotspots=(public_symbol.symbol_id,),
        risk=ImpactRisk.HIGH,
        confidence=0.8,
        uncertainty=("index_is_stale",),
        evidence=("dependency_path",),
        tests=tests,
    )
    safe_dependency = PlannerDependencyEvidence(
        node_ids=(public_symbol.symbol_id, owner),
        edge_ids=(stable_id("edge", "runtime-planner", "safe"),),
        confidence=0.9,
    )
    protected_dependency = PlannerDependencyEvidence(
        node_ids=(public_symbol.symbol_id, protected_symbol.symbol_id),
        edge_ids=(stable_id("edge", "runtime-planner", "protected"),),
    )
    diagnostics = (
        IndexDiagnostic(
            code="index.stale",
            severity=DiagnosticSeverity.WARNING,
            message="The service source changed after indexing.",
            relative_path=source.relative_path,
        ),
        IndexDiagnostic(
            code="secret.must_not_render",
            severity=DiagnosticSeverity.WARNING,
            message="REAL_KEY must not appear.",
            relative_path=protected.relative_path,
        ),
    )
    context = PlannerIntelligenceContext(
        snapshot=snapshot,
        projects=(project,),
        files=(source, test, protected),
        symbols=(public_symbol, protected_symbol),
        dependency_paths=(safe_dependency, protected_dependency),
        boundaries=(boundary,),
        context_plan=context_plan,
        impact=impact,
        diagnostics=diagnostics,
        risk_areas=(source.relative_path, protected.relative_path),
    )
    return PlannerFixture(
        context=context,
        project=project,
        source=source,
        test=test,
        protected=protected,
        symbol=public_symbol,
        protected_symbol=protected_symbol,
        boundary=boundary,
    )


def _plan(fixture: PlannerFixture) -> dict:
    return {
        "goal": "Update total calculation",
        "steps": [
            {
                "id": "step-1",
                "title": "Update service",
                "description": "Update the total calculation.",
                "risk": "high",
                "required_capabilities": [
                    "filesystem.read",
                    "filesystem.write",
                ],
                "targeted_files": [fixture.source.relative_path],
                "targeted_symbols": [fixture.symbol.symbol_id],
                "expected_impacted_components": [fixture.project.project_id],
            }
        ],
        "test_strategy": "Run affected tests.",
        "done_criteria": ["Tests pass."],
    }


def _with_unrelated_source(
    fixture: PlannerFixture,
) -> tuple[PlannerIntelligenceContext, SourceFile, Symbol]:
    path = "tools/unrelated.py"
    unrelated_file = SourceFile(
        file_id=file_id(fixture.source.repository_id, path),
        repository_id=fixture.source.repository_id,
        project_id=fixture.project.project_id,
        relative_path=path,
        language=SourceLanguage.PYTHON,
        content_hash=content_hash("unrelated"),
        size_bytes=40,
        parser_name="fixture",
        parser_version="1.0.0",
    )
    unrelated_symbol = Symbol(
        symbol_id=stable_id("symbol", "runtime-planner", "unrelated"),
        file_id=unrelated_file.file_id,
        project_id=fixture.project.project_id,
        name="unrelated_helper",
        qualified_name="tools.unrelated_helper",
        kind=SymbolKind.FUNCTION,
        language=SourceLanguage.PYTHON,
        location=SymbolLocation(
            relative_path=path,
            start_line=1,
            start_column=0,
            end_line=2,
            end_column=1,
        ),
    )
    unrelated_candidate = ContextCandidate(
        candidate_id="candidate-unrelated",
        relative_path=path,
        source_hash=unrelated_file.content_hash,
        symbol_id=unrelated_symbol.symbol_id,
        role=ContextRole.PLANNER,
        score=0.1,
        byte_count=40,
        estimated_tokens=10,
        selected=True,
        reasons=("low_rank_fallback",),
        content="UNRELATED_SENTINEL = True",
    )
    context_plan = fixture.context.context_plan.model_copy(
        update={
            "candidates": (
                *fixture.context.context_plan.candidates,
                unrelated_candidate,
            ),
            "selected_bytes": fixture.context.context_plan.selected_bytes + 40,
            "selected_tokens": fixture.context.context_plan.selected_tokens + 10,
            "plan_hash": content_hash("context-plan-with-unrelated"),
        }
    )
    context = replace(
        fixture.context,
        files=(*fixture.context.files, unrelated_file),
        symbols=(*fixture.context.symbols, unrelated_symbol),
        context_plan=context_plan,
    )
    return context, unrelated_file, unrelated_symbol


def test_planner_context_is_bounded_deterministic_and_protected() -> None:
    fixture = _fixture()

    first = fixture.context.render()
    second = fixture.context.render()
    bounded = fixture.context.render(maximum_characters=1_000)
    reordered = replace(
        fixture.context,
        projects=tuple(reversed(fixture.context.projects)),
        files=tuple(reversed(fixture.context.files)),
        symbols=tuple(reversed(fixture.context.symbols)),
        dependency_paths=tuple(reversed(fixture.context.dependency_paths)),
        boundaries=tuple(reversed(fixture.context.boundaries)),
    )

    assert first == second
    assert fixture.context.context_hash == _fixture().context.context_hash
    assert fixture.context.context_hash == reordered.context_hash
    assert fixture.project.project_id in first
    assert fixture.symbol.symbol_id in first
    assert fixture.boundary.boundary_id in first
    assert fixture.test.relative_path in first
    assert "calculate_total" in first
    assert "index.stale" in first
    assert "index_is_stale" in first
    assert fixture.protected.relative_path not in first
    assert fixture.protected_symbol.symbol_id not in first
    assert "REAL_KEY" not in first
    assert "secret.must_not_render" not in first
    assert len(bounded) <= 1_000
    assert bounded.endswith("[repository intelligence context truncated]")
    assert append_planner_intelligence("legacy", fixture.context).startswith(
        "legacy\n\nRepository Intelligence Context"
    )


def test_scope_validator_enriches_evidence_without_broadening_capabilities() -> None:
    fixture = _fixture()
    original = _plan(fixture)

    result = PlannerScopeValidator().validate(original, fixture.context)
    plan = result.plan

    assert plan["steps"][0]["required_capabilities"] == [
        "filesystem.read",
        "filesystem.write",
    ]
    assert plan["proposed_tests"] == [fixture.test.relative_path]
    assert plan["expected_impacted_components"] == [
        fixture.project.project_id,
        fixture.symbol.symbol_id,
    ]
    assert plan["architecture_constraints"] == [fixture.boundary.boundary_id]
    assert plan["intelligence_snapshot_id"] == fixture.context.snapshot.snapshot_id
    assert plan["intelligence_context_hash"] == fixture.context.context_hash
    assert plan["intelligence_scope_validated"] is True
    assert "repository_index_state:stale" in result.warnings
    assert "index_is_stale" in result.warnings
    assert "secret.must_not_render" not in result.warnings
    assert original == _plan(fixture)


@pytest.mark.parametrize(
    "claim",
    (
        "traversal",
        "protected_path",
        "protected_symbol",
        "unknown_symbol",
        "unknown_boundary",
        "unknown_component",
    ),
)
def test_scope_validator_rejects_unverifiable_or_protected_claims(
    claim: str,
) -> None:
    fixture = _fixture()
    plan = _plan(fixture)
    if claim == "traversal":
        plan["targeted_files"] = ["../outside.py"]
    elif claim == "protected_path":
        plan["targeted_files"] = [".ENV"]
    elif claim == "protected_symbol":
        plan["targeted_symbols"] = [fixture.protected_symbol.symbol_id]
    elif claim == "unknown_symbol":
        plan["targeted_symbols"] = [stable_id("symbol", "unknown")]
    elif claim == "unknown_boundary":
        plan["architecture_constraints"] = ["boundary_unknown"]
    else:
        plan["expected_impacted_components"] = ["unknown-component"]

    with pytest.raises(PlannerScopeValidationError):
        PlannerScopeValidator().validate(plan, fixture.context)


def test_scope_validator_warns_for_safe_new_paths() -> None:
    fixture = _fixture()
    plan = deepcopy(_plan(fixture))
    plan["targeted_files"] = ["services/api/src/new_service.py"]
    plan["proposed_tests"] = ["services/api/tests/test_new_service.py"]

    result = PlannerScopeValidator().validate(plan, fixture.context)

    assert "planner.targeted_files.not_indexed:services/api/src/new_service.py" in (
        result.warnings
    )
    assert (
        "planner.proposed_tests.not_indexed:services/api/tests/test_new_service.py"
        in result.warnings
    )


def test_scope_validator_rejects_protected_derived_impact_evidence() -> None:
    fixture = _fixture()
    unsafe_impact = fixture.context.impact.model_copy(
        update={
            "tests": fixture.context.impact.tests.model_copy(
                update={"selected_tests": (fixture.protected.relative_path,)}
            )
        }
    )
    context_arguments = {
        "projects": fixture.context.projects,
        "files": fixture.context.files,
        "symbols": fixture.context.symbols,
        "boundaries": fixture.context.boundaries,
    }

    with pytest.raises(ValueError, match="protected path"):
        PlannerIntelligenceContext(**context_arguments, impact=unsafe_impact)

    unsafe_context = PlannerIntelligenceContext(**context_arguments)
    object.__setattr__(unsafe_context, "impact", unsafe_impact)

    with pytest.raises(PlannerScopeValidationError, match="protected path"):
        PlannerScopeValidator().validate(_plan(fixture), unsafe_context)


def test_coder_intelligence_is_focused_and_excludes_unrelated_source() -> None:
    fixture = _fixture()
    context, unrelated_file, unrelated_symbol = _with_unrelated_source(fixture)
    plan = PlannerScopeValidator().validate(_plan(fixture), context).plan

    evidence = build_coder_intelligence(context, plan)
    rendered = evidence.render()

    assert fixture.source.relative_path in evidence.targeted_files
    assert fixture.test.relative_path in evidence.expected_tests
    assert fixture.symbol.symbol_id in {
        item.symbol_id for item in evidence.definitions
    }
    assert fixture.boundary.boundary_id in {
        item.boundary_id for item in evidence.architecture_constraints
    }
    assert fixture.symbol.symbol_id in evidence.interface_symbols
    assert unrelated_file.relative_path not in evidence.targeted_files
    assert unrelated_symbol.symbol_id not in {
        item.symbol_id for item in evidence.definitions
    }
    assert "UNRELATED_SENTINEL" not in rendered
    assert fixture.protected.relative_path not in rendered
    assert "REAL_KEY" not in rendered
    assert len(evidence.render(maximum_characters=1_000)) <= 1_000

    task_plan = {
        **plan,
        "targeted_files": [unrelated_file.relative_path],
        "steps": [
            {
                **plan["steps"][0],
                "id": "focused-step",
                "targeted_files": [fixture.source.relative_path],
            },
            {
                **plan["steps"][0],
                "id": "unrelated-step",
                "targeted_files": [unrelated_file.relative_path],
            },
        ],
    }
    task_evidence = build_coder_intelligence(
        context,
        task_plan,
        task_id="focused-step",
    )

    assert unrelated_file.relative_path not in task_evidence.targeted_files
    assert unrelated_symbol.symbol_id not in {
        item.symbol_id for item in task_evidence.definitions
    }
    assert fixture.test.relative_path not in task_evidence.expected_tests

    task_review = build_reviewer_intelligence(
        context,
        task_plan,
        (fixture.source.relative_path,),
        task_id="focused-step",
    )

    assert fixture.test.relative_path not in task_review.missing_test_candidates
    assert unrelated_symbol.symbol_id not in (
        task_review.unplanned_affected_components
    )


def test_reviewer_intelligence_compares_actual_and_planned_impact() -> None:
    fixture = _fixture()
    context, unrelated_file, unrelated_symbol = _with_unrelated_source(fixture)
    plan = PlannerScopeValidator().validate(_plan(fixture), context).plan

    evidence = build_reviewer_intelligence(
        context,
        plan,
        (
            fixture.source.relative_path,
            unrelated_file.relative_path,
            fixture.protected.relative_path,
        ),
    )
    rendered = evidence.render()

    assert unrelated_file.relative_path in evidence.unplanned_files
    assert unrelated_symbol.symbol_id in evidence.unplanned_affected_components
    assert fixture.test.relative_path in evidence.missing_test_candidates
    assert (
        f"outside_all_planned_boundaries:{unrelated_file.relative_path}"
        in evidence.boundary_risk_candidates
    )
    assert "repository_index_state:stale" in evidence.index_uncertainty
    assert "protected_changed_path_omitted" in evidence.index_uncertainty
    assert fixture.protected.relative_path not in evidence.actual_files
    assert "heuristic evidence, not proof" in rendered
    assert "not necessarily missing" in rendered
    assert "REAL_KEY" not in rendered
