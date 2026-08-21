from __future__ import annotations

import json
import os
import re
import sqlite3
import subprocess
from pathlib import Path

import pytest

from agentbus import cli
from agentbus.configuration import resolve_configuration
from agentbus.doctor import CheckStatus, run_doctor
from agentbus.execution.schema import SCHEMA_SQL, SCHEMA_VERSION
from agentbus.execution.state_store import StateStore
from agentbus.intelligence.migrations import apply_migrations as apply_index_migrations
from agentbus.intelligence.schema import LATEST_SCHEMA_VERSION
from agentbus.product import migrations as migration_module
from agentbus.product.migrations import MigrationCoordinator, MigrationState
from agentbus.replay import ReplayEngine, ReplayRequest, ReplaySessionStatus
from agentbus.trace import ContentAddressedStore, ReplayMode, Trace, TraceRecorder


FIXTURE = Path(__file__).parent / "fixtures" / "migrations" / "v06-config.json"
HISTORICAL_RUN_ID = "historical-v06-run"
HISTORICAL_REPOSITORY_ID = "historical-v06-repository"


def _git(workspace: Path, *arguments: str) -> str:
    result = subprocess.run(
        ["git", *arguments],
        cwd=workspace,
        check=True,
        shell=False,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def _repository(path: Path) -> Path:
    path.mkdir()
    hooks = path.parent / "empty-hooks"
    hooks.mkdir()
    (path / ".gitignore").write_text(
        "__pycache__/\n*.py[cod]\n.pytest_cache/\n",
        encoding="utf-8",
    )
    (path / "README.md").write_text(
        "# Historical AgentBus workspace\n",
        encoding="utf-8",
    )
    _git(path, "init", "-q", "--initial-branch=main")
    _git(path, "add", "--", ".gitignore", "README.md")
    _git(
        path,
        "-c",
        f"core.hooksPath={hooks}",
        "-c",
        "commit.gpgSign=false",
        "-c",
        "user.name=AgentBus Migration Test",
        "-c",
        "user.email=migration@agentbus.invalid",
        "commit",
        "-q",
        "-m",
        "chore: initialize historical workspace",
    )
    return path


def _historical_config(root: Path, workspace: Path) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
    values = payload["agentbus"]
    values.update(
        {
            "workspace_dir": str(workspace),
            "state_dir": str(root / "state"),
            "runs_dir": str(root / "runs"),
            "worktree_root": str(root / "worktrees"),
        }
    )
    path = root / "v06-config.json"
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return path


def _create_v1_state(path: Path, workspace: Path) -> None:
    path.parent.mkdir(parents=True)
    v1_schema = SCHEMA_SQL.split("CREATE TABLE IF NOT EXISTS worktrees", 1)[0]
    timestamp = "2026-01-01T00:00:00+00:00"
    with sqlite3.connect(path) as connection:
        connection.executescript(v1_schema)
        connection.execute(
            "INSERT INTO schema_metadata(key, value) VALUES (?, ?)",
            ("schema_version", "1"),
        )
        connection.execute(
            """INSERT INTO runs (
                run_id, original_task, workflow_type, status, model, workspace,
                created_at, updated_at, completed_at, planner_output_json,
                context_summary, failure_reason, version, graph_json,
                metadata_json, verifier_status, reviewer_status,
                changed_files_json, commit_identifier, pr_url,
                finalization_error
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                HISTORICAL_RUN_ID,
                "Preserve historical compatible state",
                "multi",
                "succeeded",
                "deterministic",
                str(workspace),
                timestamp,
                timestamp,
                timestamp,
                json.dumps({"goal": "historical", "steps": []}),
                "Historical v0.6 context",
                None,
                1,
                json.dumps({"version": 1, "tasks": []}),
                json.dumps({"release": "v0.6"}),
                "passed",
                "approved",
                json.dumps(["historical.txt"]),
                None,
                None,
                None,
            ),
        )
        connection.commit()


def _create_v1_index(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as connection:
        apply_index_migrations(connection, target_version=1)
        connection.execute(
            """INSERT INTO repositories(
                repository_id, key_hash, display_name, schema_version, created_at
            ) VALUES (?, ?, ?, ?, ?)""",
            (
                HISTORICAL_REPOSITORY_ID,
                "a" * 64,
                "Historical v0.6 repository",
                1,
                "2026-01-01T00:00:00Z",
            ),
        )
        connection.commit()


def _clear_agentbus_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in tuple(os.environ):
        if name.startswith("AGENTBUS_"):
            monkeypatch.delenv(name, raising=False)


def _historical_trace_fixture() -> Trace:
    recorder = TraceRecorder(HISTORICAL_RUN_ID)
    recorder.start_trace()
    return recorder.finish_trace()


def test_upgrade_migration_lifecycle_preserves_state_and_remains_operable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _clear_agentbus_environment(monkeypatch)
    workspace = _repository(tmp_path / "workspace")
    config_path = _historical_config(tmp_path, workspace)
    config = resolve_configuration(
        config_file=config_path,
        discover=False,
        environ={},
    ).config
    coordinator = MigrationCoordinator(config)
    historical_trace = _historical_trace_fixture()
    _create_v1_state(coordinator.state_path, workspace)
    _create_v1_index(coordinator.index_path)
    state_before = coordinator.state_path.read_bytes()
    index_before = coordinator.index_path.read_bytes()

    status = coordinator.status()
    plan = coordinator.plan()

    assert {target.state for target in status.targets} == {MigrationState.REQUIRED}
    assert plan.dry_run is True
    assert coordinator.state_path.read_bytes() == state_before
    assert coordinator.index_path.read_bytes() == index_before

    original_index_migration = migration_module.apply_index_migrations

    def interrupt_index_migration(_connection: sqlite3.Connection) -> None:
        raise RuntimeError("synthetic interrupted migration")

    monkeypatch.setattr(
        migration_module,
        "apply_index_migrations",
        interrupt_index_migration,
    )
    with pytest.raises(RuntimeError, match="synthetic interrupted"):
        coordinator.apply()
    monkeypatch.setattr(
        migration_module,
        "apply_index_migrations",
        original_index_migration,
    )
    coordinator.journal_path.write_text(
        json.dumps({"status": "in_progress"}),
        encoding="utf-8",
    )

    applied = coordinator.apply()
    verified = coordinator.verify()

    assert applied.recovered_interrupted_operation is True
    assert verified.ok is True
    assert {target.state for target in verified.targets} == {MigrationState.CURRENT}
    store = StateStore(coordinator.state_path)
    historical = store.get_run(HISTORICAL_RUN_ID)
    assert historical.original_task == "Preserve historical compatible state"
    assert historical.changed_files == ["historical.txt"]
    with sqlite3.connect(coordinator.index_path) as connection:
        version = connection.execute("PRAGMA user_version").fetchone()[0]
        repository = connection.execute(
            "SELECT display_name FROM repositories WHERE repository_id = ?",
            (HISTORICAL_REPOSITORY_ID,),
        ).fetchone()
    assert version == LATEST_SCHEMA_VERSION
    assert repository == ("Historical v0.6 repository",)
    backup_root = config.state_database_path.parent / "migration-backups"
    assert len(list(backup_root.glob("*.sqlite3"))) >= 2

    doctor = run_doctor(config)
    doctor_checks = {check.name: check for check in doctor.checks}
    assert doctor.network_used is False
    assert doctor_checks["migration:execution-state"].status == CheckStatus.OK
    assert doctor_checks["migration:repository-intelligence"].status == CheckStatus.OK

    historical_replay = ReplayEngine(
        ContentAddressedStore(tmp_path / "historical-replay-objects")
    ).replay(
        historical_trace,
        ReplayRequest(
            source_trace_id=historical_trace.trace_id,
            source_run_id=historical_trace.run_id,
            mode=ReplayMode.OFFLINE,
        ),
    )
    assert historical_replay.session.status == ReplaySessionStatus.SUCCEEDED
    assert historical_replay.session.provider_calls == 0
    assert historical_replay.session.network_calls == 0

    exit_code = cli.main(
        [
            "run",
            "--config",
            str(config_path),
            "--workflow",
            "multi",
            "--provider",
            "deterministic",
            "--workspace",
            str(workspace),
            "--durable",
            "Create and verify the deterministic AgentBus calculator.",
        ]
    )
    run_output = capsys.readouterr().out
    run_match = re.search(r"^Run ID:\s+(\S+)$", run_output, flags=re.MULTILINE)
    assert exit_code == 0, run_output
    assert run_match is not None
    assert "Status: succeeded" in run_output
    run_id = run_match.group(1)

    assert cli.main(
        [
            "replay",
            run_id,
            "--mode",
            "offline",
            "--config",
            str(config_path),
            "--json",
        ]
    ) == 0
    replay = json.loads(capsys.readouterr().out)
    assert replay["session"]["status"] == "succeeded"
    assert replay["session"]["provider_calls"] == 0
    assert replay["session"]["network_calls"] == 0

    assert cli.main(
        [
            "cleanup",
            "--config",
            str(config_path),
            "--registry-path",
            str(tmp_path / "daemon-registry.json"),
            "--all-runtime-state",
            "--yes",
            "--json",
        ]
    ) == 0
    cleanup = json.loads(capsys.readouterr().out)
    assert cleanup["ok"] is True
    assert config_path.is_file()
    assert (workspace / "agentbus_result.py").is_file()
    assert StateStore(coordinator.state_path).get_run(HISTORICAL_RUN_ID) == historical


def test_newer_schema_is_rejected_without_mutation_or_backup(tmp_path: Path) -> None:
    workspace = _repository(tmp_path / "workspace")
    config_path = _historical_config(tmp_path, workspace)
    config = resolve_configuration(
        config_file=config_path,
        discover=False,
        environ={},
    ).config
    coordinator = MigrationCoordinator(config)
    StateStore(coordinator.state_path)
    with sqlite3.connect(coordinator.state_path) as connection:
        connection.execute(
            "UPDATE schema_metadata SET value = ? WHERE key = 'schema_version'",
            (str(SCHEMA_VERSION + 1),),
        )
        connection.commit()
    before = coordinator.state_path.read_bytes()

    with pytest.raises(ValueError, match="newer"):
        coordinator.apply()

    assert coordinator.state_path.read_bytes() == before
    assert coordinator.status().targets[0].state == MigrationState.NEWER
    assert not (config.state_database_path.parent / "migration-backups").exists()
    assert not coordinator.journal_path.exists()


def test_migration_reports_redact_secret_shaped_paths(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _clear_agentbus_environment(monkeypatch)
    private_marker = "migration-private-marker"
    workspace = _repository(tmp_path / "workspace")
    runtime = tmp_path / f"api_key={private_marker}"
    config_path = _historical_config(runtime, workspace)
    config = resolve_configuration(
        config_file=config_path,
        discover=False,
        environ={},
    ).config
    coordinator = MigrationCoordinator(config)
    _create_v1_state(coordinator.state_path, workspace)

    serialized = json.dumps(coordinator.status().to_dict(), sort_keys=True)
    assert private_marker not in serialized

    assert cli.main(
        ["migrate", "apply", "--config", str(config_path)]
    ) == 0
    rendered = capsys.readouterr().out
    assert private_marker not in rendered
    assert private_marker not in coordinator.journal_path.read_text(encoding="utf-8")
