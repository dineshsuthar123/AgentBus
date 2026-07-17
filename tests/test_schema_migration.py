from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone

import pytest

from agentbus.execution.schema import MIGRATIONS, SCHEMA_SQL
from agentbus.execution.state_store import StateStore, StateStoreError


def create_v1_database(path, *, with_records=True):
    v1_sql = SCHEMA_SQL.split("CREATE TABLE IF NOT EXISTS worktrees", 1)[0]
    connection = sqlite3.connect(path)
    try:
        connection.executescript(v1_sql)
        connection.execute(
            "INSERT INTO schema_metadata(key, value) VALUES (?, ?)",
            ("schema_version", "1"),
        )
        if with_records:
            now = datetime.now(timezone.utc).isoformat()
            plan = {
                "goal": "Legacy run",
                "steps": [
                    {
                        "id": "step-1",
                        "title": "Legacy task",
                        "description": "Preserve me",
                    }
                ],
            }
            graph = {
                "version": 1,
                "tasks": [
                    {
                        "task_id": "step-1",
                        "title": "Legacy task",
                        "description": "Preserve me",
                        "dependencies": [],
                        "assigned_role": "coder",
                        "risk": "low",
                        "maximum_attempts": 2,
                        "expected_outputs": [],
                        "done_criteria": [],
                        "metadata": {},
                    }
                ],
            }
            connection.execute(
                """INSERT INTO runs VALUES (
                    ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, NULL, NULL, 1, ?, ?,
                    NULL, NULL, ?, NULL, NULL, NULL
                )""",
                (
                    "legacy-run",
                    "Legacy task",
                    "multi",
                    "running",
                    "fake",
                    "C:/legacy",
                    now,
                    now,
                    json.dumps(plan),
                    json.dumps(graph),
                    "{}",
                    "[]",
                ),
            )
            connection.execute(
                """INSERT INTO tasks VALUES (
                    ?, ?, 0, ?, ?, ?, ?, ?, ?, 2, 1, ?, ?, ?, ?, ?
                )""",
                (
                    "legacy-run",
                    "step-1",
                    "Legacy task",
                    "Preserve me",
                    "running",
                    "low",
                    "coder",
                    "[]",
                    "[]",
                    "[]",
                    "{}",
                    now,
                    now,
                ),
            )
            connection.execute(
                """INSERT INTO attempts VALUES (
                    ?, ?, ?, 1, ?, ?, NULL, NULL, NULL, NULL, ?
                )""",
                (
                    "legacy-attempt",
                    "legacy-run",
                    "step-1",
                    "running",
                    now,
                    "{}",
                ),
            )
        connection.commit()
    finally:
        connection.close()


def test_v1_database_migrates_transactionally_and_reopens(tmp_path):
    database = tmp_path / "legacy.db"
    create_v1_database(database)

    store = StateStore(database)

    assert store.schema_version == 3
    assert store.get_run("legacy-run").original_task == "Legacy task"
    assert store.get_task("legacy-run", "step-1").current_attempt_count == 1
    assert store.get_attempt("legacy-attempt").status.value == "running"
    with sqlite3.connect(database) as connection:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
    assert {
        "worktrees",
        "worker_leases",
        "task_commits",
        "integration_attempts",
        "cancellations",
    } <= tables
    assert StateStore(database).schema_version == 3


def test_v2_database_adds_cancellation_state_without_changing_runs(tmp_path):
    database = tmp_path / "v2.db"
    create_v1_database(database)
    with sqlite3.connect(database) as connection:
        for statement in MIGRATIONS[1]:
            connection.execute(statement)
        connection.execute(
            "UPDATE schema_metadata SET value = '2' WHERE key = 'schema_version'"
        )
        connection.commit()

    store = StateStore(database)

    assert store.schema_version == 3
    assert store.get_run("legacy-run").original_task == "Legacy task"
    with sqlite3.connect(database) as connection:
        cancellation_table = connection.execute(
            "SELECT name FROM sqlite_master WHERE name = 'cancellations'"
        ).fetchone()
    assert cancellation_table == ("cancellations",)


def test_failed_migration_rolls_back_schema_and_version(tmp_path, monkeypatch):
    database = tmp_path / "legacy.db"
    create_v1_database(database, with_records=False)
    monkeypatch.setitem(
        MIGRATIONS,
        1,
        (
            "CREATE TABLE migration_probe(value TEXT)",
            "THIS IS NOT VALID SQL",
        ),
    )

    with pytest.raises(StateStoreError, match="migration 1 -> 2 failed"):
        StateStore(database)

    with sqlite3.connect(database) as connection:
        version = connection.execute(
            "SELECT value FROM schema_metadata WHERE key = 'schema_version'"
        ).fetchone()[0]
        probe = connection.execute(
            "SELECT name FROM sqlite_master WHERE name = 'migration_probe'"
        ).fetchone()
    assert version == "1"
    assert probe is None


def test_state_database_backup_is_explicit_and_reopenable(tmp_path):
    store = StateStore(tmp_path / "state.db")

    backup = store.backup(tmp_path / "backups" / "state-v3.db")

    assert backup.is_file()
    assert StateStore(backup).schema_version == 3
