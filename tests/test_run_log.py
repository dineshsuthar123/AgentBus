import json
from datetime import datetime

from agentbus.memory.run_log import RunLogger


def test_run_log_writes_valid_json_lines(tmp_path):
    logger = RunLogger(log_dir=str(tmp_path), run_id="test-run")

    logger.log("run_started", {"task": "test"})
    logger.log("run_finished", {"summary": "done"})

    logs = list(tmp_path.glob("*.jsonl"))
    assert len(logs) == 1

    lines = logs[0].read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2

    for line in lines:
        event = json.loads(line)
        assert event["run_id"] == "test-run"
        assert event["type"] in {"run_started", "run_finished"}
        assert isinstance(event["data"], dict)
        datetime.fromisoformat(event["timestamp"])


def test_run_log_redacts_secret_shaped_values(tmp_path):
    logger = RunLogger(log_dir=str(tmp_path), run_id="safe-run")

    logger.log(
        "security_event",
        {"token": "secret-value", "message": "password=hunter2"},
    )

    content = logger.log_file.read_text(encoding="utf-8")
    event = json.loads(content)
    assert event["data"]["token"] == "[REDACTED]"
    assert "hunter2" not in content
