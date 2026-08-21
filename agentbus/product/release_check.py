from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any
from urllib.parse import unquote

from agentbus import __version__
from agentbus.execution.schema import SCHEMA_VERSION
from agentbus.intelligence.schema import LATEST_SCHEMA_VERSION
from agentbus.intelligence.version import INTELLIGENCE_SCHEMA_VERSION
from agentbus.product.compatibility import (
    PYTHON_COMPATIBILITY_RANGE,
    compatibility_manifest,
)
from agentbus.release_packaging import (
    audit_distributions,
    compare_distribution_sets,
)
from agentbus.release_security import audit_release_security
from agentbus.security.redaction import redact_text


class GateStatus(StrEnum):
    PASSED = "passed"
    FAILED = "failed"
    WARNING = "warning"
    SKIPPED = "skipped"


@dataclass(frozen=True)
class ReleaseGate:
    gate_id: str
    title: str
    status: GateStatus
    summary: str
    duration_seconds: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.gate_id,
            "title": self.title,
            "status": self.status.value,
            "summary": self.summary,
            "duration_seconds": round(self.duration_seconds, 3),
        }


@dataclass(frozen=True)
class ReleaseReadinessReport:
    mode: str
    version: str
    duration_seconds: float
    gates: tuple[ReleaseGate, ...]

    @property
    def ok(self) -> bool:
        return not any(gate.status == GateStatus.FAILED for gate in self.gates)

    def to_dict(self) -> dict[str, Any]:
        counts = {
            status.value: sum(gate.status == status for gate in self.gates)
            for status in GateStatus
        }
        return {
            "ok": self.ok,
            "mode": self.mode,
            "version": self.version,
            "duration_seconds": round(self.duration_seconds, 3),
            "counts": counts,
            "gates": [gate.to_dict() for gate in self.gates],
            "network_used": False,
            "published": False,
        }


@dataclass(frozen=True)
class CommandSpec:
    gate_id: str
    title: str
    command: tuple[str, ...]
    cwd: Path
    timeout_seconds: float


@dataclass(frozen=True)
class CommandOutcome:
    returncode: int
    stdout: str = ""
    stderr: str = ""
    duration_seconds: float = 0.0


CommandRunner = Callable[[CommandSpec], CommandOutcome]


def run_release_check(
    *,
    mode: str = "fast",
    root: str | Path = ".",
    runner: CommandRunner | None = None,
) -> ReleaseReadinessReport:
    if mode not in {"fast", "full"}:
        raise ValueError("Release-check mode must be 'fast' or 'full'.")
    repository = _repository_root(Path(root).expanduser().resolve())
    execute = runner or _run_command
    started = time.monotonic()
    gates = [
        _version_gate(repository),
        _schema_gate(),
        _release_files_gate(repository),
        _documentation_links_gate(repository),
        _security_gate(repository),
    ]
    gates.extend(_git_gates(repository, execute))
    with tempfile.TemporaryDirectory(prefix="agentbus-release-check-") as temporary:
        temporary_root = Path(temporary).resolve()
        for spec in release_check_commands(mode, repository, temporary_root):
            gates.append(_execute_gate(spec, execute))
        if mode == "full":
            gates.append(_package_gate(repository, temporary_root, execute))
    return ReleaseReadinessReport(
        mode=mode,
        version=__version__,
        duration_seconds=time.monotonic() - started,
        gates=tuple(gates),
    )


def release_check_commands(
    mode: str,
    root: Path,
    temporary_root: Path,
) -> tuple[CommandSpec, ...]:
    python = sys.executable
    extension = root / "extensions" / "vscode"
    node = "node"
    npm = "npm.cmd" if os.name == "nt" else "npm"
    commands = [
        CommandSpec(
            "python-compile",
            "Python bytecode compilation",
            (python, "-m", "compileall", "agentbus"),
            root,
            180,
        ),
        CommandSpec(
            "protocol-freshness",
            "Generated control protocol freshness",
            (node, "scripts/check-protocol.mjs"),
            extension,
            120,
        ),
        CommandSpec(
            "benchmark-smoke",
            "Offline startup benchmark",
            (
                python,
                "-m",
                "agentbus.cli",
                "benchmark",
                "startup",
                "--iterations",
                "1",
                "--json",
            ),
            root,
            120,
        ),
    ]
    if mode == "fast":
        return tuple(commands)
    commands.extend(
        (
            CommandSpec(
                "python-tests",
                "Complete Python test suite",
                (
                    python,
                    "-m",
                    "pytest",
                    "--basetemp",
                    str(temporary_root / "pytest"),
                ),
                root,
                1_200,
            ),
            CommandSpec(
                "control-acceptance",
                "Control-plane acceptance",
                (python, "-m", "agentbus.control.acceptance"),
                root,
                300,
            ),
            CommandSpec(
                "product-acceptance",
                "Clean-install product acceptance",
                (python, "-m", "agentbus.product_acceptance"),
                root,
                900,
            ),
            CommandSpec(
                "beta-acceptance",
                "Public beta acceptance",
                (python, "-m", "agentbus.beta_acceptance"),
                root,
                1_200,
            ),
            CommandSpec(
                "release-evaluation",
                "Offline release evaluation",
                (
                    python,
                    "-m",
                    "agentbus.eval",
                    "run",
                    "--suite",
                    "release-offline",
                    "--variant",
                    "durable-parallel-fake",
                ),
                root,
                600,
            ),
            CommandSpec(
                "intelligence-evaluation",
                "Repository-intelligence evaluation",
                (
                    python,
                    "-m",
                    "agentbus.eval",
                    "run",
                    "--suite",
                    "repository-intelligence",
                    "--variant",
                    "deterministic",
                ),
                root,
                600,
            ),
            CommandSpec(
                "reliability-soak",
                "Bounded deterministic soak",
                (
                    python,
                    "-m",
                    "agentbus.cli",
                    "soak",
                    "--duration",
                    "60",
                    "--runs",
                    "2",
                    "--parallelism",
                    "2",
                    "--json",
                ),
                root,
                180,
            ),
            CommandSpec(
                "benchmark-full",
                "Broad offline performance budget",
                (
                    python,
                    "-m",
                    "agentbus.cli",
                    "benchmark",
                    "all",
                    "--files",
                    "20",
                    "--iterations",
                    "1",
                    "--json",
                ),
                root,
                300,
            ),
            CommandSpec(
                "vscode-compile",
                "VS Code extension compilation",
                (npm, "run", "compile"),
                extension,
                300,
            ),
            CommandSpec(
                "vscode-lint",
                "VS Code extension lint",
                (npm, "run", "lint"),
                extension,
                300,
            ),
            CommandSpec(
                "vscode-tests",
                "VS Code extension unit tests",
                (npm, "test"),
                extension,
                300,
            ),
            CommandSpec(
                "vscode-electron",
                "Fresh-profile VS Code acceptance",
                (npm, "run", "test:product"),
                extension,
                600,
            ),
            CommandSpec(
                "vsix-package",
                "VSIX package build",
                (
                    node,
                    str(
                        extension
                        / "node_modules"
                        / "@vscode"
                        / "vsce"
                        / "vsce"
                    ),
                    "package",
                    "--no-dependencies",
                    "-o",
                    str(temporary_root / "agentbus-vscode.vsix"),
                ),
                extension,
                300,
            ),
            CommandSpec(
                "vsix-audit",
                "VSIX content audit",
                (
                    node,
                    "scripts/audit-vsix.mjs",
                    str(temporary_root / "agentbus-vscode.vsix"),
                ),
                extension,
                120,
            ),
        )
    )
    return tuple(commands)


def _version_gate(root: Path) -> ReleaseGate:
    started = time.monotonic()
    try:
        package = json.loads(
            (root / "extensions" / "vscode" / "package.json").read_text(
                encoding="utf-8"
            )
        )
        compatibility = package["agentbusCompatibility"]
        current = compatibility_manifest()
        valid = (
            package["version"] == "0.6.0-beta.1"
            and compatibility["python"] == PYTHON_COMPATIBILITY_RANGE
            and compatibility["controlProtocol"] == current.control_protocol
            and compatibility["stateSchema"] == current.state_schema
        )
    except (KeyError, OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
        return _gate_failure("version-consistency", "Version consistency", exc, started)
    return ReleaseGate(
        "version-consistency",
        "Version consistency",
        GateStatus.PASSED if valid else GateStatus.FAILED,
        "Python, extension, protocol, and schema compatibility agree."
        if valid
        else "Version or compatibility metadata is inconsistent.",
        time.monotonic() - started,
    )


def _schema_gate() -> ReleaseGate:
    current = compatibility_manifest()
    valid = (
        current.state_schema == SCHEMA_VERSION
        and current.intelligence_schema == INTELLIGENCE_SCHEMA_VERSION
        and LATEST_SCHEMA_VERSION >= INTELLIGENCE_SCHEMA_VERSION
    )
    return ReleaseGate(
        "migration-compatibility",
        "Migration compatibility",
        GateStatus.PASSED if valid else GateStatus.FAILED,
        "Published schemas and internal migration targets are compatible."
        if valid
        else "Published compatibility does not match migration targets.",
    )


def _release_files_gate(root: Path) -> ReleaseGate:
    required = (
        "CHANGELOG.md",
        "CONTRIBUTING.md",
        "LICENSE",
        "README.md",
        "RELEASE_CHECKLIST.md",
        "SECURITY.md",
        "docs/getting-started/install.md",
        "docs/getting-started/quickstart.md",
        "docs/reference/cli.md",
        "docs/troubleshooting/install.md",
    )
    missing = [path for path in required if not (root / path).is_file()]
    changelog = (
        (root / "CHANGELOG.md").read_text(encoding="utf-8", errors="replace")
        if (root / "CHANGELOG.md").is_file()
        else ""
    )
    if "0.6" not in changelog:
        missing.append("CHANGELOG.md v0.6 section")
    return ReleaseGate(
        "release-files",
        "Release documentation inventory",
        GateStatus.FAILED if missing else GateStatus.PASSED,
        "Missing release documentation: " + ", ".join(missing)
        if missing
        else "Required beta release and onboarding documents are present.",
    )


def _documentation_links_gate(root: Path) -> ReleaseGate:
    broken: list[str] = []
    for relative in _tracked_markdown(root):
        path = root / relative
        try:
            content = path.read_text(encoding="utf-8")
        except OSError:
            broken.append(relative.as_posix())
            continue
        for destination in re.findall(r"(?<!!)\[[^\]]*\]\(([^)]+)\)", content):
            target = destination.strip().split(maxsplit=1)[0].strip("<>")
            if not target or target.startswith(("#", "http://", "https://", "mailto:")):
                continue
            local = unquote(target.split("#", 1)[0])
            if local:
                resolved = (path.parent / local).resolve()
                if not resolved.is_relative_to(root) or not resolved.exists():
                    broken.append(f"{relative.as_posix()} -> {local}")
    return ReleaseGate(
        "documentation-links",
        "Local documentation links",
        GateStatus.FAILED if broken else GateStatus.PASSED,
        "Broken local links: " + ", ".join(broken[:20])
        if broken
        else "All tracked local Markdown links resolve.",
    )


def _security_gate(root: Path) -> ReleaseGate:
    started = time.monotonic()
    try:
        report = audit_release_security(root, include_validation=True)
    except (OSError, RuntimeError, ValueError) as exc:
        return _gate_failure("security-audit", "Release security audit", exc, started)
    defensive = report.defensive_validation
    defensive_summary = (
        f"; defensive_validation={defensive.classification.value}; "
        f"limitations={len(defensive.unresolved_limitations)}"
        if defensive is not None
        else ""
    )
    return ReleaseGate(
        "security-audit",
        "Release security audit",
        GateStatus.PASSED if report.ok else GateStatus.FAILED,
        f"Scanned {report.scanned_files} tracked files and "
        f"{len(report.scanned_artifacts)} artifact(s); "
        f"findings={len(report.findings)}{defensive_summary}.",
        time.monotonic() - started,
    )


def _git_gates(root: Path, runner: CommandRunner) -> list[ReleaseGate]:
    tracked_spec = CommandSpec(
        "git-cleanliness",
        "Tracked Git cleanliness",
        ("git", "status", "--porcelain=v1", "--untracked-files=no"),
        root,
        30,
    )
    tracked = runner(tracked_spec)
    if tracked.returncode != 0:
        clean = _outcome_gate(tracked_spec, tracked)
    else:
        dirty = bool(tracked.stdout.strip())
        clean = ReleaseGate(
            tracked_spec.gate_id,
            tracked_spec.title,
            GateStatus.FAILED if dirty else GateStatus.PASSED,
            "Tracked files contain uncommitted changes."
            if dirty
            else "Tracked files are clean.",
            tracked.duration_seconds,
        )
    untracked_spec = CommandSpec(
        "git-untracked",
        "Untracked file inventory",
        ("git", "status", "--porcelain=v1", "--untracked-files=normal"),
        root,
        30,
    )
    untracked = runner(untracked_spec)
    if untracked.returncode != 0:
        inventory = _outcome_gate(untracked_spec, untracked)
    else:
        count = sum(line.startswith("?? ") for line in untracked.stdout.splitlines())
        inventory = ReleaseGate(
            untracked_spec.gate_id,
            untracked_spec.title,
            GateStatus.WARNING if count else GateStatus.PASSED,
            f"Found {count} untracked path(s); they are not release inputs."
            if count
            else "No untracked paths were reported.",
            untracked.duration_seconds,
        )
    return [clean, inventory]


def _package_gate(
    root: Path,
    temporary_root: Path,
    runner: CommandRunner,
) -> ReleaseGate:
    started = time.monotonic()
    build_directories = (temporary_root / "build-one", temporary_root / "build-two")
    for directory in build_directories:
        directory.mkdir()
        spec = CommandSpec(
            f"package-build-{directory.name}",
            "Reproducible Python package build",
            (
                sys.executable,
                "-m",
                "build",
                "--no-isolation",
                "--outdir",
                str(directory),
            ),
            root,
            600,
        )
        outcome = runner(spec)
        if outcome.returncode != 0:
            return ReleaseGate(
                "package-audit",
                "Python package reproducibility and audit",
                GateStatus.FAILED,
                "A no-isolation package build failed: " + _safe_failure(outcome),
                time.monotonic() - started,
            )
    first = _distribution_paths(build_directories[0])
    second = _distribution_paths(build_directories[1])
    try:
        audit = audit_distributions(first, root=root)
        reproducibility = compare_distribution_sets(first, second)
    except (OSError, RuntimeError, ValueError) as exc:
        return _gate_failure(
            "package-audit",
            "Python package reproducibility and audit",
            exc,
            started,
        )
    passed = audit.ok and not reproducibility
    return ReleaseGate(
        "package-audit",
        "Python package reproducibility and audit",
        GateStatus.PASSED if passed else GateStatus.FAILED,
        f"Audited {len(audit.artifacts)} artifacts; "
        f"content_findings={len(audit.findings)}; "
        f"reproducibility_findings={len(reproducibility)}.",
        time.monotonic() - started,
    )


def _execute_gate(spec: CommandSpec, runner: CommandRunner) -> ReleaseGate:
    return _outcome_gate(spec, runner(spec))


def _outcome_gate(spec: CommandSpec, outcome: CommandOutcome) -> ReleaseGate:
    return ReleaseGate(
        spec.gate_id,
        spec.title,
        GateStatus.PASSED if outcome.returncode == 0 else GateStatus.FAILED,
        "Completed successfully."
        if outcome.returncode == 0
        else "Command failed safely: " + _safe_failure(outcome),
        outcome.duration_seconds,
    )


def _run_command(spec: CommandSpec) -> CommandOutcome:
    started = time.monotonic()
    try:
        result = subprocess.run(
            list(spec.command),
            cwd=spec.cwd,
            env=_offline_environment(),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=spec.timeout_seconds,
            shell=False,
            check=False,
        )
        return CommandOutcome(
            returncode=result.returncode,
            stdout=result.stdout[-20_000:],
            stderr=result.stderr[-20_000:],
            duration_seconds=time.monotonic() - started,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return CommandOutcome(
            returncode=124 if isinstance(exc, subprocess.TimeoutExpired) else 127,
            stderr=type(exc).__name__,
            duration_seconds=time.monotonic() - started,
        )


def _offline_environment() -> dict[str, str]:
    blocked = ("CREDENTIAL", "KEY", "PASSWORD", "SECRET", "TOKEN")
    environment = {
        key: value
        for key, value in os.environ.items()
        if key.startswith("GIT_CONFIG_KEY_")
        or not any(marker in key.upper() for marker in blocked)
    }
    environment.update(
        {
            "AGENTBUS_PROVIDER": "deterministic",
            "AGENTBUS_RELEASE_CHECK": "1",
            "ALL_PROXY": "http://127.0.0.1:9",
            "HTTP_PROXY": "http://127.0.0.1:9",
            "HTTPS_PROXY": "http://127.0.0.1:9",
            "NO_PROXY": "127.0.0.1,localhost,::1",
            "PIP_NO_INDEX": "1",
            "npm_config_offline": "true",
        }
    )
    return environment


def _repository_root(root: Path) -> Path:
    result = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        cwd=root,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
        shell=False,
        check=False,
    )
    if result.returncode != 0:
        raise ValueError("Release-check root must be a Git repository.")
    discovered = Path(result.stdout.strip()).resolve()
    if os.path.normcase(str(discovered)) != os.path.normcase(str(root)):
        raise ValueError("Release-check root must equal the Git top-level directory.")
    return discovered


def _tracked_markdown(root: Path) -> tuple[Path, ...]:
    result = subprocess.run(
        ["git", "ls-files", "-z", "*.md"],
        cwd=root,
        capture_output=True,
        timeout=30,
        shell=False,
        check=False,
    )
    if result.returncode != 0:
        return ()
    return tuple(
        Path(os.fsdecode(value))
        for value in result.stdout.split(b"\0")
        if value
    )


def _distribution_paths(directory: Path) -> tuple[Path, ...]:
    return tuple(
        [
            *sorted(directory.glob(f"agentbus-{__version__}*.whl")),
            *sorted(directory.glob(f"agentbus-{__version__}*.tar.gz")),
        ]
    )


def _safe_failure(outcome: CommandOutcome) -> str:
    detail = outcome.stderr.strip() or outcome.stdout.strip() or "nonzero exit"
    return redact_text(detail, max_chars=500) or "nonzero exit"


def _gate_failure(
    gate_id: str,
    title: str,
    error: BaseException,
    started: float,
) -> ReleaseGate:
    detail = redact_text(str(error), max_chars=300) or type(error).__name__
    return ReleaseGate(
        gate_id,
        title,
        GateStatus.FAILED,
        f"Check failed safely: {detail}",
        time.monotonic() - started,
    )
