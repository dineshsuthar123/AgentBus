from __future__ import annotations

import math
import os
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import TYPE_CHECKING, Any

from agentbus.security.redaction import safe_endpoint_host

if TYPE_CHECKING:
    from agentbus.tools.protocol import ToolResourceBudget


def _default_tool_resource_budget() -> ToolResourceBudget:
    # Tool package initialization eventually imports config through model routing.
    from agentbus.tools.protocol import ToolResourceBudget

    return ToolResourceBudget()


SUPPORTED_PROVIDERS = ("ollama", "azure", "deterministic")
SUPPORTED_AZURE_API_MODES = ("responses", "chat_completions")
SUPPORTED_DETERMINISTIC_PROFILES = (
    "python-calculator",
    "cancellation-two-task",
    "tool-safe-read",
    "tool-atomic-write",
    "tool-source-patch",
    "tool-pytest",
    "tool-git-diff",
    "tool-git-commit",
    "tool-delete-approval",
    "tool-deny-outside-read",
    "tool-deny-credential-read",
    "tool-process-timeout",
    "tool-process-cancel",
    "tool-excessive-output",
    "tool-budget-exhaustion",
    "tool-local-mcp",
    "tool-loop-limit",
)
SUPPORTED_DETERMINISTIC_FAILURES = (
    "output_error",
    "timeout",
    "service_unavailable",
)


@dataclass(frozen=True)
class AgentBusConfig:
    # Existing local defaults remain the source of truth for Ollama.
    model_name: str = "qwen2.5-coder:7b"
    ollama_url: str = "http://localhost:11434/api/generate"
    workspace_dir: str = "workspace"
    runs_dir: str = "runs"
    state_dir: str = ".agentbus"
    state_db: str = "state.db"
    max_steps: int = 12
    command_timeout_seconds: int = 90
    tool_resource_budget: ToolResourceBudget = field(
        default_factory=_default_tool_resource_budget
    )
    max_history_chars: int = 25_000
    parallel_execution: bool = False
    max_workers: int = 1
    worker_lease_seconds: float = 120.0
    worker_heartbeat_seconds: float = 30.0
    worktree_root: str | None = None
    keep_worktrees: bool = True
    integration_strategy: str = "cherry-pick"

    provider_name: str = "ollama"
    fallback_provider_name: str = "ollama"
    enable_provider_fallback: bool = False
    model_timeout_seconds: float = 180.0
    model_max_retries: int = 2
    model_retry_base_seconds: float = 0.5
    model_retry_max_seconds: float = 8.0

    # CLI role overrides apply to the active provider. Azure environment settings
    # remain separate because deployment names are not public model identifiers.
    planner_model: str | None = None
    coder_model: str | None = None
    reviewer_model: str | None = None
    summarizer_model: str | None = None

    deterministic_profile: str = "python-calculator"
    deterministic_latency_seconds: float = 0.0
    deterministic_latency_roles: tuple[str, ...] = ()
    deterministic_failure_kind: str = "service_unavailable"
    deterministic_failure_calls: tuple[int, ...] = ()
    deterministic_failure_roles: tuple[str, ...] = ()

    azure_openai_endpoint: str | None = field(default=None, repr=False)
    azure_openai_api_key: str | None = field(default=None, repr=False)
    azure_openai_auth_mode: str = "api_key"
    azure_openai_api_mode: str = "responses"
    azure_openai_default_deployment: str | None = None
    azure_openai_planner_deployment: str | None = None
    azure_openai_coder_deployment: str | None = None
    azure_openai_reviewer_deployment: str | None = None
    azure_openai_summarizer_deployment: str | None = None
    azure_openai_timeout_seconds: float | None = None
    azure_openai_max_retries: int | None = None

    def __post_init__(self) -> None:
        self.validate_static()

    @classmethod
    def from_env(cls) -> "AgentBusConfig":
        return cls(
            model_name=_env_text("AGENTBUS_MODEL") or cls.model_name,
            ollama_url=_env_text("AGENTBUS_OLLAMA_URL") or cls.ollama_url,
            workspace_dir=_env_text("AGENTBUS_WORKSPACE") or cls.workspace_dir,
            runs_dir=_env_text("AGENTBUS_RUNS_DIR") or cls.runs_dir,
            state_dir=_env_text("AGENTBUS_STATE_DIR") or cls.state_dir,
            state_db=_env_text("AGENTBUS_STATE_DB") or cls.state_db,
            max_steps=_env_int("AGENTBUS_MAX_STEPS", cls.max_steps, minimum=1),
            command_timeout_seconds=_env_int(
                "AGENTBUS_COMMAND_TIMEOUT",
                cls.command_timeout_seconds,
                minimum=1,
            ),
            max_history_chars=_env_int(
                "AGENTBUS_MAX_HISTORY_CHARS",
                cls.max_history_chars,
                minimum=1,
            ),
            parallel_execution=_env_bool(
                "AGENTBUS_PARALLEL_EXECUTION", cls.parallel_execution
            ),
            max_workers=_env_int("AGENTBUS_MAX_WORKERS", cls.max_workers, minimum=1),
            worker_lease_seconds=_env_float(
                "AGENTBUS_WORKER_LEASE_SECONDS",
                cls.worker_lease_seconds,
                minimum=0.001,
            ),
            worker_heartbeat_seconds=_env_float(
                "AGENTBUS_WORKER_HEARTBEAT_SECONDS",
                cls.worker_heartbeat_seconds,
                minimum=0.001,
            ),
            worktree_root=_env_text("AGENTBUS_WORKTREE_ROOT"),
            keep_worktrees=_env_bool("AGENTBUS_KEEP_WORKTREES", cls.keep_worktrees),
            integration_strategy=(
                _env_text("AGENTBUS_INTEGRATION_STRATEGY")
                or cls.integration_strategy
            ).lower(),
            provider_name=(
                _env_text("AGENTBUS_PROVIDER") or cls.provider_name
            ).lower(),
            fallback_provider_name=(
                _env_text("AGENTBUS_FALLBACK_PROVIDER")
                or cls.fallback_provider_name
            ).lower(),
            enable_provider_fallback=_env_bool(
                "AGENTBUS_ENABLE_PROVIDER_FALLBACK",
                cls.enable_provider_fallback,
            ),
            model_timeout_seconds=_env_float(
                "AGENTBUS_MODEL_TIMEOUT_SECONDS",
                cls.model_timeout_seconds,
                minimum=0.001,
            ),
            model_max_retries=_env_int(
                "AGENTBUS_MODEL_MAX_RETRIES",
                cls.model_max_retries,
                minimum=0,
            ),
            model_retry_base_seconds=_env_float(
                "AGENTBUS_MODEL_RETRY_BASE_SECONDS",
                cls.model_retry_base_seconds,
                minimum=0,
            ),
            model_retry_max_seconds=_env_float(
                "AGENTBUS_MODEL_RETRY_MAX_SECONDS",
                cls.model_retry_max_seconds,
                minimum=0,
            ),
            planner_model=_env_text("AGENTBUS_PLANNER_MODEL"),
            coder_model=_env_text("AGENTBUS_CODER_MODEL"),
            reviewer_model=_env_text("AGENTBUS_REVIEWER_MODEL"),
            summarizer_model=_env_text("AGENTBUS_SUMMARIZER_MODEL"),
            deterministic_profile=(
                _env_text("AGENTBUS_DETERMINISTIC_PROFILE")
                or cls.deterministic_profile
            ).lower(),
            deterministic_latency_seconds=_env_float(
                "AGENTBUS_DETERMINISTIC_LATENCY_SECONDS",
                cls.deterministic_latency_seconds,
                minimum=0,
            ),
            deterministic_latency_roles=_env_csv(
                "AGENTBUS_DETERMINISTIC_LATENCY_ROLES"
            ),
            deterministic_failure_kind=(
                _env_text("AGENTBUS_DETERMINISTIC_FAILURE_KIND")
                or cls.deterministic_failure_kind
            ).lower(),
            deterministic_failure_calls=_env_int_csv(
                "AGENTBUS_DETERMINISTIC_FAILURE_CALLS",
                minimum=1,
            ),
            deterministic_failure_roles=_env_csv(
                "AGENTBUS_DETERMINISTIC_FAILURE_ROLES"
            ),
            azure_openai_endpoint=_env_text("AZURE_OPENAI_ENDPOINT"),
            azure_openai_api_key=_env_text("AZURE_OPENAI_API_KEY"),
            azure_openai_auth_mode=(
                _env_text("AZURE_OPENAI_AUTH_MODE")
                or cls.azure_openai_auth_mode
            ).lower(),
            azure_openai_api_mode=(
                _env_text("AZURE_OPENAI_API_MODE") or cls.azure_openai_api_mode
            ).lower(),
            azure_openai_default_deployment=_env_text(
                "AZURE_OPENAI_DEFAULT_DEPLOYMENT"
            ),
            azure_openai_planner_deployment=_env_text(
                "AZURE_OPENAI_PLANNER_DEPLOYMENT"
            ),
            azure_openai_coder_deployment=_env_text(
                "AZURE_OPENAI_CODER_DEPLOYMENT"
            ),
            azure_openai_reviewer_deployment=_env_text(
                "AZURE_OPENAI_REVIEWER_DEPLOYMENT"
            ),
            azure_openai_summarizer_deployment=_env_text(
                "AZURE_OPENAI_SUMMARIZER_DEPLOYMENT"
            ),
            azure_openai_timeout_seconds=_env_optional_float(
                "AZURE_OPENAI_TIMEOUT_SECONDS",
                minimum=0.001,
            ),
            azure_openai_max_retries=_env_optional_int(
                "AZURE_OPENAI_MAX_RETRIES",
                minimum=0,
            ),
        )

    def with_overrides(
        self,
        *,
        model_name: str | None = None,
        workspace_dir: str | None = None,
        max_steps: int | None = None,
        provider_name: str | None = None,
        fallback_provider_name: str | None = None,
        enable_provider_fallback: bool | None = None,
        planner_model: str | None = None,
        coder_model: str | None = None,
        reviewer_model: str | None = None,
        summarizer_model: str | None = None,
        deterministic_profile: str | None = None,
        deterministic_latency_seconds: float | None = None,
        deterministic_latency_roles: tuple[str, ...] | None = None,
        deterministic_failure_kind: str | None = None,
        deterministic_failure_calls: tuple[int, ...] | None = None,
        deterministic_failure_roles: tuple[str, ...] | None = None,
        tool_resource_budget: ToolResourceBudget | None = None,
        model_timeout_seconds: float | None = None,
        parallel_execution: bool | None = None,
        max_workers: int | None = None,
        worktree_root: str | None = None,
        keep_worktrees: bool | None = None,
    ) -> "AgentBusConfig":
        updates: dict[str, Any] = {}
        effective_provider = (provider_name or self.provider_name).lower()

        if model_name is not None:
            updates["model_name"] = model_name
            if effective_provider == "azure":
                updates["azure_openai_default_deployment"] = model_name
        if workspace_dir is not None:
            updates["workspace_dir"] = workspace_dir
        if max_steps is not None:
            updates["max_steps"] = max_steps
        if provider_name is not None:
            updates["provider_name"] = provider_name.lower()
        if fallback_provider_name is not None:
            updates["fallback_provider_name"] = fallback_provider_name.lower()
        if enable_provider_fallback is not None:
            updates["enable_provider_fallback"] = enable_provider_fallback
        if planner_model is not None:
            updates["planner_model"] = planner_model
        if coder_model is not None:
            updates["coder_model"] = coder_model
        if reviewer_model is not None:
            updates["reviewer_model"] = reviewer_model
        if summarizer_model is not None:
            updates["summarizer_model"] = summarizer_model
        if deterministic_profile is not None:
            updates["deterministic_profile"] = deterministic_profile.lower()
        if deterministic_latency_seconds is not None:
            updates["deterministic_latency_seconds"] = (
                deterministic_latency_seconds
            )
        if deterministic_latency_roles is not None:
            updates["deterministic_latency_roles"] = tuple(
                role.lower() for role in deterministic_latency_roles
            )
        if deterministic_failure_kind is not None:
            updates["deterministic_failure_kind"] = (
                deterministic_failure_kind.lower()
            )
        if deterministic_failure_calls is not None:
            updates["deterministic_failure_calls"] = tuple(
                deterministic_failure_calls
            )
        if deterministic_failure_roles is not None:
            updates["deterministic_failure_roles"] = tuple(
                role.lower() for role in deterministic_failure_roles
            )
        if tool_resource_budget is not None:
            updates["tool_resource_budget"] = tool_resource_budget
        if model_timeout_seconds is not None:
            updates["model_timeout_seconds"] = model_timeout_seconds
        if parallel_execution is not None:
            updates["parallel_execution"] = parallel_execution
        if max_workers is not None:
            updates["max_workers"] = max_workers
        if worktree_root is not None:
            updates["worktree_root"] = worktree_root
        if keep_worktrees is not None:
            updates["keep_worktrees"] = keep_worktrees

        return replace(self, **updates)

    def validate_static(self) -> None:
        _validate_provider("AGENTBUS_PROVIDER", self.provider_name)
        _validate_provider(
            "AGENTBUS_FALLBACK_PROVIDER",
            self.fallback_provider_name,
        )
        if self.provider_name == "azure":
            self.validate_azure_modes()
        if not self.model_name.strip():
            raise ValueError("AGENTBUS_MODEL must not be empty")
        if self.max_steps <= 0:
            raise ValueError("AGENTBUS_MAX_STEPS must be greater than 0")
        if self.command_timeout_seconds <= 0:
            raise ValueError("AGENTBUS_COMMAND_TIMEOUT must be greater than 0")
        if self.max_history_chars <= 0:
            raise ValueError("AGENTBUS_MAX_HISTORY_CHARS must be greater than 0")
        if self.max_workers < 1:
            raise ValueError("AGENTBUS_MAX_WORKERS must be at least 1")
        if self.worker_lease_seconds <= 0:
            raise ValueError("AGENTBUS_WORKER_LEASE_SECONDS must be greater than 0")
        if self.worker_heartbeat_seconds <= 0:
            raise ValueError("AGENTBUS_WORKER_HEARTBEAT_SECONDS must be greater than 0")
        if self.worker_heartbeat_seconds >= self.worker_lease_seconds / 2:
            raise ValueError(
                "AGENTBUS_WORKER_HEARTBEAT_SECONDS must be less than half the "
                "worker lease duration"
            )
        if self.integration_strategy != "cherry-pick":
            raise ValueError("AGENTBUS_INTEGRATION_STRATEGY must be 'cherry-pick'")
        if not math.isfinite(self.model_timeout_seconds) or self.model_timeout_seconds <= 0:
            raise ValueError("AGENTBUS_MODEL_TIMEOUT_SECONDS must be greater than 0")
        if self.model_max_retries < 0:
            raise ValueError("AGENTBUS_MODEL_MAX_RETRIES must be at least 0")
        if (
            not math.isfinite(self.model_retry_base_seconds)
            or self.model_retry_base_seconds < 0
        ):
            raise ValueError("AGENTBUS_MODEL_RETRY_BASE_SECONDS must be at least 0")
        if (
            not math.isfinite(self.model_retry_max_seconds)
            or self.model_retry_max_seconds < self.model_retry_base_seconds
        ):
            raise ValueError(
                "AGENTBUS_MODEL_RETRY_MAX_SECONDS must be greater than or equal "
                "to AGENTBUS_MODEL_RETRY_BASE_SECONDS"
            )
        if self.enable_provider_fallback and not (
            self.provider_name == "azure"
            and self.fallback_provider_name == "ollama"
        ):
            raise ValueError(
                "Provider fallback currently supports only Azure to Ollama."
            )
        if self.azure_openai_timeout_seconds is not None and (
            not math.isfinite(self.azure_openai_timeout_seconds)
            or self.azure_openai_timeout_seconds <= 0
        ):
            raise ValueError("AZURE_OPENAI_TIMEOUT_SECONDS must be greater than 0")
        if (
            self.azure_openai_max_retries is not None
            and self.azure_openai_max_retries < 0
        ):
            raise ValueError("AZURE_OPENAI_MAX_RETRIES must be at least 0")
        if self.deterministic_profile not in SUPPORTED_DETERMINISTIC_PROFILES:
            choices = ", ".join(SUPPORTED_DETERMINISTIC_PROFILES)
            raise ValueError(
                "AGENTBUS_DETERMINISTIC_PROFILE must be one of: "
                f"{choices}"
            )
        if (
            not math.isfinite(self.deterministic_latency_seconds)
            or self.deterministic_latency_seconds < 0
        ):
            raise ValueError(
                "AGENTBUS_DETERMINISTIC_LATENCY_SECONDS must be at least 0"
            )
        if self.deterministic_failure_kind not in SUPPORTED_DETERMINISTIC_FAILURES:
            choices = ", ".join(SUPPORTED_DETERMINISTIC_FAILURES)
            raise ValueError(
                "AGENTBUS_DETERMINISTIC_FAILURE_KIND must be one of: "
                f"{choices}"
            )
        if any(call < 1 for call in self.deterministic_failure_calls):
            raise ValueError(
                "AGENTBUS_DETERMINISTIC_FAILURE_CALLS must contain positive integers"
            )
        for role in (
            *self.deterministic_latency_roles,
            *self.deterministic_failure_roles,
        ):
            _normalize_role(role)

    def resolve_model(self, role: str, *, provider: str | None = None) -> str:
        selected_provider = (provider or self.provider_name).lower()
        _validate_provider("provider", selected_provider)
        role_name = _normalize_role(role)
        role_override = (
            getattr(self, f"{role_name}_model", None)
            if selected_provider == self.provider_name
            else None
        )

        if selected_provider == "ollama":
            return role_override or self.model_name
        if selected_provider == "deterministic":
            return role_override or "deterministic-v1"

        role_deployment = getattr(
            self,
            f"azure_openai_{role_name}_deployment",
            None,
        )
        deployment = (
            role_override
            or role_deployment
            or self.azure_openai_default_deployment
        )
        if not deployment:
            env_name = f"AZURE_OPENAI_{role_name.upper()}_DEPLOYMENT"
            if role_name == "default":
                raise ValueError(
                    "No Azure default deployment is configured. Set "
                    "AZURE_OPENAI_DEFAULT_DEPLOYMENT."
                )
            raise ValueError(
                f"No Azure deployment is configured for role '{role_name}'. Set "
                f"{env_name} or AZURE_OPENAI_DEFAULT_DEPLOYMENT."
            )
        return deployment

    def validate_provider_configuration(
        self,
        provider: str,
        *,
        role: str = "default",
    ) -> str:
        selected_provider = provider.lower()
        _validate_provider("provider", selected_provider)
        model = self.resolve_model(role, provider=selected_provider)
        if selected_provider == "ollama":
            from urllib.parse import urlsplit

            parsed = urlsplit(self.ollama_url)
            if parsed.scheme not in {"http", "https"} or not parsed.hostname:
                raise ValueError(
                    "AGENTBUS_OLLAMA_URL must be an HTTP(S) URL with a hostname."
                )
            return model
        if selected_provider == "deterministic":
            return model
        if selected_provider == "azure":
            self.validate_azure_modes()
            if not self.azure_openai_endpoint:
                raise ValueError(
                    "AZURE_OPENAI_ENDPOINT is required when Azure is selected."
                )
            if not self.azure_openai_api_key:
                raise ValueError(
                    "AZURE_OPENAI_API_KEY is required for API-key authentication."
                )
            from agentbus.models.azure_openai import normalize_azure_v1_endpoint

            normalize_azure_v1_endpoint(self.azure_openai_endpoint)
        return model

    def validate_azure_modes(self) -> None:
        if self.azure_openai_auth_mode != "api_key":
            raise ValueError(
                "AZURE_OPENAI_AUTH_MODE must be 'api_key'; Entra authentication "
                "is not implemented in this checkpoint."
            )
        if self.azure_openai_api_mode not in SUPPORTED_AZURE_API_MODES:
            choices = ", ".join(SUPPORTED_AZURE_API_MODES)
            raise ValueError(f"AZURE_OPENAI_API_MODE must be one of: {choices}")

    def route_timeout(self, provider: str) -> float:
        if provider == "azure" and self.azure_openai_timeout_seconds is not None:
            return self.azure_openai_timeout_seconds
        return self.model_timeout_seconds

    def route_max_retries(self, provider: str) -> int:
        if provider == "azure" and self.azure_openai_max_retries is not None:
            return self.azure_openai_max_retries
        return self.model_max_retries

    def safe_model_summary(self) -> dict[str, Any]:
        routes: dict[str, Any] = {}
        for role in ("default", "planner", "coder", "reviewer", "summarizer"):
            try:
                model = self.resolve_model(role)
                error = None
            except ValueError as exc:
                model = None
                error = str(exc)
            routes[role] = {"model": model, "error": error}
        return {
            "provider": self.provider_name,
            "fallback_provider": self.fallback_provider_name,
            "fallback_enabled": self.enable_provider_fallback,
            "timeout_seconds": self.route_timeout(self.provider_name),
            "max_retries": self.route_max_retries(self.provider_name),
            "endpoint_host": safe_endpoint_host(self.azure_openai_endpoint),
            "azure_auth_mode": self.azure_openai_auth_mode,
            "azure_api_mode": self.azure_openai_api_mode,
            "azure_api_key_configured": bool(self.azure_openai_api_key),
            "routes": routes,
        }

    @property
    def workspace_path(self) -> Path:
        return Path(self.workspace_dir).expanduser().resolve()

    @property
    def worktree_root_path(self) -> Path:
        if self.worktree_root:
            return Path(self.worktree_root).expanduser().resolve()
        workspace = self.workspace_path
        return (workspace.parent / ".agentbus-worktrees" / workspace.name).resolve()

    @property
    def state_database_path(self) -> Path:
        database = Path(self.state_db).expanduser()
        if database.is_absolute():
            return database
        return Path(self.state_dir).expanduser() / database


def _env_text(name: str) -> str | None:
    raw = os.getenv(name)
    if raw is None:
        return None
    value = raw.strip()
    return value or None


def _env_bool(name: str, default: bool) -> bool:
    raw = _env_text(name)
    if raw is None:
        return default
    normalized = raw.lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"{name} must be true or false, got {raw!r}")


def _env_int(name: str, default: int, *, minimum: int) -> int:
    raw = _env_text(name)
    if raw is None:
        return default
    return _parse_int(name, raw, minimum=minimum)


def _env_optional_int(name: str, *, minimum: int) -> int | None:
    raw = _env_text(name)
    if raw is None:
        return None
    return _parse_int(name, raw, minimum=minimum)


def _parse_int(name: str, raw: str, *, minimum: int) -> int:
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer, got {raw!r}") from exc
    if value < minimum:
        raise ValueError(f"{name} must be at least {minimum}, got {value}")
    return value


def _env_float(name: str, default: float, *, minimum: float) -> float:
    raw = _env_text(name)
    if raw is None:
        return default
    return _parse_float(name, raw, minimum=minimum)


def _env_optional_float(name: str, *, minimum: float) -> float | None:
    raw = _env_text(name)
    if raw is None:
        return None
    return _parse_float(name, raw, minimum=minimum)


def _env_csv(name: str) -> tuple[str, ...]:
    raw = _env_text(name)
    if raw is None:
        return ()
    values = tuple(item.strip().lower() for item in raw.split(",") if item.strip())
    if len(values) != len(set(values)):
        raise ValueError(f"{name} must not contain duplicate values")
    return values


def _env_int_csv(name: str, *, minimum: int) -> tuple[int, ...]:
    values = _env_csv(name)
    return tuple(_parse_int(name, value, minimum=minimum) for value in values)


def _parse_float(name: str, raw: str, *, minimum: float) -> float:
    try:
        value = float(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be numeric, got {raw!r}") from exc
    if not math.isfinite(value) or value < minimum:
        raise ValueError(f"{name} must be at least {minimum}, got {value}")
    return value


def _validate_provider(name: str, provider: str) -> None:
    if provider not in SUPPORTED_PROVIDERS:
        choices = ", ".join(SUPPORTED_PROVIDERS)
        raise ValueError(f"{name} must be one of: {choices}; got {provider!r}")


def _normalize_role(role: str) -> str:
    normalized = getattr(role, "value", role)
    normalized = str(normalized).lower()
    if normalized not in {"default", "planner", "coder", "reviewer", "summarizer"}:
        raise ValueError(f"Unsupported model role: {normalized!r}")
    return normalized
