import pytest

from agentbus.config import AgentBusConfig
from agentbus.memory.run_log import RunLogger
from agentbus.models.base import ModelProvider
from agentbus.models.errors import (
    ModelAuthenticationError,
    ModelAuthorizationError,
    ModelBadRequestError,
    ModelConfigurationError,
    ModelContentPolicyError,
    ModelNotFoundError,
    ModelOutputError,
    ModelQuotaExceededError,
    ModelRateLimitError,
    ModelSchemaValidationError,
    ModelServiceUnavailableError,
    ModelTimeoutError,
)
from agentbus.models.router import (
    ModelProviderFactory,
    ModelRouter,
    model_request_context,
)
from agentbus.models.types import ModelResult, ModelRole, ModelUsage


class FakeProvider:
    def __init__(self, name, model, outcomes):
        self._name = name
        self._model = model
        self.outcomes = list(outcomes)
        self.calls = []

    @property
    def provider_name(self):
        return self._name

    @property
    def model_name(self):
        return self._model

    def generate_json(self, prompt, **kwargs):
        self.calls.append((prompt, kwargs))
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    generate_text = generate_json


def azure_config(**overrides):
    values = {
        "provider_name": "azure",
        "azure_openai_endpoint": "https://sample.openai.azure.com",
        "azure_openai_api_key": "fake-key",
        "azure_openai_default_deployment": "default-deployment",
        "azure_openai_planner_deployment": "planner-deployment",
        "azure_openai_coder_deployment": "coder-deployment",
        "azure_openai_reviewer_deployment": "reviewer-deployment",
        "model_max_retries": 2,
        "model_retry_base_seconds": 0.5,
        "model_retry_max_seconds": 5,
    }
    values.update(overrides)
    return AgentBusConfig(**values)


def result(provider="azure", model="deployment", role=ModelRole.DEFAULT, tokens=3):
    return ModelResult(
        value={"ok": True},
        provider=provider,
        model=model,
        role=role,
        usage=ModelUsage(input_tokens=tokens, output_tokens=1, total_tokens=tokens + 1),
    )


def router_with(config, azure, ollama=None, **kwargs):
    builders = {"azure": lambda route: azure}
    if ollama is not None:
        builders["ollama"] = lambda route: ollama
    factory = ModelProviderFactory(config, builders=builders)
    return ModelRouter(config, provider_factory=factory, **kwargs)


def test_role_routing_uses_specific_then_default_deployments():
    config = azure_config()
    assert config.resolve_model("planner") == "planner-deployment"
    assert config.resolve_model("coder") == "coder-deployment"
    assert config.resolve_model("reviewer") == "reviewer-deployment"
    assert config.resolve_model("summarizer") == "default-deployment"


def test_provider_protocol_declares_text_and_json_generation():
    assert hasattr(ModelProvider, "generate_text")
    assert hasattr(ModelProvider, "generate_json")


def test_missing_azure_role_and_default_deployment_is_actionable():
    config = azure_config(
        azure_openai_default_deployment=None,
        azure_openai_planner_deployment=None,
    )
    with pytest.raises(ValueError, match="AZURE_OPENAI_PLANNER_DEPLOYMENT"):
        config.resolve_model("planner")


def test_ollama_route_preserves_existing_model_and_url():
    config = AgentBusConfig(model_name="local-model")
    route = ModelRouter(config).route_for(ModelRole.CODER)

    assert route.provider == "ollama"
    assert route.model == "local-model"
    assert route.fallback_enabled is False


def test_transient_failure_retries_with_deterministic_backoff():
    config = azure_config()
    transient = ModelServiceUnavailableError(
        "temporary",
        provider="azure",
        model="planner-deployment",
    )
    fake = FakeProvider(
        "azure",
        "planner-deployment",
        [transient, transient, result(model="planner-deployment")],
    )
    sleeps = []
    router = router_with(config, fake, sleeper=sleeps.append, jitter=lambda: 0)

    actual = router.generate_json(ModelRole.PLANNER, "secret source prompt")

    assert actual.retry_count == 2
    assert sleeps == [0.5, 1.0]
    assert len(fake.calls) == 3


def test_retry_after_is_honored_and_capped():
    config = azure_config(model_retry_max_seconds=4)
    error = ModelRateLimitError(
        "rate",
        provider="azure",
        model="default-deployment",
        retry_after_seconds=3,
    )
    fake = FakeProvider("azure", "default-deployment", [error, result()])
    sleeps = []
    router = router_with(config, fake, sleeper=sleeps.append)

    router.generate_json(ModelRole.DEFAULT, "prompt")

    assert sleeps == [3]


def test_retry_count_is_bounded():
    config = azure_config(model_max_retries=1)
    errors = [
        ModelTimeoutError("timeout", provider="azure", model="default-deployment")
        for _ in range(2)
    ]
    fake = FakeProvider("azure", "default-deployment", errors)
    router = router_with(config, fake, sleeper=lambda delay: None, jitter=lambda: 0)

    with pytest.raises(ModelTimeoutError):
        router.generate_json(ModelRole.DEFAULT, "prompt")

    assert len(fake.calls) == 2


def test_authentication_failure_is_not_retried_or_fallen_back():
    config = azure_config(
        enable_provider_fallback=True,
        fallback_provider_name="ollama",
    )
    error = ModelAuthenticationError(
        "authentication failed",
        provider="azure",
        model="default-deployment",
    )
    azure = FakeProvider("azure", "default-deployment", [error])
    ollama = FakeProvider("ollama", "local", [result(provider="ollama")])
    router = router_with(config, azure, ollama, sleeper=lambda delay: None)

    with pytest.raises(ModelAuthenticationError):
        router.generate_json(ModelRole.DEFAULT, "prompt")

    assert len(azure.calls) == 1
    assert ollama.calls == []


@pytest.mark.parametrize(
    "error",
    [
        ModelOutputError("invalid", provider="azure", model="default-deployment"),
        ModelAuthenticationError(
            "auth", provider="azure", model="default-deployment"
        ),
    ],
)
def test_noneligible_errors_never_fall_back(error):
    config = azure_config(
        enable_provider_fallback=True,
        fallback_provider_name="ollama",
    )
    azure = FakeProvider("azure", "default-deployment", [error])
    ollama = FakeProvider("ollama", "local", [result(provider="ollama")])
    router = router_with(config, azure, ollama, sleeper=lambda delay: None)

    with pytest.raises(type(error)):
        router.generate_json(ModelRole.DEFAULT, "prompt")

    assert ollama.calls == []


@pytest.mark.parametrize(
    "error_type",
    [
        ModelAuthenticationError,
        ModelAuthorizationError,
        ModelNotFoundError,
        ModelQuotaExceededError,
        ModelBadRequestError,
        ModelContentPolicyError,
        ModelConfigurationError,
        ModelSchemaValidationError,
    ],
)
def test_auth_configuration_policy_and_schema_errors_never_fall_back(error_type):
    config = azure_config(
        enable_provider_fallback=True,
        fallback_provider_name="ollama",
        model_max_retries=0,
    )
    error = error_type(
        "safe failure",
        provider="azure",
        model="default-deployment",
    )
    azure = FakeProvider("azure", "default-deployment", [error])
    ollama = FakeProvider("ollama", "local", [result(provider="ollama")])

    with pytest.raises(error_type):
        router_with(config, azure, ollama).generate_json(
            ModelRole.DEFAULT,
            "prompt",
        )

    assert len(azure.calls) == 1
    assert ollama.calls == []


@pytest.mark.parametrize("error_type", [ModelTimeoutError, ModelRateLimitError])
def test_timeout_and_exhausted_rate_limit_may_fall_back(error_type):
    config = azure_config(
        enable_provider_fallback=True,
        fallback_provider_name="ollama",
        model_max_retries=0,
    )
    error = error_type(
        "transient",
        provider="azure",
        model="default-deployment",
    )
    azure = FakeProvider("azure", "default-deployment", [error])
    ollama = FakeProvider(
        "ollama",
        "local",
        [result(provider="ollama", model="local")],
    )

    actual = router_with(config, azure, ollama).generate_json(
        ModelRole.DEFAULT,
        "prompt",
    )

    assert actual.fallback_used is True
    assert actual.original_error_category == error.error_category


def test_exhausted_transient_failure_uses_explicit_ollama_fallback():
    config = azure_config(
        enable_provider_fallback=True,
        fallback_provider_name="ollama",
        model_max_retries=1,
    )
    errors = [
        ModelServiceUnavailableError(
            "temporary",
            provider="azure",
            model="default-deployment",
        )
        for _ in range(2)
    ]
    azure = FakeProvider("azure", "default-deployment", errors)
    fallback_result = result(provider="ollama", model="local-model")
    ollama = FakeProvider("ollama", "local-model", [fallback_result])
    router = router_with(
        config,
        azure,
        ollama,
        sleeper=lambda delay: None,
        jitter=lambda: 0,
    )

    actual = router.generate_json(ModelRole.DEFAULT, "prompt")

    assert actual.provider == "ollama"
    assert actual.fallback_used is True
    assert actual.original_provider == "azure"
    assert actual.original_error_category == "service_unavailable"
    assert len(azure.calls) == 2
    assert len(ollama.calls) == 1
    assert router.usage_ledger.records()[0].result.fallback_used is True


def test_fallback_is_disabled_by_default():
    config = azure_config(model_max_retries=0)
    error = ModelTimeoutError(
        "timeout",
        provider="azure",
        model="default-deployment",
    )
    azure = FakeProvider("azure", "default-deployment", [error])
    ollama = FakeProvider("ollama", "local", [result(provider="ollama")])

    with pytest.raises(ModelTimeoutError):
        router_with(config, azure, ollama).generate_json(
            ModelRole.DEFAULT,
            "prompt",
        )

    assert ollama.calls == []


def test_usage_aggregates_by_run_task_role_provider_and_model():
    config = azure_config(model_max_retries=0)
    fake = FakeProvider(
        "azure",
        "planner-deployment",
        [
            result(model="planner-deployment", role=ModelRole.PLANNER, tokens=5),
            result(model="planner-deployment", role=ModelRole.PLANNER, tokens=7),
        ],
    )
    router = router_with(config, fake)

    with model_request_context(run_id="run-1", task_id="task-1"):
        router.generate_json(ModelRole.PLANNER, "first")
        router.generate_json(ModelRole.PLANNER, "second")

    total = router.usage_ledger.total(
        run_id="run-1",
        task_id="task-1",
        role="planner",
        provider="azure",
        model="planner-deployment",
    )
    assert total.input_tokens == 12
    assert total.output_tokens == 2
    assert total.total_tokens == 14


def test_router_events_never_log_prompt_or_secret(tmp_path):
    config = azure_config(model_max_retries=0)
    fake = FakeProvider("azure", "default-deployment", [result()])
    logger = RunLogger(log_dir=str(tmp_path))
    router = router_with(config, fake, logger=logger)

    router.generate_json(
        ModelRole.DEFAULT,
        "TOP-SECRET-SOURCE-PROMPT",
        metadata={"api_key": "super-secret-key"},
    )

    log = logger.log_file.read_text(encoding="utf-8")
    assert "TOP-SECRET-SOURCE-PROMPT" not in log
    assert "super-secret-key" not in log
    assert "model_route_selected" in log
    assert "model_usage_recorded" in log
