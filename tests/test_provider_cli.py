import sys

import pytest

from agentbus import main as main_module
from agentbus.config import AgentBusConfig
from agentbus.models.types import ModelResult, ModelUsage


def azure_config():
    return AgentBusConfig(
        provider_name="azure",
        azure_openai_endpoint="https://sample.openai.azure.com",
        azure_openai_api_key="cli-super-secret",
        azure_openai_default_deployment="default-deployment",
        azure_openai_planner_deployment="planner-deployment",
        azure_openai_coder_deployment="coder-deployment",
        azure_openai_reviewer_deployment="reviewer-deployment",
    )


def test_cli_parses_provider_and_role_overrides(monkeypatch):
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "agentbus.main",
            "--provider",
            "azure",
            "--fallback-provider",
            "ollama",
            "--enable-provider-fallback",
            "--model",
            "default-deployment",
            "--planner-model",
            "planner-deployment",
            "--coder-model",
            "coder-deployment",
            "--reviewer-model",
            "reviewer-deployment",
            "--model-timeout",
            "45",
            "Task",
        ],
    )

    args = main_module.parse_args()

    assert args.provider == "azure"
    assert args.enable_provider_fallback is True
    assert args.planner_model == "planner-deployment"
    assert args.model_timeout == 45


def test_list_providers_never_prompts_or_constructs_router(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["agentbus.main", "--list-providers"])
    monkeypatch.setattr(
        "builtins.input",
        lambda prompt: pytest.fail("diagnostics must not prompt"),
    )
    monkeypatch.setattr(
        main_module,
        "build_model_router",
        lambda config: pytest.fail("list must not construct router"),
    )

    assert main_module.main() == 0
    output = capsys.readouterr().out
    assert "ollama" in output
    assert "azure" in output


def test_show_model_config_redacts_key(monkeypatch, capsys):
    settings = azure_config()
    monkeypatch.setattr(main_module.AgentBusConfig, "from_env", lambda: settings)
    monkeypatch.setattr(sys, "argv", ["agentbus.main", "--show-model-config"])

    assert main_module.main() == 0
    output = capsys.readouterr().out
    assert "cli-super-secret" not in output
    assert '"azure_api_key_configured": true' in output
    assert "sample.openai.azure.com" in output


def test_check_azure_without_live_is_local_only(monkeypatch, capsys):
    settings = azure_config()
    monkeypatch.setattr(main_module.AgentBusConfig, "from_env", lambda: settings)
    monkeypatch.setattr(sys, "argv", ["agentbus.main", "--check-provider", "azure"])
    monkeypatch.setattr(
        main_module,
        "build_model_router",
        lambda config: pytest.fail("non-live check must not construct router"),
    )

    assert main_module.main() == 0
    output = capsys.readouterr().out
    assert "Readiness: locally configured" in output
    assert "Network request: skipped" in output


def test_check_azure_reports_all_missing_configuration(monkeypatch, capsys):
    monkeypatch.setattr(
        main_module.AgentBusConfig,
        "from_env",
        lambda: AgentBusConfig(),
    )
    monkeypatch.setattr(sys, "argv", ["agentbus.main", "--check-provider", "azure"])

    assert main_module.main() == 1
    output = capsys.readouterr().out
    assert "AZURE_OPENAI_DEFAULT_DEPLOYMENT" in output
    assert "AZURE_OPENAI_ENDPOINT" in output
    assert "AZURE_OPENAI_API_KEY" in output
    assert "Network request" not in output


def test_check_ollama_without_live_does_not_make_request(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["agentbus.main", "--check-provider", "ollama"])
    monkeypatch.setattr(
        main_module,
        "build_model_router",
        lambda config: pytest.fail("non-live check must not construct router"),
    )

    assert main_module.main() == 0
    output = capsys.readouterr().out
    assert "Provider: ollama" in output
    assert "Network request: skipped" in output


def test_explicit_live_check_uses_injected_fake_router(monkeypatch, capsys):
    settings = azure_config()
    seen = []

    class FakeRouter:
        def generate_json(self, role, prompt, **kwargs):
            seen.append((role, prompt, kwargs))
            return ModelResult(
                value={"status": "ok"},
                provider="azure",
                model="default-deployment",
                request_id="fake-request",
                latency_seconds=0.01,
                usage=ModelUsage(input_tokens=2, output_tokens=1, total_tokens=3),
            )

    monkeypatch.setattr(main_module.AgentBusConfig, "from_env", lambda: settings)
    monkeypatch.setattr(main_module, "build_model_router", lambda config: FakeRouter())
    monkeypatch.setattr(
        sys,
        "argv",
        ["agentbus.main", "--check-provider", "azure", "--live"],
    )

    assert main_module.main() == 0
    output = capsys.readouterr().out
    assert len(seen) == 1
    assert "LIVE opt-in" in output
    assert "fake-request" in output
    assert "cli-super-secret" not in output


def test_live_without_check_provider_is_rejected(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["agentbus.main", "--live"])

    with pytest.raises(SystemExit) as captured:
        main_module.parse_args()

    assert captured.value.code == 2
