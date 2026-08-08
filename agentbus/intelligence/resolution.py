from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass

from agentbus.intelligence.identities import stable_id
from agentbus.intelligence.models import (
    DependencyKind,
    Module,
    Symbol,
    SymbolLocation,
    SymbolReference,
)


@dataclass(frozen=True)
class ResolutionMatch:
    target_symbol_id: str | None
    confidence: float
    method: str
    explanation: str
    candidate_ids: tuple[str, ...] = ()

    @property
    def resolved(self) -> bool:
        return self.target_symbol_id is not None


class ReferenceResolver:
    """Resolve static names without presenting ambiguous guesses as facts."""

    def __init__(
        self,
        symbols: Iterable[Symbol],
        modules: Iterable[Module],
    ) -> None:
        self.symbols = tuple(
            sorted(symbols, key=lambda item: item.symbol_id)
        )
        self.modules = tuple(
            sorted(modules, key=lambda item: item.module_id)
        )
        self._symbols_by_id = {
            item.symbol_id: item for item in self.symbols
        }
        self._modules_by_id = {
            item.module_id: item for item in self.modules
        }
        self._by_qualified_name: dict[str, tuple[Symbol, ...]] = {}
        qualified: dict[str, list[Symbol]] = defaultdict(list)
        names: dict[str, list[Symbol]] = defaultdict(list)
        for symbol in self.symbols:
            qualified[symbol.qualified_name].append(symbol)
            names[symbol.name].append(symbol)
        self._by_qualified_name = {
            key: tuple(sorted(value, key=lambda item: item.symbol_id))
            for key, value in qualified.items()
        }
        self._by_name = {
            key: tuple(sorted(value, key=lambda item: item.symbol_id))
            for key, value in names.items()
        }

    def rebind(
        self,
        references: Iterable[SymbolReference],
        *,
        previous_symbols: Iterable[Symbol] = (),
    ) -> tuple[SymbolReference, ...]:
        previous_by_id = {
            item.symbol_id: item for item in previous_symbols
        }
        ordered = tuple(
            sorted(references, key=lambda item: item.reference_id)
        )
        preliminary: list[tuple[SymbolReference, str, ResolutionMatch]] = []
        for reference in ordered:
            source = self._source_symbol(reference)
            target = self._target_text(reference, previous_by_id)
            match = self._resolve_scoped(reference, target, source)
            preliminary.append((reference, target, match))

        imported_targets: dict[str, set[str]] = defaultdict(set)
        for reference, _, match in preliminary:
            if (
                reference.kind == DependencyKind.IMPORTS
                and match.target_symbol_id is not None
            ):
                imported_targets[reference.source_file_id].add(
                    match.target_symbol_id
                )

        rebound: dict[str, SymbolReference] = {}
        for reference, target, match in preliminary:
            source = self._source_symbol(reference)
            if not match.resolved:
                match = self._resolve_heuristic(
                    target,
                    source,
                    imported_targets.get(reference.source_file_id, set()),
                )
            bound = self._bound_reference(
                reference,
                target=target,
                source=source,
                match=match,
            )
            existing = rebound.get(bound.reference_id)
            if existing is not None and existing != bound:
                raise ValueError(
                    "reference resolution produced conflicting identities"
                )
            rebound[bound.reference_id] = bound
        return tuple(
            rebound[identity] for identity in sorted(rebound)
        )

    def resolve_name(
        self,
        target: str,
        *,
        source_symbol_id: str | None = None,
        imported_symbol_ids: Iterable[str] = (),
    ) -> ResolutionMatch:
        source = (
            self._symbols_by_id.get(source_symbol_id)
            if source_symbol_id
            else None
        )
        synthetic = _synthetic_reference(source_symbol_id, target)
        scoped = self._resolve_scoped(synthetic, target, source)
        if scoped.resolved:
            return scoped
        return self._resolve_heuristic(
            target,
            source,
            set(imported_symbol_ids),
        )

    def _resolve_scoped(
        self,
        reference: SymbolReference,
        target: str,
        source: Symbol | None,
    ) -> ResolutionMatch:
        if (
            reference.target_symbol_id
            and reference.target_symbol_id in self._symbols_by_id
        ):
            return ResolutionMatch(
                target_symbol_id=reference.target_symbol_id,
                confidence=1.0,
                method="existing",
                explanation=(
                    "The parser target identity exists in the current snapshot."
                ),
            )
        exact = self._unique_qualified(target, source=source)
        if exact is not None:
            return _match(
                exact,
                confidence=1.0,
                method="exact_qualified",
                explanation="A unique exact qualified name matched.",
            )

        module = (
            self._modules_by_id.get(source.module_id)
            if source is not None and source.module_id
            else None
        )
        if module is not None:
            relative = _relative_import_name(module.qualified_name, target)
            if relative is not None:
                matched = self._unique_qualified(relative, source=source)
                if matched is not None:
                    return _match(
                        matched,
                        confidence=0.9,
                        method="relative_import",
                        explanation=(
                            "A unique module-relative qualified name matched."
                        ),
                    )
            if not target.startswith("."):
                matched = self._unique_qualified(
                    f"{module.qualified_name}.{target}",
                    source=source,
                )
                if matched is not None:
                    return _match(
                        matched,
                        confidence=0.95,
                        method="module_scope",
                        explanation=(
                            "A unique symbol in the source module matched."
                        ),
                    )
        if source is not None and not target.startswith("."):
            parts = source.qualified_name.split(".")
            for end in range(len(parts) - 1, 0, -1):
                matched = self._unique_qualified(
                    f"{'.'.join(parts[:end])}.{target}",
                    source=source,
                )
                if matched is not None:
                    return _match(
                        matched,
                        confidence=0.9,
                        method="enclosing_scope",
                        explanation=(
                            "A unique symbol in an enclosing scope matched."
                        ),
                    )
        return _unresolved()

    def _resolve_heuristic(
        self,
        target: str,
        source: Symbol | None,
        imported_symbol_ids: set[str],
    ) -> ResolutionMatch:
        simple_name = target.rsplit(".", 1)[-1]
        imported = tuple(
            sorted(
                (
                    self._symbols_by_id[identity]
                    for identity in imported_symbol_ids
                    if identity in self._symbols_by_id
                    and self._symbols_by_id[identity].name == simple_name
                    and (
                        source is None
                        or self._symbols_by_id[identity].language
                        == source.language
                    )
                ),
                key=lambda item: item.symbol_id,
            )
        )
        if len(imported) == 1:
            return _match(
                imported[0],
                confidence=0.8,
                method="imported_name",
                explanation=(
                    "A unique explicitly imported symbol name matched."
                ),
            )

        named = tuple(
            item
            for item in self._by_name.get(simple_name, ())
            if source is None or item.language == source.language
        )
        if source is not None and source.project_id is not None:
            project_matches = tuple(
                item
                for item in named
                if item.project_id == source.project_id
            )
            if len(project_matches) == 1:
                return _match(
                    project_matches[0],
                    confidence=0.6,
                    method="unique_project_name",
                    explanation=(
                        "A unique same-project symbol name matched "
                        "heuristically."
                    ),
                )
        if len(named) == 1:
            return _match(
                named[0],
                confidence=0.55,
                method="unique_repository_name",
                explanation=(
                    "A unique repository symbol name matched heuristically."
                ),
            )
        return ResolutionMatch(
            target_symbol_id=None,
            confidence=0.0,
            method="ambiguous" if named else "unresolved",
            explanation=(
                "No unique bounded static target could be established."
            ),
            candidate_ids=tuple(
                item.symbol_id for item in named[:32]
            ),
        )

    def _source_symbol(
        self,
        reference: SymbolReference,
    ) -> Symbol | None:
        return (
            self._symbols_by_id.get(reference.source_symbol_id)
            if reference.source_symbol_id
            else None
        )

    def _target_text(
        self,
        reference: SymbolReference,
        previous_by_id: dict[str, Symbol],
    ) -> str:
        current = (
            self._symbols_by_id.get(reference.target_symbol_id)
            if reference.target_symbol_id
            else None
        )
        previous = (
            previous_by_id.get(reference.target_symbol_id)
            if reference.target_symbol_id
            else None
        )
        return (
            reference.unresolved_target
            or (current.qualified_name if current is not None else None)
            or (previous.qualified_name if previous is not None else None)
            or "unresolved"
        )

    def _bound_reference(
        self,
        reference: SymbolReference,
        *,
        target: str,
        source: Symbol | None,
        match: ResolutionMatch,
    ) -> SymbolReference:
        explanation = reference.explanation
        if match.resolved:
            if (
                match.method != "existing"
                or " Resolution: " not in explanation
            ):
                parser_explanation = explanation.split(
                    " Resolution: ",
                    maxsplit=1,
                )[0]
                explanation = (
                    f"{parser_explanation} Resolution: {match.explanation}"
                )[:2_048]
        return SymbolReference(
            reference_id=reference.reference_id,
            source_symbol_id=source.symbol_id if source is not None else None,
            source_file_id=reference.source_file_id,
            target_symbol_id=match.target_symbol_id,
            unresolved_target=None if match.resolved else target,
            kind=reference.kind,
            location=reference.location,
            confidence=(
                min(reference.confidence, match.confidence)
                if match.resolved
                else reference.confidence
            ),
            explanation=explanation,
        )

    def _unique_qualified(
        self,
        name: str,
        *,
        source: Symbol | None,
    ) -> Symbol | None:
        matches = tuple(
            item
            for item in self._by_qualified_name.get(name, ())
            if source is None or item.language == source.language
        )
        return matches[0] if len(matches) == 1 else None


def _match(
    symbol: Symbol,
    *,
    confidence: float,
    method: str,
    explanation: str,
) -> ResolutionMatch:
    return ResolutionMatch(
        target_symbol_id=symbol.symbol_id,
        confidence=confidence,
        method=method,
        explanation=explanation,
        candidate_ids=(symbol.symbol_id,),
    )


def _unresolved() -> ResolutionMatch:
    return ResolutionMatch(
        target_symbol_id=None,
        confidence=0.0,
        method="unresolved",
        explanation="No unique bounded static target could be established.",
    )


def _relative_import_name(
    module_name: str,
    target: str,
) -> str | None:
    if not target.startswith("."):
        return None
    level = len(target) - len(target.lstrip("."))
    remainder = target[level:]
    module_parts = module_name.split(".")
    if level > len(module_parts):
        return None
    base = module_parts[:-level]
    parts = [*base, *remainder.split(".")] if remainder else base
    return ".".join(part for part in parts if part) or None


def _synthetic_reference(
    source_symbol_id: str | None,
    target: str,
) -> SymbolReference:
    source_file_id = stable_id("file", "resolver-query")
    return SymbolReference(
        reference_id=stable_id("reference", "resolver-query", target),
        source_symbol_id=source_symbol_id,
        source_file_id=source_file_id,
        unresolved_target=target,
        location=SymbolLocation(
            relative_path="resolver-query.txt",
            start_line=1,
            start_column=0,
            end_line=1,
            end_column=0,
        ),
        confidence=1.0,
        explanation="Synthetic bounded resolution query.",
    )
