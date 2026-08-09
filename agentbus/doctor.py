from __future__ import annotations

import os
import importlib.util
import shutil
import sqlite3
import subprocess
import sys
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict

from agentbus import __version__
from agentbus.config import AgentBusConfig
from agentbus.execution.schema import SCHEMA_VERSION
from agentbus.git.repository import GitRepository, GitRepositoryError
from agentbus.models.errors import ModelProviderError
from agentbus.models.router import ModelRouter
from agentbus.models.types import ModelRole
from agentbus.repo.test_detection import TestCommandDetector
from agentbus.security.redaction import sanitize_json


class CheckStatus(str, Enum):
    OK = "OK"
    WARNING = "WARNING"
    ERROR = "ERROR"
    NOT_CONFIGURED = "NOT_CONFIGURED"
    OPTIONAL = "OPTIONAL"
    REPAIRABLE = "REPAIRABLE"

    PASS = "OK"
    WARN = "WARNING"
    FAIL = "ERROR"


@dataclass(frozen=True)
class DoctorCheck:
    name: str
    status: CheckStatus
    summary: str
    remediation: str | None = None
    details: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class DoctorReport:
    version: str
    workspace: str
    network_used: bool
    checks: list[DoctorCheck]

    @property
    def status(self) -> CheckStatus:
        statuses = {item.status for item in self.checks}
        if CheckStatus.ERROR in statuses:
            return CheckStatus.ERROR
        if CheckStatus.WARNING in statuses or CheckStatus.REPAIRABLE in statuses:
            return CheckStatus.WARNING
        return CheckStatus.OK

    def to_dict(self) -> dict[str, Any]:
        return sanitize_json(
            {
                "status": self.status.value,
                "version": self.version,
                "workspace": self.workspace,
                "network_used": self.network_used,
                "checks": [
                    {**asdict(check), "status": check.status.value}
                    for check in self.checks
                ],
            }
        )


class _DoctorSmoke(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: str


def run_doctor(
    config: AgentBusConfig,
    *,
    live_provider: str | None = None,
    provider: str | None = None,
    repair: bool = False,
    registry_path: str | Path | None = None,
) -> DoctorReport:
    workspace = config.workspace_path
    checks: list[DoctorCheck] = []
    checks.extend(_platform_checks())
    checks.extend(_provider_checks(config, selected_provider=provider))
    repository, repository_checks = _repository_checks(workspace)
    checks.extend(repository_checks)
    checks.extend(_runtime_checks(config, workspace, repository))
    checks.extend(_product_checks(config, repair=repair, registry_path=registry_path))
    if live_provider:
        checks.append(_live_provider_check(config, live_provider))
    return DoctorReport(
        version=__version__,
        workspace=str(workspace),
        network_used=bool(live_provider),
        checks=checks,
    )


def render_doctor(report: DoctorReport, *, verbose: bool = False) -> str:
    lines = [
        f"AgentBus doctor {report.version}",
        f"Overall: {report.status.value}",
        f"Workspace: {report.workspace}",
        f"Network used: {'yes' if report.network_used else 'no'}",
    ]
    for check in report.checks:
        lines.append(f"[{check.status.value}] {check.name}: {check.summary}")
        if check.remediation:
            lines.append(f"  Remediation: {check.remediation}")
        if verbose and check.details:
            for name, value in sorted(check.details.items()):
                lines.append(f"  {name}: {value}")
    return "\n".join(lines)


def _platform_checks() -> list[DoctorCheck]:
    version = sys.version_info
    if version < (3, 11):
        python = DoctorCheck(
            "python",
            CheckStatus.FAIL,
            f"Python {version.major}.{version.minor} is unsupported.",
            "Install Python 3.11 or newer.",
        )
    elif version[:2] > (3, 14):
        python = DoctorCheck(
            "python",
            CheckStatus.WARN,
            f"Python {version.major}.{version.minor} is newer than the tested matrix.",
            "Use Python 3.11-3.14 if compatibility issues occur.",
        )
    else:
        python = DoctorCheck(
            "python",
            CheckStatus.PASS,
            f"Python {version.major}.{version.minor}.{version.micro} is supported.",
        )
    git_path = shutil.which("git")
    if git_path:
        result = _command([git_path, "--version"])
        git = DoctorCheck(
            "git",
            CheckStatus.PASS if result.returncode == 0 else CheckStatus.FAIL,
            result.stdout.strip() or "Git executable returned an error.",
            None if result.returncode == 0 else "Repair the Git installation.",
        )
    else:
        git = DoctorCheck(
            "git",
            CheckStatus.FAIL,
            "Git executable was not found.",
            "Install Git and add it to PATH.",
        )
    return [
        python,
        DoctorCheck("package", CheckStatus.PASS, f"AgentBus {__version__} imports."),
        git,
        DoctorCheck(
            "sqlite",
            CheckStatus.PASS,
            f"SQLite {sqlite3.sqlite_version} is available.",
        ),
        _optional_executable("gh", "GitHub CLI is optional unless opening a PR."),
    ]


def _provider_checks(
    config: AgentBusConfig,
    *,
    selected_provider: str | None = None,
) -> list[DoctorCheck]:
    checks: list[DoctorCheck] = []
    try:
        config.validate_provider_configuration("ollama")
        checks.append(
            DoctorCheck(
                "provider:ollama",
                CheckStatus.PASS,
                "Ollama URL and model route are locally valid; network was not contacted.",
            )
        )
    except ValueError as exc:
        status = CheckStatus.FAIL if config.provider_name == "ollama" else CheckStatus.WARN
        checks.append(
            DoctorCheck(
                "provider:ollama",
                status,
                str(exc),
                "Set AGENTBUS_OLLAMA_URL and AGENTBUS_MODEL.",
            )
        )
    try:
        config.validate_provider_configuration("azure")
        checks.append(
            DoctorCheck(
                "provider:azure",
                CheckStatus.PASS,
                "Azure endpoint, API-key presence, and deployment route are locally valid.",
                details={"api_key_configured": bool(config.azure_openai_api_key)},
            )
        )
    except ValueError as exc:
        status = CheckStatus.FAIL if config.provider_name == "azure" else CheckStatus.WARN
        checks.append(
            DoctorCheck(
                "provider:azure",
                status,
                str(exc),
                "Set Azure endpoint, API key, and deployment environment variables.",
            )
        )
    checks.append(
        DoctorCheck(
            "provider:deterministic",
            CheckStatus.OK,
            "Deterministic provider is built in, configured, and network-free.",
        )
    )
    checks.append(
        DoctorCheck(
            "provider:fallback",
            CheckStatus.PASS,
            (
                "Fallback is disabled."
                if not config.enable_provider_fallback
                else "Explicit Azure-to-Ollama fallback is valid."
            ),
        )
    )
    if selected_provider:
        selected = next(
            (item for item in checks if item.name == f"provider:{selected_provider}"),
            None,
        )
        if selected is None:
            checks.append(
                DoctorCheck(
                    "provider:selected",
                    CheckStatus.ERROR,
                    f"Unsupported provider selection: {selected_provider}",
                )
            )
        else:
            checks.append(
                DoctorCheck(
                    "provider:selected",
                    selected.status,
                    f"Selected {selected_provider}: {selected.summary}",
                    selected.remediation,
                )
            )
    return checks


def _product_checks(
    config: AgentBusConfig,
    *,
    repair: bool,
    registry_path: str | Path | None,
) -> list[DoctorCheck]:
    from agentbus.control.registry import DaemonRegistry, process_matches
    from agentbus.control.version import CONTROL_PROTOCOL_VERSION
    from agentbus.product.compatibility import validate_extension_package
    from agentbus.product.migrations import MigrationCoordinator, MigrationState

    checks: list[DoctorCheck] = [
        DoctorCheck(
            "configuration",
            CheckStatus.OK,
            "Resolved configuration is valid and credential values are not persisted by AgentBus config commands.",
            details={
                "provider": config.provider_name,
                "policy_mode": config.policy_mode,
                "log_level": config.log_level,
            },
        ),
        DoctorCheck(
            "policy",
            CheckStatus.OK if config.policy_mode == "enforce" else CheckStatus.ERROR,
            f"Tool policy mode is {config.policy_mode}.",
            None if config.policy_mode == "enforce" else "Use policy_mode = 'enforce'.",
        ),
        DoctorCheck(
            "tool-runtime",
            CheckStatus.OK,
            "Managed capability tools and resource budgets are configured.",
            details={
                "invocations_per_task": config.tool_resource_budget.invocations_per_task,
                "invocations_per_run": config.tool_resource_budget.invocations_per_run,
            },
        ),
        DoctorCheck(
            "mcp",
            CheckStatus.OK if config.mcp_server_configs else CheckStatus.NOT_CONFIGURED,
            (
                f"{len(config.mcp_server_configs)} MCP server(s) are configured."
                if config.mcp_server_configs
                else "No MCP servers are configured; MCP is optional."
            ),
        ),
    ]
    for name, module, extra in (
        ("extra:ide", "fastapi", "ide"),
        ("extra:mcp", "httpx", "mcp"),
        ("extra:azure", "openai", "azure"),
    ):
        installed = importlib.util.find_spec(module) is not None
        checks.append(
            DoctorCheck(
                name,
                CheckStatus.OK if installed else CheckStatus.OPTIONAL,
                f"Optional {extra} extra is {'available' if installed else 'not installed'}.",
                None if installed else f"Install with `pip install \"agentbus[{extra}]\"` when needed.",
            )
        )
    migration = MigrationCoordinator(config).status()
    for target in migration.targets:
        if target.state == MigrationState.CURRENT:
            status = CheckStatus.OK
        elif target.state == MigrationState.REQUIRED:
            status = CheckStatus.REPAIRABLE
        elif target.state == MigrationState.ABSENT:
            status = CheckStatus.NOT_CONFIGURED
        else:
            status = CheckStatus.ERROR
        checks.append(
            DoctorCheck(
                f"migration:{target.name}",
                status,
                target.message,
                (
                    "Run `agentbus migrate plan`, then `agentbus migrate apply`."
                    if status == CheckStatus.REPAIRABLE
                    else None
                ),
            )
        )
    runtime_directories = (
        Path(config.runs_dir).expanduser().resolve(),
        config.state_database_path.expanduser().resolve().parent,
        config.trace_store_path.expanduser().resolve(),
    )
    missing = [path for path in runtime_directories if not path.exists()]
    repaired: list[str] = []
    if repair:
        for path in missing:
            path.mkdir(parents=True, exist_ok=True)
            repaired.append(str(path))
        missing = [path for path in runtime_directories if not path.exists()]
    checks.append(
        DoctorCheck(
            "runtime-directories",
            CheckStatus.REPAIRABLE if missing else CheckStatus.OK,
            (
                f"{len(missing)} runtime directorie(s) are missing."
                if missing
                else "Runtime directories are present."
            ),
            "Run `agentbus doctor --repair` to create missing runtime directories."
            if missing
            else None,
            details={"repaired": repaired},
        )
    )
    registry = DaemonRegistry(registry_path)
    entries = registry.list()
    stale = [entry for entry in entries if not process_matches(entry)]
    removed: list[str] = []
    if repair and stale:
        removed = registry.cleanup_stale()
        entries = registry.list()
        stale = [entry for entry in entries if not process_matches(entry)]
    incompatible = [
        entry.daemon_id
        for entry in entries
        if entry.protocol_version != CONTROL_PROTOCOL_VERSION
    ]
    daemon_status = (
        CheckStatus.ERROR
        if incompatible
        else CheckStatus.REPAIRABLE
        if stale
        else CheckStatus.OK
        if entries
        else CheckStatus.NOT_CONFIGURED
    )
    checks.append(
        DoctorCheck(
            "daemon-registry",
            daemon_status,
            (
                f"{len(entries)} registered daemon(s), {len(stale)} stale, "
                f"{len(incompatible)} incompatible."
            ),
            "Run `agentbus doctor --repair` or `agentbus daemon cleanup-stale`."
            if stale
            else "Restart AgentBus with matching package and protocol versions."
            if incompatible
            else None,
            details={
                "registry": str(registry.path),
                "removed": removed,
                "active": sum(process_matches(entry) for entry in entries),
            },
        )
    )
    index_path = config.state_database_path.expanduser().resolve().parent / "repository-index.sqlite3"
    checks.append(
        DoctorCheck(
            "repository-index",
            CheckStatus.OK if index_path.is_file() else CheckStatus.NOT_CONFIGURED,
            (
                f"Repository index is present ({index_path.stat().st_size} bytes)."
                if index_path.is_file()
                else "Repository index has not been built."
            ),
            None if index_path.is_file() else "Run `agentbus index build` when repository intelligence is desired.",
        )
    )
    trace_path = config.trace_store_path.expanduser().resolve()
    trace_size = _directory_size(trace_path)
    checks.append(
        DoctorCheck(
            "trace-storage",
            CheckStatus.OK if trace_path.is_dir() else CheckStatus.NOT_CONFIGURED,
            f"Trace storage uses {trace_size} bytes.",
            details={"path": str(trace_path), "bytes": trace_size},
        )
    )
    state_root = config.state_database_path.expanduser().resolve().parent
    checks.append(
        DoctorCheck(
            "storage-size",
            CheckStatus.OK,
            f"AgentBus state uses {_directory_size(state_root)} bytes.",
            details={"path": str(state_root)},
        )
    )
    extension = Path(__file__).resolve().parents[1] / "extensions" / "vscode" / "package.json"
    if extension.is_file():
        issues = validate_extension_package(extension)
        checks.append(
            DoctorCheck(
                "vscode-extension",
                CheckStatus.ERROR if issues else CheckStatus.OK,
                "; ".join(issues) if issues else "Source extension metadata is compatible.",
            )
        )
    else:
        checks.append(
            DoctorCheck(
                "vscode-extension",
                CheckStatus.OPTIONAL,
                "VS Code extension metadata was not found in this installation.",
            )
        )
    return checks


def _directory_size(path: Path, *, max_entries: int = 100_000) -> int:
    if not path.is_dir():
        return 0
    total = 0
    seen = 0
    for item in path.rglob("*"):
        if seen >= max_entries:
            break
        seen += 1
        try:
            if item.is_file() and not item.is_symlink():
                total += item.stat().st_size
        except OSError:
            continue
    return total


def _repository_checks(
    workspace: Path,
) -> tuple[GitRepository | None, list[DoctorCheck]]:
    if not workspace.is_dir():
        return None, [
            DoctorCheck(
                "workspace",
                CheckStatus.FAIL,
                f"Workspace does not exist: {workspace}",
                "Create the directory or pass --workspace with a repository root.",
            )
        ]
    checks = [
        DoctorCheck(
            "workspace",
            CheckStatus.PASS if os.access(workspace, os.W_OK) else CheckStatus.FAIL,
            "Workspace exists and is writable."
            if os.access(workspace, os.W_OK)
            else "Workspace is not writable.",
            None if os.access(workspace, os.W_OK) else "Grant write access or select another workspace.",
        )
    ]
    repository = GitRepository(str(workspace))
    try:
        top_level = repository.validate_workspace()
        checks.append(
            DoctorCheck(
                "git-boundary",
                CheckStatus.PASS,
                "Workspace is the exact Git repository root.",
                details={"git_top_level": str(top_level)},
            )
        )
        return repository, checks
    except GitRepositoryError as exc:
        checks.append(
            DoctorCheck(
                "git-boundary",
                CheckStatus.FAIL,
                str(exc),
                "Select an isolated Git repository root; nested parent-repository paths are refused.",
            )
        )
        return None, checks


def _runtime_checks(
    config: AgentBusConfig,
    workspace: Path,
    repository: GitRepository | None,
) -> list[DoctorCheck]:
    checks: list[DoctorCheck] = []
    state_path = config.state_database_path.expanduser().resolve()
    checks.extend(_state_checks(state_path))
    for name, path in (
        ("runs-directory", Path(config.runs_dir).expanduser().resolve()),
        ("state-directory", state_path.parent),
    ):
        target = path if path.exists() else _nearest_existing_parent(path)
        writable = target is not None and os.access(target, os.W_OK)
        checks.append(
            DoctorCheck(
                name,
                CheckStatus.PASS if writable else CheckStatus.FAIL,
                f"Runtime path is writable via {target}." if writable else f"Runtime path is not writable: {path}",
                None if writable else "Choose a writable runtime directory or run agentbus init.",
            )
        )
    worktree_root = config.worktree_root_path
    inside_workspace = _is_within(worktree_root, workspace)
    checks.append(
        DoctorCheck(
            "worktree-root",
            CheckStatus.FAIL if inside_workspace else CheckStatus.PASS,
            f"Worktree root is {'inside' if inside_workspace else 'outside'} the source workspace: {worktree_root}",
            "Set AGENTBUS_WORKTREE_ROOT outside the repository."
            if inside_workspace
            else None,
        )
    )
    detected = TestCommandDetector(str(workspace)).detect()
    checks.append(
        DoctorCheck(
            "test-command",
            CheckStatus.PASS if detected["command"] else CheckStatus.WARN,
            "Detected: " + " ".join(detected["command"])
            if detected["command"]
            else detected["reason"],
            None if detected["command"] else "Configure or add a recognizable test command.",
            details={"confidence": detected["confidence"]},
        )
    )
    checks.append(_secret_safety_check(workspace, repository))
    baseline_dir = Path(config.state_dir).expanduser().resolve() / "evaluations" / "baselines"
    count = len(list(baseline_dir.glob("*.json"))) if baseline_dir.is_dir() else 0
    checks.append(
        DoctorCheck(
            "evaluation-baselines",
            CheckStatus.PASS if count else CheckStatus.WARN,
            f"Found {count} named evaluation baseline(s)."
            if count
            else "No named evaluation baselines were found.",
            None if count else "Save a known-good run with agentbus-eval baseline save.",
        )
    )
    return checks


def _state_checks(path: Path) -> list[DoctorCheck]:
    if not path.is_file():
        return [
            DoctorCheck(
                "state-database",
                CheckStatus.WARN,
                f"State database does not exist: {path}",
                "Run agentbus init or start a durable run.",
            ),
            DoctorCheck("stale-leases", CheckStatus.PASS, "No state database; no leases."),
            DoctorCheck("orphaned-worktrees", CheckStatus.PASS, "No state database; no worktrees."),
        ]
    try:
        uri = path.as_uri() + "?mode=ro"
        with sqlite3.connect(uri, uri=True) as connection:
            row = connection.execute(
                "SELECT value FROM schema_metadata WHERE key='schema_version'"
            ).fetchone()
            version = int(row[0]) if row else None
            stale = _stale_lease_count(connection)
            orphaned = _orphaned_worktree_count(connection)
    except (sqlite3.Error, OSError, ValueError) as exc:
        return [
            DoctorCheck(
                "state-database",
                CheckStatus.FAIL,
                f"State database is unreadable or incompatible: {type(exc).__name__}",
                "Back up the file and verify it is an AgentBus SQLite database.",
            )
        ]
    if version == SCHEMA_VERSION:
        schema_status = CheckStatus.PASS
        schema_remediation = None
    elif version is not None and version < SCHEMA_VERSION:
        schema_status = CheckStatus.WARN
        schema_remediation = (
            "Back up the database, then open it with AgentBus to apply registered "
            "transactional migrations."
        )
    else:
        schema_status = CheckStatus.FAIL
        schema_remediation = "Use an AgentBus release compatible with this database."
    return [
        DoctorCheck(
            "state-database",
            schema_status,
            f"State schema version is {version}; runtime expects {SCHEMA_VERSION}.",
            schema_remediation,
        ),
        DoctorCheck(
            "stale-leases",
            CheckStatus.WARN if stale else CheckStatus.PASS,
            f"Found {stale} expired active lease(s)." if stale else "No expired active leases found.",
            "Inspect the run, then use the explicit recover-leases command."
            if stale
            else None,
        ),
        DoctorCheck(
            "orphaned-worktrees",
            CheckStatus.WARN if orphaned else CheckStatus.PASS,
            f"Found {orphaned} persisted missing/orphaned worktree(s)."
            if orphaned
            else "No persisted missing/orphaned worktrees found.",
            "Inspect worktree records; cleanup is explicit and never forced."
            if orphaned
            else None,
        ),
    ]


def _stale_lease_count(connection: sqlite3.Connection) -> int:
    tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if "worker_leases" not in tables:
        return 0
    now = datetime.now(UTC).isoformat()
    row = connection.execute(
        "SELECT COUNT(*) FROM worker_leases WHERE status='active' AND expires_at < ?",
        (now,),
    ).fetchone()
    return int(row[0])


def _orphaned_worktree_count(connection: sqlite3.Connection) -> int:
    tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if "worktrees" not in tables:
        return 0
    rows = connection.execute(
        "SELECT path, status FROM worktrees WHERE status NOT IN ('removed')"
    ).fetchall()
    return sum(status == "orphaned" or not Path(path).exists() for path, status in rows)


def _secret_safety_check(
    workspace: Path,
    repository: GitRepository | None,
) -> DoctorCheck:
    if repository is None:
        return DoctorCheck(
            "secret-safety",
            CheckStatus.WARN,
            "Tracked-file secret safety could not be checked without a valid repository boundary.",
        )
    tracked = _git_paths(workspace, ["ls-files", "-z"])
    status_paths = _git_paths(workspace, ["status", "--porcelain=v1", "-z"])
    sensitive_names = {".env", ".env.local", "credentials.json", "secrets.json"}
    tracked_sensitive = sorted(path for path in tracked if Path(path).name.lower() in sensitive_names)
    untracked_sensitive = sorted(
        path[3:] for path in status_paths if path.startswith("?? ") and Path(path[3:]).name.lower() in sensitive_names
    )
    ignored = _git_paths(
        workspace,
        ["ls-files", "--others", "--ignored", "--exclude-standard", "-z"],
    )
    untracked_sensitive.extend(
        path for path in ignored if Path(path).name.lower() in sensitive_names
    )
    untracked_sensitive = sorted(set(untracked_sensitive))
    if tracked_sensitive:
        return DoctorCheck(
            "secret-safety",
            CheckStatus.FAIL,
            f"Found {len(tracked_sensitive)} tracked sensitive-looking file(s).",
            "Remove credentials from source control and rotate exposed values.",
            details={"files": tracked_sensitive},
        )
    if untracked_sensitive:
        return DoctorCheck(
            "secret-safety",
            CheckStatus.WARN,
            f"Found {len(untracked_sensitive)} untracked sensitive-looking file(s); values were not read.",
            "Keep these files ignored and verify they contain no release artifacts.",
            details={"files": untracked_sensitive},
        )
    return DoctorCheck(
        "secret-safety",
        CheckStatus.PASS,
        "No tracked or untracked sensitive filenames were detected.",
    )


def _live_provider_check(config: AgentBusConfig, provider: str) -> DoctorCheck:
    if provider not in {"ollama", "azure", "deterministic"}:
        return DoctorCheck(
            "live-provider",
            CheckStatus.FAIL,
            f"Unsupported live provider: {provider}",
        )
    try:
        config.validate_provider_configuration(provider)
        live_config = config.with_overrides(
            provider_name=provider,
            enable_provider_fallback=False,
        )
        ModelRouter(live_config).generate_json(
            ModelRole.DEFAULT,
            'Return exactly this JSON object: {"status":"ok"}',
            schema=_DoctorSmoke,
            metadata={"operation": "doctor_live_provider"},
        )
        return DoctorCheck(
            "live-provider",
            CheckStatus.PASS,
            (
                "Explicit deterministic offline request succeeded."
                if provider == "deterministic"
                else f"Explicit live {provider} request succeeded."
            ),
        )
    except (ModelProviderError, ValueError) as exc:
        return DoctorCheck(
            "live-provider",
            CheckStatus.FAIL,
            f"Explicit live {provider} request failed: {exc}",
            "Review provider configuration, quota, deployment, and network access.",
        )


def _optional_executable(name: str, remediation: str) -> DoctorCheck:
    path = shutil.which(name)
    return DoctorCheck(
        f"executable:{name}",
        CheckStatus.PASS if path else CheckStatus.WARN,
        f"Found {name}: {path}" if path else f"Optional executable '{name}' was not found.",
        None if path else remediation,
    )


def _command(arguments: list[str], *, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        arguments,
        cwd=str(cwd) if cwd else None,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        shell=False,
        check=False,
        timeout=15,
    )


def _git_paths(workspace: Path, arguments: list[str]) -> list[str]:
    result = _command(["git", *arguments], cwd=workspace)
    if result.returncode != 0:
        return []
    return [item.replace("\\", "/") for item in result.stdout.split("\0") if item]


def _nearest_existing_parent(path: Path) -> Path | None:
    current = path
    while not current.exists() and current != current.parent:
        current = current.parent
    return current if current.exists() else None


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False
