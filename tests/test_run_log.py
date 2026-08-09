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
        assert event["level"] == "info"
        assert event["component"] == "runtime"
        assert event["run_id"] == "test-run"
        assert event["type"] in {"run_started", "run_finished"}
        assert isinstance(event["data"], dict)
        datetime.fromisoformat(event["timestamp"])


def test_run_log_includes_safe_task_and_invocation_context(tmp_path):
    logger = RunLogger(log_dir=str(tmp_path), run_id="context-run")

    logger.log(
        "tool_failed",
        {"task_id": "step-1", "invocation_id": "invoke-1", "error": "safe"},
    )

    event = json.loads(logger.log_file.read_text(encoding="utf-8"))
    assert event["level"] == "error"
    assert event["task_id"] == "step-1"
    assert event["invocation_id"] == "invoke-1"


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


def test_run_log_redacts_url_queries_and_bearer_tokens(tmp_path):
    logger = RunLogger(log_dir=str(tmp_path), run_id="safe-url-run")
    logger.log(
        "security_event",
        {
            "message": (
                "Bearer token-value "
                "https://example.test/path?sig=credential-value"
            )
        },
    )

    content = logger.log_file.read_text(encoding="utf-8")
    assert "token-value" not in content
    assert "credential-value" not in content
    assert "[REDACTED]" in content
