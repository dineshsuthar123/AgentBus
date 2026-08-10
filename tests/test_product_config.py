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


def test_configuration_layers_have_documented_precedence(tmp_path):
    user = tmp_path / "user.toml"
    workspace = tmp_path / "repository"
    workspace_config = workspace / ".agentbus" / "config.toml"
    explicit = tmp_path / "explicit.toml"
    workspace_config.parent.mkdir(parents=True)
    user.write_text("[agentbus]\nmax_steps = 2\n", encoding="utf-8")
    workspace_config.write_text("[agentbus]\nmax_steps = 3\n", encoding="utf-8")
    explicit.write_text("[agentbus]\nmax_steps = 4\n", encoding="utf-8")

    resolved = resolve_configuration(
        workspace=workspace,
        user_config_file=user,
        config_file=explicit,
        cli_overrides={"max_steps": 5},
        environ={"AGENTBUS_MAX_STEPS": "6"},
    )

    assert resolved.config.max_steps == 6
    assert resolved.sources["max_steps"] == "environment:AGENTBUS_MAX_STEPS"
    assert resolved.layer_paths == {
        "user": user.resolve(),
        "workspace": workspace_config.resolve(),
        "explicit": explicit.resolve(),
    }


def test_workspace_configuration_never_searches_parent_directories(tmp_path):
    parent_config = tmp_path / ".agentbus" / "config.toml"
    workspace = tmp_path / "child"
    parent_config.parent.mkdir()
    workspace.mkdir()
    parent_config.write_text("[agentbus]\nmax_steps = 2\n", encoding="utf-8")

    resolved = resolve_configuration(
        workspace=workspace,
        user_config_file=tmp_path / "missing-user.toml",
        environ={},
    )

    assert resolved.config.max_steps == AgentBusConfig.max_steps
    assert resolved.layer_paths["workspace"] is None


def test_workspace_configuration_cannot_redirect_execution(tmp_path):
    workspace = tmp_path / "repository"
    config = workspace / ".agentbus" / "config.toml"
    config.parent.mkdir(parents=True)
    config.write_text(
        f"[agentbus]\nworkspace_dir = {str(tmp_path / 'outside')!r}\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="cannot redirect"):
        resolve_configuration(
            workspace=workspace,
            user_config_file=tmp_path / "missing-user.toml",
            environ={},
        )


def test_workspace_configuration_link_outside_workspace_is_rejected(tmp_path):
    workspace = tmp_path / "repository"
    config_dir = workspace / ".agentbus"
    target = tmp_path / "outside.toml"
    config_dir.mkdir(parents=True)
    target.write_text("[agentbus]\nmax_steps = 2\n", encoding="utf-8")
    try:
        (config_dir / "config.toml").symlink_to(target)
    except OSError:
        pytest.skip("file symlinks are unavailable on this platform")

    with pytest.raises(ValueError, match="outside the workspace"):
        resolve_configuration(
            workspace=workspace,
            user_config_file=tmp_path / "missing-user.toml",
            environ={},
        )


def test_configuration_files_cannot_store_credentials(tmp_path):
    config = tmp_path / "config.toml"
    config.write_text(
        "[agentbus]\nazure_openai_api_key = 'must-not-be-stored'\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="cannot contain credentials") as exc:
        resolve_configuration(config_file=config, discover=False, environ={})

    assert "must-not-be-stored" not in str(exc.value)


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
