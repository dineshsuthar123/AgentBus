from __future__ import annotations

import json
import time
from typing import Any, Callable
from urllib.parse import urlsplit, urlunsplit

from pydantic import BaseModel, ValidationError

from agentbus.models.base import validate_json_schema
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
from agentbus.models.types import ModelResult, ModelRole, ModelUsage
from agentbus.security.redaction import sanitize_json


class AzureOpenAIProvider:
    """Azure OpenAI v1 adapter using the standard OpenAI Python client."""

    def __init__(
        self,
        *,
        endpoint: str,
        api_key: str,
        deployment: str,
        auth_mode: str = "api_key",
        api_mode: str = "responses",
        timeout_seconds: float = 180,
        role: ModelRole = ModelRole.DEFAULT,
        client: Any | None = None,
        client_factory: Callable[..., Any] | None = None,
        clock: Callable[[], float] = time.perf_counter,
    ):
        if auth_mode != "api_key":
            raise ModelConfigurationError(
                "Azure authentication mode must be 'api_key' in this checkpoint.",
                provider="azure",
                model=deployment or None,
            )
        if not api_key or not api_key.strip():
            raise ModelConfigurationError(
                "AZURE_OPENAI_API_KEY is required for API-key authentication.",
                provider="azure",
                model=deployment or None,
            )
        if not deployment or not deployment.strip():
            raise ModelConfigurationError(
                "An Azure OpenAI deployment is required for the selected role.",
                provider="azure",
            )
        if api_mode not in {"responses", "chat_completions"}:
            raise ModelConfigurationError(
                "AZURE_OPENAI_API_MODE must be 'responses' or 'chat_completions'.",
                provider="azure",
                model=deployment,
            )
        if timeout_seconds <= 0:
            raise ModelConfigurationError(
                "Azure OpenAI timeout must be greater than zero.",
                provider="azure",
                model=deployment,
            )

        self.base_url = normalize_azure_v1_endpoint(endpoint)
        self._api_key = api_key.strip()
        self._deployment = deployment.strip()
        self.auth_mode = auth_mode
        self.api_mode = api_mode
        self.timeout_seconds = float(timeout_seconds)
        self.role = role
        self._client = client
        self._client_factory = client_factory
        self.clock = clock

    @property
    def provider_name(self) -> str:
        return "azure"

    @property
    def model_name(self) -> str:
        return self._deployment

    def generate_text(
        self,
        prompt: str,
        *,
        system_prompt: str | None = None,
        timeout_seconds: float | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> ModelResult:
        return self._generate(
            prompt,
            schema=None,
            json_requested=False,
            system_prompt=system_prompt,
            timeout_seconds=timeout_seconds,
            metadata=metadata,
        )

    def generate_json(
        self,
        prompt: str,
        *,
        schema: type[BaseModel] | dict[str, Any] | None = None,
        system_prompt: str | None = None,
        timeout_seconds: float | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> ModelResult:
        return self._generate(
            prompt,
            schema=schema,
            json_requested=True,
            system_prompt=system_prompt,
            timeout_seconds=timeout_seconds,
            metadata=metadata,
        )

    def _generate(
        self,
        prompt: str,
        *,
        schema: type[BaseModel] | dict[str, Any] | None,
        json_requested: bool,
        system_prompt: str | None,
        timeout_seconds: float | None,
        metadata: dict[str, Any] | None,
    ) -> ModelResult:
        started = self.clock()
        try:
            if self.api_mode == "responses":
                response = self._responses_request(
                    prompt,
                    schema=schema,
                    json_requested=json_requested,
                    system_prompt=system_prompt,
                    timeout_seconds=timeout_seconds,
                    metadata=metadata,
                )
            else:
                response = self._chat_request(
                    prompt,
                    schema=schema,
                    json_requested=json_requested,
                    system_prompt=system_prompt,
                    timeout_seconds=timeout_seconds,
                    metadata=metadata,
                )
        except ModelProviderError:
            raise
        except ValidationError as exc:
            raise ModelSchemaValidationError(
                "Azure OpenAI output failed SDK schema parsing.",
                provider=self.provider_name,
                model=self.model_name,
            ) from exc
        except Exception as exc:
            raise map_azure_exception(exc, model=self.model_name) from exc

        latency = max(0.0, self.clock() - started)
        if not json_requested:
            text = self._extract_text(response)
            return self._result(text, response, latency)

        parsed = self._extract_json(response, schema)
        return self._result(parsed, response, latency)

    def _responses_request(
        self,
        prompt: str,
        *,
        schema: type[BaseModel] | dict[str, Any] | None,
        json_requested: bool,
        system_prompt: str | None,
        timeout_seconds: float | None,
        metadata: dict[str, Any] | None,
    ) -> Any:
        client = self._get_client()
        arguments: dict[str, Any] = {
            "model": self.model_name,
            "input": prompt,
            "store": False,
            "timeout": timeout_seconds or self.timeout_seconds,
        }
        if system_prompt:
            arguments["instructions"] = system_prompt
        safe_metadata = _request_metadata(metadata)
        if safe_metadata:
            arguments["metadata"] = safe_metadata

        if isinstance(schema, type) and issubclass(schema, BaseModel):
            return client.responses.parse(text_format=schema, **arguments)
        if json_requested:
            arguments["text"] = {
                "format": (
                    {
                        "type": "json_schema",
                        "name": "agentbus_output",
                        "schema": schema,
                        "strict": True,
                    }
                    if isinstance(schema, dict)
                    else {"type": "json_object"}
                )
            }
        return client.responses.create(**arguments)

    def _chat_request(
        self,
        prompt: str,
        *,
        schema: type[BaseModel] | dict[str, Any] | None,
        json_requested: bool,
        system_prompt: str | None,
        timeout_seconds: float | None,
        metadata: dict[str, Any] | None,
    ) -> Any:
        client = self._get_client()
        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})
        arguments: dict[str, Any] = {
            "model": self.model_name,
            "messages": messages,
            "store": False,
            "timeout": timeout_seconds or self.timeout_seconds,
        }
        safe_metadata = _request_metadata(metadata)
        if safe_metadata:
            arguments["metadata"] = safe_metadata
        if isinstance(schema, type) and issubclass(schema, BaseModel):
            return client.chat.completions.parse(
                response_format=schema,
                **arguments,
            )
        if json_requested:
            arguments["response_format"] = (
                {
                    "type": "json_schema",
                    "json_schema": {
                        "name": "agentbus_output",
                        "schema": schema,
                        "strict": True,
                    },
                }
                if isinstance(schema, dict)
                else {"type": "json_object"}
            )
        return client.chat.completions.create(**arguments)

    def _get_client(self) -> Any:
        if self._client is not None:
            return self._client
        factory = self._client_factory
        if factory is None:
            try:
                from openai import OpenAI
            except ImportError as exc:
                raise ModelConfigurationError(
                    "The 'openai' package is required for Azure OpenAI support.",
                    provider=self.provider_name,
                    model=self.model_name,
                ) from exc
            factory = OpenAI
        try:
            self._client = factory(
                api_key=self._api_key,
                base_url=self.base_url,
                timeout=self.timeout_seconds,
                max_retries=0,
            )
        except ModelProviderError:
            raise
        except Exception as exc:
            raise ModelConfigurationError(
                "Unable to construct the Azure OpenAI client.",
                provider=self.provider_name,
                model=self.model_name,
            ) from exc
        return self._client

    def _extract_text(self, response: Any) -> str:
        if self.api_mode == "responses":
            text = getattr(response, "output_text", None)
        else:
            choices = getattr(response, "choices", None) or []
            message = getattr(choices[0], "message", None) if choices else None
            text = getattr(message, "content", None)
        if not isinstance(text, str) or not text.strip():
            if _has_content_policy_refusal(response, self.api_mode):
                raise ModelContentPolicyError(
                    "Azure OpenAI returned a content-policy refusal.",
                    provider=self.provider_name,
                    model=self.model_name,
                    request_id=_request_id(response),
                )
            raise ModelOutputError(
                "Azure OpenAI returned no usable text output.",
                provider=self.provider_name,
                model=self.model_name,
                request_id=_request_id(response),
            )
        return text.strip()

    def _extract_json(
        self,
        response: Any,
        schema: type[BaseModel] | dict[str, Any] | None,
    ) -> dict[str, Any]:
        parsed_value = _parsed_output(response, self.api_mode)
        if isinstance(schema, type) and issubclass(schema, BaseModel):
            try:
                if parsed_value is not None:
                    validated = schema.model_validate(parsed_value)
                else:
                    validated = schema.model_validate_json(self._extract_text(response))
            except ValidationError as exc:
                raise ModelSchemaValidationError(
                    "Azure OpenAI output failed local schema validation.",
                    provider=self.provider_name,
                    model=self.model_name,
                    request_id=_request_id(response),
                ) from exc
            return validated.model_dump(mode="json")

        try:
            parsed = json.loads(self._extract_text(response))
        except json.JSONDecodeError as exc:
            raise ModelOutputError(
                "Azure OpenAI returned malformed JSON.",
                provider=self.provider_name,
                model=self.model_name,
                request_id=_request_id(response),
            ) from exc
        if not isinstance(parsed, dict):
            raise ModelOutputError(
                "Azure OpenAI JSON output must be an object.",
                provider=self.provider_name,
                model=self.model_name,
                request_id=_request_id(response),
            )
        if isinstance(schema, dict):
            validate_json_schema(
                parsed,
                schema,
                provider=self.provider_name,
                model=self.model_name,
                request_id=_request_id(response),
            )
        return parsed

    def _result(
        self,
        value: str | dict[str, Any],
        response: Any,
        latency: float,
    ) -> ModelResult:
        return ModelResult(
            value=value,
            provider=self.provider_name,
            model=self.model_name,
            role=self.role,
            request_id=_request_id(response),
            usage=_usage(response, self.api_mode),
            finish_status=_finish_status(response, self.api_mode),
            latency_seconds=latency,
            provider_metadata={"api_mode": self.api_mode},
        )


def normalize_azure_v1_endpoint(endpoint: str) -> str:
    if not endpoint or not endpoint.strip():
        raise ModelConfigurationError(
            "AZURE_OPENAI_ENDPOINT is required when Azure is selected.",
            provider="azure",
        )
    try:
        parsed = urlsplit(endpoint.strip())
    except ValueError as exc:
        raise ModelConfigurationError(
            "AZURE_OPENAI_ENDPOINT is not a valid URL.",
            provider="azure",
        ) from exc
    if parsed.scheme.lower() != "https" or not parsed.hostname:
        raise ModelConfigurationError(
            "AZURE_OPENAI_ENDPOINT must be an HTTPS URL with a hostname.",
            provider="azure",
        )
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ModelConfigurationError(
            "AZURE_OPENAI_ENDPOINT must not contain credentials, query, or fragment data.",
            provider="azure",
        )
    path = parsed.path.rstrip("/")
    if path and path.lower() != "/openai/v1":
        raise ModelConfigurationError(
            "AZURE_OPENAI_ENDPOINT path must be empty or '/openai/v1/'.",
            provider="azure",
        )
    return urlunsplit(("https", parsed.netloc, "/openai/v1/", "", ""))


def map_azure_exception(error: Exception, *, model: str) -> ModelProviderError:
    status = _status_code(error)
    request_id = getattr(error, "request_id", None)
    retry_after = _retry_after(error)
    message_lower = str(error).lower()
    class_name = type(error).__name__.lower()

    common = {
        "provider": "azure",
        "model": model,
        "http_status": status,
        "request_id": request_id,
        "retry_after_seconds": retry_after,
    }
    if "timeout" in class_name:
        return ModelTimeoutError("Azure OpenAI request timed out.", **common)
    if "contentfilterfinishreason" in class_name:
        return ModelContentPolicyError(
            "Azure OpenAI stopped generation under its content policy.",
            **common,
        )
    if "lengthfinishreason" in class_name:
        return ModelOutputError(
            "Azure OpenAI output ended before structured parsing completed.",
            **common,
        )
    if status is None and (
        "connection" in class_name or "transport" in class_name
    ):
        return ModelTransportError("Azure OpenAI connection failed.", **common)
    if status == 401:
        return ModelAuthenticationError(
            "Azure OpenAI authentication failed. Check the configured API key.",
            **common,
        )
    if status == 403:
        return ModelAuthorizationError(
            "Azure OpenAI authorization failed for the selected deployment.",
            **common,
        )
    if status == 404:
        return ModelNotFoundError(
            "Azure OpenAI deployment was not found.",
            **common,
        )
    if status == 408:
        return ModelTimeoutError("Azure OpenAI request timed out.", **common)
    if status == 429:
        if "quota" in message_lower or "insufficient_quota" in message_lower:
            return ModelQuotaExceededError(
                "Azure OpenAI quota is exhausted.",
                **common,
            )
        return ModelRateLimitError(
            "Azure OpenAI rate limit was reached.",
            **common,
        )
    if status is not None and status >= 500:
        return ModelServiceUnavailableError(
            "Azure OpenAI service is temporarily unavailable.",
            **common,
        )
    if status == 400:
        if any(
            marker in message_lower
            for marker in ("content_filter", "content policy", "responsibleai")
        ):
            return ModelContentPolicyError(
                "Azure OpenAI rejected the request under its content policy.",
                **common,
            )
        if "not supported" in message_lower and any(
            marker in message_lower
            for marker in ("json_schema", "response_format", "structured output")
        ):
            return ModelConfigurationError(
                "The selected Azure deployment does not support configured structured output.",
                **common,
            )
        return ModelBadRequestError(
            "Azure OpenAI rejected the request as invalid.",
            **common,
        )
    if status is None:
        return ModelProviderError(
            "Azure OpenAI client request failed without a provider response.",
            **common,
        )
    return ModelProviderError("Azure OpenAI request failed.", **common)


def _request_metadata(metadata: dict[str, Any] | None) -> dict[str, str]:
    if not metadata:
        return {}
    sanitized = sanitize_json(metadata, max_chars=512)
    return {
        str(key)[:64]: str(value)[:512]
        for key, value in list(sanitized.items())[:16]
        if value is not None
    }


def _request_id(response: Any) -> str | None:
    value = getattr(response, "_request_id", None)
    return value if isinstance(value, str) and value else None


def _parsed_output(response: Any, api_mode: str) -> Any:
    if api_mode == "responses":
        return getattr(response, "output_parsed", None)
    choices = getattr(response, "choices", None) or []
    message = getattr(choices[0], "message", None) if choices else None
    return getattr(message, "parsed", None)


def _usage(response: Any, api_mode: str) -> ModelUsage:
    usage = getattr(response, "usage", None)
    if usage is None:
        return ModelUsage()
    if api_mode == "responses":
        input_tokens = _optional_nonnegative(getattr(usage, "input_tokens", None))
        output_tokens = _optional_nonnegative(getattr(usage, "output_tokens", None))
        cached = _nested_cached(usage, "input_tokens_details")
    else:
        input_tokens = _optional_nonnegative(getattr(usage, "prompt_tokens", None))
        output_tokens = _optional_nonnegative(
            getattr(usage, "completion_tokens", None)
        )
        cached = _nested_cached(usage, "prompt_tokens_details")
    total_tokens = _optional_nonnegative(getattr(usage, "total_tokens", None))
    return ModelUsage(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        total_tokens=total_tokens,
        cached_tokens=cached,
    )


def _nested_cached(usage: Any, field: str) -> int | None:
    details = getattr(usage, field, None)
    return _optional_nonnegative(getattr(details, "cached_tokens", None))


def _finish_status(response: Any, api_mode: str) -> str | None:
    if api_mode == "responses":
        status = getattr(response, "status", None)
    else:
        choices = getattr(response, "choices", None) or []
        status = getattr(choices[0], "finish_reason", None) if choices else None
    return status if isinstance(status, str) else None


def _status_code(error: Exception) -> int | None:
    value = getattr(error, "status_code", None)
    if isinstance(value, int):
        return value
    response = getattr(error, "response", None)
    value = getattr(response, "status_code", None)
    return value if isinstance(value, int) else None


def _retry_after(error: Exception) -> float | None:
    response = getattr(error, "response", None)
    headers = getattr(response, "headers", None)
    if headers is None:
        return None
    raw = headers.get("retry-after") or headers.get("Retry-After")
    scale = 1.0
    if raw is None:
        raw = headers.get("retry-after-ms") or headers.get("Retry-After-Ms")
        scale = 0.001
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return None
    return value * scale if value >= 0 else None


def _optional_nonnegative(value: Any) -> int | None:
    return value if isinstance(value, int) and value >= 0 else None


def _has_content_policy_refusal(response: Any, api_mode: str) -> bool:
    if api_mode == "chat_completions":
        choices = getattr(response, "choices", None) or []
        message = getattr(choices[0], "message", None) if choices else None
        return bool(getattr(message, "refusal", None))
    details = getattr(response, "incomplete_details", None)
    reason = getattr(details, "reason", None)
    if isinstance(reason, str) and "content" in reason.lower():
        return True
    for item in getattr(response, "output", None) or []:
        for content in getattr(item, "content", None) or []:
            if getattr(content, "type", None) == "refusal":
                return True
    return False
