from __future__ import annotations

import random
import time
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any, Callable, Iterator

from pydantic import BaseModel

from agentbus.config import AgentBusConfig
from agentbus.execution.cancellation import (
    CancellationRequested,
    CancellationToken,
)
from agentbus.models.base import ModelProvider
from agentbus.models.deterministic import DeterministicProvider
from agentbus.models.errors import (
    ModelCancellationError,
    ModelConfigurationError,
    ModelOutputError,
    ModelProviderError,
)
from agentbus.models.ollama import OllamaProvider
from agentbus.models.types import ModelResult, ModelRole, ModelRoute
from agentbus.models.usage import UsageLedger
from agentbus.security.redaction import sanitize_json


ProviderBuilder = Callable[[ModelRoute], ModelProvider]
_REQUEST_CONTEXT: ContextVar[dict[str, str | None]] = ContextVar(
    "agentbus_model_request_context",
    default={"run_id": None, "task_id": None},
)
_CANCELLATION_CONTEXT: ContextVar[CancellationToken | None] = ContextVar(
    "agentbus_model_cancellation_context",
    default=None,
)


class ModelProviderFactory:
    def __init__(
        self,
        config: AgentBusConfig,
        builders: dict[str, ProviderBuilder] | None = None,
    ):
        self.config = config
        self.builders = builders or {}

    def create(self, route: ModelRoute) -> ModelProvider:
        if route.provider in self.builders:
            return self.builders[route.provider](route)
        if route.provider == "ollama":
            return OllamaProvider(
                model=route.model,
                url=self.config.ollama_url,
                timeout_seconds=route.timeout_seconds,
                role=route.role,
            )
        if route.provider == "deterministic":
            latency_roles = set(self.config.deterministic_latency_roles)
            failure_roles = set(self.config.deterministic_failure_roles)
            return DeterministicProvider(
                role=route.role,
                model=route.model,
                profile=self.config.deterministic_profile,
                latency_seconds=(
                    self.config.deterministic_latency_seconds
                    if not latency_roles or route.role.value in latency_roles
                    else 0.0
                ),
                failure_kind=self.config.deterministic_failure_kind,
                failure_calls=(
                    self.config.deterministic_failure_calls
                    if not failure_roles or route.role.value in failure_roles
                    else ()
                ),
            )
        if route.provider == "azure":
            try:
                from agentbus.models.azure_openai import AzureOpenAIProvider
            except ModuleNotFoundError as exc:
                if exc.name == "openai":
                    raise ModelConfigurationError(
                        "Azure support is not installed. Install AgentBus with the "
                        "'azure' extra.",
                        provider="azure",
                        model=route.model,
                    ) from exc
                raise
            return AzureOpenAIProvider(
                endpoint=self.config.azure_openai_endpoint or "",
                api_key=self.config.azure_openai_api_key or "",
                deployment=route.model,
                auth_mode=self.config.azure_openai_auth_mode,
                api_mode=self.config.azure_openai_api_mode,
                timeout_seconds=route.timeout_seconds,
                role=route.role,
            )
        raise ValueError(f"Unsupported model provider: {route.provider!r}")


class ModelRouter:
    def __init__(
        self,
        config: AgentBusConfig | None = None,
        *,
        provider_factory: ModelProviderFactory | None = None,
        logger: Any | None = None,
        usage_ledger: UsageLedger | None = None,
        sleeper: Callable[[float], None] = time.sleep,
        jitter: Callable[[], float] = random.random,
    ):
        self.config = config or AgentBusConfig.from_env()
        self.provider_factory = provider_factory or ModelProviderFactory(self.config)
        self.logger = logger
        self.usage_ledger = usage_ledger or UsageLedger()
        self.sleeper = sleeper
        self.jitter = jitter
        self._providers: dict[tuple[Any, ...], ModelProvider] = {}

    def set_logger(self, logger: Any | None) -> None:
        self.logger = logger

    def route_for(
        self,
        role: ModelRole | str,
        *,
        provider: str | None = None,
        allow_fallback: bool = True,
    ) -> ModelRoute:
        model_role = _model_role(role)
        selected_provider = (provider or self.config.provider_name).lower()
        model = self.config.resolve_model(model_role.value, provider=selected_provider)
        fallback_enabled = bool(
            allow_fallback
            and self.config.enable_provider_fallback
            and selected_provider == "azure"
            and self.config.fallback_provider_name == "ollama"
        )
        return ModelRoute(
            provider=selected_provider,
            model=model,
            role=model_role,
            timeout_seconds=self.config.route_timeout(selected_provider),
            max_retries=self.config.route_max_retries(selected_provider),
            fallback_provider=(
                self.config.fallback_provider_name if fallback_enabled else None
            ),
            fallback_enabled=fallback_enabled,
        )

    def for_role(self, role: ModelRole | str) -> "RoutedModel":
        return RoutedModel(self, _model_role(role))

    def generate_text(
        self,
        role: ModelRole | str,
        prompt: str,
        *,
        system_prompt: str | None = None,
        timeout_seconds: float | None = None,
        metadata: dict[str, Any] | None = None,
        cancellation: CancellationToken | None = None,
    ) -> ModelResult:
        return self._generate(
            "generate_text",
            role,
            prompt,
            system_prompt=system_prompt,
            timeout_seconds=timeout_seconds,
            metadata=metadata,
            cancellation=cancellation,
        )

    def generate_json(
        self,
        role: ModelRole | str,
        prompt: str,
        *,
        schema: type[BaseModel] | dict[str, Any] | None = None,
        system_prompt: str | None = None,
        timeout_seconds: float | None = None,
        metadata: dict[str, Any] | None = None,
        cancellation: CancellationToken | None = None,
    ) -> ModelResult:
        return self._generate(
            "generate_json",
            role,
            prompt,
            schema=schema,
            system_prompt=system_prompt,
            timeout_seconds=timeout_seconds,
            metadata=metadata,
            cancellation=cancellation,
        )

    def _generate(
        self,
        method_name: str,
        role: ModelRole | str,
        prompt: str,
        **kwargs: Any,
    ) -> ModelResult:
        cancellation = kwargs.get("cancellation") or _CANCELLATION_CONTEXT.get()
        if cancellation is not None:
            kwargs["cancellation"] = cancellation
        else:
            kwargs.pop("cancellation", None)
        if cancellation is not None:
            try:
                cancellation.checkpoint(
                    "model-router",
                    stage=f"before:{method_name}",
                )
            except CancellationRequested as exc:
                raise _model_cancellation(exc, role) from exc
        correlation = dict(kwargs.get("metadata") or {})
        for key, value in _REQUEST_CONTEXT.get().items():
            if value is not None:
                correlation.setdefault(key, value)
        kwargs["metadata"] = correlation or None
        route = self.route_for(role)
        self._log("model_route_selected", _route_metadata(route))
        try:
            result = self._call_with_retries(route, method_name, prompt, kwargs)
        except ModelProviderError as error:
            if not self._should_fallback(route, error):
                raise
            result = self._fallback(route, method_name, prompt, kwargs, error)
        if cancellation is not None:
            try:
                cancellation.checkpoint(
                    "model-router",
                    stage=f"after:{method_name}",
                )
            except CancellationRequested as exc:
                raise _model_cancellation(exc, role) from exc
        return result

    def _call_with_retries(
        self,
        route: ModelRoute,
        method_name: str,
        prompt: str,
        kwargs: dict[str, Any],
        *,
        record_result: bool = True,
    ) -> ModelResult:
        try:
            provider = self._provider(route)
        except ModelProviderError as error:
            self._log(
                "model_request_failed",
                {**_route_metadata(route), "attempt": 0, **error.safe_metadata()},
            )
            raise
        for retry_count in range(route.max_retries + 1):
            self._log(
                "model_request_started",
                {**_route_metadata(route), "attempt": retry_count + 1},
            )
            try:
                method = getattr(provider, method_name)
                result = method(
                    prompt,
                    timeout_seconds=kwargs.get("timeout_seconds")
                    or route.timeout_seconds,
                    **{
                        key: value
                        for key, value in kwargs.items()
                        if key != "timeout_seconds"
                    },
                )
            except CancellationRequested as error:
                raise _model_cancellation(error, route.role) from error
            except ModelProviderError as error:
                self._log(
                    "model_request_failed",
                    {
                        **_route_metadata(route),
                        "attempt": retry_count + 1,
                        **error.safe_metadata(),
                    },
                )
                if not error.retryable or retry_count >= route.max_retries:
                    raise
                delay = self._retry_delay(retry_count + 1, error)
                self._log(
                    "model_request_retried",
                    {
                        **_route_metadata(route),
                        "retry_count": retry_count + 1,
                        "delay_seconds": delay,
                        "error_category": error.error_category,
                    },
                )
                cancellation = kwargs.get("cancellation")
                if (
                    isinstance(cancellation, CancellationToken)
                    and cancellation.wait(delay)
                ):
                    try:
                        cancellation.checkpoint(
                            "model-router",
                            stage="retry-delay",
                        )
                    except CancellationRequested as exc:
                        raise _model_cancellation(exc, route.role) from exc
                else:
                    self.sleeper(delay)
                continue
            except Exception as error:
                normalized = ModelProviderError(
                    "Model provider raised an unclassified error.",
                    provider=route.provider,
                    model=route.model,
                )
                self._log(
                    "model_request_failed",
                    {
                        **_route_metadata(route),
                        "attempt": retry_count + 1,
                        **normalized.safe_metadata(),
                    },
                )
                raise normalized from error

            if not isinstance(result, ModelResult):
                invalid_result = ModelOutputError(
                    "Model provider returned an invalid result object.",
                    provider=route.provider,
                    model=route.model,
                )
                self._log(
                    "model_request_failed",
                    {
                        **_route_metadata(route),
                        "attempt": retry_count + 1,
                        **invalid_result.safe_metadata(),
                    },
                )
                raise invalid_result

            result = result.model_copy(
                update={"role": route.role, "retry_count": retry_count}
            )
            if record_result:
                self._record_result(result)
            self._log("model_request_succeeded", result.event_metadata())
            return result
        raise AssertionError("provider retry loop ended unexpectedly")

    def _fallback(
        self,
        primary_route: ModelRoute,
        method_name: str,
        prompt: str,
        kwargs: dict[str, Any],
        original_error: ModelProviderError,
    ) -> ModelResult:
        fallback_route = self.route_for(
            primary_route.role,
            provider="ollama",
            allow_fallback=False,
        ).model_copy(update={"max_retries": 0})
        self._log(
            "provider_fallback_started",
            {
                **_route_metadata(fallback_route),
                "original_provider": primary_route.provider,
                "original_model": primary_route.model,
                "original_error_category": original_error.error_category,
            },
        )
        try:
            result = self._call_with_retries(
                fallback_route,
                method_name,
                prompt,
                kwargs,
                record_result=False,
            )
        except ModelProviderError as fallback_error:
            self._log(
                "provider_fallback_failed",
                {
                    **_route_metadata(fallback_route),
                    "original_provider": primary_route.provider,
                    **fallback_error.safe_metadata(),
                },
            )
            raise fallback_error from original_error
        result = result.model_copy(
            update={
                "retry_count": primary_route.max_retries + result.retry_count,
                "fallback_used": True,
                "original_provider": primary_route.provider,
                "original_error_category": original_error.error_category,
            }
        )
        self._record_result(result)
        self._log("provider_fallback_succeeded", result.event_metadata())
        return result

    def _provider(self, route: ModelRoute) -> ModelProvider:
        key = (
            route.provider,
            route.model,
            route.role.value,
            route.timeout_seconds,
        )
        if key not in self._providers:
            self._providers[key] = self.provider_factory.create(route)
        return self._providers[key]

    def _retry_delay(
        self,
        retry_number: int,
        error: ModelProviderError,
    ) -> float:
        maximum = self.config.model_retry_max_seconds
        if error.retry_after_seconds is not None:
            return min(maximum, max(0.0, error.retry_after_seconds))
        base = self.config.model_retry_base_seconds * (2 ** (retry_number - 1))
        jitter = max(0.0, min(1.0, float(self.jitter()))) * min(base * 0.25, maximum)
        return min(maximum, base + jitter)

    @staticmethod
    def _should_fallback(
        route: ModelRoute,
        error: ModelProviderError,
    ) -> bool:
        return bool(
            route.fallback_enabled
            and route.provider == "azure"
            and route.fallback_provider == "ollama"
            and error.retryable
            and error.fallback_eligible
        )

    def _record_result(self, result: ModelResult) -> None:
        context = _REQUEST_CONTEXT.get()
        self.usage_ledger.record(
            result,
            run_id=context.get("run_id"),
            task_id=context.get("task_id"),
        )
        self._log("model_usage_recorded", result.event_metadata())

    def _log(self, event_type: str, metadata: dict[str, Any]) -> None:
        if self.logger is None:
            return
        context = _REQUEST_CONTEXT.get()
        payload = {
            **context,
            **metadata,
        }
        self.logger.log(event_type, sanitize_json(payload))


class RoutedModel:
    """Backward-compatible dict/string model facade bound to one role."""

    def __init__(self, router: ModelRouter, role: ModelRole):
        self.router = router
        self.role = role
        self.last_result: ModelResult | None = None
        self._results: list[ModelResult] = []

    def generate_json(
        self,
        prompt: str,
        *,
        schema: type[BaseModel] | dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        self.last_result = self.router.generate_json(
            self.role,
            prompt,
            schema=schema,
            **kwargs,
        )
        self._results.append(self.last_result)
        return self.last_result.json_value()

    def generate_text(self, prompt: str, **kwargs: Any) -> str:
        self.last_result = self.router.generate_text(self.role, prompt, **kwargs)
        self._results.append(self.last_result)
        return self.last_result.text_value()

    def drain_results(self) -> list[ModelResult]:
        results = list(self._results)
        self._results.clear()
        return results


@contextmanager
def model_request_context(
    *,
    run_id: str | None = None,
    task_id: str | None = None,
    cancellation: CancellationToken | None = None,
) -> Iterator[None]:
    current_request = _REQUEST_CONTEXT.get()
    request_token = _REQUEST_CONTEXT.set(
        {
            "run_id": (
                run_id if run_id is not None else current_request.get("run_id")
            ),
            "task_id": (
                task_id if task_id is not None else current_request.get("task_id")
            ),
        }
    )
    cancellation_token = _CANCELLATION_CONTEXT.set(
        cancellation or _CANCELLATION_CONTEXT.get()
    )
    try:
        yield
    finally:
        _CANCELLATION_CONTEXT.reset(cancellation_token)
        _REQUEST_CONTEXT.reset(request_token)


def _model_role(role: ModelRole | str) -> ModelRole:
    if isinstance(role, ModelRole):
        return role
    try:
        return ModelRole(str(role).lower())
    except ValueError as exc:
        raise ValueError(f"Unsupported model role: {role!r}") from exc


def _route_metadata(route: ModelRoute) -> dict[str, Any]:
    return {
        "role": route.role.value,
        "provider": route.provider,
        "model": route.model,
        "timeout_seconds": route.timeout_seconds,
        "max_retries": route.max_retries,
        "fallback_enabled": route.fallback_enabled,
        "fallback_provider": route.fallback_provider,
    }


def _model_cancellation(
    error: CancellationRequested,
    role: ModelRole | str,
) -> ModelCancellationError:
    state = error.state
    return ModelCancellationError(
        "Model operation was cooperatively cancelled.",
        provider="router",
        model=_model_role(role).value,
        metadata={
            "acknowledgement_source": error.source,
            "acknowledgement_stage": error.stage,
            "requested_at": (
                state.requested_at.isoformat() if state.requested_at else None
            ),
        },
    )
