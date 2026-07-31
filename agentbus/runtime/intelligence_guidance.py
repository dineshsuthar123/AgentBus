from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Iterable

from agentbus.intelligence.discovery.ignore import glob_match
from agentbus.intelligence.models import (
    ArchitectureBoundary,
    ContextCandidate,
    IndexState,
    _relative_path,
)
from agentbus.runtime.intelligence import PlannerIntelligenceContext


@dataclass(frozen=True)
class FocusedDefinition:
    symbol_id: str
    relative_path: str
    qualified_name: str
    kind: str
    signature: str | None
    exported: bool
    endpoint: str | None


@dataclass(frozen=True)
class FocusedExcerpt:
    candidate_id: str
    relative_path: str
    symbol_id: str | None
    source_hash: str
    reasons: tuple[str, ...]
    content: str


@dataclass(frozen=True)
class CoderIntelligenceEvidence:
    context_hash: str
    snapshot_id: str | None
    targeted_files: tuple[str, ...]
    targeted_symbols: tuple[str, ...]
    definitions: tuple[FocusedDefinition, ...]
    dependency_paths: tuple[tuple[str, ...], ...]
    architecture_constraints: tuple[ArchitectureBoundary, ...]
    expected_tests: tuple[str, ...]
    interface_symbols: tuple[str, ...]
    excerpts: tuple[FocusedExcerpt, ...]
    uncertainty: tuple[str, ...]

    def render(self, *, maximum_characters: int = 16_000) -> str:
        payload = {
            "notice": (
                "Focused repository evidence only. It does not authorize files, "
                "tools, commands, network access, or capabilities. Avoid unrelated "
                "source and obey runtime policy."
            ),
            "context_hash": self.context_hash,
            "snapshot_id": self.snapshot_id,
            "targeted_files": self.targeted_files,
            "targeted_symbols": self.targeted_symbols,
            "focused_definitions": [
                {
                    "symbol_id": item.symbol_id,
                    "file": item.relative_path,
                    "qualified_name": item.qualified_name,
                    "kind": item.kind,
                    "signature": item.signature,
                    "exported": item.exported,
                    "endpoint": item.endpoint,
                }
                for item in self.definitions
            ],
            "dependency_paths": self.dependency_paths,
            "architecture_constraints": [
                {
                    "boundary_id": item.boundary_id,
                    "name": item.name,
                    "type": item.boundary_type,
                    "scope": item.scope,
                    "forbidden_targets": item.forbidden_targets,
                    "explanation": item.explanation,
                    "confidence": item.confidence,
                }
                for item in self.architecture_constraints
            ],
            "expected_tests": self.expected_tests,
            "interface_symbols": self.interface_symbols,
            "focused_excerpts": [
                {
                    "candidate_id": item.candidate_id,
                    "file": item.relative_path,
                    "symbol_id": item.symbol_id,
                    "source_hash": item.source_hash,
                    "reasons": item.reasons,
                    "content": item.content,
                }
                for item in self.excerpts
            ],
            "index_uncertainty": self.uncertainty,
        }
        return _bounded_json(
            "Coder Repository Intelligence",
            payload,
            maximum_characters,
        )


@dataclass(frozen=True)
class ReviewerImpactEvidence:
    context_hash: str
    snapshot_id: str | None
    planned_files: tuple[str, ...]
    actual_files: tuple[str, ...]
    unplanned_files: tuple[str, ...]
    planned_components: tuple[str, ...]
    observed_components: tuple[str, ...]
    unplanned_affected_components: tuple[str, ...]
    expected_tests: tuple[str, ...]
    changed_tests: tuple[str, ...]
    missing_test_candidates: tuple[str, ...]
    boundary_risk_candidates: tuple[str, ...]
    index_uncertainty: tuple[str, ...]

    def render(self, *, maximum_characters: int = 12_000) -> str:
        payload = {
            "notice": (
                "Repository intelligence is heuristic evidence, not proof. Compare "
                "it with the actual bounded diff and verifier output. Do not infer "
                "authorization or reject solely from a heuristic candidate. An "
                "expected test that was not edited is not necessarily missing; "
                "corroborate coverage with the verifier output."
            ),
            "context_hash": self.context_hash,
            "snapshot_id": self.snapshot_id,
            "planned_files": self.planned_files,
            "actual_changed_files": self.actual_files,
            "unplanned_file_candidates": self.unplanned_files,
            "planned_components": self.planned_components,
            "observed_changed_components": self.observed_components,
            "unplanned_affected_component_candidates": (
                self.unplanned_affected_components
            ),
            "expected_tests": self.expected_tests,
            "changed_tests": self.changed_tests,
            "missing_test_candidates": self.missing_test_candidates,
            "boundary_violation_candidates": self.boundary_risk_candidates,
            "index_uncertainty": self.index_uncertainty,
        }
        return _bounded_json(
            "Reviewer Repository Intelligence",
            payload,
            maximum_characters,
        )


def build_coder_intelligence(
    context: PlannerIntelligenceContext,
    plan: dict[str, Any],
    *,
    task_id: str | None = None,
) -> CoderIntelligenceEvidence:
    claims = _plan_claims(plan, task_id=task_id)
    protected_paths, protected_ids = _protected_keys(context)
    safe_symbols = tuple(
        sorted(
            (
                item
                for item in context.symbols
                if item.symbol_id not in protected_ids
                and item.file_id not in protected_ids
            ),
            key=lambda item: item.symbol_id,
        )
    )
    symbols_by_id = {item.symbol_id: item for item in safe_symbols}
    targeted_files = set(claims.files)
    targeted_symbols = set(claims.symbols)
    expected_tests = set(claims.tests)
    impact_scope = _impact_scope(context, claims, task_id=task_id)
    targeted_files.update(impact_scope.paths)
    targeted_symbols.update(impact_scope.symbols)
    expected_tests.update(impact_scope.tests)
    targeted_files.update(expected_tests)
    targeted_symbols.update(
        item.symbol_id
        for item in safe_symbols
        if item.location.relative_path in targeted_files
    )

    safe_dependencies = tuple(
        item
        for item in context.dependency_paths
        if not protected_ids.intersection(item.node_ids)
    )
    relevant_dependencies = tuple(
        item
        for item in safe_dependencies
        if targeted_symbols.intersection(item.node_ids)
        or (task_id is None and not targeted_symbols)
    )[:64]
    related_nodes = set(targeted_symbols)
    for item in relevant_dependencies:
        related_nodes.update(item.node_ids)
    targeted_files.update(
        symbol.location.relative_path
        for symbol_id in related_nodes
        if (symbol := symbols_by_id.get(symbol_id)) is not None
    )

    definitions = tuple(
        FocusedDefinition(
            symbol_id=item.symbol_id,
            relative_path=item.location.relative_path,
            qualified_name=item.qualified_name,
            kind=item.kind.value,
            signature=item.signature,
            exported=item.exported,
            endpoint=item.endpoint,
        )
        for item in safe_symbols
        if item.symbol_id in related_nodes
        or item.location.relative_path in targeted_files
    )[:128]
    interface_symbols = tuple(
        item.symbol_id
        for item in definitions
        if item.exported or item.signature or item.endpoint
    )
    constraints = _selected_boundaries(
        context,
        claims.boundaries,
        targeted_files,
    )
    selected_candidates = _selected_candidates(
        context,
        protected_paths,
        protected_ids,
    )
    focused_candidates = tuple(
        item
        for item in selected_candidates
        if item.relative_path in targeted_files
        or (item.symbol_id is not None and item.symbol_id in related_nodes)
    )
    if not focused_candidates and task_id is None:
        focused_candidates = selected_candidates[:8]
    excerpts = tuple(
        FocusedExcerpt(
            candidate_id=item.candidate_id,
            relative_path=item.relative_path,
            symbol_id=item.symbol_id,
            source_hash=item.source_hash,
            reasons=item.reasons,
            content=(item.content or "")[:3_000],
        )
        for item in focused_candidates[:12]
    )
    return CoderIntelligenceEvidence(
        context_hash=context.context_hash,
        snapshot_id=context.snapshot.snapshot_id if context.snapshot else None,
        targeted_files=_bounded_unique(targeted_files, maximum=1_000),
        targeted_symbols=_bounded_unique(targeted_symbols, maximum=1_000),
        definitions=definitions,
        dependency_paths=tuple(
            item.node_ids for item in relevant_dependencies
        ),
        architecture_constraints=constraints,
        expected_tests=_bounded_unique(expected_tests, maximum=2_000),
        interface_symbols=interface_symbols,
        excerpts=excerpts,
        uncertainty=_context_uncertainty(context, protected_paths),
    )


def build_reviewer_intelligence(
    context: PlannerIntelligenceContext,
    plan: dict[str, Any],
    changed_files: Iterable[str],
    *,
    task_id: str | None = None,
) -> ReviewerImpactEvidence:
    claims = _plan_claims(plan, task_id=task_id)
    protected_paths, protected_ids = _protected_keys(context)
    actual: list[str] = []
    protected_change_omitted = False
    for raw_path in changed_files:
        try:
            path = _relative_path(raw_path)
        except (TypeError, ValueError):
            continue
        if path.casefold() in protected_paths:
            protected_change_omitted = True
            continue
        actual.append(path)
    actual_files = _bounded_unique(actual, maximum=1_000)
    expected_tests = set(claims.tests)
    allowed_files = set(claims.files)
    planned_components = set(claims.components)
    planned_components.update(claims.symbols)
    impact_scope = _impact_scope(context, claims, task_id=task_id)
    expected_tests.update(impact_scope.tests)
    allowed_files.update(impact_scope.paths)
    likely_components = set(impact_scope.components)
    allowed_files.update(expected_tests)
    safe_files = {
        item.relative_path: item
        for item in context.files
        if not item.protected
    }
    planned_components.update(
        source.project_id
        for path in claims.files
        if (source := safe_files.get(path)) is not None
        and source.project_id is not None
    )
    safe_symbols = {
        item.symbol_id: item
        for item in context.symbols
        if item.symbol_id not in protected_ids
        and item.file_id not in protected_ids
    }
    planned_components.update(
        symbol.project_id
        for symbol_id in claims.symbols
        if (symbol := safe_symbols.get(symbol_id)) is not None
        and symbol.project_id is not None
    )
    observed_components: set[str] = set()
    changed_tests: set[str] = set()
    for path in actual_files:
        source = safe_files.get(path)
        if source is not None:
            if source.project_id:
                observed_components.add(source.project_id)
            if source.test:
                changed_tests.add(path)
        elif _looks_like_test(path):
            changed_tests.add(path)
    observed_components.update(
        item.symbol_id
        for item in context.symbols
        if item.symbol_id not in protected_ids
        and item.file_id not in protected_ids
        and item.location.relative_path in actual_files
    )
    unplanned_components = (
        observed_components.union(likely_components) - planned_components
    )
    constraints = _selected_boundaries(
        context,
        claims.boundaries,
        allowed_files,
    )
    boundary_risks = _boundary_risks(
        constraints,
        actual_files,
        changed_tests,
    )
    uncertainty = list(_context_uncertainty(context, protected_paths))
    if protected_change_omitted:
        uncertainty.append("protected_changed_path_omitted")
    return ReviewerImpactEvidence(
        context_hash=context.context_hash,
        snapshot_id=context.snapshot.snapshot_id if context.snapshot else None,
        planned_files=_bounded_unique(allowed_files, maximum=1_000),
        actual_files=actual_files,
        unplanned_files=_bounded_unique(
            set(actual_files) - allowed_files,
            maximum=1_000,
        ),
        planned_components=_bounded_unique(planned_components, maximum=2_000),
        observed_components=_bounded_unique(
            observed_components,
            maximum=2_000,
        ),
        unplanned_affected_components=_bounded_unique(
            unplanned_components,
            maximum=2_000,
        ),
        expected_tests=_bounded_unique(expected_tests, maximum=2_000),
        changed_tests=_bounded_unique(changed_tests, maximum=2_000),
        missing_test_candidates=_bounded_unique(
            expected_tests - changed_tests,
            maximum=2_000,
        ),
        boundary_risk_candidates=boundary_risks,
        index_uncertainty=_bounded_unique(uncertainty, maximum=256),
    )


@dataclass(frozen=True)
class _PlanClaims:
    files: tuple[str, ...]
    symbols: tuple[str, ...]
    components: tuple[str, ...]
    tests: tuple[str, ...]
    boundaries: tuple[str, ...]


@dataclass(frozen=True)
class _ImpactScope:
    paths: tuple[str, ...] = ()
    symbols: tuple[str, ...] = ()
    components: tuple[str, ...] = ()
    tests: tuple[str, ...] = ()


def _plan_claims(plan: dict[str, Any], *, task_id: str | None) -> _PlanClaims:
    selected_steps: list[dict[str, Any]] = []
    steps = plan.get("steps", [])
    if isinstance(steps, list):
        selected_steps = [item for item in steps if isinstance(item, dict)]
        if task_id is not None:
            selected_steps = [
                item for item in selected_steps if item.get("id") == task_id
            ]

    def sources(field_name: str) -> list[dict[str, Any]]:
        if task_id is None:
            return [plan, *selected_steps]
        return [
            item for item in selected_steps if field_name in item
        ]

    return _PlanClaims(
        files=_paths_from(sources("targeted_files"), "targeted_files"),
        symbols=_strings_from(
            sources("targeted_symbols"),
            "targeted_symbols",
            maximum=1_000,
        ),
        components=_strings_from(
            sources("expected_impacted_components"),
            "expected_impacted_components",
            maximum=2_000,
        ),
        tests=_paths_from(
            sources("proposed_tests"),
            "proposed_tests",
            maximum=2_000,
        ),
        boundaries=_strings_from(
            sources("architecture_constraints"),
            "architecture_constraints",
            maximum=256,
        ),
    )


def _impact_scope(
    context: PlannerIntelligenceContext,
    claims: _PlanClaims,
    *,
    task_id: str | None,
) -> _ImpactScope:
    impact = context.impact
    if impact is None:
        return _ImpactScope()
    impact_symbols = {
        *impact.changed_symbols,
        *impact.affected_public_apis,
        *impact.affected_endpoints,
        *impact.affected_configurations,
    }
    impact_components = {
        *impact.affected_projects,
        *impact_symbols,
    }
    impact_tests = {
        *impact.tests.selected_tests,
        *impact.tests.mandatory_tests,
    }
    if task_id is None:
        return _ImpactScope(
            paths=_bounded_unique(impact.changed_paths, maximum=1_000),
            symbols=_bounded_unique(impact_symbols, maximum=2_000),
            components=_bounded_unique(impact_components, maximum=2_000),
            tests=_bounded_unique(impact_tests, maximum=2_000),
        )

    files_by_path = {
        item.relative_path: item
        for item in context.files
        if not item.protected
    }
    protected_file_ids = {
        item.file_id for item in context.files if item.protected
    }
    symbols_by_id = {
        item.symbol_id: item
        for item in context.symbols
        if item.file_id not in protected_file_ids
    }
    project_ids = {
        value
        for value in claims.components
        if value.startswith("project_")
    }
    for path in (*claims.files, *claims.tests):
        source = files_by_path.get(path)
        if source is not None and source.project_id:
            project_ids.add(source.project_id)
        project_ids.update(_projects_for_path(context, path))
    for symbol_id in (*claims.symbols, *claims.components):
        symbol = symbols_by_id.get(symbol_id)
        if symbol is not None and symbol.project_id:
            project_ids.add(symbol.project_id)
    seed_symbols = {
        *claims.symbols,
        *(
            item.symbol_id
            for item in symbols_by_id.values()
            if item.location.relative_path in claims.files
        ),
    }
    related_nodes = set(seed_symbols)
    for dependency in context.dependency_paths:
        if related_nodes.intersection(dependency.node_ids):
            related_nodes.update(dependency.node_ids)
    related_paths = {
        *claims.files,
        *claims.tests,
        *(
            symbol.location.relative_path
            for symbol_id in related_nodes
            if (symbol := symbols_by_id.get(symbol_id)) is not None
        ),
    }
    scoped_symbols = impact_symbols.intersection(related_nodes)
    scoped_projects = set(impact.affected_projects).intersection(project_ids)
    return _ImpactScope(
        paths=_bounded_unique(
            (path for path in impact.changed_paths if path in related_paths),
            maximum=1_000,
        ),
        symbols=_bounded_unique(scoped_symbols, maximum=2_000),
        components=_bounded_unique(
            {
                *scoped_projects,
                *scoped_symbols,
            },
            maximum=2_000,
        ),
        tests=_bounded_unique(
            (path for path in impact_tests if path in related_paths),
            maximum=2_000,
        ),
    )


def _projects_for_path(
    context: PlannerIntelligenceContext,
    path: str,
) -> set[str]:
    return {
        item.project_id
        for item in context.projects
        if item.root
        and (path == item.root or path.startswith(f"{item.root}/"))
    }


def _paths_from(
    values: Iterable[dict[str, Any]],
    field_name: str,
    *,
    maximum: int = 1_000,
) -> tuple[str, ...]:
    paths: list[str] = []
    for value in values:
        raw_paths = value.get(field_name)
        if not isinstance(raw_paths, (list, tuple)):
            continue
        for raw_path in raw_paths:
            try:
                paths.append(_relative_path(raw_path))
            except (TypeError, ValueError):
                continue
    return _bounded_unique(paths, maximum=maximum)


def _strings_from(
    values: Iterable[dict[str, Any]],
    field_name: str,
    *,
    maximum: int,
) -> tuple[str, ...]:
    selected: list[str] = []
    for value in values:
        raw_items = value.get(field_name)
        if not isinstance(raw_items, (list, tuple)):
            continue
        selected.extend(
            item for item in raw_items if isinstance(item, str) and item
        )
    return _bounded_unique(selected, maximum=maximum)


def _selected_boundaries(
    context: PlannerIntelligenceContext,
    claimed_ids: Iterable[str],
    paths: Iterable[str],
) -> tuple[ArchitectureBoundary, ...]:
    selected_ids = set(claimed_ids)
    path_values = tuple(paths)
    return tuple(
        item
        for item in sorted(
            context.boundaries,
            key=lambda value: value.boundary_id,
        )
        if item.boundary_id in selected_ids
        or any(
            glob_match(path, scope)
            for path in path_values
            for scope in item.scope
        )
    )[:64]


def _selected_candidates(
    context: PlannerIntelligenceContext,
    protected_paths: set[str],
    protected_ids: set[str],
) -> tuple[ContextCandidate, ...]:
    if context.context_plan is None:
        return ()
    return tuple(
        item
        for item in context.context_plan.candidates
        if item.selected
        and item.relative_path.casefold() not in protected_paths
        and item.symbol_id not in protected_ids
    )


def _boundary_risks(
    boundaries: Iterable[ArchitectureBoundary],
    actual_files: Iterable[str],
    changed_tests: set[str],
) -> tuple[str, ...]:
    risks: set[str] = set()
    actual = tuple(actual_files)
    selected = tuple(boundaries)
    for path in actual:
        if path in changed_tests:
            continue
        if selected and not any(
            glob_match(path, scope)
            for boundary in selected
            for scope in boundary.scope
        ):
            risks.add(f"outside_all_planned_boundaries:{path}")
    for boundary in selected:
        scoped = tuple(
            path
            for path in actual
            if any(glob_match(path, scope) for scope in boundary.scope)
        )
        for path in actual:
            if path not in changed_tests and scoped and any(
                glob_match(path, target)
                for target in boundary.forbidden_targets
            ):
                risks.add(f"possible_forbidden_crossing:{boundary.boundary_id}:{path}")
    return _bounded_unique(risks, maximum=256)


def _context_uncertainty(
    context: PlannerIntelligenceContext,
    protected_paths: set[str],
) -> tuple[str, ...]:
    values: list[str] = []
    if context.snapshot is None:
        values.append("repository_index_snapshot_unavailable")
    elif context.snapshot.state != IndexState.CURRENT:
        values.append(f"repository_index_state:{context.snapshot.state.value}")
    if context.context_plan and context.context_plan.stale_warning:
        values.append(context.context_plan.stale_warning)
    diagnostics = list(context.diagnostics)
    if context.snapshot is not None:
        diagnostics.extend(context.snapshot.diagnostics)
    values.extend(
        item.code
        for item in diagnostics
        if item.relative_path is None
        or item.relative_path.casefold() not in protected_paths
    )
    if context.impact is not None:
        values.extend(context.impact.uncertainty)
        values.extend(context.impact.tests.escalation_reasons)
        if context.impact.truncated:
            values.append("impact_analysis_truncated")
    return _bounded_unique(values, maximum=256)


def _protected_keys(
    context: PlannerIntelligenceContext,
) -> tuple[set[str], set[str]]:
    protected_files = tuple(item for item in context.files if item.protected)
    protected_paths = {item.relative_path.casefold() for item in protected_files}
    protected_ids = {item.file_id for item in protected_files}
    protected_ids.update(
        item.symbol_id
        for item in context.symbols
        if item.file_id in protected_ids
    )
    return protected_paths, protected_ids


def _looks_like_test(path: str) -> bool:
    name = path.rsplit("/", 1)[-1].casefold()
    return (
        name.startswith("test_")
        or name.endswith(("_test.py", ".test.ts", ".test.tsx", ".spec.ts"))
        or "/tests/" in f"/{path.casefold()}/"
    )


def _bounded_unique(
    values: Iterable[str],
    *,
    maximum: int,
) -> tuple[str, ...]:
    return tuple(sorted(set(values)))[:maximum]


def _bounded_json(
    title: str,
    payload: dict[str, Any],
    maximum_characters: int,
) -> str:
    if maximum_characters < 1_000 or maximum_characters > 100_000:
        raise ValueError("maximum_characters must be between 1000 and 100000")
    rendered = f"{title}\n" + json.dumps(
        payload,
        ensure_ascii=True,
        sort_keys=True,
        indent=2,
    )
    if len(rendered) <= maximum_characters:
        return rendered
    suffix = "\n[repository intelligence guidance truncated]"
    return rendered[: maximum_characters - len(suffix)] + suffix
