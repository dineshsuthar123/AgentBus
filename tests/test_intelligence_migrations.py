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


def test_stable_file_identity_can_be_retained_across_snapshots():
    connection = sqlite3.connect(":memory:")
    apply_migrations(connection)
    connection.execute(
        """
        INSERT INTO repositories(
            repository_id, key_hash, display_name, schema_version, created_at
        ) VALUES (?, ?, ?, ?, ?)
        """,
        ("repo_" + "a" * 64, "b" * 64, "sample", 1, "2026-01-01T00:00:00Z"),
    )
    connection.execute(
        """
        INSERT INTO workspaces(workspace_id, repository_id, roots_json, created_at)
        VALUES (?, ?, ?, ?)
        """,
        (
            "workspace_" + "c" * 64,
            "repo_" + "a" * 64,
            '[""]',
            "2026-01-01T00:00:00Z",
        ),
    )
    for suffix in ("1", "2"):
        snapshot = "snapshot_" + suffix * 64
        connection.execute(
            """
            INSERT INTO index_snapshots(
                snapshot_id, repository_id, workspace_id, state, created_at,
                project_map_hash, graph_hash, parser_versions_json,
                source_fingerprint
            ) VALUES (?, ?, ?, 'current', ?, ?, ?, '{}', ?)
            """,
            (
                snapshot,
                "repo_" + "a" * 64,
                "workspace_" + "c" * 64,
                "2026-01-01T00:00:00Z",
                "d" * 64,
                "e" * 64,
                "f" * 64,
            ),
        )
        connection.execute(
            """
            INSERT INTO content_hashes(content_hash, size_bytes, first_seen_at)
            VALUES (?, 1, ?)
            ON CONFLICT(content_hash) DO NOTHING
            """,
            ("0" * 64, "2026-01-01T00:00:00Z"),
        )
        connection.execute(
            """
            INSERT INTO indexed_files(
                file_id, repository_id, snapshot_id, relative_path, language,
                content_hash, size_bytes, parser_name, parser_version
            ) VALUES (?, ?, ?, ?, 'python', ?, 1, 'python-ast', '1')
            """,
            (
                "file_" + "9" * 64,
                "repo_" + "a" * 64,
                snapshot,
                "src/app.py",
                "0" * 64,
            ),
        )

    rows = connection.execute(
        "SELECT snapshot_id, file_id FROM indexed_files ORDER BY snapshot_id"
    ).fetchall()
    assert len(rows) == 2
    assert rows[0][1] == rows[1][1]


def test_snapshot_delete_cascades_composite_identity_rows():
    connection = sqlite3.connect(":memory:")
    apply_migrations(connection)
    repository = "repo_" + "a" * 64
    workspace = "workspace_" + "b" * 64
    snapshot = "snapshot_" + "c" * 64
    connection.execute(
        """
        INSERT INTO repositories(
            repository_id, key_hash, display_name, schema_version, created_at
        ) VALUES (?, ?, 'sample', 1, '2026-01-01T00:00:00Z')
        """,
        (repository, "d" * 64),
    )
    connection.execute(
        """
        INSERT INTO workspaces(workspace_id, repository_id, roots_json, created_at)
        VALUES (?, ?, '[""]', '2026-01-01T00:00:00Z')
        """,
        (workspace, repository),
    )
    connection.execute(
        """
        INSERT INTO index_snapshots(
            snapshot_id, repository_id, workspace_id, state, created_at,
            project_map_hash, graph_hash, parser_versions_json, source_fingerprint
        ) VALUES (?, ?, ?, 'current', '2026-01-01T00:00:00Z', ?, ?, '{}', ?)
        """,
        (snapshot, repository, workspace, "e" * 64, "f" * 64, "0" * 64),
    )
    connection.execute(
        """
        INSERT INTO projects(
            project_id, repository_id, snapshot_id, name, kind, root,
            source_roots_json, test_roots_json, generated_roots_json,
            manifest_paths_json, workspace_project_ids_json
        ) VALUES (?, ?, ?, 'sample', 'python', '', '[]', '[]', '[]', '[]', '[]')
        """,
        ("project_" + "1" * 64, repository, snapshot),
    )
    connection.execute(
        """
        INSERT INTO content_hashes(content_hash, size_bytes, first_seen_at)
        VALUES (?, 1, '2026-01-01T00:00:00Z')
        """,
        ("2" * 64,),
    )
    connection.execute(
        """
        INSERT INTO indexed_files(
            file_id, repository_id, project_id, snapshot_id, relative_path,
            language, content_hash, size_bytes, parser_name, parser_version
        ) VALUES (?, ?, ?, ?, 'src/app.py', 'python', ?, 1, 'python-ast', '1')
        """,
        (
            "file_" + "3" * 64,
            repository,
            "project_" + "1" * 64,
            snapshot,
            "2" * 64,
        ),
    )
    connection.commit()

    connection.execute("DELETE FROM index_snapshots WHERE snapshot_id = ?", (snapshot,))
    connection.commit()

    assert connection.execute("SELECT COUNT(*) FROM projects").fetchone()[0] == 0
    assert connection.execute("SELECT COUNT(*) FROM indexed_files").fetchone()[0] == 0
    verify_schema(connection)


def _table_names(connection: sqlite3.Connection) -> set[str]:
    return {
        row[0]
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        ).fetchall()
    }
