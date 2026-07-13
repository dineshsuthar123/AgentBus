from __future__ import annotations

import uuid
from threading import Lock
from typing import Any, Literal

from pydantic import Field

from agentbus.evaluation.budget import EvaluationBudget
from agentbus.evaluation.errors import ScriptedProviderError
from agentbus.evaluation.models import EvaluationModel
from agentbus.models.errors import (
    ModelAuthenticationError,
    ModelOutputError,
    ModelServiceUnavailableError,
    ModelTimeoutError,
)
from agentbus.models.types import ModelResult, ModelRole, ModelUsage
from agentbus.security.redaction import sanitize_json


class ScriptedOutcome(EvaluationModel):
    kind: Literal[
        "success",
        "malformed",
        "timeout",
        "transient_failure",
        "authentication_failure",
    ] = "success"
    value: str | dict[str, Any] = Field(default_factory=dict)
    usage: ModelUsage = Field(
        default_factory=lambda: ModelUsage(
            input_tokens=8,
            output_tokens=4,
            total_tokens=12,
            cached_tokens=0,
        )
    )
    latency_seconds: float = Field(default=0.001, ge=0)
    provider_metadata: dict[str, Any] = Field(default_factory=dict)
    retry_count: int = Field(default=0, ge=0)
    fallback_used: bool = False
    original_provider: str | None = None
    original_error_category: str | None = None


class ScriptedCall(EvaluationModel):
    case_id: str
    task_id: str
    role: str
    attempt: int
    provider: str
    model: str
    succeeded: bool
    error_category: str | None = None


class ScriptedResponseStore:
    """Thread-safe exact routing for deterministic provider responses."""

    def __init__(self):
        self._scripts: dict[tuple[str, str, str, int], ScriptedOutcome] = {}
        self._consumed: set[tuple[str, str, str, int]] = set()
        self._calls: list[ScriptedCall] = []
        self._results: list[ModelResult] = []
        self._lock = Lock()

    def register(
        self,
        *,
        case_id: str,
        task_id: str,
        role: ModelRole | str,
        attempt: int,
        outcome: ScriptedOutcome,
    ) -> None:
        key = (case_id, task_id, ModelRole(role).value, attempt)
        with self._lock:
            if key in self._scripts:
                raise ScriptedProviderError(f"Duplicate scripted provider route: {key}")
            self._scripts[key] = outcome

    def consume(
        self,
        *,
        case_id: str,
        task_id: str,
        role: ModelRole | str,
        attempt: int,
        provider: str,
        model: str,
    ) -> ScriptedOutcome:
        key = (case_id, task_id, ModelRole(role).value, attempt)
        with self._lock:
            if key not in self._scripts:
                raise ScriptedProviderError(
                    "No deterministic response for "
                    f"case={case_id}, task={task_id}, role={key[2]}, attempt={attempt}."
                )
            if key in self._consumed:
                raise ScriptedProviderError(f"Scripted response was consumed twice: {key}")
            self._consumed.add(key)
            return self._scripts[key]

    def record_call(self, call: ScriptedCall, result: ModelResult | None = None) -> None:
        with self._lock:
            self._calls.append(call)
            if result is not None:
                self._results.append(result)

    def calls(self) -> list[ScriptedCall]:
        with self._lock:
            return list(self._calls)

    def results(self) -> list[ModelResult]:
        with self._lock:
            return list(self._results)


class DeterministicFakeProvider:
    def __init__(
        self,
        *,
        role: ModelRole | str,
        scripts: ScriptedResponseStore,
        provider: str = "fake",
        model: str = "deterministic-v1",
        budget: EvaluationBudget | None = None,
    ):
        self.role = ModelRole(role)
        self.scripts = scripts
        self._provider = provider
        self._model = model
        self.budget = budget

    @property
    def provider_name(self) -> str:
        return self._provider

    @property
    def model_name(self) -> str:
        return self._model

    def generate_json(self, prompt: str, **kwargs: Any) -> ModelResult:
        return self._generate(prompt, kwargs.get("metadata"))

    def generate_text(self, prompt: str, **kwargs: Any) -> ModelResult:
        return self._generate(prompt, kwargs.get("metadata"))

    def _generate(self, prompt: str, metadata: dict[str, Any] | None) -> ModelResult:
        correlation = sanitize_json(metadata or {})
        case_id = str(correlation.get("case_id", ""))
        task_id = str(correlation.get("task_id", ""))
        attempt = int(correlation.get("attempt", 0))
        if not case_id or not task_id or attempt < 1:
            raise ScriptedProviderError(
                "Fake provider requires case_id, task_id, and positive attempt metadata."
            )
        if self.budget is not None:
            self.budget.reserve(prompt, maximum_output_tokens=64)
        outcome = self.scripts.consume(
            case_id=case_id,
            task_id=task_id,
            role=self.role,
            attempt=attempt,
            provider=self.provider_name,
            model=self.model_name,
        )
        error = self._error(outcome.kind)
        if error is not None:
            self.scripts.record_call(
                ScriptedCall(
                    case_id=case_id,
                    task_id=task_id,
                    role=self.role.value,
                    attempt=attempt,
                    provider=self.provider_name,
                    model=self.model_name,
                    succeeded=False,
                    error_category=error.error_category,
                )
            )
            raise error
        result = ModelResult(
            value=outcome.value,
            provider=self.provider_name,
            model=self.model_name,
            role=self.role,
            request_id=f"eval-{uuid.uuid4().hex[:12]}",
            usage=outcome.usage,
            latency_seconds=outcome.latency_seconds,
            provider_metadata=sanitize_json(outcome.provider_metadata),
            retry_count=outcome.retry_count,
            fallback_used=outcome.fallback_used,
            original_provider=outcome.original_provider,
            original_error_category=outcome.original_error_category,
        )
        if self.budget is not None:
            self.budget.record(result)
        self.scripts.record_call(
            ScriptedCall(
                case_id=case_id,
                task_id=task_id,
                role=self.role.value,
                attempt=attempt,
                provider=self.provider_name,
                model=self.model_name,
                succeeded=True,
            ),
            result,
        )
        return result

    def _error(self, kind: str):
        arguments = {"provider": self.provider_name, "model": self.model_name}
        if kind == "malformed":
            return ModelOutputError("Deterministic malformed output.", **arguments)
        if kind == "timeout":
            return ModelTimeoutError("Deterministic timeout.", **arguments)
        if kind == "transient_failure":
            return ModelServiceUnavailableError(
                "Deterministic transient failure.", **arguments
            )
        if kind == "authentication_failure":
            return ModelAuthenticationError(
                "Deterministic authentication failure.", **arguments
            )
        return None


class BudgetedProvider:
    """Wraps any live provider with local pre-call budget enforcement."""

    def __init__(self, inner, budget: EvaluationBudget):
        self.inner = inner
        self.budget = budget

    @property
    def provider_name(self) -> str:
        return self.inner.provider_name

    @property
    def model_name(self) -> str:
        return self.inner.model_name

    def generate_json(self, prompt: str, **kwargs: Any) -> ModelResult:
        return self._call("generate_json", prompt, kwargs)

    def generate_text(self, prompt: str, **kwargs: Any) -> ModelResult:
        return self._call("generate_text", prompt, kwargs)

    def _call(self, method: str, prompt: str, kwargs: dict[str, Any]) -> ModelResult:
        self.budget.reserve(prompt)
        remaining = self.budget.remaining_seconds()
        requested = kwargs.get("timeout_seconds")
        kwargs["timeout_seconds"] = min(requested or remaining, remaining)
        result = getattr(self.inner, method)(prompt, **kwargs)
        self.budget.record(result)
        return result


class BudgetedProviderFactory:
    def __init__(self, inner, budget: EvaluationBudget):
        self.inner = inner
        self.budget = budget
        # The parallel runtime clones provider factories from their builders.
        # Keep those clones budgeted instead of exposing the unwrapped builders.
        self.builders = {
            provider: self.create for provider in ("azure", "ollama")
        }

    def create(self, route):
        return BudgetedProvider(self.inner.create(route), self.budget)
