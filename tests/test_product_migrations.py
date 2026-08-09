import json
import sqlite3

import pytest

from agentbus.config import AgentBusConfig
from agentbus.execution.schema import MIGRATIONS, SCHEMA_SQL, SCHEMA_VERSION
from agentbus.execution.state_store import StateStore
from agentbus.intelligence.schema import LATEST_SCHEMA_VERSION
from agentbus.product.migrations import MigrationCoordinator, MigrationState


def _config(tmp_path):
    return AgentBusConfig(
        state_dir=str(tmp_path / "state"),
        state_db="state.db",
    )


def _create_v1_state(path):
    path.parent.mkdir(parents=True)
    v1_sql = SCHEMA_SQL.split("CREATE TABLE IF NOT EXISTS worktrees", 1)[0]
    with sqlite3.connect(path) as connection:
        connection.executescript(v1_sql)
        connection.execute(
            "INSERT INTO schema_metadata(key, value) VALUES ('schema_version', '1')"
        )
        connection.commit()


def test_migration_status_does_not_create_absent_databases(tmp_path):
    coordinator = MigrationCoordinator(_config(tmp_path))

    report = coordinator.status()

    assert report.ok is True
    assert {target.state for target in report.targets} == {MigrationState.ABSENT}
    assert not coordinator.state_path.exists()
    assert not coordinator.index_path.exists()


def test_state_migration_is_backed_up_applied_and_idempotent(tmp_path):
    coordinator = MigrationCoordinator(_config(tmp_path))
    _create_v1_state(coordinator.state_path)

    planned = coordinator.plan()
    applied = coordinator.apply()
    repeated = coordinator.apply()

    assert planned.targets[0].state == MigrationState.REQUIRED
    assert applied.targets[0].state == MigrationState.CURRENT
    assert StateStore(coordinator.state_path).schema_version == SCHEMA_VERSION
    assert len(applied.backups) == 1
    assert applied.backups[0].is_file()
    assert repeated.backups == ()


def test_migration_dry_run_never_changes_database(tmp_path):
    coordinator = MigrationCoordinator(_config(tmp_path))
    _create_v1_state(coordinator.state_path)
    before = coordinator.state_path.read_bytes()

    report = coordinator.apply(dry_run=True)

    assert report.dry_run is True
    assert coordinator.state_path.read_bytes() == before
    assert not coordinator.journal_path.exists()


def test_newer_state_schema_is_rejected_without_changes(tmp_path):
    coordinator = MigrationCoordinator(_config(tmp_path))
    StateStore(coordinator.state_path)
    with sqlite3.connect(coordinator.state_path) as connection:
        connection.execute(
            "UPDATE schema_metadata SET value = ? WHERE key = 'schema_version'",
            (str(SCHEMA_VERSION + 1),),
        )
        connection.commit()

    with pytest.raises(ValueError, match="newer"):
        coordinator.apply()

    assert coordinator.status().targets[0].state == MigrationState.NEWER


def test_repository_index_migration_and_verification(tmp_path):
    coordinator = MigrationCoordinator(_config(tmp_path))
    coordinator.index_path.parent.mkdir(parents=True)
    with sqlite3.connect(coordinator.index_path):
        pass

    applied = coordinator.apply()
    verified = coordinator.verify()

    index = next(item for item in applied.targets if item.name == "repository-intelligence")
    assert index.current_version == LATEST_SCHEMA_VERSION
    assert index.state == MigrationState.CURRENT
    assert verified.ok is True


def test_interrupted_migration_journal_is_reported_on_safe_resume(tmp_path):
    coordinator = MigrationCoordinator(_config(tmp_path))
    _create_v1_state(coordinator.state_path)
    coordinator.journal_path.write_text(
        json.dumps({"status": "in_progress"}),
        encoding="utf-8",
    )

    report = coordinator.apply()

    assert report.recovered_interrupted_operation is True
    journal = json.loads(coordinator.journal_path.read_text(encoding="utf-8"))
    assert journal["status"] == "complete"


def test_failed_underlying_migration_keeps_version_and_backup(tmp_path, monkeypatch):
    coordinator = MigrationCoordinator(_config(tmp_path))
    _create_v1_state(coordinator.state_path)
    monkeypatch.setitem(MIGRATIONS, 1, ("THIS IS NOT SQL",))

    with pytest.raises(Exception, match="migration 1 -> 2 failed"):
        coordinator.apply()

    with sqlite3.connect(coordinator.state_path) as connection:
        version = connection.execute(
            "SELECT value FROM schema_metadata WHERE key = 'schema_version'"
        ).fetchone()[0]
    assert version == "1"
    assert list((coordinator.state_path.parent / "migration-backups").glob("*.sqlite3"))
    assert json.loads(coordinator.journal_path.read_text(encoding="utf-8"))["status"] == "failed"
