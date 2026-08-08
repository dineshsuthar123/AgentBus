from __future__ import annotations

import math
import re
from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Protocol

from agentbus.intelligence.cache import CacheEntry, IntelligenceCache
from agentbus.intelligence.errors import QueryLimitError
from agentbus.intelligence.identities import stable_hash
from agentbus.intelligence.models import (
    Project,
    SearchQuery,
    SearchResult,
    SourceFile,
    Symbol,
    SymbolKind,
)


_PROVIDER_COMPONENT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]*$")


@dataclass(frozen=True)
class EmbeddingProviderDescriptor:
    provider_name: str
    model_name: str
    version: str
    dimensions: int
    local: bool
    sends_source_off_device: bool = False

    def __post_init__(self) -> None:
        for name, value in (
            ("provider_name", self.provider_name),
            ("model_name", self.model_name),
            ("version", self.version),
        ):
            if (
                not value
                or len(value) > 256
                or not _PROVIDER_COMPONENT.fullmatch(value)
            ):
                raise ValueError(f"{name} contains unsupported characters")
        _bounded(self.dimensions, "dimensions", 1, 8_192)

    @property
    def fingerprint(self) -> str:
        return stable_hash(
            {
                "provider": self.provider_name,
                "model": self.model_name,
                "version": self.version,
                "dimensions": self.dimensions,
                "local": self.local,
                "sends_source_off_device": self.sends_source_off_device,
            }
        )


class SemanticEmbeddingProvider(Protocol):
    @property
    def descriptor(self) -> EmbeddingProviderDescriptor:
        ...

    def embed(
        self,
        texts: tuple[str, ...],
    ) -> Sequence[Sequence[float]]:
        ...


@dataclass(frozen=True)
class SemanticSearchConfig:
    enabled: bool = False
    maximum_chunk_characters: int = 2_048
    maximum_chunks_per_document: int = 8
    maximum_total_chunks: int = 200_000
    maximum_batch_size: int = 64
    minimum_similarity: float = 0.0

    def __post_init__(self) -> None:
        _bounded(
            self.maximum_chunk_characters,
            "maximum_chunk_characters",
            128,
            16_384,
        )
        _bounded(
            self.maximum_chunks_per_document,
            "maximum_chunks_per_document",
            1,
            64,
        )
        _bounded(
            self.maximum_total_chunks,
            "maximum_total_chunks",
            1,
            1_000_000,
        )
        _bounded(
            self.maximum_batch_size,
            "maximum_batch_size",
            1,
            1_024,
        )
        if self.minimum_similarity < 0 or self.minimum_similarity > 1:
            raise ValueError("minimum_similarity must be between 0 and 1")


@dataclass(frozen=True)
class SemanticIndexStatus:
    enabled: bool
    available: bool
    model_fingerprint: str | None
    document_count: int
    message: str


@dataclass(frozen=True)
class _SemanticDocument:
    document_id: str
    cache_key: str
    source: SourceFile
    symbol: Symbol | None
    text: str
    chunk_index: int
    test: bool

    @property
    def result_identity(self) -> str:
        return (
            self.symbol.symbol_id
            if self.symbol is not None
            else self.source.file_id
        )


@dataclass(frozen=True)
class _EmbeddedDocument:
    document: _SemanticDocument
    vector: tuple[float, ...]


@dataclass(frozen=True)
class _SemanticMatch:
    entry: _EmbeddedDocument
    similarity: float


class OptionalSemanticSearch:
    """Opt-in local semantic search with metadata-only persistence."""

    def __init__(
        self,
        *,
        provider: SemanticEmbeddingProvider | None = None,
        config: SemanticSearchConfig | None = None,
        cache: IntelligenceCache | None = None,
        repository_id: str | None = None,
    ) -> None:
        if (cache is None) != (repository_id is None):
            raise ValueError(
                "semantic cache and repository_id must be configured together"
            )
        self.provider = provider
        self.config = config or SemanticSearchConfig()
        self.cache = cache
        self.repository_id = repository_id
        self._entries: tuple[_EmbeddedDocument, ...] = ()
        self._failure_message: str | None = None

    @property
    def status(self) -> SemanticIndexStatus:
        reason = self._unavailable_reason()
        descriptor = self.provider.descriptor if self.provider else None
        return SemanticIndexStatus(
            enabled=self.config.enabled,
            available=reason is None,
            model_fingerprint=(
                descriptor.fingerprint if descriptor is not None else None
            ),
            document_count=len(self._entries),
            message=reason or "Local semantic search is available.",
        )

    def build(
        self,
        projects: Iterable[Project],
        files: Iterable[SourceFile],
        symbols: Iterable[Symbol],
        *,
        snapshot_id: str | None = None,
    ) -> SemanticIndexStatus:
        self._failure_message = None
        reason = self._configuration_reason()
        if reason is not None:
            self._entries = ()
            return self.status
        documents = self._documents(
            tuple(projects),
            tuple(files),
            tuple(symbols),
        )
        previous_keys = {
            entry.document.cache_key for entry in self._entries
        }
        persisted_keys: set[str] = set()
        try:
            vectors = self._embed_documents(documents)
            entries = tuple(
                _EmbeddedDocument(document=document, vector=vector)
                for document, vector in zip(
                    documents,
                    vectors,
                    strict=True,
                )
            )
            for entry in entries:
                self._persist(entry, snapshot_id=snapshot_id)
                persisted_keys.add(entry.document.cache_key)
        except Exception:
            for cache_key in persisted_keys:
                self._delete_cache_entry(cache_key)
            self._entries = ()
            self._failure_message = (
                "Local semantic provider failed; lexical retrieval remains available."
            )
            return self.status

        for cache_key in previous_keys.difference(persisted_keys):
            self._delete_cache_entry(cache_key)
        self._entries = entries
        return self.status

    def search(
        self,
        query: SearchQuery,
        *,
        stale: bool = False,
    ) -> tuple[SearchResult, ...]:
        if self._unavailable_reason() is not None or not self._entries:
            return ()
        query = SearchQuery.model_validate(query.model_dump(mode="python"))
        try:
            vector = self._embed_batch((query.text,))[0]
        except Exception:
            self._failure_message = (
                "Local semantic query failed; lexical retrieval remains available."
            )
            return ()
        best_by_identity: dict[str, _SemanticMatch] = {}
        for entry in self._entries:
            if not _matches_filters(entry.document, query):
                continue
            similarity = sum(
                left * right
                for left, right in zip(
                    vector,
                    entry.vector,
                    strict=True,
                )
            )
            if (
                similarity <= 0
                or similarity < self.config.minimum_similarity
            ):
                continue
            match = _SemanticMatch(entry=entry, similarity=similarity)
            identity = entry.document.result_identity
            current = best_by_identity.get(identity)
            if current is None or _match_key(match) < _match_key(current):
                best_by_identity[identity] = match
        matches = sorted(best_by_identity.values(), key=_match_key)
        selected = matches[query.offset : query.offset + query.limit]
        fingerprint = self.provider.descriptor.fingerprint
        return tuple(
            SearchResult(
                rank=query.offset + index + 1,
                score=round(max(0.0, match.similarity) * 100.0, 6),
                score_components={
                    "semantic": round(
                        max(0.0, match.similarity) * 100.0,
                        6,
                    )
                },
                relative_path=match.entry.document.source.relative_path,
                source_hash=match.entry.document.source.content_hash,
                project_id=match.entry.document.source.project_id,
                symbol=match.entry.document.symbol,
                stale=stale,
                explanation=(
                    "Local semantic similarity from model fingerprint "
                    f"{fingerprint}."
                ),
            )
            for index, match in enumerate(selected)
        )

    def invalidate_stale(
        self,
        files: Iterable[SourceFile],
    ) -> int:
        current_hashes = {
            item.file_id: item.content_hash
            for item in files
            if not item.protected
        }
        retained: list[_EmbeddedDocument] = []
        removed: list[_EmbeddedDocument] = []
        for entry in self._entries:
            if (
                current_hashes.get(entry.document.source.file_id)
                == entry.document.source.content_hash
            ):
                retained.append(entry)
            else:
                removed.append(entry)
        for cache_key in {
            item.document.cache_key for item in removed
        }:
            self._delete_cache_entry(cache_key)
        self._entries = tuple(retained)
        return len(removed)

    def _documents(
        self,
        projects: tuple[Project, ...],
        files: tuple[SourceFile, ...],
        symbols: tuple[Symbol, ...],
    ) -> tuple[_SemanticDocument, ...]:
        project_by_id = {item.project_id: item for item in projects}
        symbols_by_file: dict[str, list[Symbol]] = defaultdict(list)
        for symbol in symbols:
            symbols_by_file[symbol.file_id].append(symbol)
        documents: list[_SemanticDocument] = []
        for source in sorted(files, key=lambda item: item.file_id):
            if source.protected:
                continue
            project = (
                project_by_id.get(source.project_id)
                if source.project_id is not None
                else None
            )
            base_parts = [source.relative_path]
            if project is not None:
                base_parts.extend((project.name, project.root))
            self._append_chunks(
                documents,
                source,
                None,
                "\n".join(base_parts),
            )
            for symbol in sorted(
                symbols_by_file[source.file_id],
                key=lambda item: item.symbol_id,
            ):
                parts = [
                    source.relative_path,
                    symbol.name,
                    symbol.qualified_name,
                    symbol.kind.value,
                ]
                for value in (
                    symbol.signature,
                    symbol.documentation,
                    symbol.endpoint,
                ):
                    if value:
                        parts.append(value)
                parts.extend(sorted(symbol.attributes))
                self._append_chunks(
                    documents,
                    source,
                    symbol,
                    "\n".join(parts),
                )
        return tuple(documents)

    def _append_chunks(
        self,
        documents: list[_SemanticDocument],
        source: SourceFile,
        symbol: Symbol | None,
        text: str,
    ) -> None:
        identity = (
            symbol.symbol_id if symbol is not None else source.file_id
        )
        chunks = _chunks(
            text,
            maximum_characters=self.config.maximum_chunk_characters,
            maximum_chunks=self.config.maximum_chunks_per_document,
        )
        for index, chunk in enumerate(chunks):
            document_id = "semantic_" + stable_hash(
                {"identity": identity, "chunk": index}
            )
            documents.append(
                _SemanticDocument(
                    document_id=document_id,
                    cache_key=f"document:{document_id}",
                    source=source,
                    symbol=symbol,
                    text=chunk,
                    chunk_index=index,
                    test=(
                        source.test
                        or (
                            symbol is not None
                            and (
                                symbol.test
                                or symbol.kind == SymbolKind.TEST
                            )
                        )
                    ),
                )
            )
            if len(documents) > self.config.maximum_total_chunks:
                raise QueryLimitError(
                    "Semantic chunk count exceeded the configured limit."
                )

    def _embed_documents(
        self,
        documents: tuple[_SemanticDocument, ...],
    ) -> tuple[tuple[float, ...], ...]:
        vectors: list[tuple[float, ...]] = []
        for offset in range(
            0,
            len(documents),
            self.config.maximum_batch_size,
        ):
            batch = documents[
                offset : offset + self.config.maximum_batch_size
            ]
            vectors.extend(
                self._embed_batch(tuple(item.text for item in batch))
            )
        return tuple(vectors)

    def _embed_batch(
        self,
        texts: tuple[str, ...],
    ) -> tuple[tuple[float, ...], ...]:
        if self.provider is None:
            raise RuntimeError("semantic provider is unavailable")
        raw_vectors = tuple(self.provider.embed(texts))
        if len(raw_vectors) != len(texts):
            raise ValueError(
                "semantic provider returned an unexpected vector count"
            )
        dimensions = self.provider.descriptor.dimensions
        return tuple(
            _normalize_vector(vector, dimensions)
            for vector in raw_vectors
        )

    def _persist(
        self,
        entry: _EmbeddedDocument,
        *,
        snapshot_id: str | None,
    ) -> None:
        if (
            self.cache is None
            or self.repository_id is None
            or self.provider is None
        ):
            raise RuntimeError("semantic metadata cache is unavailable")
        metadata: dict[str, object] = {
            "document_id": entry.document.document_id,
            "model_fingerprint": self.provider.descriptor.fingerprint,
            "dimensions": self.provider.descriptor.dimensions,
            "source_hash": entry.document.source.content_hash,
            "chunk_index": entry.document.chunk_index,
        }
        if snapshot_id is not None:
            metadata["snapshot_id"] = snapshot_id
        self.cache.put(
            CacheEntry(
                repository_id=self.repository_id,
                namespace="semantic",
                cache_key=entry.document.cache_key,
                value_hash=stable_hash(entry.vector),
                metadata=metadata,
            )
        )

    def _delete_cache_entry(self, cache_key: str) -> None:
        if self.cache is not None and self.repository_id is not None:
            self.cache.delete(
                self.repository_id,
                "semantic",
                cache_key,
            )

    def _configuration_reason(self) -> str | None:
        if not self.config.enabled:
            return "Semantic search is disabled."
        if self.provider is None:
            return "No local semantic provider is configured."
        descriptor = self.provider.descriptor
        if not descriptor.local or descriptor.sends_source_off_device:
            return "Semantic provider is not approved for local source processing."
        if self.cache is None or self.repository_id is None:
            return "Semantic metadata persistence is not configured."
        return None

    def _unavailable_reason(self) -> str | None:
        return self._failure_message or self._configuration_reason()


def _chunks(
    text: str,
    *,
    maximum_characters: int,
    maximum_chunks: int,
) -> tuple[str, ...]:
    remaining = text.strip()
    chunks: list[str] = []
    while remaining and len(chunks) < maximum_chunks:
        if len(remaining) <= maximum_characters:
            chunks.append(remaining)
            break
        split_at = remaining.rfind(" ", 0, maximum_characters + 1)
        newline_at = remaining.rfind("\n", 0, maximum_characters + 1)
        split_at = max(split_at, newline_at)
        if split_at <= 0:
            split_at = maximum_characters
        chunks.append(remaining[:split_at].strip())
        remaining = remaining[split_at:].strip()
    return tuple(item for item in chunks if item)


def _normalize_vector(
    vector: Sequence[float],
    dimensions: int,
) -> tuple[float, ...]:
    values = tuple(float(item) for item in vector)
    if len(values) != dimensions:
        raise ValueError(
            "semantic provider returned an unexpected vector dimension"
        )
    if any(not math.isfinite(item) for item in values):
        raise ValueError("semantic provider returned a non-finite vector")
    magnitude = math.sqrt(sum(item * item for item in values))
    if not math.isfinite(magnitude):
        raise ValueError("semantic provider returned an unbounded vector")
    if magnitude == 0:
        return tuple(0.0 for _ in values)
    return tuple(item / magnitude for item in values)


def _matches_filters(
    document: _SemanticDocument,
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
        not prefix
        or source.relative_path == prefix
        or source.relative_path.startswith(f"{prefix}/")
        for prefix in query.path_prefixes
    ):
        return False
    if query.test_only and not document.test:
        return False
    return True


def _match_key(
    match: _SemanticMatch,
) -> tuple[float, str, int, str, int]:
    document = match.entry.document
    return (
        -match.similarity,
        document.source.relative_path.casefold(),
        0 if document.symbol is not None else 1,
        document.result_identity,
        document.chunk_index,
    )


def _bounded(
    value: int,
    name: str,
    minimum: int,
    maximum: int,
) -> None:
    if value < minimum or value > maximum:
        raise ValueError(f"{name} must be between {minimum} and {maximum}")
