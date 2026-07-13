import threading

import pytest

from agentbus.evaluation.budget import EvaluationBudget
from agentbus.evaluation.errors import EvaluationBudgetExceeded, ScriptedProviderError
from agentbus.evaluation.providers import (
    BudgetedProviderFactory,
    DeterministicFakeProvider,
    ScriptedOutcome,
    ScriptedResponseStore,
)
from agentbus.models.errors import ModelOutputError, ModelServiceUnavailableError
from agentbus.models.types import ModelRole, ModelUsage
from agentbus.models.types import ModelResult, ModelRoute


def register(store, task_id, attempt, outcome=None):
    store.register(
        case_id="case",
        task_id=task_id,
        role=ModelRole.CODER,
        attempt=attempt,
        outcome=outcome or ScriptedOutcome(value={"task": task_id}),
    )


def call(provider, task_id, attempt=1):
    return provider.generate_json(
        "prompt",
        metadata={"case_id": "case", "task_id": task_id, "attempt": attempt},
    )


def test_fake_provider_routes_by_case_task_role_and_attempt():
    store = ScriptedResponseStore()
    register(store, "task-a", 1, ScriptedOutcome(value={"value": "A"}))
    register(store, "task-a", 2, ScriptedOutcome(value={"value": "A2"}))
    provider = DeterministicFakeProvider(role=ModelRole.CODER, scripts=store)

    assert call(provider, "task-a", 1).json_value() == {"value": "A"}
    assert call(provider, "task-a", 2).json_value() == {"value": "A2"}


def test_fake_provider_is_concurrency_safe_without_cross_task_consumption():
    store = ScriptedResponseStore()
    register(store, "task-a", 1)
    register(store, "task-b", 1)
    provider = DeterministicFakeProvider(role=ModelRole.CODER, scripts=store)
    results = {}

    threads = [
        threading.Thread(
            target=lambda task=task: results.setdefault(task, call(provider, task).json_value())
        )
        for task in ("task-a", "task-b")
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert results == {"task-a": {"task": "task-a"}, "task-b": {"task": "task-b"}}


def test_fake_provider_transient_and_malformed_failures_are_explicit():
    store = ScriptedResponseStore()
    register(store, "transient", 1, ScriptedOutcome(kind="transient_failure"))
    register(store, "malformed", 1, ScriptedOutcome(kind="malformed"))
    provider = DeterministicFakeProvider(role=ModelRole.CODER, scripts=store)

    with pytest.raises(ModelServiceUnavailableError):
        call(provider, "transient")
    with pytest.raises(ModelOutputError):
        call(provider, "malformed")


def test_fake_provider_records_usage_metadata():
    store = ScriptedResponseStore()
    register(
        store,
        "usage",
        1,
        ScriptedOutcome(
            value={"ok": True},
            usage=ModelUsage(input_tokens=2, output_tokens=3, total_tokens=5),
        ),
    )
    provider = DeterministicFakeProvider(role=ModelRole.CODER, scripts=store)

    result = call(provider, "usage")

    assert result.usage.total_tokens == 5
    assert store.calls()[0].task_id == "usage"
    assert store.results() == [result]


def test_fake_provider_refuses_missing_or_reused_correlation():
    store = ScriptedResponseStore()
    register(store, "task", 1)
    provider = DeterministicFakeProvider(role=ModelRole.CODER, scripts=store)

    with pytest.raises(ScriptedProviderError, match="requires"):
        provider.generate_json("prompt")
    call(provider, "task")
    with pytest.raises(ScriptedProviderError, match="consumed twice"):
        call(provider, "task")


def test_budget_blocks_request_before_provider_call():
    store = ScriptedResponseStore()
    register(store, "task", 1)
    budget = EvaluationBudget(max_requests=1, max_tokens=10, timeout_seconds=10)
    provider = DeterministicFakeProvider(
        role=ModelRole.CODER,
        scripts=store,
        budget=budget,
    )

    with pytest.raises(EvaluationBudgetExceeded, match="token budget"):
        call(provider, "task")

    assert store.calls() == []


@pytest.mark.parametrize("timeout", [0, -1, float("nan"), float("inf")])
def test_budget_rejects_non_positive_or_non_finite_timeout(timeout):
    with pytest.raises(ValueError, match="positive"):
        EvaluationBudget(max_requests=1, max_tokens=1, timeout_seconds=timeout)


def test_budget_wrapper_survives_runtime_provider_factory_cloning():
    calls = []

    class InnerProvider:
        provider_name = "ollama"
        model_name = "local"

        def generate_text(self, prompt, **kwargs):
            calls.append(prompt)
            return ModelResult(
                value="ok",
                provider="ollama",
                model="local",
                role=ModelRole.CODER,
                usage=ModelUsage(input_tokens=1, output_tokens=1, total_tokens=2),
            )

    class InnerFactory:
        def create(self, route):
            return InnerProvider()

    budget = EvaluationBudget(max_requests=1, max_tokens=1_000, timeout_seconds=10)
    wrapped = BudgetedProviderFactory(InnerFactory(), budget)
    route = ModelRoute(
        provider="ollama",
        model="local",
        role=ModelRole.CODER,
        timeout_seconds=5,
        max_retries=0,
    )

    cloned_provider = wrapped.builders["ollama"](route)
    cloned_provider.generate_text("bounded")

    assert calls == ["bounded"]
    assert budget.snapshot()["requests"] == 1
