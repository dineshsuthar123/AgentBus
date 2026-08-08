from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import PurePosixPath

from agentbus.intelligence.discovery.ignore import glob_match
from agentbus.intelligence.errors import QueryLimitError
from agentbus.intelligence.identities import stable_hash
from agentbus.intelligence.models import (
    ArchitectureBoundary,
    DiagnosticSeverity,
    IndexDiagnostic,
    Project,
    SourceFile,
    Symbol,
    SymbolKind,
    _relative_path,
)
from agentbus.intelligence.traversal import (
    DependencyGraph,
    ProjectBoundaryCrossing,
)


_LAYER_NAMES = {
    "api",
    "application",
    "domain",
    "infrastructure",
    "internal",
    "persistence",
    "presentation",
}
_SECURITY_NAMES = {
    "auth",
    "authentication",
    "authorization",
    "crypto",
    "identity",
    "payments",
    "security",
}
_SERVICE_NAMES = {
    "api",
    "backend",
    "server",
    "service",
    "services",
}
_SHARED_NAMES = {
    "common",
    "lib",
    "libs",
    "packages",
    "shared",
}


@dataclass(frozen=True)
class ArchitectureLimits:
    maximum_boundaries: int = 1_000
    maximum_evidence_paths: int = 128
    maximum_diagnostics: int = 256
    maximum_high_risk_paths: int = 2_000

    def __post_init__(self) -> None:
        _bounded(self.maximum_boundaries, "maximum_boundaries", 1, 10_000)
        _bounded(
            self.maximum_evidence_paths,
            "maximum_evidence_paths",
            1,
            128,
        )
        _bounded(
            self.maximum_diagnostics,
            "maximum_diagnostics",
            1,
            1_000,
        )
        _bounded(
            self.maximum_high_risk_paths,
            "maximum_high_risk_paths",
            1,
            10_000,
        )


@dataclass(frozen=True)
class ArchitectureInference:
    boundaries: tuple[ArchitectureBoundary, ...]
    diagnostics: tuple[IndexDiagnostic, ...]
    high_risk_paths: tuple[str, ...]
    project_crossing_edge_ids: tuple[str, ...]
    dependency_cycles: tuple[tuple[str, ...], ...]

    def boundaries_for(
        self,
        relative_path: str,
    ) -> tuple[ArchitectureBoundary, ...]:
        normalized = _relative_path(relative_path)
        return tuple(
            boundary
            for boundary in self.boundaries
            if any(
                glob_match(normalized, scope)
                for scope in boundary.scope
            )
        )


class ArchitectureAnalyzer:
    """Infer architecture hints only from bounded repository evidence."""

    def __init__(
        self,
        *,
        limits: ArchitectureLimits | None = None,
    ) -> None:
        self.limits = limits or ArchitectureLimits()

    def analyze(
        self,
        projects: Iterable[Project],
        files: Iterable[SourceFile],
        symbols: Iterable[Symbol],
        graph: DependencyGraph,
        *,
        generated_roots: Iterable[str] = (),
    ) -> ArchitectureInference:
        project_records = tuple(
            sorted(projects, key=lambda item: item.project_id)
        )
        file_records = tuple(
            sorted(files, key=lambda item: item.relative_path)
        )
        symbol_records = tuple(
            sorted(symbols, key=lambda item: item.symbol_id)
        )
        boundaries: dict[str, ArchitectureBoundary] = {}
        diagnostics: list[IndexDiagnostic] = []

        for project in project_records:
            project_files = tuple(
                item
                for item in file_records
                if item.project_id == project.project_id
            )
            project_symbols = tuple(
                item
                for item in symbol_records
                if item.project_id == project.project_id
            )
            boundary = self._project_boundary(
                project,
                project_files,
                project_symbols,
            )
            self._add_boundary(boundaries, boundary, diagnostics)

        for scope, evidence in _layer_evidence(
            project_records,
            file_records,
            maximum_evidence_paths=self.limits.maximum_evidence_paths,
        ).items():
            self._add_boundary(
                boundaries,
                _boundary(
                    name=f"Layer: {PurePosixPath(scope).name}",
                    scope=(f"{scope}/**",),
                    boundary_type="layer",
                    evidence=evidence,
                    confidence=0.7,
                    explanation=(
                        "A conventional layer directory is present in "
                        "discovered project source."
                    ),
                ),
                diagnostics,
            )

        for root in sorted(
            {_relative_path(item) for item in generated_roots}
        ):
            self._add_boundary(
                boundaries,
                _boundary(
                    name=f"Generated area: {root}",
                    scope=(f"{root}/**",),
                    boundary_type="generated",
                    evidence=(root,),
                    confidence=1.0,
                    explanation=(
                        "Repository discovery classified this directory "
                        "as generated output."
                    ),
                ),
                diagnostics,
            )

        security_evidence = _security_evidence(
            file_records,
            maximum_evidence_paths=self.limits.maximum_evidence_paths,
        )
        for scope, evidence in security_evidence.items():
            self._add_boundary(
                boundaries,
                _boundary(
                    name=f"Security-sensitive area: {scope}",
                    scope=(f"{scope}/**",),
                    boundary_type="security_sensitive",
                    evidence=evidence,
                    confidence=0.7,
                    explanation=(
                        "Repository paths explicitly use a conventional "
                        "security-sensitive component name."
                    ),
                ),
                diagnostics,
            )

        crossings = ()
        cycles = ()
        crossing_records = ()
        try:
            crossing_records = graph.project_boundary_crossings()
            crossings = tuple(
                item.edge.edge_id for item in crossing_records
            )
            if crossings:
                self._diagnostic(
                    diagnostics,
                    "architecture.project_crossings",
                    "Resolved dependencies cross discovered project boundaries.",
                    details={"crossing_count": len(crossings)},
                )
            cycle_records = graph.cycles()
            cycles = tuple(item.node_ids for item in cycle_records)
            if cycles:
                self._diagnostic(
                    diagnostics,
                    "architecture.dependency_cycles",
                    "Resolved dependency cycles were detected.",
                    severity=DiagnosticSeverity.WARNING,
                    details={"cycle_count": len(cycles)},
                )
        except QueryLimitError:
            self._diagnostic(
                diagnostics,
                "architecture.graph_limit",
                "Architecture graph analysis reached a configured bound.",
                severity=DiagnosticSeverity.WARNING,
            )

        high_risk = _high_risk_paths(
            file_records,
            symbol_records,
            security_evidence,
            crossing_records,
        )[: self.limits.maximum_high_risk_paths]
        return ArchitectureInference(
            boundaries=tuple(
                sorted(boundaries.values(), key=lambda item: item.boundary_id)
            ),
            diagnostics=tuple(
                diagnostics[: self.limits.maximum_diagnostics]
            ),
            high_risk_paths=high_risk,
            project_crossing_edge_ids=crossings,
            dependency_cycles=cycles,
        )

    def _project_boundary(
        self,
        project: Project,
        files: tuple[SourceFile, ...],
        symbols: tuple[Symbol, ...],
    ) -> ArchitectureBoundary:
        evidence = tuple(
            dict.fromkeys(
                (
                    *project.manifest_paths,
                    *(item.relative_path for item in files[:8]),
                )
            )
        )[: self.limits.maximum_evidence_paths]
        if not evidence:
            evidence = (project.source_roots[0],) if project.source_roots else ()
        if not evidence:
            raise ValueError(
                "project architecture inference requires path evidence"
            )
        names = {
            part.casefold()
            for value in (project.root, project.name)
            for part in PurePosixPath(value).parts
        }
        has_endpoint = any(item.endpoint is not None for item in symbols)
        if names.intersection(_SERVICE_NAMES) or has_endpoint:
            boundary_type = "service"
            confidence = 0.85 if has_endpoint else 0.75
            explanation = (
                "Discovered project layout or indexed endpoints identify "
                "a service boundary."
            )
        elif names.intersection(_SHARED_NAMES):
            boundary_type = "shared_library"
            confidence = 0.75
            explanation = (
                "Discovered project layout identifies a shared-library "
                "boundary."
            )
        else:
            boundary_type = "component"
            confidence = 0.7
            explanation = (
                "A discovered project manifest and source roots identify "
                "a component boundary."
            )
        scope = f"{project.root}/**" if project.root else "**"
        return _boundary(
            name=project.name,
            scope=(scope,),
            boundary_type=boundary_type,
            evidence=evidence,
            confidence=confidence,
            explanation=explanation,
        )

    def _add_boundary(
        self,
        boundaries: dict[str, ArchitectureBoundary],
        boundary: ArchitectureBoundary,
        diagnostics: list[IndexDiagnostic],
    ) -> None:
        if len(boundaries) >= self.limits.maximum_boundaries:
            self._diagnostic(
                diagnostics,
                "architecture.boundary_limit",
                "Architecture inference reached the configured boundary limit.",
                severity=DiagnosticSeverity.WARNING,
            )
            return
        boundaries.setdefault(boundary.boundary_id, boundary)

    def _diagnostic(
        self,
        diagnostics: list[IndexDiagnostic],
        code: str,
        message: str,
        *,
        severity: DiagnosticSeverity = DiagnosticSeverity.INFO,
        details: dict[str, int] | None = None,
    ) -> None:
        if len(diagnostics) >= self.limits.maximum_diagnostics:
            return
        diagnostics.append(
            IndexDiagnostic(
                code=code,
                severity=severity,
                message=message,
                recoverable=True,
                details=details or {},
            )
        )


def _boundary(
    *,
    name: str,
    scope: tuple[str, ...],
    boundary_type: str,
    evidence: tuple[str, ...],
    confidence: float,
    explanation: str,
) -> ArchitectureBoundary:
    identity = "boundary_" + stable_hash(
        {
            "name": name,
            "scope": scope,
            "type": boundary_type,
            "evidence": evidence,
        }
    )
    return ArchitectureBoundary(
        boundary_id=identity,
        name=name,
        scope=scope,
        boundary_type=boundary_type,
        source_evidence=evidence,
        confidence=confidence,
        explanation=explanation,
    )


def _layer_evidence(
    projects: tuple[Project, ...],
    files: tuple[SourceFile, ...],
    *,
    maximum_evidence_paths: int,
) -> dict[str, tuple[str, ...]]:
    roots: dict[str, list[str]] = defaultdict(list)
    source_roots = {
        root
        for project in projects
        for root in project.source_roots
        if root
    }
    for source in files:
        parts = PurePosixPath(source.relative_path).parts[:-1]
        for index, part in enumerate(parts):
            if part.casefold() not in _LAYER_NAMES:
                continue
            scope = PurePosixPath(*parts[: index + 1]).as_posix()
            if source_roots and not any(
                scope == root or scope.startswith(f"{root}/")
                for root in source_roots
            ):
                continue
            roots[scope].append(source.relative_path)
            break
    return {
        scope: tuple(
            sorted(set(evidence))[:maximum_evidence_paths]
        )
        for scope, evidence in sorted(roots.items())
    }


def _security_evidence(
    files: tuple[SourceFile, ...],
    *,
    maximum_evidence_paths: int,
) -> dict[str, tuple[str, ...]]:
    roots: dict[str, list[str]] = defaultdict(list)
    for source in files:
        parts = PurePosixPath(source.relative_path).parts[:-1]
        for index, part in enumerate(parts):
            if part.casefold() not in _SECURITY_NAMES:
                continue
            scope = PurePosixPath(*parts[: index + 1]).as_posix()
            roots[scope].append(source.relative_path)
            break
    return {
        scope: tuple(
            sorted(set(evidence))[:maximum_evidence_paths]
        )
        for scope, evidence in sorted(roots.items())
    }


def _high_risk_paths(
    files: tuple[SourceFile, ...],
    symbols: tuple[Symbol, ...],
    security_evidence: dict[str, tuple[str, ...]],
    crossings: Iterable[ProjectBoundaryCrossing],
) -> tuple[str, ...]:
    paths = {
        path
        for evidence in security_evidence.values()
        for path in evidence
    }
    paths.update(
        item.location.relative_path
        for item in symbols
        if (
            item.endpoint is not None
            or item.kind == SymbolKind.CONFIGURATION_UNIT
        )
    )
    symbols_by_id = {item.symbol_id: item for item in symbols}
    files_by_id = {item.file_id: item for item in files}
    for crossing in crossings:
        for identity in (
            crossing.edge.source_id,
            crossing.edge.target_id,
        ):
            symbol = symbols_by_id.get(identity)
            source = files_by_id.get(identity)
            if symbol is not None:
                paths.add(symbol.location.relative_path)
            elif source is not None:
                paths.add(source.relative_path)
    return tuple(sorted(paths))


def _bounded(
    value: int,
    name: str,
    minimum: int,
    maximum: int,
) -> None:
    if value < minimum or value > maximum:
        raise ValueError(f"{name} must be between {minimum} and {maximum}")
