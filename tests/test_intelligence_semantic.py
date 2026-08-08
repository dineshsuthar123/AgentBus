from __future__ import annotations

import json
import sqlite3
from collections.abc import Sequence
from datetime import datetime, timezone
from pathlib import Path

from agentbus.intelligence import (
    EmbeddingProviderDescriptor,
    IndexSnapshot,
    IndexState,
    IndexStore,
    IntelligenceCache,
    OptionalSemanticSearch,
    Project,
    ProjectKind,
    SearchQuery,
    SemanticSearchConfig,
    SourceFile,
    SourceLanguage,
    Symbol,
    SymbolKind,
    SymbolLocation,
    content_hash,
    file_id,
    project_id,
    repository_identity,
    snapshot_id,
    stable_id,
    workspace_identity,
)
from agentbus.intelligence.fingerprints import parser_versions_fingerprint


class DeterministicEmbeddingProvider:
    def __init__(
        self,
        *,
        local: bool = True,
        sends_source_off_device: bool = False,
        fail: bool = False,
    ) -> None:
        self.descriptor = EmbeddingProviderDescriptor(
            provider_name="fixture",
            model_name="token-axis",
            version="1.0.0",
            dimensions=3,
            local=local,
            sends_source_off_device=sends_source_off_device,
        )
        self.fail = fail
        self.calls: list[tuple[str, ...]] = []

    def embed(
        self,
        texts: tuple[str, ...],
    ) -> Sequence[Sequence[float]]:
        self.calls.append(texts)
        if self.fail:
            raise RuntimeError("fixture provider failure")
        return tuple(
            (
                float("invoice" in text.casefold()),
                float("test" in text.casefold()),
                float("timeout" in text.casefold()),
            )
            for text in texts
        )


def _records():
    repository = repository_identity("fixtures/semantic-search")
    owner = project_id(
        repository.repository_id,
        "services/billing",
        ProjectKind.PYTHON,
        name="billing",
    )
    project = Project(
        project_id=owner,
        repository_id=repository.repository_id,
        name="billing",
        kind=ProjectKind.PYTHON,
        root="services/billing",
        source_roots=("services/billing",),
    )
    source = SourceFile(
        file_id=file_id(
            repository.repository_id,
            "services/billing/invoice.py",
        ),
        repository_id=repository.repository_id,
        project_id=owner,
        relative_path="services/billing/invoice.py",
        language=SourceLanguage.PYTHON,
        content_hash="1" * 64,
        size_bytes=100,
        parser_name="fixture",
        parser_version="1.0.0",
    )
    protected = SourceFile(
        file_id=file_id(
            repository.repository_id,
            "services/billing/secret.py",
        ),
        repository_id=repository.repository_id,
        project_id=owner,
        relative_path="services/billing/secret.py",
        language=SourceLanguage.PYTHON,
        content_hash="2" * 64,
        size_bytes=100,
        parser_name="fixture",
        parser_version="1.0.0",
        protected=True,
    )
    invoice = Symbol(
        symbol_id=stable_id("symbol", "semantic", "invoice"),
        file_id=source.file_id,
        project_id=owner,
        name="calculate_invoice",
        qualified_name="billing.calculate_invoice",
        kind=SymbolKind.FUNCTION,
        language=SourceLanguage.PYTHON,
        location=SymbolLocation(
            relative_path=source.relative_path,
            start_line=1,
            start_column=0,
            end_line=1,
            end_column=1,
        ),
        documentation="Compute an invoice total.",
        exported=True,
    )
    secret = Symbol(
        symbol_id=stable_id("symbol", "semantic", "secret"),
        file_id=protected.file_id,
        project_id=owner,
        name="REAL_SECRET",
        qualified_name="billing.REAL_SECRET",
        kind=SymbolKind.CONSTANT,
        language=SourceLanguage.PYTHON,
        location=SymbolLocation(
            relative_path=protected.relative_path,
            start_line=1,
            start_column=0,
            end_line=1,
            end_column=1,
        ),
    )
    return repository, (project,), (source, protected), (invoice, secret)


def _cache(
    tmp_path: Path,
    repository,
) -> tuple[IntelligenceCache, str]:
    store = IndexStore(tmp_path / "semantic.sqlite3")
    workspace = workspace_identity(repository.repository_id, [""])
    parser_versions: dict[str, str] = {}
    identity = snapshot_id(
        repository.repository_id,
        content_hash("semantic-source"),
        parser_versions_fingerprint(parser_versions),
        content_hash("semantic-projects"),
        content_hash("semantic-graph"),
    )
    snapshot = IndexSnapshot(
        snapshot_id=identity,
        repository_id=repository.repository_id,
        workspace_id=workspace.workspace_id,
        state=IndexState.CURRENT,
        created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        completed_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        project_map_hash=content_hash("semantic-projects"),
        graph_hash=content_hash("semantic-graph"),
        parser_versions=parser_versions,
        source_fingerprint=content_hash("semantic-source"),
    )
    store.publish_snapshot(repository, workspace, snapshot)
    return IntelligenceCache(store), identity


def test_semantic_search_is_disabled_by_default(tmp_path: Path) -> None:
    repository, projects, files, symbols = _records()
    cache, snapshot = _cache(tmp_path, repository)
    provider = DeterministicEmbeddingProvider()
    search = OptionalSemanticSearch(
        provider=provider,
        cache=cache,
        repository_id=repository.repository_id,
    )

    status = search.build(
        projects,
        files,
        symbols,
        snapshot_id=snapshot,
    )

    assert status.enabled is False
    assert status.available is False
    assert status.document_count == 0
    assert provider.calls == []
    assert search.search(SearchQuery(text="invoice")) == ()


def test_local_semantic_search_persists_only_safe_fingerprints(
    tmp_path: Path,
) -> None:
    repository, projects, files, symbols = _records()
    cache, snapshot = _cache(tmp_path, repository)
    provider = DeterministicEmbeddingProvider()
    search = OptionalSemanticSearch(
        provider=provider,
        config=SemanticSearchConfig(
            enabled=True,
            maximum_batch_size=2,
        ),
        cache=cache,
        repository_id=repository.repository_id,
    )

    status = search.build(
        projects,
        files,
        symbols,
        snapshot_id=snapshot,
    )
    results = search.search(SearchQuery(text="invoice"))

    assert status.available is True
    assert status.model_fingerprint == provider.descriptor.fingerprint
    assert results[0].symbol is not None
    assert results[0].symbol.name == "calculate_invoice"
    assert results[0].source_hash == "1" * 64
    assert results[0].score_components["semantic"] == 100.0
    assert all(
        "REAL_SECRET" not in text
        for batch in provider.calls
        for text in batch
    )

    with sqlite3.connect(cache.store.database_path) as connection:
        rows = connection.execute(
            """
            SELECT metadata_json
            FROM intelligence_cache
            WHERE namespace = 'semantic'
            """
        ).fetchall()
    assert rows
    for (payload,) in rows:
        metadata = json.loads(payload)
        assert metadata["model_fingerprint"] == provider.descriptor.fingerprint
        assert metadata["source_hash"] == "1" * 64
        assert "vector" not in payload
        assert "invoice total" not in payload
        assert "REAL_SECRET" not in payload


def test_semantic_search_rejects_nonlocal_source_processing(
    tmp_path: Path,
) -> None:
    repository, projects, files, symbols = _records()
    cache, snapshot = _cache(tmp_path, repository)
    provider = DeterministicEmbeddingProvider(
        local=False,
        sends_source_off_device=True,
    )
    search = OptionalSemanticSearch(
        provider=provider,
        config=SemanticSearchConfig(enabled=True),
        cache=cache,
        repository_id=repository.repository_id,
    )

    status = search.build(
        projects,
        files,
        symbols,
        snapshot_id=snapshot,
    )

    assert status.available is False
    assert "not approved" in status.message
    assert provider.calls == []


def test_semantic_chunking_and_provider_batches_are_bounded(
    tmp_path: Path,
) -> None:
    repository, projects, files, symbols = _records()
    cache, snapshot = _cache(tmp_path, repository)
    provider = DeterministicEmbeddingProvider()
    long_symbol = symbols[0].model_copy(
        update={"documentation": "invoice " * 100}
    )
    search = OptionalSemanticSearch(
        provider=provider,
        config=SemanticSearchConfig(
            enabled=True,
            maximum_chunk_characters=128,
            maximum_chunks_per_document=2,
            maximum_batch_size=1,
        ),
        cache=cache,
        repository_id=repository.repository_id,
    )

    status = search.build(
        projects,
        files,
        (long_symbol, symbols[1]),
        snapshot_id=snapshot,
    )

    assert status.available is True
    assert status.document_count == 3
    assert all(len(batch) == 1 for batch in provider.calls)
    assert all(
        len(text) <= 128
        for batch in provider.calls
        for text in batch
    )


def test_stale_source_hashes_invalidate_embeddings(tmp_path: Path) -> None:
    repository, projects, files, symbols = _records()
    cache, snapshot = _cache(tmp_path, repository)
    search = OptionalSemanticSearch(
        provider=DeterministicEmbeddingProvider(),
        config=SemanticSearchConfig(enabled=True),
        cache=cache,
        repository_id=repository.repository_id,
    )
    search.build(projects, files, symbols, snapshot_id=snapshot)
    changed = files[0].model_copy(
        update={"content_hash": "9" * 64}
    )

    removed = search.invalidate_stale((changed, files[1]))

    assert removed >= 2
    assert search.search(SearchQuery(text="invoice")) == ()
    with sqlite3.connect(cache.store.database_path) as connection:
        remaining = connection.execute(
            """
            SELECT COUNT(*)
            FROM intelligence_cache
            WHERE namespace = 'semantic'
            """
        ).fetchone()[0]
    assert remaining == 0


def test_provider_failure_degrades_without_exposing_error_details(
    tmp_path: Path,
) -> None:
    repository, projects, files, symbols = _records()
    cache, snapshot = _cache(tmp_path, repository)
    provider = DeterministicEmbeddingProvider(fail=True)
    search = OptionalSemanticSearch(
        provider=provider,
        config=SemanticSearchConfig(enabled=True),
        cache=cache,
        repository_id=repository.repository_id,
    )

    status = search.build(
        projects,
        files,
        symbols,
        snapshot_id=snapshot,
    )

    assert status.available is False
    assert status.document_count == 0
    assert "lexical retrieval remains available" in status.message
    assert "fixture provider failure" not in status.message
    assert search.search(SearchQuery(text="invoice")) == ()
