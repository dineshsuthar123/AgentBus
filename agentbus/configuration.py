from __future__ import annotations

import json
import os
import tomllib
from dataclasses import asdict, dataclass, fields
from pathlib import Path
from typing import Any, Mapping

from agentbus.config import AgentBusConfig
from agentbus.security.redaction import is_sensitive_key, safe_endpoint_host


ENVIRONMENT_FIELDS: dict[str, str] = {
    "AGENTBUS_MODEL": "model_name",
    "AGENTBUS_OLLAMA_URL": "ollama_url",
    "AGENTBUS_WORKSPACE": "workspace_dir",
    "AGENTBUS_RUNS_DIR": "runs_dir",
    "AGENTBUS_STATE_DIR": "state_dir",
    "AGENTBUS_STATE_DB": "state_db",
    "AGENTBUS_MAX_STEPS": "max_steps",
    "AGENTBUS_COMMAND_TIMEOUT": "command_timeout_seconds",
    "AGENTBUS_MAX_HISTORY_CHARS": "max_history_chars",
    "AGENTBUS_PARALLEL_EXECUTION": "parallel_execution",
    "AGENTBUS_MAX_WORKERS": "max_workers",
    "AGENTBUS_WORKER_LEASE_SECONDS": "worker_lease_seconds",
    "AGENTBUS_WORKER_HEARTBEAT_SECONDS": "worker_heartbeat_seconds",
    "AGENTBUS_WORKTREE_ROOT": "worktree_root",
    "AGENTBUS_KEEP_WORKTREES": "keep_worktrees",
    "AGENTBUS_INTEGRATION_STRATEGY": "integration_strategy",
    "AGENTBUS_PROVIDER": "provider_name",
    "AGENTBUS_FALLBACK_PROVIDER": "fallback_provider_name",
    "AGENTBUS_ENABLE_PROVIDER_FALLBACK": "enable_provider_fallback",
    "AGENTBUS_MODEL_TIMEOUT_SECONDS": "model_timeout_seconds",
    "AGENTBUS_MODEL_MAX_RETRIES": "model_max_retries",
    "AGENTBUS_MODEL_RETRY_BASE_SECONDS": "model_retry_base_seconds",
    "AGENTBUS_MODEL_RETRY_MAX_SECONDS": "model_retry_max_seconds",
    "AGENTBUS_PLANNER_MODEL": "planner_model",
    "AGENTBUS_CODER_MODEL": "coder_model",
    "AGENTBUS_REVIEWER_MODEL": "reviewer_model",
    "AGENTBUS_SUMMARIZER_MODEL": "summarizer_model",
    "AZURE_OPENAI_ENDPOINT": "azure_openai_endpoint",
    "AZURE_OPENAI_API_KEY": "azure_openai_api_key",
    "AZURE_OPENAI_AUTH_MODE": "azure_openai_auth_mode",
    "AZURE_OPENAI_API_MODE": "azure_openai_api_mode",
    "AZURE_OPENAI_DEFAULT_DEPLOYMENT": "azure_openai_default_deployment",
    "AZURE_OPENAI_PLANNER_DEPLOYMENT": "azure_openai_planner_deployment",
    "AZURE_OPENAI_CODER_DEPLOYMENT": "azure_openai_coder_deployment",
    "AZURE_OPENAI_REVIEWER_DEPLOYMENT": "azure_openai_reviewer_deployment",
    "AZURE_OPENAI_SUMMARIZER_DEPLOYMENT": "azure_openai_summarizer_deployment",
    "AZURE_OPENAI_TIMEOUT_SECONDS": "azure_openai_timeout_seconds",
    "AZURE_OPENAI_MAX_RETRIES": "azure_openai_max_retries",
}

_BOOLEAN_FIELDS = {
    "parallel_execution",
    "keep_worktrees",
    "enable_provider_fallback",
}
_INTEGER_FIELDS = {
    "max_steps",
    "command_timeout_seconds",
    "max_history_chars",
    "max_workers",
    "model_max_retries",
    "azure_openai_max_retries",
}
_FLOAT_FIELDS = {
    "worker_lease_seconds",
    "worker_heartbeat_seconds",
    "model_timeout_seconds",
    "model_retry_base_seconds",
    "model_retry_max_seconds",
    "azure_openai_timeout_seconds",
}
_LOWERCASE_FIELDS = {
    "provider_name",
    "fallback_provider_name",
    "integration_strategy",
    "azure_openai_auth_mode",
    "azure_openai_api_mode",
}


@dataclass(frozen=True)
class ResolvedConfiguration:
    config: AgentBusConfig
    sources: dict[str, str]
    config_file: Path | None = None

    def safe_values(self) -> dict[str, dict[str, Any]]:
        values = asdict(self.config)
        output: dict[str, dict[str, Any]] = {}
        for name in sorted(values):
            value = values[name]
            if is_sensitive_key(name):
                value = "[configured]" if value else "[not configured]"
            elif name in {"azure_openai_endpoint", "ollama_url"}:
                value = safe_endpoint_host(value)
            output[name] = {"value": value, "source": self.sources[name]}
        return output


def resolve_configuration(
    *,
    config_file: str | Path | None = None,
    cli_overrides: Mapping[str, Any] | None = None,
    environ: Mapping[str, str] | None = None,
) -> ResolvedConfiguration:
    """Resolve defaults < explicit file < environment < CLI without dotenv search."""

    defaults = AgentBusConfig()
    values = asdict(defaults)
    sources = {name: "default" for name in values}
    loaded_path: Path | None = None

    if config_file is not None:
        loaded_path = Path(config_file).expanduser().resolve(strict=True)
        for name, value in _load_file(loaded_path).items():
            values[name] = value
            sources[name] = f"config:{loaded_path}"

    environment = os.environ if environ is None else environ
    for variable, field_name in ENVIRONMENT_FIELDS.items():
        raw = environment.get(variable)
        if raw is None or not str(raw).strip():
            continue
        values[field_name] = _parse_environment_value(variable, field_name, str(raw))
        sources[field_name] = f"environment:{variable}"

    for name, value in (cli_overrides or {}).items():
        if value is None:
            continue
        if name not in values:
            raise ValueError(f"Unknown AgentBus configuration option: {name}")
        values[name] = value
        sources[name] = "cli"

    config = AgentBusConfig(**values)
    return ResolvedConfiguration(config=config, sources=sources, config_file=loaded_path)


def configuration_paths(resolved: ResolvedConfiguration) -> dict[str, str | None]:
    config = resolved.config
    return {
        "config_file": str(resolved.config_file) if resolved.config_file else None,
        "workspace": str(config.workspace_path),
        "state_database": str(config.state_database_path.expanduser().resolve()),
        "runs_directory": str(Path(config.runs_dir).expanduser().resolve()),
        "worktree_root": str(config.worktree_root_path),
        "dotenv_search": "disabled",
    }


def _load_file(path: Path) -> dict[str, Any]:
    if path.suffix.lower() == ".json":
        try:
            document = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(f"Unable to read AgentBus JSON config: {path}") from exc
    elif path.suffix.lower() == ".toml":
        try:
            with path.open("rb") as handle:
                document = tomllib.load(handle)
        except (OSError, tomllib.TOMLDecodeError) as exc:
            raise ValueError(f"Unable to read AgentBus TOML config: {path}") from exc
    else:
        raise ValueError("AgentBus config files must use .toml or .json")
    if not isinstance(document, dict):
        raise ValueError("AgentBus config must contain an object/table")
    raw = document.get("agentbus", document)
    if not isinstance(raw, dict):
        raise ValueError("The 'agentbus' config section must be a table/object")
    allowed = {field.name for field in fields(AgentBusConfig)}
    unknown = sorted(set(raw) - allowed)
    if unknown:
        raise ValueError("Unknown AgentBus config option(s): " + ", ".join(unknown))
    return dict(raw)


def _parse_environment_value(variable: str, field_name: str, raw: str) -> Any:
    value = raw.strip()
    if field_name in _BOOLEAN_FIELDS:
        normalized = value.lower()
        if normalized in {"1", "true", "yes", "on"}:
            return True
        if normalized in {"0", "false", "no", "off"}:
            return False
        raise ValueError(f"{variable} must be true or false, got {value!r}")
    if field_name in _INTEGER_FIELDS:
        try:
            return int(value)
        except ValueError as exc:
            raise ValueError(f"{variable} must be an integer, got {value!r}") from exc
    if field_name in _FLOAT_FIELDS:
        try:
            return float(value)
        except ValueError as exc:
            raise ValueError(f"{variable} must be numeric, got {value!r}") from exc
    return value.lower() if field_name in _LOWERCASE_FIELDS else value
