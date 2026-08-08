from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from pydantic import ValidationError

from agentbus.intelligence import (
    CacheEntry,
    IndexSnapshot,
    IndexState,
    IndexStore,
    IntelligenceCache,
    content_hash,
    repository_identity,
    snapshot_id,
    workspace_identity,
)
from agentbus.intelligence.fingerprints import parser_versions_fingerprint


def test_cache_round_trip_persists_metadata_without_values(tmp_path: Path) -> None:
    store, repository_id = _indexed_store(tmp_path)
    cache = IntelligenceCache(store)
    entry = CacheEntry(
        repository_id=repository_id,
        namespace="semantic",
        cache_key="chunk:abc123",
        value_hash=content_hash("derived-vector"),
        metadata={
            "model_fingerprint": content_hash("offline-model"),
            "dimensions": 8,
            "source_hash": content_hash("source"),
        },
    )

    assert cache.put(entry) == entry
    assert cache.get(repository_id, "semantic", "chunk:abc123") == entry

    with sqlite3.connect(store.database_path) as connection:
        columns = {
            row[1]
            for row in connection.execute(
                "PRAGMA table_info(intelligence_cache)"
            ).fetchall()
        }
        payload = connection.execute(
            "SELECT metadata_json FROM intelligence_cache"
        ).fetchone()[0]
    assert "value" not in columns
    assert "embedding" not in columns
    assert "derived-vector" not in payload


def test_cache_expiration_uses_injected_clock_without_sleep(tmp_path: Path) -> None:
    store, repository_id = _indexed_store(tmp_path)
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    cache = IntelligenceCache(store, clock=lambda: now)
    expired = CacheEntry(
        repository_id=repository_id,
        namespace="retrieval",
        cache_key="expired",
        value_hash=content_hash("expired"),
        expires_at=now - timedelta(seconds=1),
    )
    current = expired.model_copy(
        update={
            "cache_key": "current",
            "value_hash": content_hash("current"),
            "expires_at": now + timedelta(minutes=5),
        }
    )
    cache.put(expired)
    cache.put(current)

    assert cache.get(repository_id, "retrieval", "expired") is None
    assert cache.get(repository_id, "retrieval", "current") == current
    assert cache.purge_expired() == 1
    assert cache.delete(repository_id, "retrieval", "current") is True
    assert cache.delete(repository_id, "retrieval", "current") is False


def test_cache_updates_are_idempotent_and_bounded(tmp_path: Path) -> None:
    store, repository_id = _indexed_store(tmp_path)
    cache = IntelligenceCache(store)
    first = CacheEntry(
        repository_id=repository_id,
        namespace="context",
        cache_key="plan:abc",
        value_hash=content_hash("first"),
        metadata={"version": 1},
    )
    second = first.model_copy(
        update={
            "value_hash": content_hash("second"),
            "metadata": {"version": 2},
        }
    )

    cache.put(first)
    cache.put(second)

    assert cache.get(repository_id, "context", "plan:abc") == second
    with sqlite3.connect(store.database_path) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM intelligence_cache"
        ).fetchone()[0] == 1
    with pytest.raises(ValidationError, match="unsupported characters"):
        CacheEntry(
            repository_id=repository_id,
            namespace="../escape",
            cache_key="key",
            value_hash=content_hash("value"),
        )


@pytest.mark.parametrize(
    "metadata",
    [
        {"source_content": "print('must not persist')"},
        {"api_key": "real-secret"},
        {"location": r"C:\Users\person\private\repository.py"},
        {"note": "authorization=real-secret"},
        {"vector": [0.1, 0.2]},
    ],
)
def test_cache_rejects_source_secrets_vectors_and_private_paths(
    tmp_path: Path,
    metadata: dict[str, object],
) -> None:
    _, repository_id = _indexed_store(tmp_path)

    with pytest.raises(ValidationError, match="not permitted|secret|private"):
        CacheEntry(
            repository_id=repository_id,
            namespace="semantic",
            cache_key="unsafe",
            value_hash=content_hash("value"),
            metadata=metadata,
        )


def test_cache_revalidates_copied_models_at_persistence_boundary(
    tmp_path: Path,
) -> None:
    store, repository_id = _indexed_store(tmp_path)
    cache = IntelligenceCache(store)
    safe = CacheEntry(
        repository_id=repository_id,
        namespace="semantic",
        cache_key="safe",
        value_hash=content_hash("value"),
    )
    bypassed = safe.model_copy(
        update={"metadata": {"source_content": "must not persist"}}
    )

    with pytest.raises(ValidationError, match="not permitted"):
        cache.put(bypassed)

    assert cache.get(repository_id, "semantic", "safe") is None


def _indexed_store(tmp_path: Path) -> tuple[IndexStore, str]:
    store = IndexStore(tmp_path / "repository.sqlite3")
    repository = repository_identity("example/cache")
    workspace = workspace_identity(repository.repository_id, [""])
    parser_versions: dict[str, str] = {}
    identity = snapshot_id(
        repository.repository_id,
        content_hash("empty-source"),
        parser_versions_fingerprint(parser_versions),
        content_hash("empty-project-map"),
        content_hash("empty-graph"),
    )
    snapshot = IndexSnapshot(
        snapshot_id=identity,
        repository_id=repository.repository_id,
        workspace_id=workspace.workspace_id,
        state=IndexState.CURRENT,
        created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        completed_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        project_map_hash=content_hash("empty-project-map"),
        graph_hash=content_hash("empty-graph"),
        parser_versions=parser_versions,
        source_fingerprint=content_hash("empty-source"),
    )
    store.publish_snapshot(repository, workspace, snapshot)
    return store, repository.repository_id
