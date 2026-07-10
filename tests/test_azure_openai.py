from types import SimpleNamespace
from typing import Literal

import pytest
from pydantic import BaseModel, ConfigDict

from agentbus.models.azure_openai import (
    AzureOpenAIProvider,
    map_azure_exception,
    normalize_azure_v1_endpoint,
)
from agentbus.models.errors import (
    ModelAuthenticationError,
    ModelAuthorizationError,
    ModelBadRequestError,
    ModelConfigurationError,
    ModelContentPolicyError,
    ModelNotFoundError,
    ModelOutputError,
    ModelProviderError,
    ModelQuotaExceededError,
    ModelRateLimitError,
    ModelSchemaValidationError,
    ModelServiceUnavailableError,
    ModelTimeoutError,
    ModelTransportError,
)


class Detail(BaseModel):
    model_config = ConfigDict(extra="forbid")

    count: int
    label: Literal["ready", "blocked"]


class StructuredOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    detail: Detail
    note: str | None = None


class FakeMethod:
    def __init__(self, *outcomes):
        self.outcomes = list(outcomes)
        self.calls = []

    def __call__(self, **kwargs):
        self.calls.append(kwargs)
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


class FakeClient:
    def __init__(self, *responses):
        create = FakeMethod(*responses)
        parse = FakeMethod(*responses)
        self.responses = SimpleNamespace(create=create, parse=parse)
        completions = SimpleNamespace(create=create, parse=parse)
        self.chat = SimpleNamespace(completions=completions)


def response(
    output_text='{"name":"alpha","detail":{"count":2,"label":"ready"}}',
    *,
    output_parsed=None,
):
    return SimpleNamespace(
        output_text=output_text,
        output_parsed=output_parsed,
        _request_id="req-safe-123",
        status="completed",
        usage=SimpleNamespace(
            input_tokens=11,
            output_tokens=7,
            total_tokens=18,
            input_tokens_details=SimpleNamespace(cached_tokens=3),
        ),
    )


def provider(client, **overrides):
    values = {
        "endpoint": "https://sample.openai.azure.com",
        "api_key": "fake-test-key",
        "deployment": "planner-deployment",
        "client": client,
    }
    values.update(overrides)
    return AzureOpenAIProvider(**values)


@pytest.mark.parametrize(
    "raw",
    [
        "https://sample.openai.azure.com",
        "https://sample.openai.azure.com/",
        "https://sample.openai.azure.com/openai/v1",
        "https://sample.openai.azure.com/openai/v1/",
    ],
)
def test_normalize_azure_endpoint_is_idempotent(raw):
    assert (
        normalize_azure_v1_endpoint(raw)
        == "https://sample.openai.azure.com/openai/v1/"
    )


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "http://sample.openai.azure.com",
        "https://user:pass@sample.openai.azure.com",
        "https://sample.openai.azure.com?api-key=secret",
        "https://sample.openai.azure.com/openai/deployments/foo",
    ],
)
def test_normalize_azure_endpoint_rejects_unsafe_or_legacy_shapes(raw):
    with pytest.raises(ModelConfigurationError):
        normalize_azure_v1_endpoint(raw)


def test_responses_text_result_captures_safe_metadata_and_is_stateless():
    fake_response = response("hello")
    client = FakeClient(fake_response)
    clock = iter([10.0, 10.25])
    model = provider(client, clock=lambda: next(clock))

    result = model.generate_text(
        "minimal prompt",
        system_prompt="be concise",
        metadata={"run_id": "run-1", "api_key": "must-not-leak"},
    )

    assert result.value == "hello"
    assert result.request_id == "req-safe-123"
    assert result.usage.model_dump() == {
        "input_tokens": 11,
        "output_tokens": 7,
        "total_tokens": 18,
        "cached_tokens": 3,
    }
    assert result.latency_seconds == 0.25
    call = client.responses.create.calls[0]
    assert call["model"] == "planner-deployment"
    assert call["instructions"] == "be concise"
    assert call["store"] is False
    assert call["metadata"]["api_key"] == "[REDACTED]"
    assert "previous_response_id" not in call


def test_pydantic_structured_response_is_requested_and_validated_locally():
    parsed = {
        "name": "alpha",
        "detail": {"count": 2, "label": "ready"},
        "note": None,
    }
    client = FakeClient(response(output_parsed=parsed))

    result = provider(client).generate_json("structured", schema=StructuredOutput)

    assert result.json_value() == parsed
    call = client.responses.parse.calls[0]
    assert call["text_format"] is StructuredOutput
    assert call["model"] == "planner-deployment"


@pytest.mark.parametrize(
    "parsed",
    [
        {"name": "alpha", "detail": {"label": "ready"}},
        {
            "name": "alpha",
            "detail": {"count": 2, "label": "unknown"},
        },
        {
            "name": "alpha",
            "detail": {"count": 2, "label": "ready", "extra": True},
        },
    ],
)
def test_pydantic_structured_response_rejects_missing_enum_and_extra_fields(parsed):
    client = FakeClient(response(output_parsed=parsed))

    with pytest.raises(ModelSchemaValidationError):
        provider(client).generate_json("structured", schema=StructuredOutput)


def test_pydantic_structured_response_rejects_malformed_json_fallback():
    client = FakeClient(response("not-json", output_parsed=None))

    with pytest.raises(ModelSchemaValidationError):
        provider(client).generate_json("structured", schema=StructuredOutput)


def test_json_object_rejects_missing_or_malformed_output():
    with pytest.raises(ModelOutputError, match="no usable text"):
        provider(FakeClient(response(None))).generate_json("missing")

    with pytest.raises(ModelOutputError, match="malformed JSON"):
        provider(FakeClient(response("not-json"))).generate_json("malformed")


def test_detectable_response_refusal_maps_to_content_policy_error():
    refused = response(None)
    refused.output = [
        SimpleNamespace(content=[SimpleNamespace(type="refusal")])
    ]

    with pytest.raises(ModelContentPolicyError):
        provider(FakeClient(refused)).generate_text("refused")


def test_dictionary_schema_is_validated_locally():
    schema = {
        "type": "object",
        "properties": {"status": {"enum": ["ok"]}},
        "required": ["status"],
        "additionalProperties": False,
    }
    valid = provider(FakeClient(response('{"status":"ok"}'))).generate_json(
        "schema",
        schema=schema,
    )
    assert valid.value == {"status": "ok"}

    with pytest.raises(ModelSchemaValidationError):
        provider(FakeClient(response('{"status":"bad"}'))).generate_json(
            "schema",
            schema=schema,
        )


def test_chat_completions_mode_uses_messages_and_usage_fields():
    chat_response = SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(content="chat text", parsed=None),
                finish_reason="stop",
            )
        ],
        _request_id="chat-request",
        usage=SimpleNamespace(
            prompt_tokens=4,
            completion_tokens=2,
            total_tokens=6,
            prompt_tokens_details=SimpleNamespace(cached_tokens=1),
        ),
    )
    client = FakeClient(chat_response)

    result = provider(client, api_mode="chat_completions").generate_text(
        "hello",
        system_prompt="system",
    )

    assert result.value == "chat text"
    assert result.finish_status == "stop"
    assert result.usage.total_tokens == 6
    assert client.chat.completions.create.calls[0]["messages"] == [
        {"role": "system", "content": "system"},
        {"role": "user", "content": "hello"},
    ]


class FakeSdkError(Exception):
    def __init__(self, message, status_code=None, headers=None):
        super().__init__(message)
        self.status_code = status_code
        self.request_id = "error-request"
        self.response = SimpleNamespace(
            status_code=status_code,
            headers=headers or {},
        )


class APITimeoutError(FakeSdkError):
    pass


class APIConnectionError(FakeSdkError):
    pass


class LengthFinishReasonError(FakeSdkError):
    pass


class ContentFilterFinishReasonError(FakeSdkError):
    pass


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (APITimeoutError("timed out"), ModelTimeoutError),
        (APIConnectionError("connection failed"), ModelTransportError),
        (LengthFinishReasonError("truncated"), ModelOutputError),
        (ContentFilterFinishReasonError("filtered"), ModelContentPolicyError),
        (FakeSdkError("bad", 400), ModelBadRequestError),
        (FakeSdkError("key=super-secret", 401), ModelAuthenticationError),
        (FakeSdkError("forbidden", 403), ModelAuthorizationError),
        (FakeSdkError("deployment", 404), ModelNotFoundError),
        (FakeSdkError("timeout", 408), ModelTimeoutError),
        (FakeSdkError("rate", 429), ModelRateLimitError),
        (FakeSdkError("insufficient_quota", 429), ModelQuotaExceededError),
        (FakeSdkError("server", 503), ModelServiceUnavailableError),
        (FakeSdkError("content_filter", 400), ModelContentPolicyError),
        (
            FakeSdkError("json_schema is not supported", 400),
            ModelConfigurationError,
        ),
    ],
)
def test_sdk_errors_map_to_normalized_safe_errors(error, expected):
    mapped = map_azure_exception(error, model="deployment")

    assert isinstance(mapped, expected)
    assert mapped.request_id == "error-request"
    assert "super-secret" not in str(mapped)


def test_retry_after_seconds_and_milliseconds_are_extracted():
    seconds = map_azure_exception(
        FakeSdkError("rate", 429, {"Retry-After": "3"}),
        model="deployment",
    )
    milliseconds = map_azure_exception(
        FakeSdkError("rate", 429, {"retry-after-ms": "250"}),
        model="deployment",
    )

    assert seconds.retry_after_seconds == 3
    assert milliseconds.retry_after_seconds == 0.25


def test_unknown_client_error_is_not_misclassified_as_retryable_transport():
    mapped = map_azure_exception(
        FakeSdkError("internal SDK error api_key=must-not-leak"),
        model="deployment",
    )

    assert type(mapped) is ModelProviderError
    assert mapped.retryable is False
    assert mapped.fallback_eligible is False
    assert "must-not-leak" not in str(mapped)


def test_client_factory_is_lazy_and_sdk_retries_are_disabled():
    seen = []

    def factory(**kwargs):
        seen.append(kwargs)
        return FakeClient(response("ok"))

    model = provider(None, client_factory=factory)
    assert seen == []

    model.generate_text("hello")

    assert seen[0]["base_url"].endswith("/openai/v1/")
    assert seen[0]["max_retries"] == 0
    assert seen[0]["api_key"] == "fake-test-key"


def test_constructor_validation_never_echoes_key():
    with pytest.raises(ModelConfigurationError) as captured:
        AzureOpenAIProvider(
            endpoint="https://sample.openai.azure.com?api-key=top-secret",
            api_key="top-secret",
            deployment="deployment",
        )

    assert "top-secret" not in str(captured.value)
