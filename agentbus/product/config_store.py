from __future__ import annotations

import json
import os
import tomllib
import uuid
from dataclasses import dataclass, fields
from enum import StrEnum
from pathlib import Path
from typing import Any, Mapping

from agentbus.config import AgentBusConfig
from agentbus.configuration import (
    default_user_config_path,
    default_workspace_config_path,
    resolve_configuration,
)
from agentbus.security.redaction import is_sensitive_key


class ConfigScope(StrEnum):
    USER = "user"
    WORKSPACE = "workspace"


@dataclass(frozen=True)
class ConfigMutation:
    path: Path
    key: str
    previous: Any
    value: Any
    changed: bool


def config_target_path(
    scope: ConfigScope | str,
    *,
    workspace: str | Path = ".",
    environ: Mapping[str, str] | None = None,
) -> Path:
    selected = ConfigScope(scope)
    if selected == ConfigScope.USER:
        return default_user_config_path(environ)
    return default_workspace_config_path(workspace)


def parse_config_value(key: str, raw: str) -> Any:
    field_map = {item.name: item for item in fields(AgentBusConfig)}
    if key not in field_map:
        raise ValueError(f"Unsupported AgentBus configuration key: {key}")
    _reject_secret_key(key)
    default = getattr(AgentBusConfig(), key)
    text = raw.strip()
    if isinstance(default, bool):
        if text.lower() in {"true", "1", "yes", "on"}:
            return True
        if text.lower() in {"false", "0", "no", "off"}:
            return False
        raise ValueError(f"{key} must be true or false")
    if isinstance(default, int) and not isinstance(default, bool):
        try:
            return int(text)
        except ValueError as exc:
            raise ValueError(f"{key} must be an integer") from exc
    if isinstance(default, float):
        try:
            return float(text)
        except ValueError as exc:
            raise ValueError(f"{key} must be numeric") from exc
    if isinstance(default, (tuple, list, dict)) or key in {
        "mcp_server_configs",
        "tool_resource_budget",
    }:
        try:
            return json.loads(text)
        except json.JSONDecodeError as exc:
            raise ValueError(f"{key} must be valid JSON") from exc
    return raw


def set_config_value(path: str | Path, key: str, value: Any) -> ConfigMutation:
    target = Path(path).expanduser()
    _validate_key(key)
    document = read_config_document(target)
    previous = document.get(key)
    document[key] = value
    _atomic_write_validated(target, document)
    return ConfigMutation(
        path=target.resolve(),
        key=key,
        previous=previous,
        value=value,
        changed=previous != value,
    )


def unset_config_value(path: str | Path, key: str) -> ConfigMutation:
    target = Path(path).expanduser()
    _validate_key(key)
    document = read_config_document(target)
    sentinel = object()
    previous = document.pop(key, sentinel)
    changed = previous is not sentinel
    if changed:
        _atomic_write_validated(target, document)
    return ConfigMutation(
        path=target.resolve() if target.exists() else target.absolute(),
        key=key,
        previous=None if previous is sentinel else previous,
        value=None,
        changed=changed,
    )


def read_config_document(path: str | Path) -> dict[str, Any]:
    target = Path(path).expanduser()
    if not target.exists():
        return {}
    if target.is_symlink():
        raise ValueError("AgentBus refuses to edit a configuration symlink")
    if target.suffix.lower() == ".json":
        try:
            loaded = json.loads(target.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(f"Unable to read AgentBus JSON config: {target}") from exc
    elif target.suffix.lower() == ".toml":
        try:
            with target.open("rb") as handle:
                loaded = tomllib.load(handle)
        except (OSError, tomllib.TOMLDecodeError) as exc:
            raise ValueError(f"Unable to read AgentBus TOML config: {target}") from exc
    else:
        raise ValueError("AgentBus config files must use .toml or .json")
    if not isinstance(loaded, dict):
        raise ValueError("AgentBus config must contain an object/table")
    document = loaded.get("agentbus", loaded)
    if not isinstance(document, dict):
        raise ValueError("The 'agentbus' config section must be a table/object")
    return dict(document)


def write_config_document(
    path: str | Path,
    document: Mapping[str, Any],
) -> Path:
    target = Path(path).expanduser()
    for key in document:
        _validate_key(str(key))
    _atomic_write_validated(target, document)
    return target.resolve()


def ensure_safe_config_target(
    path: str | Path,
    *,
    workspace: str | Path | None = None,
) -> Path:
    target = Path(path).expanduser().absolute()
    if target.exists() and target.is_symlink():
        raise ValueError("AgentBus refuses to edit a configuration symlink")
    if workspace is None:
        return target
    root = Path(workspace).expanduser().resolve(strict=True)
    parent = target.parent
    parent.mkdir(parents=True, exist_ok=True)
    canonical_parent = parent.resolve(strict=True)
    if not canonical_parent.is_relative_to(root):
        raise ValueError("Workspace configuration directory resolves outside the workspace")
    return canonical_parent / target.name


def _atomic_write_validated(path: Path, document: Mapping[str, Any]) -> None:
    parent = path.parent
    parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and path.is_symlink():
        raise ValueError("AgentBus refuses to replace a configuration symlink")
    suffix = path.suffix.lower()
    if suffix == ".json":
        content = json.dumps({"agentbus": document}, indent=2, sort_keys=True) + "\n"
    elif suffix == ".toml":
        content = render_toml(document)
    else:
        raise ValueError("AgentBus config files must use .toml or .json")
    temporary = parent / f".{path.stem}.{uuid.uuid4().hex}{suffix}"
    try:
        temporary.write_text(content, encoding="utf-8", newline="\n")
        resolve_configuration(config_file=temporary, discover=False, environ={})
        with temporary.open("r+b") as handle:
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        if os.name != "nt":
            path.chmod(0o600)
    finally:
        if temporary.exists():
            temporary.unlink()


def render_toml(document: Mapping[str, Any]) -> str:
    lines: list[str] = []
    _emit_toml_table(lines, ("agentbus",), document, array=False)
    return "\n".join(lines).rstrip() + "\n"


def _emit_toml_table(
    lines: list[str],
    path: tuple[str, ...],
    document: Mapping[str, Any],
    *,
    array: bool,
) -> None:
    header = ".".join(_toml_key(part) for part in path)
    lines.append(f"[[{header}]]" if array else f"[{header}]")
    nested: list[tuple[str, Any]] = []
    for key in sorted(document):
        value = document[key]
        if isinstance(value, Mapping) or _is_table_array(value):
            nested.append((key, value))
        else:
            lines.append(f"{_toml_key(key)} = {_toml_value(value)}")
    for key, value in nested:
        lines.append("")
        child = (*path, key)
        if isinstance(value, Mapping):
            _emit_toml_table(lines, child, value, array=False)
        else:
            for index, item in enumerate(value):
                if index:
                    lines.append("")
                _emit_toml_table(lines, child, item, array=True)


def _is_table_array(value: Any) -> bool:
    return isinstance(value, (list, tuple)) and bool(value) and all(
        isinstance(item, Mapping) for item in value
    )


def _toml_key(value: str) -> str:
    if value.replace("_", "").replace("-", "").isalnum():
        return value
    return json.dumps(value, ensure_ascii=True)


def _toml_value(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return repr(value)
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=True)
    if isinstance(value, (list, tuple)):
        return "[" + ", ".join(_toml_value(item) for item in value) + "]"
    if isinstance(value, Mapping) and not value:
        return "{}"
    raise ValueError(f"Unsupported TOML configuration value: {type(value).__name__}")


def _validate_key(key: str) -> None:
    if key not in {item.name for item in fields(AgentBusConfig)}:
        raise ValueError(f"Unsupported AgentBus configuration key: {key}")
    _reject_secret_key(key)


def _reject_secret_key(key: str) -> None:
    if is_sensitive_key(key):
        raise ValueError(
            "AgentBus credentials must use the process environment or a secure store"
        )
