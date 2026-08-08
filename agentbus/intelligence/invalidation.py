from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from enum import Enum

from agentbus.intelligence.models import (
    SourceFile,
    Symbol,
    SymbolReference,
    _relative_path,
)


class InvalidationCause(str, Enum):
    CONTENT_CHANGED = "content_changed"
    DELETED = "deleted"
    RENAMED = "renamed"
    PARSER_VERSION_CHANGED = "parser_version_changed"
    CONFIGURATION_CHANGED = "configuration_changed"
    DEPENDENCY_CHANGED = "dependency_changed"


@dataclass(frozen=True)
class InvalidationLimits:
    maximum_depth: int = 8
    maximum_files: int = 10_000
    maximum_references: int = 100_000

    def __post_init__(self) -> None:
        _bounded(self.maximum_depth, "maximum_depth", 1, 64)
        _bounded(self.maximum_files, "maximum_files", 1, 1_000_000)
        _bounded(
            self.maximum_references,
            "maximum_references",
            1,
            2_000_000,
        )


@dataclass(frozen=True, order=True)
class InvalidationReason:
    relative_path: str
    cause: InvalidationCause
    depth: int = 0
    source_path: str | None = None
    reference_id: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "relative_path",
            _relative_path(self.relative_path),
        )
        if self.source_path is not None:
            object.__setattr__(
                self,
                "source_path",
                _relative_path(self.source_path),
            )
        if self.depth < 0 or self.depth > 64:
            raise ValueError("invalidation reason depth is out of bounds")


@dataclass(frozen=True)
class InvalidationPlan:
    direct_paths: tuple[str, ...]
    dependent_paths: tuple[str, ...]
    reasons: tuple[InvalidationReason, ...]
    inspected_references: int
    truncated: bool = False
    requires_full_reindex: bool = False

    @property
    def all_paths(self) -> tuple[str, ...]:
        return tuple(
            sorted({*self.direct_paths, *self.dependent_paths})
        )

    def reason_for(self, relative_path: str) -> InvalidationReason | None:
        normalized = _relative_path(relative_path)
        return next(
            (
                reason
                for reason in self.reasons
                if reason.relative_path == normalized
            ),
            None,
        )


class DependencyInvalidator:
    """Plan bounded reverse-dependency invalidation from a prior snapshot."""

    def __init__(
        self,
        *,
        limits: InvalidationLimits | None = None,
    ) -> None:
        self.limits = limits or InvalidationLimits()

    def plan(
        self,
        files: Iterable[SourceFile],
        symbols: Iterable[Symbol],
        references: Iterable[SymbolReference],
        *,
        changed_paths: Iterable[str] = (),
        deleted_paths: Iterable[str] = (),
        renamed_paths: Iterable[tuple[str, str]] = (),
        parser_names: Iterable[str] = (),
        project_ids: Iterable[str] = (),
        changed_qualified_names: Iterable[str] = (),
    ) -> InvalidationPlan:
        file_records = tuple(
            sorted(files, key=lambda item: item.file_id)
        )
        symbol_records = tuple(
            sorted(symbols, key=lambda item: item.symbol_id)
        )
        reference_records = tuple(
            sorted(references, key=lambda item: item.reference_id)
        )
        files_by_id = {
            source.file_id: source for source in file_records
        }
        symbols_by_file: dict[str, set[str]] = defaultdict(set)
        symbols_by_id: dict[str, Symbol] = {}
        for symbol in symbol_records:
            symbols_by_id[symbol.symbol_id] = symbol
            symbols_by_file[symbol.file_id].add(symbol.symbol_id)

        direct_reasons: dict[str, InvalidationReason] = {}
        for path in _normalized_paths(changed_paths):
            direct_reasons[path] = InvalidationReason(
                relative_path=path,
                cause=InvalidationCause.CONTENT_CHANGED,
            )
        for path in _normalized_paths(deleted_paths):
            direct_reasons[path] = InvalidationReason(
                relative_path=path,
                cause=InvalidationCause.DELETED,
            )
        for source, target in _normalized_renames(renamed_paths):
            direct_reasons[source] = InvalidationReason(
                relative_path=source,
                cause=InvalidationCause.RENAMED,
                source_path=target,
            )
            direct_reasons[target] = InvalidationReason(
                relative_path=target,
                cause=InvalidationCause.RENAMED,
                source_path=source,
            )

        invalid_parser_names = {
            str(name) for name in parser_names if str(name)
        }
        invalid_project_ids = {
            str(identity) for identity in project_ids if str(identity)
        }
        for source in file_records:
            if source.parser_name in invalid_parser_names:
                direct_reasons.setdefault(
                    source.relative_path,
                    InvalidationReason(
                        relative_path=source.relative_path,
                        cause=InvalidationCause.PARSER_VERSION_CHANGED,
                    ),
                )
            if (
                source.project_id is not None
                and source.project_id in invalid_project_ids
            ):
                direct_reasons[source.relative_path] = InvalidationReason(
                    relative_path=source.relative_path,
                    cause=InvalidationCause.CONFIGURATION_CHANGED,
                )

        direct_paths = tuple(sorted(direct_reasons))
        if len(direct_paths) > self.limits.maximum_files:
            bounded_paths = direct_paths[: self.limits.maximum_files]
            return InvalidationPlan(
                direct_paths=bounded_paths,
                dependent_paths=(),
                reasons=tuple(
                    direct_reasons[path] for path in bounded_paths
                ),
                inspected_references=0,
                truncated=True,
                requires_full_reindex=True,
            )

        truncated = (
            len(reference_records) > self.limits.maximum_references
        )
        bounded_references = reference_records[
            : self.limits.maximum_references
        ]
        reverse_resolved: dict[str, list[SymbolReference]] = defaultdict(list)
        reverse_unresolved: dict[str, list[SymbolReference]] = defaultdict(list)
        for reference in bounded_references:
            if reference.target_symbol_id is not None:
                reverse_resolved[reference.target_symbol_id].append(reference)
            elif reference.unresolved_target is not None:
                reverse_unresolved[reference.unresolved_target].append(
                    reference
                )

        paths_by_file_id = {
            source.file_id: source.relative_path
            for source in file_records
        }
        known_paths = set(paths_by_file_id.values())
        direct_file_ids = {
            source.file_id
            for source in file_records
            if source.relative_path in direct_reasons
        }
        frontier_symbols = {
            symbol_id
            for file_identity in direct_file_ids
            for symbol_id in symbols_by_file.get(file_identity, ())
        }
        frontier_names = {
            symbols_by_id[identity].qualified_name
            for identity in frontier_symbols
        }
        frontier_names.update(
            name
            for name in changed_qualified_names
            if isinstance(name, str) and name
        )
        visited_paths = set(direct_paths)
        dependent_reasons: dict[str, InvalidationReason] = {}
        inspected_reference_ids: set[str] = set()
        depth = 0

        while frontier_symbols or frontier_names:
            depth += 1
            if depth > self.limits.maximum_depth:
                truncated = True
                break
            candidates: dict[str, tuple[SymbolReference, str]] = {}
            for identity in sorted(frontier_symbols):
                for reference in reverse_resolved.get(identity, ()):
                    candidates.setdefault(
                        reference.reference_id,
                        (reference, symbols_by_id[identity].location.relative_path),
                    )
            for name in sorted(frontier_names):
                for reference in reverse_unresolved.get(name, ()):
                    candidates.setdefault(
                        reference.reference_id,
                        (reference, name),
                    )

            next_file_ids: set[str] = set()
            for reference, target_source in (
                candidates[identity]
                for identity in sorted(candidates)
            ):
                inspected_reference_ids.add(reference.reference_id)
                source_path = paths_by_file_id.get(
                    reference.source_file_id
                )
                if source_path is None or source_path in visited_paths:
                    continue
                if len(visited_paths) >= self.limits.maximum_files:
                    truncated = True
                    break
                visited_paths.add(source_path)
                next_file_ids.add(reference.source_file_id)
                dependent_reasons[source_path] = InvalidationReason(
                    relative_path=source_path,
                    cause=InvalidationCause.DEPENDENCY_CHANGED,
                    depth=depth,
                    source_path=(
                        target_source if target_source in known_paths else None
                    ),
                    reference_id=reference.reference_id,
                )
            if truncated:
                break
            frontier_symbols = {
                identity
                for file_identity in next_file_ids
                for identity in symbols_by_file.get(file_identity, ())
            }
            frontier_names = {
                symbols_by_id[identity].qualified_name
                for identity in frontier_symbols
            }

        reasons = tuple(
            sorted(
                (*direct_reasons.values(), *dependent_reasons.values()),
                key=lambda item: (
                    item.depth,
                    item.relative_path,
                    item.cause.value,
                ),
            )
        )
        return InvalidationPlan(
            direct_paths=direct_paths,
            dependent_paths=tuple(sorted(dependent_reasons)),
            reasons=reasons,
            inspected_references=len(inspected_reference_ids),
            truncated=truncated,
            requires_full_reindex=truncated,
        )


def _normalized_paths(paths: Iterable[str]) -> tuple[str, ...]:
    return tuple(sorted({_relative_path(path) for path in paths}))


def _normalized_renames(
    renames: Iterable[tuple[str, str]],
) -> tuple[tuple[str, str], ...]:
    normalized = {
        (_relative_path(source), _relative_path(target))
        for source, target in renames
    }
    return tuple(sorted(normalized))


def _bounded(
    value: int,
    name: str,
    minimum: int,
    maximum: int,
) -> None:
    if value < minimum or value > maximum:
        raise ValueError(f"{name} must be between {minimum} and {maximum}")
