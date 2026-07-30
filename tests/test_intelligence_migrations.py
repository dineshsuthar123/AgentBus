import sqlite3

import pytest

from agentbus.intelligence.errors import IndexSchemaError
from agentbus.intelligence.migrations import (
    apply_migrations,
    schema_version,
    verify_schema,
)
from agentbus.intelligence.schema import LATEST_SCHEMA_VERSION


EXPECTED_TABLES = {
    "architecture_boundaries",
    "content_hashes",
    "dependency_edges",
    "index_diagnostics",
    "index_operations",
    "index_snapshots",
    "indexed_files",
    "intelligence_cache",
    "invalidation_state",
    "modules",
    "ownership_rules",
    "projects",
    "repositories",
    "schema_migrations",
    "search_terms",
    "symbol_references",
    "symbols",
    "workspaces",
}


def test_empty_database_migrates_to_current_schema():
    connection = sqlite3.connect(":memory:")

    version = apply_migrations(connection)

    assert version == LATEST_SCHEMA_VERSION
    assert schema_version(connection) == LATEST_SCHEMA_VERSION
    assert EXPECTED_TABLES <= _table_names(connection)
    verify_schema(connection)


def test_migrations_are_incremental_and_idempotent():
    connection = sqlite3.connect(":memory:")

    assert apply_migrations(connection, target_version=1) == 1
    assert "indexed_files" in _table_names(connection)
    assert "symbols" not in _table_names(connection)
    assert apply_migrations(connection) == LATEST_SCHEMA_VERSION
    assert apply_migrations(connection) == LATEST_SCHEMA_VERSION
    applied = connection.execute(
        "SELECT version, name FROM schema_migrations ORDER BY version"
    ).fetchall()

    assert [row[0] for row in applied] == list(
        range(1, LATEST_SCHEMA_VERSION + 1)
    )


def test_newer_or_downgrade_schema_is_rejected():
    connection = sqlite3.connect(":memory:")
    connection.execute(f"PRAGMA user_version = {LATEST_SCHEMA_VERSION + 1}")

    with pytest.raises(IndexSchemaError, match="newer AgentBus"):
        apply_migrations(connection)

    current = sqlite3.connect(":memory:")
    apply_migrations(current)
    with pytest.raises(IndexSchemaError, match="downgrade"):
        apply_migrations(current, target_version=1)


def test_foreign_keys_are_enforced_after_migration():
    connection = sqlite3.connect(":memory:")
    apply_migrations(connection)

    with pytest.raises(sqlite3.IntegrityError):
        connection.execute(
            """
            INSERT INTO workspaces(workspace_id, repository_id, roots_json, created_at)
            VALUES ('workspace_missing', 'repo_missing', '[]', '2026-01-01T00:00:00Z')
            """
        )


def test_schema_never_persists_complete_source_content():
    connection = sqlite3.connect(":memory:")
    apply_migrations(connection)

    columns = {
        row[1]
        for table in EXPECTED_TABLES
        for row in connection.execute(f"PRAGMA table_info({table})").fetchall()
    }

    assert "source_content" not in columns
    assert "raw_source" not in columns
    assert "embedding_vector" not in columns


def _table_names(connection: sqlite3.Connection) -> set[str]:
    return {
        row[0]
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        ).fetchall()
    }
