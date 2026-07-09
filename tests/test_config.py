import pytest

from agentbus.config import AgentBusConfig


ENV_VARS = [
    "AGENTBUS_MODEL",
    "AGENTBUS_OLLAMA_URL",
    "AGENTBUS_WORKSPACE",
    "AGENTBUS_RUNS_DIR",
    "AGENTBUS_MAX_STEPS",
    "AGENTBUS_COMMAND_TIMEOUT",
    "AGENTBUS_MAX_HISTORY_CHARS",
]


def test_default_config(monkeypatch):
    for name in ENV_VARS:
        monkeypatch.delenv(name, raising=False)

    config = AgentBusConfig.from_env()

    assert config.model_name == "qwen2.5-coder:7b"
    assert config.ollama_url == "http://localhost:11434/api/generate"
    assert config.workspace_dir == "workspace"
    assert config.runs_dir == "runs"
    assert config.max_steps == 12
    assert config.command_timeout_seconds == 90
    assert config.max_history_chars == 25_000


def test_env_overrides(monkeypatch):
    monkeypatch.setenv("AGENTBUS_MODEL", "custom-model")
    monkeypatch.setenv("AGENTBUS_OLLAMA_URL", "http://localhost:1234/api/generate")
    monkeypatch.setenv("AGENTBUS_WORKSPACE", "custom-workspace")
    monkeypatch.setenv("AGENTBUS_RUNS_DIR", "custom-runs")
    monkeypatch.setenv("AGENTBUS_MAX_STEPS", "3")
    monkeypatch.setenv("AGENTBUS_COMMAND_TIMEOUT", "4")
    monkeypatch.setenv("AGENTBUS_MAX_HISTORY_CHARS", "500")

    config = AgentBusConfig.from_env()

    assert config.model_name == "custom-model"
    assert config.ollama_url == "http://localhost:1234/api/generate"
    assert config.workspace_dir == "custom-workspace"
    assert config.runs_dir == "custom-runs"
    assert config.max_steps == 3
    assert config.command_timeout_seconds == 4
    assert config.max_history_chars == 500


def test_invalid_env_int(monkeypatch):
    monkeypatch.setenv("AGENTBUS_MAX_STEPS", "many")

    with pytest.raises(ValueError, match="AGENTBUS_MAX_STEPS"):
        AgentBusConfig.from_env()
