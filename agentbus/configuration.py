from __future__ import annotations

import json
import os
import tomllib
from dataclasses import asdict, dataclass, fields
from pathlib import Path
from typing import TYPE_CHECKING, Any, Mapping

from agentbus.config import AgentBusConfig
from agentbus.security.redaction import is_sensitive_key, safe_endpoint_host

if TYPE_CHECKING:
    from agentbus.mcp.models import McpServerConfig
    from agentbus.tools.protocol import ToolResourceBudget


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
    "AGENTBUS_DURABLE_EXECUTION": "durable_execution",
    "AGENTBUS_PARALLEL_EXECUTION": "parallel_execution",
    "AGENTBUS_MAX_WORKERS": "max_workers",
    "AGENTBUS_WORKER_LEASE_SECONDS": "worker_lease_seconds",
    "AGENTBUS_WORKER_HEARTBEAT_SECONDS": "worker_heartbeat_seconds",
    "AGENTBUS_WORKTREE_ROOT": "worktree_root",
    "AGENTBUS_KEEP_WORKTREES": "keep_worktrees",
    "AGENTBUS_INTEGRATION_STRATEGY": "integration_strategy",
    "AGENTBUS_POLICY_MODE": "policy_mode",
    "AGENTBUS_REPOSITORY_INTELLIGENCE": "repository_intelligence",
    "AGENTBUS_SEMANTIC_RETRIEVAL": "semantic_retrieval",
    "AGENTBUS_TRACE_RETENTION_DAYS": "trace_retention_days",
    "AGENTBUS_DAEMON_AUTO_START": "daemon_auto_start",
    "AGENTBUS_DAEMON_IDLE_TIMEOUT_SECONDS": "daemon_idle_timeout_seconds",
    "AGENTBUS_LOG_LEVEL": "log_level",
    "AGENTBUS_LOG_RETENTION_FILES": "log_retention_files",
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
    "durable_execution",
    "parallel_execution",
    "keep_worktrees",
    "enable_provider_fallback",
    "repository_intelligence",
    "semantic_retrieval",
    "daemon_auto_start",
}
_INTEGER_FIELDS = {
    "max_steps",
    "command_timeout_seconds",
    "max_history_chars",
    "max_workers",
    "model_max_retries",
    "azure_openai_max_retries",
    "trace_retention_days",
    "daemon_idle_timeout_seconds",
    "log_retention_files",
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
    "policy_mode",
    "log_level",
}


@dataclass(frozen=True)
class ResolvedConfiguration:
    config: AgentBusConfig
    sources: dict[str, str]
    config_file: Path | None = None
    layer_paths: dict[str, Path | None] | None = None

    def safe_values(self) -> dict[str, dict[str, Any]]:
        values = asdict(self.config)
        output: dict[str, dict[str, Any]] = {}
        for name in sorted(values):
            value = values[name]
            if is_sensitive_key(name):
                value = "[configured]" if value else "[not configured]"
            elif name == "mcp_server_configs":
                value = [
                    {
                        "server_id": server.server_id,
                        "transport": server.transport.value,
                        "configured_tools": sorted(server.capability_map),
                    }
                    for server in self.config.mcp_server_configs
                ]
            elif name == "tool_resource_budget":
                value = self.config.tool_resource_budget.model_dump(mode="json")
            elif name in {"azure_openai_endpoint", "ollama_url"}:
                value = safe_endpoint_host(value)
            output[name] = {"value": value, "source": self.sources[name]}
        return output


def resolve_configuration(
    *,
    config_file: str | Path | None = None,
    cli_overrides: Mapping[str, Any] | None = None,
    environ: Mapping[str, str] | None = None,
    workspace: str | Path | None = None,
    user_config_file: str | Path | None = None,
    workspace_config_file: str | Path | None = None,
    discover: bool = True,
) -> ResolvedConfiguration:
    """Resolve documented product layers without parent or dotenv searches."""

    defaults = AgentBusConfig()
    values = asdict(defaults)
    sources = {name: "default" for name in values}
    loaded_path: Path | None = None
    environment = os.environ if environ is None else environ
    overrides = dict(cli_overrides or {})
    discovery_root = _workspace_discovery_root(
        workspace=workspace,
        cli_overrides=overrides,
        environ=environment,
    )
    layer_paths: dict[str, Path | None] = {
        "user": None,
        "workspace": None,
        "explicit": None,
    }

    if discover:
        user_path = (
            Path(user_config_file).expanduser()
            if user_config_file is not None
            else default_user_config_path(environment)
        )
        workspace_path = (
            Path(workspace_config_file).expanduser()
            if workspace_config_file is not None
            else discovery_root / ".agentbus" / "config.toml"
        )
        for layer, path in (("user", user_path), ("workspace", workspace_path)):
            if not path.exists():
                continue
            canonical = _canonical_config_path(
                path,
                workspace_root=discovery_root if layer == "workspace" else None,
            )
            document = _load_file(canonical)
            if layer == "workspace":
                _validate_workspace_document(document, discovery_root)
            _apply_layer(values, sources, document, f"{layer}:{canonical}")
            layer_paths[layer] = canonical
            loaded_path = canonical

    if config_file is not None:
        loaded_path = _canonical_config_path(Path(config_file).expanduser())
        _apply_layer(
            values,
            sources,
            _load_file(loaded_path),
            f"explicit:{loaded_path}",
        )
        layer_paths["explicit"] = loaded_path

    for name, value in overrides.items():
        if value is None:
            continue
        if name not in values:
            raise ValueError(f"Unknown AgentBus configuration option: {name}")
        values[name] = value
        sources[name] = "cli"

    for variable, field_name in ENVIRONMENT_FIELDS.items():
        raw = environment.get(variable)
        if raw is None or not str(raw).strip():
            continue
        values[field_name] = _parse_environment_value(variable, field_name, str(raw))
        sources[field_name] = f"environment:{variable}"

    values["mcp_server_configs"] = _coerce_mcp_server_configs(
        values["mcp_server_configs"]
    )
    values["tool_resource_budget"] = _coerce_tool_resource_budget(
        values["tool_resource_budget"]
    )
    config = AgentBusConfig(**values)
    return ResolvedConfiguration(
        config=config,
        sources=sources,
        config_file=loaded_path,
        layer_paths=layer_paths,
    )


def configuration_paths(resolved: ResolvedConfiguration) -> dict[str, str | None]:
    config = resolved.config
    layers = resolved.layer_paths or {}
    return {
        "config_file": str(resolved.config_file) if resolved.config_file else None,
        "user_config": _optional_path(layers.get("user")),
        "workspace_config": _optional_path(layers.get("workspace")),
        "explicit_config": _optional_path(layers.get("explicit")),
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
    sensitive = sorted(name for name in raw if is_sensitive_key(name))
    if sensitive:
        raise ValueError(
            "AgentBus config files cannot contain credentials: "
            + ", ".join(sensitive)
        )
    return dict(raw)


def default_user_config_path(environ: Mapping[str, str] | None = None) -> Path:
    environment = os.environ if environ is None else environ
    if os.name == "nt":
        base = environment.get("APPDATA")
        root = Path(base).expanduser() if base else Path.home() / "AppData" / "Roaming"
        return root / "AgentBus" / "config.toml"
    base = environment.get("XDG_CONFIG_HOME")
    root = Path(base).expanduser() if base else Path.home() / ".config"
    return root / "agentbus" / "config.toml"


def default_workspace_config_path(workspace: str | Path) -> Path:
    return Path(workspace).expanduser().resolve() / ".agentbus" / "config.toml"


def _workspace_discovery_root(
    *,
    workspace: str | Path | None,
    cli_overrides: Mapping[str, Any],
    environ: Mapping[str, str],
) -> Path:
    selected = workspace or cli_overrides.get("workspace_dir")
    if selected is None:
        selected = environ.get("AGENTBUS_WORKSPACE")
    return Path(selected or Path.cwd()).expanduser().resolve()


def _canonical_config_path(
    path: Path,
    *,
    workspace_root: Path | None = None,
) -> Path:
    try:
        canonical = path.resolve(strict=True)
    except OSError as exc:
        raise ValueError(f"Unable to resolve AgentBus config file: {path}") from exc
    if not canonical.is_file():
        raise ValueError(f"AgentBus config path is not a file: {path}")
    if workspace_root is not None:
        root = workspace_root.resolve()
        if not canonical.is_relative_to(root):
            raise ValueError(
                "Workspace configuration resolves outside the workspace: "
                f"{path}"
            )
    return canonical


def _validate_workspace_document(document: Mapping[str, Any], root: Path) -> None:
    configured_workspace = document.get("workspace_dir")
    if configured_workspace is None:
        return
    target = Path(str(configured_workspace)).expanduser()
    if not target.is_absolute():
        target = root / target
    if target.resolve() != root.resolve():
        raise ValueError(
            "Workspace configuration cannot redirect execution outside its workspace"
        )


def _apply_layer(
    values: dict[str, Any],
    sources: dict[str, str],
    document: Mapping[str, Any],
    source: str,
) -> None:
    for name, value in document.items():
        values[name] = value
        sources[name] = source


def _optional_path(value: Path | None) -> str | None:
    return str(value) if value is not None else None


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


def _coerce_mcp_server_configs(value: Any) -> tuple[McpServerConfig, ...]:
    from agentbus.mcp.models import McpServerConfig

    if not isinstance(value, (list, tuple)):
        raise ValueError("mcp_server_configs must be a list of server objects")
    servers: list[McpServerConfig] = []
    for index, raw in enumerate(value):
        if isinstance(raw, McpServerConfig):
            servers.append(raw)
            continue
        if not isinstance(raw, dict):
            raise ValueError(
                f"MCP server configuration {index} must be an object"
            )
        try:
            servers.append(McpServerConfig.model_validate(raw))
        except ValueError as exc:
            locations = sorted(
                {
                    ".".join(str(part) for part in error.get("loc", ()))
                    for error in getattr(exc, "errors", lambda: [])()
                }
            )
            location = ", ".join(item for item in locations if item)
            suffix = f" ({location})" if location else ""
            raise ValueError(
                f"Invalid MCP server configuration {index}{suffix}"
            ) from None
    return tuple(servers)


def _coerce_tool_resource_budget(value: Any) -> ToolResourceBudget:
    from agentbus.tools.protocol import ToolResourceBudget

    if isinstance(value, ToolResourceBudget):
        return value
    if not isinstance(value, dict):
        raise ValueError("tool_resource_budget must be an object")
    try:
        return ToolResourceBudget.model_validate(value)
    except ValueError as exc:
        locations = sorted(
            {
                ".".join(str(part) for part in error.get("loc", ()))
                for error in getattr(exc, "errors", lambda: [])()
            }
        )
        location = ", ".join(item for item in locations if item)
        suffix = f" ({location})" if location else ""
        raise ValueError(
            f"Invalid tool resource budget configuration{suffix}"
        ) from None
