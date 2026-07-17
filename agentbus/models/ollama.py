from __future__ import annotations

import json
import re
import time
from contextlib import nullcontext
from typing import Any, Callable

import requests
from pydantic import BaseModel, ValidationError

from agentbus.config import AgentBusConfig
from agentbus.execution.cancellation import (
    CancellationRequested,
    CancellationState,
    CancellationToken,
)
from agentbus.models.base import validate_json_schema
from agentbus.models.errors import (
    ModelAuthenticationError,
    ModelAuthorizationError,
    ModelBadRequestError,
    ModelCancellationError,
    ModelNotFoundError,
    ModelOutputError,
    ModelProviderError,
    ModelRateLimitError,
    ModelSchemaValidationError,
    ModelServiceUnavailableError,
    ModelTimeoutError,
    ModelTransportError,
)
from agentbus.models.types import ModelResult, ModelRole, ModelUsage


class OllamaProvider:
    def __init__(
        self,
        *,
        model: str,
        url: str,
        timeout_seconds: float = 180,
        role: ModelRole = ModelRole.DEFAULT,
        clock: Callable[[], float] = time.perf_counter,
    ):
        self._model = model
        self.url = url
        self.timeout_seconds = timeout_seconds
        self.role = role
        self.clock = clock

    @property
    def provider_name(self) -> str:
        return "ollama"

    @property
    def model_name(self) -> str:
        return self._model

    def generate_text(
        self,
        prompt: str,
        *,
        system_prompt: str | None = None,
        timeout_seconds: float | None = None,
        metadata: dict[str, Any] | None = None,
        cancellation: CancellationToken | None = None,
    ) -> ModelResult:
        payload: dict[str, Any] = {
            "model": self.model_name,
            "prompt": prompt,
            "stream": False,
            "options": {"temperature": 0.1},
        }
        if system_prompt:
            payload["system"] = system_prompt
        response_payload, latency, cancellation_state = self._request(
            payload,
            timeout_seconds=timeout_seconds,
            cancellation=cancellation,
        )
        text = self._response_text(response_payload)
        return self._result(
            text,
            response_payload,
            latency,
            cancellation_state,
        )

    def generate_json(
        self,
        prompt: str,
        *,
        schema: type[BaseModel] | dict[str, Any] | None = None,
        system_prompt: str | None = None,
        timeout_seconds: float | None = None,
        metadata: dict[str, Any] | None = None,
        cancellation: CancellationToken | None = None,
    ) -> ModelResult:
        payload: dict[str, Any] = {
            "model": self.model_name,
            "prompt": prompt,
            "stream": False,
            "format": (
                schema.model_json_schema()
                if isinstance(schema, type) and issubclass(schema, BaseModel)
                else schema or "json"
            ),
            "options": {"temperature": 0.1},
        }
        if system_prompt:
            payload["system"] = system_prompt
        response_payload, latency, cancellation_state = self._request(
            payload,
            timeout_seconds=timeout_seconds,
            cancellation=cancellation,
        )
        raw = self._response_text(response_payload)
        parsed = self._parse_model_json(raw)
        if isinstance(schema, type) and issubclass(schema, BaseModel):
            try:
                parsed = schema.model_validate(parsed).model_dump(mode="json")
            except ValidationError as exc:
                raise ModelSchemaValidationError(
                    "Ollama output failed local schema validation.",
                    provider=self.provider_name,
                    model=self.model_name,
                ) from exc
        elif isinstance(schema, dict):
            validate_json_schema(
                parsed,
                schema,
                provider=self.provider_name,
                model=self.model_name,
            )
        return self._result(
            parsed,
            response_payload,
            latency,
            cancellation_state,
        )

    def _request(
        self,
        payload: dict[str, Any],
        *,
        timeout_seconds: float | None,
        cancellation: CancellationToken | None,
    ) -> tuple[dict[str, Any], float, CancellationState | None]:
        started = self.clock()
        try:
            operation = (
                cancellation.operation(
                    "ollama.http_request",
                    source="provider:ollama",
                    interruptible=False,
                    provider=self.provider_name,
                )
                if cancellation is not None
                else nullcontext()
            )
            with operation:
                response = requests.post(
                    self.url,
                    json=payload,
                    timeout=timeout_seconds or self.timeout_seconds,
                )
                response.raise_for_status()
                if cancellation is not None and cancellation.is_requested:
                    cancellation.acknowledge(
                        "provider:ollama",
                        stage="after-response",
                        provider=self.provider_name,
                    )
        except CancellationRequested as exc:
            raise ModelCancellationError(
                "Ollama request was cancelled before transport started.",
                provider=self.provider_name,
                model=self.model_name,
                metadata={
                    "acknowledgement_source": exc.source,
                    "acknowledgement_stage": exc.stage,
                    "cancellation_supported": False,
                },
            ) from exc
        except requests.Timeout as exc:
            raise ModelTimeoutError(
                "Ollama request timed out.",
                provider=self.provider_name,
                model=self.model_name,
            ) from exc
        except requests.ConnectionError as exc:
            raise ModelTransportError(
                "Unable to connect to Ollama.",
                provider=self.provider_name,
                model=self.model_name,
            ) from exc
        except requests.HTTPError as exc:
            raise self._http_error(exc) from exc
        except requests.RequestException as exc:
            raise ModelTransportError(
                "Ollama request failed.",
                provider=self.provider_name,
                model=self.model_name,
            ) from exc

        try:
            body = response.json()
        except ValueError as exc:
            raise ModelOutputError(
                "Ollama returned a non-JSON HTTP body.",
                provider=self.provider_name,
                model=self.model_name,
            ) from exc
        if not isinstance(body, dict):
            raise ModelOutputError(
                "Ollama returned an unexpected response object.",
                provider=self.provider_name,
                model=self.model_name,
            )
        return (
            body,
            max(0.0, self.clock() - started),
            cancellation.snapshot() if cancellation is not None else None,
        )

    def _http_error(self, error: requests.HTTPError) -> ModelProviderError:
        status = getattr(getattr(error, "response", None), "status_code", None)
        error_type: type[ModelProviderError]
        if status == 400:
            error_type = ModelBadRequestError
        elif status == 401:
            error_type = ModelAuthenticationError
        elif status == 403:
            error_type = ModelAuthorizationError
        elif status == 404:
            error_type = ModelNotFoundError
        elif status == 429:
            error_type = ModelRateLimitError
        elif status is not None and status >= 500:
            error_type = ModelServiceUnavailableError
        else:
            error_type = ModelTransportError
        return error_type(
            "Ollama request failed.",
            provider=self.provider_name,
            model=self.model_name,
            http_status=status,
        )

    def _response_text(self, payload: dict[str, Any]) -> str:
        if "response" not in payload:
            raise ModelOutputError(
                "Ollama response is missing the 'response' field.",
                provider=self.provider_name,
                model=self.model_name,
            )
        raw = payload["response"]
        if not isinstance(raw, str) or not raw.strip():
            raise ModelOutputError(
                "Ollama 'response' field must be a non-empty string.",
                provider=self.provider_name,
                model=self.model_name,
            )
        return raw.strip()

    def _parse_model_json(self, raw: str) -> dict[str, Any]:
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            match = re.search(r"\{.*\}", raw, re.DOTALL)
            if not match:
                raise ModelOutputError(
                    "Model did not return valid JSON.",
                    provider=self.provider_name,
                    model=self.model_name,
                )
            try:
                parsed = json.loads(match.group(0))
            except json.JSONDecodeError as exc:
                raise ModelOutputError(
                    "Model did not return valid JSON.",
                    provider=self.provider_name,
                    model=self.model_name,
                ) from exc
        if not isinstance(parsed, dict):
            raise ModelOutputError(
                "Model JSON output must be an object.",
                provider=self.provider_name,
                model=self.model_name,
            )
        return parsed

    def _result(
        self,
        value: str | dict[str, Any],
        payload: dict[str, Any],
        latency: float,
        cancellation: CancellationState | None = None,
    ) -> ModelResult:
        input_tokens = _optional_int(payload.get("prompt_eval_count"))
        output_tokens = _optional_int(payload.get("eval_count"))
        total_tokens = (
            (input_tokens or 0) + (output_tokens or 0)
            if input_tokens is not None or output_tokens is not None
            else None
        )
        return ModelResult(
            value=value,
            provider=self.provider_name,
            model=self.model_name,
            role=self.role,
            usage=ModelUsage(
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                total_tokens=total_tokens,
            ),
            finish_status="completed" if payload.get("done") else None,
            latency_seconds=latency,
            cancellation_requested=bool(
                cancellation and cancellation.requested
            ),
            cancellation_acknowledged=bool(
                cancellation
                and cancellation.provider_cancellation_acknowledged_at
            ),
            cancellation_supported=False,
            completed_after_cancellation=bool(
                cancellation and cancellation.requested
            ),
            provider_metadata={"runtime": "local"},
        )


class OllamaModel:
    """Backward-compatible dict/string facade over the Ollama provider."""

    def __init__(
        self,
        model: str | None = None,
        url: str | None = None,
        config: AgentBusConfig | None = None,
    ):
        config = config or AgentBusConfig.from_env()
        self.provider = OllamaProvider(
            model=model or config.model_name,
            url=url or config.ollama_url,
            timeout_seconds=getattr(config, "model_timeout_seconds", 180),
        )
        self.last_result: ModelResult | None = None

    @property
    def model(self) -> str:
        return self.provider.model_name

    @property
    def url(self) -> str:
        return self.provider.url

    def generate_json(
        self,
        prompt: str,
        *,
        schema: type[BaseModel] | dict[str, Any] | None = None,
        **kwargs,
    ) -> dict[str, Any]:
        try:
            self.last_result = self.provider.generate_json(
                prompt,
                schema=schema,
                **kwargs,
            )
        except ModelOutputError:
            raise
        except ModelProviderError as exc:
            raise ModelOutputError(
                "Ollama request failed.",
                provider="ollama",
                model=self.model,
                retryable=exc.retryable,
            ) from exc
        return self.last_result.json_value()

    def generate_text(self, prompt: str, **kwargs) -> str:
        self.last_result = self.provider.generate_text(prompt, **kwargs)
        return self.last_result.text_value()


def _optional_int(value: Any) -> int | None:
    return value if isinstance(value, int) and value >= 0 else None
