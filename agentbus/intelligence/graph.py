from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from agentbus.intelligence.errors import QueryLimitError
from agentbus.intelligence.identities import edge_id, stable_hash
from agentbus.intelligence.models import (
    DependencyEdge,
    DependencyKind,
    SourceFile,
    Symbol,
    SymbolReference,
)


@dataclass(frozen=True)
class GraphBuildLimits:
    maximum_references: int = 1_000_000
    maximum_edges: int = 1_000_000

    def __post_init__(self) -> None:
        if self.maximum_references < 1 or self.maximum_references > 1_000_000:
            raise ValueError(
                "maximum_references must be between 1 and 1000000"
            )
        if self.maximum_edges < 1 or self.maximum_edges > 1_000_000:
            raise ValueError(
                "maximum_edges must be between 1 and 1000000"
            )


@dataclass(frozen=True)
class GraphBuildResult:
    edges: tuple[DependencyEdge, ...]
    resolved_references: int
    unresolved_references: int
    test_relationships: int


class DependencyGraphBuilder:
    """Materialize typed, explainable graph edges from parser references."""

    def __init__(
        self,
        *,
        limits: GraphBuildLimits | None = None,
    ) -> None:
        self.limits = limits or GraphBuildLimits()

    def build(
        self,
        files: Iterable[SourceFile],
        symbols: Iterable[Symbol],
        references: Iterable[SymbolReference],
    ) -> GraphBuildResult:
        source_files = {
            item.file_id: item
            for item in files
        }
        symbol_records = {
            item.symbol_id: item
            for item in symbols
        }
        reference_records = tuple(
            sorted(references, key=lambda item: item.reference_id)
        )
        if len(reference_records) > self.limits.maximum_references:
            raise QueryLimitError(
                "Repository references exceed the graph build limit."
            )

        edges: dict[str, DependencyEdge] = {}
        resolved_count = 0
        unresolved_count = 0
        test_count = 0
        for reference in reference_records:
            source_file = source_files.get(reference.source_file_id)
            if source_file is None:
                continue
            source_symbol = (
                symbol_records.get(reference.source_symbol_id)
                if reference.source_symbol_id
                else None
            )
            source_identity = (
                source_symbol.symbol_id
                if source_symbol is not None
                else source_file.file_id
            )
            target_symbol = (
                symbol_records.get(reference.target_symbol_id)
                if reference.target_symbol_id
                else None
            )
            resolved = target_symbol is not None
            if resolved:
                target_identity = target_symbol.symbol_id
                confidence = reference.confidence
                explanation = reference.explanation
                resolved_count += 1
            else:
                target_identity = _unresolved_target_id(reference)
                confidence = min(reference.confidence, 0.49)
                explanation = (
                    "Unresolved parser reference retained as an opaque "
                    "graph node. "
                    f"{reference.explanation}"
                )
                unresolved_count += 1

            edge = _edge(
                reference,
                source_identity=source_identity,
                target_identity=target_identity,
                kind=reference.kind,
                confidence=confidence,
                parser_name=source_file.parser_name,
                parser_version=source_file.parser_version,
                explanation=explanation,
                resolved=resolved,
            )
            edges[edge.edge_id] = edge
            if (
                source_symbol is not None
                and source_symbol.test
                and resolved
                and reference.kind != DependencyKind.TESTS
            ):
                test_edge = _edge(
                    reference,
                    source_identity=source_identity,
                    target_identity=target_identity,
                    kind=DependencyKind.TESTS,
                    confidence=min(confidence, 0.95),
                    parser_name=source_file.parser_name,
                    parser_version=source_file.parser_version,
                    explanation=(
                        "A statically identified test symbol depends on "
                        "the resolved target."
                    ),
                    resolved=True,
                )
                edges[test_edge.edge_id] = test_edge
                test_count += 1
            if len(edges) > self.limits.maximum_edges:
                raise QueryLimitError(
                    "Repository dependency edges exceed the graph build limit."
                )

        return GraphBuildResult(
            edges=tuple(
                sorted(edges.values(), key=lambda item: item.edge_id)
            ),
            resolved_references=resolved_count,
            unresolved_references=unresolved_count,
            test_relationships=test_count,
        )


def _edge(
    reference: SymbolReference,
    *,
    source_identity: str,
    target_identity: str,
    kind: DependencyKind,
    confidence: float,
    parser_name: str,
    parser_version: str,
    explanation: str,
    resolved: bool,
) -> DependencyEdge:
    identity = edge_id(
        source_identity,
        target_identity,
        kind.value,
        location_key=reference.reference_id,
    )
    return DependencyEdge(
        edge_id=identity,
        kind=kind,
        source_id=source_identity,
        target_id=target_identity,
        location=reference.location,
        confidence=confidence,
        parser_name=parser_name,
        parser_version=parser_version,
        explanation=explanation[:2_048],
        resolved=resolved,
    )


def _unresolved_target_id(reference: SymbolReference) -> str:
    return "unresolved_" + stable_hash(
        {
            "source_file_id": reference.source_file_id,
            "target": (
                reference.unresolved_target
                or reference.target_symbol_id
                or "unknown"
            ),
        }
    )
