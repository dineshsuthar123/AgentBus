from __future__ import annotations

import json

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
