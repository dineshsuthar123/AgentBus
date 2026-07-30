from __future__ import annotations

import sqlite3

from agentbus.intelligence.errors import IndexSchemaError
from agentbus.intelligence.schema import LATEST_SCHEMA_VERSION, MIGRATIONS


def apply_migrations(
    connection: sqlite3.Connection,
    *,
    target_version: int = LATEST_SCHEMA_VERSION,
) -> int:
    if target_version < 0 or target_version > LATEST_SCHEMA_VERSION:
        raise IndexSchemaError(
            f"Unsupported repository intelligence schema target: {target_version}."
        )

    current = schema_version(connection)
    if current > LATEST_SCHEMA_VERSION:
        raise IndexSchemaError(
            "Repository intelligence index was created by a newer AgentBus version "
            f"(database={current}, supported={LATEST_SCHEMA_VERSION})."
        )
    if current > target_version:
        raise IndexSchemaError(
            f"Schema downgrade is not supported ({current} -> {target_version})."
        )

    connection.execute("PRAGMA foreign_keys = ON")
    for migration in MIGRATIONS:
        if migration.version <= current or migration.version > target_version:
            continue
        _apply_migration(connection, migration.version, migration.name, migration.sql)
        current = migration.version
    return current


def schema_version(connection: sqlite3.Connection) -> int:
    row = connection.execute("PRAGMA user_version").fetchone()
    return int(row[0]) if row else 0


def verify_schema(connection: sqlite3.Connection) -> None:
    version = schema_version(connection)
    if version != LATEST_SCHEMA_VERSION:
        raise IndexSchemaError(
            "Repository intelligence schema is not current "
            f"(database={version}, expected={LATEST_SCHEMA_VERSION})."
        )
    violations = connection.execute("PRAGMA foreign_key_check").fetchall()
    if violations:
        raise IndexSchemaError(
            f"Repository intelligence foreign-key check failed ({len(violations)} violations)."
        )
    integrity = connection.execute("PRAGMA quick_check").fetchone()
    if not integrity or integrity[0] != "ok":
        message = integrity[0] if integrity else "no result"
        raise IndexSchemaError(
            f"Repository intelligence integrity check failed: {message}."
        )


def _apply_migration(
    connection: sqlite3.Connection,
    version: int,
    name: str,
    sql: str,
) -> None:
    escaped_name = name.replace("'", "''")
    script = f"""
BEGIN IMMEDIATE;
CREATE TABLE IF NOT EXISTS schema_migrations (
    version INTEGER PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    applied_at TEXT NOT NULL
);
{sql}
INSERT INTO schema_migrations(version, name, applied_at)
VALUES ({version}, '{escaped_name}', strftime('%Y-%m-%dT%H:%M:%fZ', 'now'));
PRAGMA user_version = {version};
COMMIT;
"""
    try:
        connection.executescript(script)
    except sqlite3.DatabaseError as exc:
        if connection.in_transaction:
            connection.rollback()
        raise IndexSchemaError(
            f"Repository intelligence migration {version} ({name}) failed."
        ) from exc
