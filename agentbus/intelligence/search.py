from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import PurePosixPath

from agentbus.intelligence.errors import QueryLimitError
from agentbus.intelligence.models import (
    Module,
    Project,
    SearchQuery,
    SearchResult,
    SourceFile,
    Symbol,
    SymbolKind,
)
from agentbus.intelligence.storage import IndexStore


_CAMEL_BOUNDARY = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")
_WORD = re.compile(r"[^\W_]+", re.UNICODE)
_FIELD_WEIGHTS = {
    "identifier": 12.0,
    "qualified_name": 10.0,
    "endpoint": 11.0,
    "path": 8.0,
    "module": 7.0,
    "signature": 6.0,
    "project": 5.0,
    "kind": 4.0,
    "configuration_key": 4.0,
    "documentation": 3.0,
}


@dataclass(frozen=True)
class LexicalSearchLimits:
    maximum_documents: int = 200_000
    maximum_query_terms: int = 64
    maximum_field_characters: int = 16_384
    maximum_values_per_field: int = 256

    def __post_init__(self) -> None:
        _bounded(
            self.maximum_documents,
            "maximum_documents",
            1,
            1_000_000,
        )
        _bounded(
            self.maximum_query_terms,
            "maximum_query_terms",
            1,
            256,
        )
        _bounded(
            self.maximum_field_characters,
            "maximum_field_characters",
            128,
            65_536,
        )
        _bounded(
            self.maximum_values_per_field,
            "maximum_values_per_field",
            1,
            1_024,
        )


@dataclass(frozen=True)
class _SearchField:
    name: str
    raw_values: tuple[str, ...]
    normalized_values: tuple[str, ...]
    token_sets: tuple[frozenset[str], ...]

    def contains_token(self, token: str) -> bool:
        return any(token in tokens for tokens in self.token_sets)

    def contains_phrase(self, phrase: str) -> bool:
        return any(phrase in value for value in self.normalized_values)

    def exact(self, value: str) -> bool:
        return any(value == item.casefold() for item in self.raw_values)


@dataclass(frozen=True)
class _LexicalDocument:
    source: SourceFile
    project: Project | None
    symbol: Symbol | None
    fields: tuple[_SearchField, ...]
    test: bool
    public: bool

    @property
    def identity(self) -> str:
        return (
            self.symbol.symbol_id
            if self.symbol is not None
            else self.source.file_id
        )


@dataclass(frozen=True)
class _QueryTerms:
    exact: str
    tokens: tuple[str, ...]
    phrases: tuple[str, ...]


@dataclass(frozen=True)
class _ScoredDocument:
    document: _LexicalDocument
    score: float
    components: dict[str, float]
    matched_terms: tuple[str, ...]


class RepositoryLexicalIndex:
    """Bounded deterministic search over one persisted index snapshot."""

    def __init__(
        self,
        projects: Iterable[Project],
        files: Iterable[SourceFile],
        modules: Iterable[Module],
        symbols: Iterable[Symbol],
        *,
        snapshot_id: str | None = None,
        limits: LexicalSearchLimits | None = None,
    ) -> None:
        self.snapshot_id = snapshot_id
        self.limits = limits or LexicalSearchLimits()
        self._documents = self._build_documents(
            tuple(projects),
            tuple(files),
            tuple(modules),
            tuple(symbols),
        )

    @classmethod
    def from_store(
        cls,
        store: IndexStore,
        snapshot_id: str,
        *,
        limits: LexicalSearchLimits | None = None,
    ) -> RepositoryLexicalIndex:
        store.get_snapshot(snapshot_id)
        return cls(
            store.list_projects(snapshot_id),
            store.list_files(snapshot_id),
            store.list_modules(snapshot_id),
            store.list_symbols(snapshot_id),
            snapshot_id=snapshot_id,
            limits=limits,
        )

    @property
    def document_count(self) -> int:
        return len(self._documents)

    def search(
        self,
        query: SearchQuery,
        *,
        stale: bool = False,
    ) -> tuple[SearchResult, ...]:
        query = SearchQuery.model_validate(query.model_dump(mode="python"))
        terms = _query_terms(query.text, self.limits.maximum_query_terms)
        scored: list[_ScoredDocument] = []
        for document in self._documents:
            if not _matches_filters(document, query):
                continue
            match = _score(document, terms)
            if match is not None:
                scored.append(match)
        scored.sort(
            key=lambda item: (
                -item.score,
                item.document.source.relative_path.casefold(),
                (
                    item.document.symbol.qualified_name.casefold()
                    if item.document.symbol is not None
                    else ""
                ),
                item.document.identity,
            )
        )
        selected = scored[query.offset : query.offset + query.limit]
        return tuple(
            SearchResult(
                rank=query.offset + index + 1,
                score=item.score,
                score_components=item.components,
                matched_terms=item.matched_terms,
                relative_path=item.document.source.relative_path,
                source_hash=item.document.source.content_hash,
                project_id=item.document.source.project_id,
                symbol=item.document.symbol,
                stale=stale,
                explanation=_explanation(item.components),
            )
            for index, item in enumerate(selected)
        )

    def _build_documents(
        self,
        projects: tuple[Project, ...],
        files: tuple[SourceFile, ...],
        modules: tuple[Module, ...],
        symbols: tuple[Symbol, ...],
    ) -> tuple[_LexicalDocument, ...]:
        project_by_id = {item.project_id: item for item in projects}
        modules_by_path: dict[str, list[Module]] = defaultdict(list)
        for module in modules:
            modules_by_path[module.relative_path].append(module)
        symbols_by_file: dict[str, list[Symbol]] = defaultdict(list)
        for symbol in symbols:
            symbols_by_file[symbol.file_id].append(symbol)

        documents: list[_LexicalDocument] = []
        for source in sorted(files, key=lambda item: item.file_id):
            if source.protected:
                continue
            project = (
                project_by_id.get(source.project_id)
                if source.project_id is not None
                else None
            )
            source_modules = tuple(
                sorted(
                    modules_by_path[source.relative_path],
                    key=lambda item: item.module_id,
                )
            )
            documents.append(
                self._document(source, project, source_modules, None)
            )
            for symbol in sorted(
                symbols_by_file[source.file_id],
                key=lambda item: item.symbol_id,
            ):
                documents.append(
                    self._document(
                        source,
                        project,
                        source_modules,
                        symbol,
                    )
                )
                self._check_document_limit(documents)
            self._check_document_limit(documents)
        return tuple(documents)

    def _document(
        self,
        source: SourceFile,
        project: Project | None,
        modules: tuple[Module, ...],
        symbol: Symbol | None,
    ) -> _LexicalDocument:
        path = PurePosixPath(source.relative_path)
        values: dict[str, tuple[str, ...]] = {
            "path": (source.relative_path,),
            "identifier": (path.name, path.stem),
            "project": (
                (project.name, project.root)
                if project is not None
                else ()
            ),
            "module": tuple(
                value
                for module in modules
                for value in (module.name, module.qualified_name)
            ),
        }
        public = any(module.public for module in modules)
        test = source.test
        if symbol is not None:
            values.update(
                {
                    "identifier": (symbol.name,),
                    "qualified_name": (symbol.qualified_name,),
                    "signature": (
                        (symbol.signature,)
                        if symbol.signature is not None
                        else ()
                    ),
                    "documentation": (
                        (symbol.documentation,)
                        if symbol.documentation is not None
                        else ()
                    ),
                    "endpoint": (
                        (symbol.endpoint,)
                        if symbol.endpoint is not None
                        else ()
                    ),
                    "kind": (symbol.kind.value,),
                    "configuration_key": tuple(
                        sorted(symbol.attributes)
                    ),
                }
            )
            public = public or symbol.exported
            test = test or symbol.test or symbol.kind == SymbolKind.TEST
        fields = tuple(
            self._field(name, field_values)
            for name, field_values in sorted(values.items())
            if field_values
        )
        return _LexicalDocument(
            source=source,
            project=project,
            symbol=symbol,
            fields=fields,
            test=test,
            public=public,
        )

    def _field(
        self,
        name: str,
        values: tuple[str, ...],
    ) -> _SearchField:
        bounded_values = tuple(
            value[: self.limits.maximum_field_characters]
            for value in values[: self.limits.maximum_values_per_field]
            if value
        )
        return _SearchField(
            name=name,
            raw_values=bounded_values,
            normalized_values=tuple(
                _normalized_phrase(value) for value in bounded_values
            ),
            token_sets=tuple(
                frozenset(_tokens(value)) for value in bounded_values
            ),
        )

    def _check_document_limit(
        self,
        documents: list[_LexicalDocument],
    ) -> None:
        if len(documents) > self.limits.maximum_documents:
            raise QueryLimitError(
                "Lexical index document count exceeded the configured limit."
            )


def _query_terms(text: str, maximum_terms: int) -> _QueryTerms:
    stripped = text.strip()
    phrases = tuple(
        dict.fromkeys(
            phrase
            for phrase in (
                _normalized_phrase(value)
                for value in re.findall(r'"([^"]+)"', stripped)
            )
            if phrase
        )
    )
    tokens = tuple(dict.fromkeys(_tokens(stripped.replace('"', " "))))
    if len(tokens) > maximum_terms:
        raise QueryLimitError(
            "Lexical query term count exceeded the configured limit."
        )
    exact = stripped.casefold()
    if len(stripped) >= 2 and stripped.startswith('"') and stripped.endswith('"'):
        exact = stripped[1:-1].strip().casefold()
    return _QueryTerms(exact=exact, tokens=tokens, phrases=phrases)


def _score(
    document: _LexicalDocument,
    query: _QueryTerms,
) -> _ScoredDocument | None:
    fields = {item.name: item for item in document.fields}
    if query.phrases and not all(
        any(field.contains_phrase(phrase) for field in document.fields)
        for phrase in query.phrases
    ):
        return None

    components: dict[str, float] = {}
    _exact_score(components, fields, query.exact)
    phrase_matches = sum(
        1
        for phrase in query.phrases
        if any(field.contains_phrase(phrase) for field in document.fields)
    )
    if phrase_matches:
        components["phrase"] = 30.0 * phrase_matches

    matched_terms: list[str] = []
    token_score = 0.0
    for token in query.tokens:
        matches = tuple(
            _FIELD_WEIGHTS[field.name]
            for field in document.fields
            if field.contains_token(token)
        )
        if not matches:
            continue
        matched_terms.append(token)
        token_score += max(matches)
    if query.tokens and len(matched_terms) != len(query.tokens):
        return None
    if token_score:
        components["tokens"] = token_score

    kind = document.symbol.kind if document.symbol is not None else None
    if kind is not None and kind.value in query.tokens:
        components["symbol_kind"] = 8.0
    if document.test and "test" in query.tokens:
        components["test"] = 4.0
    if document.public and components:
        components["public_api"] = 2.0
    if not components:
        return None
    score = round(sum(components.values()), 6)
    return _ScoredDocument(
        document=document,
        score=score,
        components=dict(sorted(components.items())),
        matched_terms=tuple(matched_terms),
    )


def _exact_score(
    components: dict[str, float],
    fields: dict[str, _SearchField],
    query: str,
) -> None:
    if not query:
        return
    exact_weights = (
        ("identifier", "exact_identifier", 120.0),
        ("qualified_name", "exact_qualified_name", 105.0),
        ("endpoint", "exact_endpoint", 100.0),
        ("path", "exact_path", 95.0),
        ("project", "exact_project", 60.0),
        ("module", "exact_module", 60.0),
    )
    for field_name, component_name, weight in exact_weights:
        field = fields.get(field_name)
        if field is not None and field.exact(query):
            components[component_name] = weight


def _matches_filters(
    document: _LexicalDocument,
    query: SearchQuery,
) -> bool:
    source = document.source
    symbol = document.symbol
    if source.protected:
        return False
    if query.project_ids and source.project_id not in query.project_ids:
        return False
    if query.languages and source.language not in query.languages:
        return False
    if query.symbol_kinds and (
        symbol is None or symbol.kind not in query.symbol_kinds
    ):
        return False
    if query.path_prefixes and not any(
        _under_prefix(source.relative_path, prefix)
        for prefix in query.path_prefixes
    ):
        return False
    if query.test_only and not document.test:
        return False
    return True


def _under_prefix(relative_path: str, prefix: str) -> bool:
    return (
        not prefix
        or relative_path == prefix
        or relative_path.startswith(f"{prefix}/")
    )


def _tokens(value: str) -> tuple[str, ...]:
    separated = _CAMEL_BOUNDARY.sub(" ", value.replace("_", " "))
    return tuple(match.group(0).casefold() for match in _WORD.finditer(separated))


def _normalized_phrase(value: str) -> str:
    return " ".join(_tokens(value))


def _explanation(components: dict[str, float]) -> str:
    ordered = sorted(
        components.items(),
        key=lambda item: (-item[1], item[0]),
    )
    details = ", ".join(
        f"{name.replace('_', ' ')}={score:g}"
        for name, score in ordered
    )
    return f"Deterministic lexical ranking: {details}."


def _bounded(
    value: int,
    name: str,
    minimum: int,
    maximum: int,
) -> None:
    if value < minimum or value > maximum:
        raise ValueError(f"{name} must be between {minimum} and {maximum}")
