import pytest

from agentbus.config import AgentBusConfig


ENV_VARS = [
    "AGENTBUS_MODEL",
    "AGENTBUS_OLLAMA_URL",
    "AGENTBUS_WORKSPACE",
    "AGENTBUS_RUNS_DIR",
    "AGENTBUS_STATE_DIR",
    "AGENTBUS_STATE_DB",
    "AGENTBUS_MAX_STEPS",
    "AGENTBUS_COMMAND_TIMEOUT",
    "AGENTBUS_MAX_HISTORY_CHARS",
    "AGENTBUS_PROVIDER",
    "AGENTBUS_FALLBACK_PROVIDER",
    "AGENTBUS_ENABLE_PROVIDER_FALLBACK",
    "AGENTBUS_MODEL_TIMEOUT_SECONDS",
    "AGENTBUS_MODEL_MAX_RETRIES",
    "AGENTBUS_MODEL_RETRY_BASE_SECONDS",
    "AGENTBUS_MODEL_RETRY_MAX_SECONDS",
    "AZURE_OPENAI_ENDPOINT",
    "AZURE_OPENAI_API_KEY",
    "AZURE_OPENAI_AUTH_MODE",
    "AZURE_OPENAI_API_MODE",
    "AZURE_OPENAI_DEFAULT_DEPLOYMENT",
    "AZURE_OPENAI_PLANNER_DEPLOYMENT",
    "AZURE_OPENAI_CODER_DEPLOYMENT",
    "AZURE_OPENAI_REVIEWER_DEPLOYMENT",
    "AZURE_OPENAI_SUMMARIZER_DEPLOYMENT",
    "AZURE_OPENAI_TIMEOUT_SECONDS",
    "AZURE_OPENAI_MAX_RETRIES",
]


def test_default_config(monkeypatch):
    for name in ENV_VARS:
        monkeypatch.delenv(name, raising=False)

    config = AgentBusConfig.from_env()

    assert config.model_name == "qwen2.5-coder:7b"
    assert config.ollama_url == "http://localhost:11434/api/generate"
    assert config.workspace_dir == "workspace"
    assert config.runs_dir == "runs"
    assert config.state_database_path.as_posix() == ".agentbus/state.db"
    assert config.max_steps == 12
    assert config.command_timeout_seconds == 90
    assert config.max_history_chars == 25_000
    assert config.provider_name == "ollama"
    assert config.enable_provider_fallback is False
    assert config.fallback_provider_name == "ollama"


def test_env_overrides(monkeypatch):
    monkeypatch.setenv("AGENTBUS_MODEL", "custom-model")
    monkeypatch.setenv("AGENTBUS_OLLAMA_URL", "http://localhost:1234/api/generate")
    monkeypatch.setenv("AGENTBUS_WORKSPACE", "custom-workspace")
    monkeypatch.setenv("AGENTBUS_RUNS_DIR", "custom-runs")
    monkeypatch.setenv("AGENTBUS_STATE_DIR", "custom-state")
    monkeypatch.setenv("AGENTBUS_STATE_DB", "durable.sqlite3")
    monkeypatch.setenv("AGENTBUS_MAX_STEPS", "3")
    monkeypatch.setenv("AGENTBUS_COMMAND_TIMEOUT", "4")
    monkeypatch.setenv("AGENTBUS_MAX_HISTORY_CHARS", "500")

    config = AgentBusConfig.from_env()

    assert config.model_name == "custom-model"
    assert config.ollama_url == "http://localhost:1234/api/generate"
    assert config.workspace_dir == "custom-workspace"
    assert config.runs_dir == "custom-runs"
    assert config.state_database_path.as_posix() == "custom-state/durable.sqlite3"
    assert config.max_steps == 3
    assert config.command_timeout_seconds == 4
    assert config.max_history_chars == 500


def test_invalid_env_int(monkeypatch):
    monkeypatch.setenv("AGENTBUS_MAX_STEPS", "many")

    with pytest.raises(ValueError, match="AGENTBUS_MAX_STEPS"):
        AgentBusConfig.from_env()


def test_azure_configuration_loads_without_constructing_a_client(monkeypatch):
    for name in ENV_VARS:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("AGENTBUS_PROVIDER", "azure")
    monkeypatch.setenv(
        "AZURE_OPENAI_ENDPOINT",
        "https://sample.openai.azure.com/openai/v1/",
    )
    monkeypatch.setenv("AZURE_OPENAI_API_KEY", "fake-secret-key")
    monkeypatch.setenv("AZURE_OPENAI_DEFAULT_DEPLOYMENT", "default-deployment")
    monkeypatch.setenv("AZURE_OPENAI_PLANNER_DEPLOYMENT", "planner-deployment")
    monkeypatch.setenv("AZURE_OPENAI_CODER_DEPLOYMENT", "coder-deployment")
    monkeypatch.setenv("AZURE_OPENAI_REVIEWER_DEPLOYMENT", "reviewer-deployment")
    monkeypatch.setenv("AZURE_OPENAI_TIMEOUT_SECONDS", "30.5")
    monkeypatch.setenv("AZURE_OPENAI_MAX_RETRIES", "1")

    config = AgentBusConfig.from_env()

    assert config.provider_name == "azure"
    assert config.resolve_model("planner") == "planner-deployment"
    assert config.resolve_model("coder") == "coder-deployment"
    assert config.resolve_model("summarizer") == "default-deployment"
    assert config.route_timeout("azure") == 30.5
    assert config.route_max_retries("azure") == 1
    assert config.validate_provider_configuration("azure") == "default-deployment"


def test_empty_environment_values_are_treated_as_unset(monkeypatch):
    for name in ENV_VARS:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("AGENTBUS_PROVIDER", "   ")
    monkeypatch.setenv("AZURE_OPENAI_API_KEY", "  ")
    monkeypatch.setenv("AZURE_OPENAI_DEFAULT_DEPLOYMENT", "")

    config = AgentBusConfig.from_env()

    assert config.provider_name == "ollama"
    assert config.azure_openai_api_key is None
    assert config.azure_openai_default_deployment is None


def test_selected_azure_validation_rejects_missing_requirements():
    config = AgentBusConfig(provider_name="azure")

    with pytest.raises(ValueError, match="deployment"):
        config.validate_provider_configuration("azure")

    config = AgentBusConfig(
        provider_name="azure",
        azure_openai_default_deployment="deployment",
    )
    with pytest.raises(ValueError, match="AZURE_OPENAI_ENDPOINT"):
        config.validate_provider_configuration("azure")

    config = AgentBusConfig(
        provider_name="azure",
        azure_openai_default_deployment="deployment",
        azure_openai_endpoint="https://sample.openai.azure.com",
    )
    with pytest.raises(ValueError, match="AZURE_OPENAI_API_KEY"):
        config.validate_provider_configuration("azure")


def test_config_repr_and_safe_summary_never_expose_secret_or_endpoint_query():
    config = AgentBusConfig(
        azure_openai_endpoint="https://sample.openai.azure.com?token=secret-query",
        azure_openai_api_key="super-secret-key",
    )

    rendered = repr(config)
    summary = str(config.safe_model_summary())

    assert "super-secret-key" not in rendered + summary
    assert "secret-query" not in rendered + summary
    assert "sample.openai.azure.com" in summary


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("AGENTBUS_MODEL_TIMEOUT_SECONDS", "0"),
        ("AGENTBUS_MODEL_MAX_RETRIES", "-1"),
        ("AGENTBUS_MODEL_RETRY_BASE_SECONDS", "-0.1"),
        ("AZURE_OPENAI_TIMEOUT_SECONDS", "0"),
        ("AZURE_OPENAI_MAX_RETRIES", "-1"),
    ],
)
def test_invalid_numeric_ranges_are_rejected(monkeypatch, name, value):
    monkeypatch.setenv(name, value)

    with pytest.raises(ValueError, match=name):
        AgentBusConfig.from_env()


def test_invalid_provider_and_unsafe_fallback_policy_are_rejected(monkeypatch):
    monkeypatch.setenv("AGENTBUS_PROVIDER", "unknown")
    with pytest.raises(ValueError, match="AGENTBUS_PROVIDER"):
        AgentBusConfig.from_env()

    monkeypatch.setenv("AGENTBUS_PROVIDER", "ollama")
    monkeypatch.setenv("AGENTBUS_ENABLE_PROVIDER_FALLBACK", "true")
    with pytest.raises(ValueError, match="Azure to Ollama"):
        AgentBusConfig.from_env()


@pytest.mark.parametrize(
    "updates",
    [
        {"model_timeout_seconds": float("nan")},
        {"model_retry_base_seconds": float("inf")},
        {"azure_openai_timeout_seconds": -1},
        {"azure_openai_max_retries": -1},
    ],
)
def test_direct_config_construction_rejects_nonfinite_and_invalid_ranges(updates):
    with pytest.raises(ValueError):
        AgentBusConfig(**updates)
