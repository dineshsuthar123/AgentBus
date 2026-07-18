from __future__ import annotations

import threading
from types import SimpleNamespace

import pytest

from agentbus.agents.reviewer import ReviewerOutput
from agentbus.execution.cancellation import (
    CancellationRequested,
    CancellationToken,
)
from agentbus.models.azure_openai import AzureOpenAIProvider
from agentbus.models.deterministic import DeterministicProvider
from agentbus.models.errors import ModelCancellationError
from agentbus.models.ollama import OllamaProvider
from agentbus.models.types import ModelRole


def test_cancellation_request_is_thread_safe_and_idempotent():
    token = CancellationToken()
    barrier = threading.Barrier(12)
    results: list[bool] = []
    result_lock = threading.Lock()

    def request() -> None:
        barrier.wait()
        result = token.request("first reason")
        with result_lock:
            results.append(result)

    threads = [threading.Thread(target=request) for _ in range(12)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=2)

    assert all(not thread.is_alive() for thread in threads)
    assert results.count(True) == 1
    assert results.count(False) == 11
    state = token.snapshot()
    assert state.requested is True
    assert state.reason == "first reason"
    assert state.propagation_sources == ["cancellation-token"]

    assert token.acknowledge("worker", stage="before-verifier") is True
    assert token.acknowledge("reviewer", stage="later") is False
    with pytest.raises(CancellationRequested):
        token.checkpoint("scheduler", stage="next-round")
    state = token.snapshot()
    assert state.acknowledgement_source == "worker"
    assert state.acknowledgement_stage == "before-verifier"


def test_deterministic_provider_cooperatively_interrupts_injected_latency():
    token = CancellationToken()
    provider = DeterministicProvider(
        role=ModelRole.REVIEWER,
        latency_seconds=60,
    )
    errors: list[Exception] = []

    def generate() -> None:
        try:
            provider.generate_json(
                "review",
                schema=ReviewerOutput,
                cancellation=token,
            )
        except Exception as exc:
            errors.append(exc)

    thread = threading.Thread(target=generate)
    thread.start()
    operation = token.wait_for_active_operation(
        source="provider:deterministic",
        timeout_seconds=2,
    )
    assert operation is not None

    assert token.request("stop deterministic provider") is True
    thread.join(timeout=2)

    assert thread.is_alive() is False
    assert len(errors) == 1
    assert isinstance(errors[0], ModelCancellationError)
    state = token.snapshot()
    assert state.acknowledged is True
    assert state.provider_cancellation_acknowledged_at is not None
    assert state.provider_acknowledgement_source == "provider:deterministic"
    assert state.operations_completed_after_request == []


def test_ollama_reports_non_interruptible_completion_after_cancellation(
    monkeypatch,
):
    token = CancellationToken()

    class Response:
        def raise_for_status(self) -> None:
            return None

        def json(self):
            return {
                "response": "completed normally",
                "done": True,
                "prompt_eval_count": 3,
                "eval_count": 2,
            }

    def post(*args, **kwargs):
        token.request("cancel during Ollama request")
        return Response()

    monkeypatch.setattr("agentbus.models.ollama.requests.post", post)
    provider = OllamaProvider(
        model="offline",
        url="http://localhost:11434/api/generate",
    )

    result = provider.generate_text("hello", cancellation=token)

    assert result.text_value() == "completed normally"
    assert result.cancellation_requested is True
    assert result.cancellation_acknowledged is True
    assert result.cancellation_supported is False
    assert result.completed_after_cancellation is True
    state = token.snapshot()
    assert state.operations_completed_after_request == ["ollama.http_request"]


def test_azure_reports_non_interruptible_completion_after_cancellation():
    token = CancellationToken()
    response = SimpleNamespace(
        output_text="completed normally",
        _request_id="azure-request",
        usage=SimpleNamespace(
            input_tokens=4,
            output_tokens=2,
            total_tokens=6,
            input_tokens_details=SimpleNamespace(cached_tokens=0),
        ),
        status="completed",
    )

    class Responses:
        def create(self, **kwargs):
            token.request("cancel during Azure request")
            return response

    provider = AzureOpenAIProvider(
        endpoint="https://sample.openai.azure.com",
        api_key="offline-test-key",
        deployment="deployment",
        client=SimpleNamespace(responses=Responses()),
    )

    result = provider.generate_text("hello", cancellation=token)

    assert result.text_value() == "completed normally"
    assert result.cancellation_requested is True
    assert result.cancellation_acknowledged is True
    assert result.cancellation_supported is False
    assert result.completed_after_cancellation is True
    state = token.snapshot()
    assert state.operations_completed_after_request == [
        "azure_openai.sdk_request"
    ]


@pytest.mark.parametrize(
    "provider",
    [
        OllamaProvider(
            model="offline",
            url="http://localhost:11434/api/generate",
        ),
        AzureOpenAIProvider(
            endpoint="https://sample.openai.azure.com",
            api_key="offline-test-key",
            deployment="deployment",
            client=SimpleNamespace(),
        ),
    ],
)
def test_real_provider_adapters_do_not_start_transport_after_prior_cancellation(
    provider,
):
    token = CancellationToken()
    token.request("already cancelled")

    with pytest.raises(ModelCancellationError) as captured:
        provider.generate_text("must not execute", cancellation=token)

    assert captured.value.retryable is False
    state = token.snapshot()
    assert state.provider_cancellation_acknowledged_at is not None
