import pytest

from agentbus.config import AgentBusConfig
from agentbus.configuration import resolve_configuration


def test_product_configuration_defaults_are_safe_and_offline():
    config = AgentBusConfig()

    assert config.durable_execution is True
    assert config.policy_mode == "enforce"
    assert config.repository_intelligence is True
    assert config.semantic_retrieval is False
    assert config.trace_retention_days == 30
    assert config.daemon_auto_start is True
    assert config.daemon_idle_timeout_seconds == 86_400
    assert config.log_level == "warning"
    assert config.log_retention_files == 5
    assert config.vscode_default_workflow == "multi"
    assert config.vscode_default_durable is True
    assert config.vscode_default_parallel is False


def test_product_environment_overrides_are_explicitly_parsed():
    resolved = resolve_configuration(
        environ={
            "AGENTBUS_DURABLE_EXECUTION": "false",
            "AGENTBUS_REPOSITORY_INTELLIGENCE": "false",
            "AGENTBUS_SEMANTIC_RETRIEVAL": "true",
            "AGENTBUS_TRACE_RETENTION_DAYS": "7",
            "AGENTBUS_DAEMON_AUTO_START": "false",
            "AGENTBUS_DAEMON_IDLE_TIMEOUT_SECONDS": "0",
            "AGENTBUS_LOG_LEVEL": "DEBUG",
            "AGENTBUS_LOG_RETENTION_FILES": "12",
        }
    )

    config = resolved.config
    assert config.durable_execution is False
    assert config.repository_intelligence is False
    assert config.semantic_retrieval is True
    assert config.trace_retention_days == 7
    assert config.daemon_auto_start is False
    assert config.daemon_idle_timeout_seconds == 0
    assert config.log_level == "debug"
    assert config.log_retention_files == 12
    assert resolved.sources["log_level"] == "environment:AGENTBUS_LOG_LEVEL"


@pytest.mark.parametrize(
    "updates",
    [
        {"policy_mode": "audit"},
        {"trace_retention_days": 0},
        {"daemon_idle_timeout_seconds": -1},
        {"log_level": "verbose"},
        {"log_retention_files": 0},
        {"log_retention_files": 101},
        {"vscode_default_workflow": "automatic"},
    ],
)
def test_unsafe_or_invalid_product_configuration_is_rejected(updates):
    with pytest.raises(ValueError):
        AgentBusConfig(**updates)
