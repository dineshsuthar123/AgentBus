from __future__ import annotations

import importlib.util
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from agentbus.config import SUPPORTED_PROVIDERS
from agentbus.execution.state_store import StateStore
from agentbus.product.compatibility import current_python_supported
from agentbus.product.config_store import (
    ConfigScope,
    config_target_path,
    ensure_safe_config_target,
    read_config_document,
    write_config_document,
)


@dataclass(frozen=True)
class SetupDetection:
    name: str
    available: bool
    detail: str
    optional: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "available": self.available,
            "detail": self.detail,
            "optional": self.optional,
        }


@dataclass(frozen=True)
class SetupResult:
    config_file: Path
    state_database: Path
    workspace: Path
    provider: str
    scope: ConfigScope
    detections: tuple[SetupDetection, ...]
    created: tuple[Path, ...]
    planned: tuple[Path, ...]
    dry_run: bool
    existing_configuration_preserved: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": True,
            "config_file": str(self.config_file),
            "state_database": str(self.state_database),
            "workspace": str(self.workspace),
            "provider": self.provider,
            "scope": self.scope.value,
            "detections": [item.to_dict() for item in self.detections],
            "created": [str(path) for path in self.created],
            "planned": [str(path) for path in self.planned],
            "dry_run": self.dry_run,
            "existing_configuration_preserved": self.existing_configuration_preserved,
            "network_used": False,
            "credentials_written": False,
        }


def detect_setup_environment(
    workspace: str | Path,
    *,
    environ: Mapping[str, str] | None = None,
) -> tuple[SetupDetection, ...]:
    root = Path(workspace).expanduser().resolve()
    environment = os.environ if environ is None else environ
    git = shutil.which("git")
    repository = False
    if git and root.is_dir():
        try:
            result = subprocess.run(
                [git, "rev-parse", "--show-toplevel"],
                cwd=root,
                check=False,
                capture_output=True,
                text=True,
                timeout=5,
                shell=False,
            )
            repository = result.returncode == 0
        except (OSError, subprocess.SubprocessError):
            repository = False
    azure_names = (
        "AZURE_OPENAI_ENDPOINT",
        "AZURE_OPENAI_API_KEY",
        "AZURE_OPENAI_DEFAULT_DEPLOYMENT",
    )
    azure_count = sum(bool(environment.get(name, "").strip()) for name in azure_names)
    detections = (
        SetupDetection(
            "python",
            current_python_supported(),
            f"Python {sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}",
        ),
        SetupDetection("git", bool(git), "Git found." if git else "Git not found."),
        SetupDetection(
            "repository",
            repository,
            "Git repository detected." if repository else "No Git repository detected.",
            optional=True,
        ),
        SetupDetection(
            "deterministic-provider",
            True,
            "Built in and network-free.",
        ),
        SetupDetection(
            "ollama",
            bool(shutil.which("ollama")),
            "Ollama executable found." if shutil.which("ollama") else "Ollama is optional and was not found.",
            optional=True,
        ),
        SetupDetection(
            "azure-configuration",
            azure_count == len(azure_names),
            f"{azure_count}/{len(azure_names)} required Azure settings are present.",
            optional=True,
        ),
        SetupDetection(
            "ide-extra",
            importlib.util.find_spec("fastapi") is not None,
            "IDE dependencies available." if importlib.util.find_spec("fastapi") else "Install agentbus[ide] for the control plane.",
            optional=True,
        ),
        SetupDetection(
            "mcp-extra",
            importlib.util.find_spec("httpx") is not None,
            "MCP HTTP support available." if importlib.util.find_spec("httpx") else "Install agentbus[mcp] for HTTP MCP clients.",
            optional=True,
        ),
        SetupDetection(
            "repository-intelligence",
            True,
            "Built-in multilingual index support is available.",
        ),
        SetupDetection(
            "vscode",
            bool(shutil.which("code")),
            "VS Code command found." if shutil.which("code") else "VS Code command not detected.",
            optional=True,
        ),
    )
    return detections


def run_setup(
    *,
    workspace: str | Path = ".",
    provider: str = "deterministic",
    scope: ConfigScope | str = ConfigScope.USER,
    durable: bool = True,
    repository_index: bool = True,
    enable_mcp: bool = False,
    keep_worktrees: bool = True,
    config_root: str | Path | None = None,
    dry_run: bool = False,
    force: bool = False,
    environ: Mapping[str, str] | None = None,
) -> SetupResult:
    if provider not in SUPPORTED_PROVIDERS:
        raise ValueError("Unsupported AgentBus provider: " + provider)
    selected_scope = ConfigScope(scope)
    workspace_path = Path(workspace).expanduser().resolve(strict=True)
    if not workspace_path.is_dir():
        raise ValueError("AgentBus setup workspace must be a directory")
    target = (
        Path(config_root).expanduser().resolve() / "config.toml"
        if config_root is not None
        else config_target_path(
            selected_scope,
            workspace=workspace_path,
            environ=environ,
        )
    )
    target = ensure_safe_config_target(
        target,
        workspace=workspace_path if selected_scope == ConfigScope.WORKSPACE and config_root is None else None,
    )
    root = target.parent
    state_database = root / "state.db"
    planned = (
        root,
        root / "runs",
        root / "evaluations",
        root / "logs",
        target,
        state_database,
    )
    detections = detect_setup_environment(workspace_path, environ=environ)
    if target.exists() and not force:
        read_config_document(target)
        return SetupResult(
            config_file=target.resolve(),
            state_database=state_database.resolve(),
            workspace=workspace_path,
            provider=provider,
            scope=selected_scope,
            detections=detections,
            created=(),
            planned=planned,
            dry_run=dry_run,
            existing_configuration_preserved=True,
        )
    if dry_run:
        return SetupResult(
            config_file=target.absolute(),
            state_database=state_database.absolute(),
            workspace=workspace_path,
            provider=provider,
            scope=selected_scope,
            detections=detections,
            created=(),
            planned=planned,
            dry_run=True,
            existing_configuration_preserved=False,
        )
    created: list[Path] = []
    for directory in planned[:4]:
        if not directory.exists():
            directory.mkdir(parents=True, exist_ok=True)
            created.append(directory.resolve())
    document = {
        "provider_name": provider,
        "workspace_dir": str(workspace_path),
        "runs_dir": str(root / "runs"),
        "state_dir": str(root),
        "state_db": "state.db",
        "durable_execution": durable,
        "repository_intelligence": repository_index,
        "keep_worktrees": keep_worktrees,
        "mcp_server_configs": [] if enable_mcp else [],
    }
    write_config_document(target, document)
    created.append(target.resolve())
    database_existed = state_database.exists()
    StateStore(state_database)
    if not database_existed:
        created.append(state_database.resolve())
    return SetupResult(
        config_file=target.resolve(),
        state_database=state_database.resolve(),
        workspace=workspace_path,
        provider=provider,
        scope=selected_scope,
        detections=detections,
        created=tuple(created),
        planned=planned,
        dry_run=False,
        existing_configuration_preserved=False,
    )
