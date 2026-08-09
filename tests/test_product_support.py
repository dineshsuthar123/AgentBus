from __future__ import annotations

import json
import zipfile

import pytest

from agentbus.cli import main
from agentbus.config import AgentBusConfig
from agentbus.product.logging import ProductLogWriter
from agentbus.product.support import create_support_bundle


def _config(tmp_path):
    workspace = tmp_path / "private-repository"
    workspace.mkdir()
    state = tmp_path / "private-state"
    return AgentBusConfig(
        workspace_dir=str(workspace),
        state_dir=str(state),
        state_db="state.db",
        runs_dir=str(state / "runs"),
        provider_name="deterministic",
    )


def test_default_support_bundle_is_bounded_and_private(tmp_path):
    config = _config(tmp_path)
    private_marker = "support-private-marker"
    (config.workspace_path / "source.py").write_text(
        f'SECRET = "{private_marker}"\n',
        encoding="utf-8",
    )
    log_path = config.state_database_path.parent / "logs" / "product.log"
    ProductLogWriter(log_path).write(
        level="error",
        component="doctor",
        message="AGENTBUS-E2001 password=hunter2",
        fields={"workspace": str(config.workspace_path), "api_key": private_marker},
    )
    output = tmp_path / "support.zip"

    result = create_support_bundle(
        config,
        output=output,
        registry_path=tmp_path / "registry.json",
    )

    assert result.output == output
    assert result.source_derived_included is False
    assert result.byte_size < 10 * 1024 * 1024
    with zipfile.ZipFile(output) as archive:
        names = set(archive.namelist())
        combined = b"\n".join(archive.read(name) for name in names)
        manifest = json.loads(archive.read("manifest.json"))
    assert "environment.json" in names
    assert "doctor.json" in names
    assert "configuration-shape.json" in names
    assert not any(name.startswith("consented/") for name in names)
    assert private_marker.encode() not in combined
    assert b"hunter2" not in combined
    assert str(config.workspace_path).encode() not in combined
    assert b"source.py" not in combined
    assert manifest["source_derived_included"] is False
    assert "source code" in manifest["excluded_by_default"]


def test_source_derived_run_log_requires_explicit_consent(tmp_path):
    config = _config(tmp_path)
    runs = config.state_database_path.parent / "runs"
    runs.mkdir(parents=True)
    (runs / "20260101_000000_run-1.jsonl").write_text(
        json.dumps(
            {
                "timestamp": "2026-01-01T00:00:00+00:00",
                "run_id": "run-1",
                "type": "completed",
                "data": {"summary": "consented metadata"},
            }
        )
        + "\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="requires --consent-source-derived"):
        create_support_bundle(
            config,
            output=tmp_path / "refused.zip",
            include_run="run-1",
        )

    result = create_support_bundle(
        config,
        output=tmp_path / "consented.zip",
        include_run="run-1",
        consent_source_derived=True,
        registry_path=tmp_path / "registry.json",
    )

    assert result.source_derived_included is True
    with zipfile.ZipFile(result.output) as archive:
        payload = json.loads(archive.read("consented/run-log.json"))
    assert payload["consent_recorded"] is True
    assert payload["run_id"] == "run-1"


def test_support_bundle_refuses_existing_output(tmp_path):
    config = _config(tmp_path)
    output = tmp_path / "support.zip"
    output.write_bytes(b"user data")

    with pytest.raises(ValueError, match="already exists"):
        create_support_bundle(config, output=output)

    assert output.read_bytes() == b"user data"


def test_support_bundle_cli_creates_private_json_report(tmp_path, capsys):
    config = _config(tmp_path)
    config_path = tmp_path / "config.json"
    config_path.write_text(
        json.dumps(
            {
                "workspace_dir": config.workspace_dir,
                "state_dir": config.state_dir,
                "state_db": config.state_db,
                "runs_dir": config.runs_dir,
                "provider_name": "deterministic",
            }
        ),
        encoding="utf-8",
    )
    output = tmp_path / "cli-support.zip"

    exit_code = main(
        [
            "support-bundle",
            "--config",
            str(config_path),
            "--registry-path",
            str(tmp_path / "registry.json"),
            "--output",
            str(output),
            "--json",
        ]
    )

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["output"] == str(output)
    assert payload["source_derived_included"] is False
    assert payload["network_used"] is False
    assert output.is_file()
