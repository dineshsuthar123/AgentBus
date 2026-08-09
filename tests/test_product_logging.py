from __future__ import annotations

import json

from agentbus.cli import main
from agentbus.config import AgentBusConfig
from agentbus.product.logging import ProductLogWriter, read_product_logs


def _config(tmp_path):
    return AgentBusConfig(
        workspace_dir=str(tmp_path / "workspace"),
        state_dir=str(tmp_path / "state"),
        state_db="state.db",
        runs_dir=str(tmp_path / "runs"),
        provider_name="deterministic",
    )


def test_product_log_writer_redacts_and_rotates_bounded_files(tmp_path):
    path = tmp_path / "logs" / "product.log"
    writer = ProductLogWriter(path, max_bytes=1_024, retained_files=2)

    for index in range(30):
        writer.write(
            level="info",
            component="doctor",
            message=f"entry {index} Bearer private-token",
            run_id="run-1",
            fields={"api_key": "private-key", "index": index},
        )

    files = sorted(path.parent.glob("product.log*"))
    assert [item.name for item in files] == [
        "product.log",
        "product.log.1",
        "product.log.2",
    ]
    content = "".join(item.read_text(encoding="utf-8") for item in files)
    assert "private-token" not in content
    assert "private-key" not in content
    assert "[REDACTED]" in content
    for line in content.splitlines():
        payload = json.loads(line)
        assert payload["level"] == "info"
        assert payload["component"] == "doctor"


def test_log_reader_filters_runs_and_bounds_output(tmp_path):
    config = _config(tmp_path)
    runs = tmp_path / "runs"
    runs.mkdir()
    for run_id in ("run-1", "run-2"):
        path = runs / f"20260101_000000_{run_id}.jsonl"
        path.write_text(
            "\n".join(
                json.dumps(
                    {
                        "timestamp": f"2026-01-01T00:00:0{index}+00:00",
                        "run_id": run_id,
                        "type": "step",
                        "data": {"index": index, "token": "private"},
                    }
                )
                for index in range(5)
            )
            + "\n",
            encoding="utf-8",
        )

    entries = read_product_logs(config, tail=3, run_id="run-1")

    assert len(entries) == 3
    assert {entry.run_id for entry in entries} == {"run-1"}
    assert all(entry.component == "runtime" for entry in entries)
    assert "private" not in str([entry.to_dict() for entry in entries])


def test_log_reader_never_follows_run_log_symlink(tmp_path):
    config = _config(tmp_path)
    runs = tmp_path / "runs"
    runs.mkdir()
    outside = tmp_path / "outside.jsonl"
    outside.write_text('{"message":"outside-private"}\n', encoding="utf-8")
    link = runs / "20260101_000000_run-1.jsonl"
    try:
        link.symlink_to(outside)
    except OSError:
        return

    assert read_product_logs(config, run_id="run-1") == ()


def test_logs_cli_filters_run_and_returns_redacted_json(tmp_path, capsys):
    config = _config(tmp_path)
    runs = tmp_path / "runs"
    runs.mkdir()
    (runs / "20260101_000000_run-1.jsonl").write_text(
        json.dumps(
            {
                "timestamp": "2026-01-01T00:00:00+00:00",
                "run_id": "run-1",
                "type": "completed",
                "data": {"authorization": "Bearer private-token"},
            }
        )
        + "\n",
        encoding="utf-8",
    )
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

    exit_code = main(
        [
            "logs",
            "--config",
            str(config_path),
            "--run",
            "run-1",
            "--tail",
            "1",
            "--json",
        ]
    )

    assert exit_code == 0
    output = capsys.readouterr().out
    payload = json.loads(output)
    assert len(payload["entries"]) == 1
    assert payload["entries"][0]["run_id"] == "run-1"
    assert "private-token" not in output
    assert payload["network_used"] is False


def test_logs_cli_rejects_unsafe_run_identifier(tmp_path, capsys):
    config_path = tmp_path / "config.json"
    config_path.write_text(
        json.dumps(
            {
                "workspace_dir": str(tmp_path),
                "state_dir": str(tmp_path / "state"),
                "provider_name": "deterministic",
            }
        ),
        encoding="utf-8",
    )

    exit_code = main(
        [
            "logs",
            "--config",
            str(config_path),
            "--run",
            "../outside",
            "--json",
        ]
    )

    assert exit_code == 2
    assert "unsupported" in json.loads(capsys.readouterr().out)["error"]
