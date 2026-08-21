from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from agentbus.config import AgentBusConfig
from agentbus.git.repository import GitRepository
from agentbus.intelligence.service import RepositoryIntelligenceService
from agentbus.product.errors import (
    ProductErrorCategory,
    as_product_error,
)
from agentbus.runtime.orchestrator import MultiAgentOrchestrator, OrchestrationResult
from agentbus.runtime.verifier import Verifier
from agentbus.security.redaction import redact_text


_OWNER_FILE = ".agentbus-quickstart-owner.json"
_TEMP_PREFIX = "agentbus-quickstart-"
_TASK = "Create and verify the deterministic AgentBus calculator."
_EXPECTED_FILES = ("agentbus_result.py", "test_agentbus_result.py")


@dataclass(frozen=True)
class QuickstartStep:
    name: str
    status: str
    detail: str
    duration_ms: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "status": self.status,
            "detail": self.detail,
            "duration_ms": self.duration_ms,
        }


@dataclass(frozen=True)
class QuickstartResult:
    ok: bool
    provider: str
    workspace: Path | None
    kept_demo: bool
    cleaned: bool
    duration_seconds: float
    steps: tuple[QuickstartStep, ...]
    changed_files: tuple[str, ...]
    indexed_files: int
    planner_steps: int
    verifier_passed: bool
    reviewer_approved: bool
    report: str | None
    error: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "provider": self.provider,
            "workspace": str(self.workspace) if self.workspace else None,
            "kept_demo": self.kept_demo,
            "cleaned": self.cleaned,
            "duration_seconds": round(self.duration_seconds, 3),
            "steps": [step.to_dict() for step in self.steps],
            "changed_files": list(self.changed_files),
            "indexed_files": self.indexed_files,
            "planner_steps": self.planner_steps,
            "verifier_passed": self.verifier_passed,
            "reviewer_approved": self.reviewer_approved,
            "report": self.report,
            "error": self.error,
            "network_used": False,
        }


def run_quickstart(
    *,
    keep_demo: bool = False,
    temp_parent: str | Path | None = None,
) -> QuickstartResult:
    started = time.perf_counter()
    steps: list[QuickstartStep] = []
    container: Path | None = None
    workspace: Path | None = None
    owner_token: str | None = None
    allowed_parent: Path | None = None
    changed_files: tuple[str, ...] = ()
    indexed_files = 0
    planner_steps = 0
    verifier_passed = False
    reviewer_approved = False
    report: str | None = None
    error: dict[str, Any] | None = None
    succeeded = False
    cleaned = False

    try:
        _run_step(steps, "environment", _validate_environment)
        allowed_parent = _temporary_parent(temp_parent)
        owner_token = uuid.uuid4().hex
        container = _run_step(
            steps,
            "demo_repository",
            lambda: _create_repository(allowed_parent, owner_token),
            detail=lambda value: f"Created local Git demo at {value / 'repository'}.",
        )
        workspace = container / "repository"
        runtime = container / "runtime"
        index_report = _run_step(
            steps,
            "repository_index",
            lambda: _index_repository(workspace, runtime / "repository-index.sqlite3"),
            detail=lambda value: f"Indexed {value.indexed_count} repository files offline.",
        )
        indexed_files = index_report.indexed_count
        result = _run_step(
            steps,
            "managed_task",
            lambda: _run_task(workspace, runtime),
            detail=lambda value: value.final_summary,
        )
        planner_steps, changed_files, verifier_passed, reviewer_approved = (
            _validate_task_result(result, workspace)
        )
        _append_evidence_steps(
            steps,
            result,
            planner_steps=planner_steps,
            changed_files=changed_files,
        )
        report = result.final_summary
        steps.append(
            QuickstartStep(
                name="report",
                status="passed",
                detail="Built the final deterministic execution report.",
                duration_ms=0,
            )
        )
        succeeded = True
    except Exception as exc:
        product_error = as_product_error(
            exc,
            category=_failure_category(steps),
            message="AgentBus quickstart did not complete.",
            likely_cause="A required local quickstart step failed.",
            recommended_action=(
                "Run `agentbus doctor`, address the reported local issue, and retry "
                "`agentbus quickstart`."
            ),
            docs_topic="getting-started/quickstart",
        )
        error = product_error.to_dict()
    finally:
        if (
            container is not None
            and owner_token is not None
            and allowed_parent is not None
        ):
            if keep_demo:
                steps.append(
                    QuickstartStep(
                        name="cleanup",
                        status="skipped",
                        detail="Retained the demo because --keep-demo was requested.",
                        duration_ms=0,
                    )
                )
            else:
                cleanup_started = time.perf_counter()
                try:
                    _remove_owned_container(container, allowed_parent, owner_token)
                    cleaned = True
                    steps.append(
                        QuickstartStep(
                            name="cleanup",
                            status="passed",
                            detail="Removed the marker-owned temporary demo and runtime state.",
                            duration_ms=_elapsed_ms(cleanup_started),
                        )
                    )
                except Exception as exc:
                    succeeded = False
                    cleanup_error = as_product_error(
                        exc,
                        category=ProductErrorCategory.WORKSPACE_ERROR,
                        message="Quickstart completed but temporary cleanup was refused.",
                        likely_cause=(
                            "The ownership or containment checks no longer matched the "
                            "temporary directory."
                        ),
                        recommended_action=(
                            "Inspect the reported demo path. AgentBus intentionally left it "
                            "in place rather than deleting an uncertain target."
                        ),
                        docs_topic="getting-started/quickstart",
                    )
                    error = cleanup_error.to_dict()
                    steps.append(
                        QuickstartStep(
                            name="cleanup",
                            status="failed",
                            detail=cleanup_error.message,
                            duration_ms=_elapsed_ms(cleanup_started),
                        )
                    )

    return QuickstartResult(
        ok=succeeded,
        provider="deterministic",
        workspace=workspace,
        kept_demo=keep_demo,
        cleaned=cleaned,
        duration_seconds=time.perf_counter() - started,
        steps=tuple(steps),
        changed_files=changed_files,
        indexed_files=indexed_files,
        planner_steps=planner_steps,
        verifier_passed=verifier_passed,
        reviewer_approved=reviewer_approved,
        report=report,
        error=error,
    )


def _run_step(
    steps: list[QuickstartStep],
    name: str,
    action: Callable[[], Any],
    *,
    detail: Callable[[Any], str] | None = None,
) -> Any:
    started = time.perf_counter()
    try:
        value = action()
    except Exception as exc:
        steps.append(
            QuickstartStep(
                name=name,
                status="failed",
                detail=redact_text(str(exc), max_chars=500) or type(exc).__name__,
                duration_ms=_elapsed_ms(started),
            )
        )
        raise
    steps.append(
        QuickstartStep(
            name=name,
            status="passed",
            detail=(detail(value) if detail else str(value)),
            duration_ms=_elapsed_ms(started),
        )
    )
    return value


def _validate_environment() -> str:
    if sys.version_info < (3, 11):
        raise RuntimeError("AgentBus quickstart requires Python 3.11 or newer.")
    if shutil.which("git") is None:
        raise RuntimeError("Git is required for the AgentBus quickstart.")
    version = _git(["--version"], Path.cwd())
    return f"Python {sys.version_info.major}.{sys.version_info.minor}; {version}."


def _temporary_parent(value: str | Path | None) -> Path:
    parent = Path(value or tempfile.gettempdir()).expanduser().resolve()
    if not parent.is_dir():
        raise ValueError("Quickstart temporary parent must be an existing directory.")
    return parent


def _create_repository(parent: Path, owner_token: str) -> Path:
    container = Path(
        tempfile.mkdtemp(prefix=_TEMP_PREFIX, dir=str(parent))
    ).resolve()
    marker = {
        "schema": 1,
        "owner": "agentbus-quickstart",
        "token": owner_token,
        "root": str(container),
    }
    (container / _OWNER_FILE).write_text(
        json.dumps(marker, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    repository = container / "repository"
    runtime = container / "runtime"
    repository.mkdir()
    runtime.mkdir()
    (runtime / "empty-hooks").mkdir()
    (repository / ".gitignore").write_text(
        "__pycache__/\n*.py[cod]\n.pytest_cache/\n",
        encoding="utf-8",
        newline="\n",
    )
    (repository / "README.md").write_text(
        "# AgentBus Quickstart\n\nA temporary offline demonstration repository.\n",
        encoding="utf-8",
        newline="\n",
    )
    (repository / "QUICKSTART_TASK.md").write_text(
        f"# Task\n\n{_TASK}\n",
        encoding="utf-8",
        newline="\n",
    )
    (repository / "quickstart_context.py").write_text(
        '"""Small source file indexed before the managed task runs."""\n\n'
        'QUICKSTART_MODE = "offline"\n',
        encoding="utf-8",
        newline="\n",
    )
    _git(["init", "-q", "--initial-branch=main"], repository)
    _git(
        [
            "add",
            "--",
            ".gitignore",
            "README.md",
            "QUICKSTART_TASK.md",
            "quickstart_context.py",
        ],
        repository,
    )
    _git(
        [
            "-c",
            f"core.hooksPath={runtime / 'empty-hooks'}",
            "-c",
            "commit.gpgSign=false",
            "-c",
            "user.name=AgentBus Quickstart",
            "-c",
            "user.email=quickstart@agentbus.invalid",
            "commit",
            "-q",
            "-m",
            "chore: initialize AgentBus quickstart",
        ],
        repository,
    )
    return container


def _index_repository(workspace: Path, database: Path):
    service = RepositoryIntelligenceService(workspace, database)
    return service.build()


def _run_task(workspace: Path, runtime: Path) -> OrchestrationResult:
    config = AgentBusConfig(
        workspace_dir=str(workspace),
        runs_dir=str(runtime / "runs"),
        state_dir=str(runtime),
        state_db="state.db",
        provider_name="deterministic",
        fallback_provider_name="deterministic",
        enable_provider_fallback=False,
        deterministic_profile="python-calculator",
        model_max_retries=0,
        command_timeout_seconds=30,
        repository_intelligence=False,
        parallel_execution=False,
        keep_worktrees=False,
        worktree_root=str(runtime / "worktrees"),
    )
    verifier = Verifier(
        config=config,
        command=[sys.executable, "-B", "-m", "unittest", "discover", "-q"],
    )
    orchestrator = MultiAgentOrchestrator(config=config, verifier=verifier)
    return orchestrator.run(_TASK)


def _validate_task_result(
    result: OrchestrationResult,
    workspace: Path,
) -> tuple[int, tuple[str, ...], bool, bool]:
    planner_steps = len(result.plan.get("steps", []))
    if planner_steps < 1:
        raise RuntimeError("The deterministic planner did not return a task step.")
    changed_files = tuple(GitRepository(workspace=str(workspace)).changed_files())
    missing = sorted(set(_EXPECTED_FILES) - set(changed_files))
    if missing:
        raise RuntimeError(
            "The managed filesystem task did not create: " + ", ".join(missing)
        )
    verifier_passed = bool(result.verifier_result.get("passed"))
    if not verifier_passed:
        raise RuntimeError("The deterministic quickstart test verification failed.")
    reviewer_approved = bool(result.reviewer_result.get("approved"))
    if not reviewer_approved:
        raise RuntimeError("The deterministic reviewer rejected the quickstart task.")
    return planner_steps, changed_files, verifier_passed, reviewer_approved


def _append_evidence_steps(
    steps: list[QuickstartStep],
    result: OrchestrationResult,
    *,
    planner_steps: int,
    changed_files: tuple[str, ...],
) -> None:
    evidence = (
        ("planner", f"Planner produced {planner_steps} scoped task step(s)."),
        (
            "managed_filesystem",
            "Managed tools created " + ", ".join(_EXPECTED_FILES) + ".",
        ),
        (
            "tests",
            "Executed " + " ".join(result.verifier_result.get("command", [])) + ".",
        ),
        ("verification", "Verifier passed the generated unittest suite."),
        ("review", "Reviewer approved the verified local changes."),
        ("changes", "Scoped changes: " + ", ".join(changed_files) + "."),
    )
    steps.extend(
        QuickstartStep(name=name, status="passed", detail=detail, duration_ms=0)
        for name, detail in evidence
    )


def _remove_owned_container(
    container: Path,
    allowed_parent: Path,
    owner_token: str,
) -> None:
    resolved = container.resolve()
    parent = allowed_parent.resolve()
    if resolved.parent != parent or not resolved.name.startswith(_TEMP_PREFIX):
        raise RuntimeError("Quickstart cleanup target escaped its temporary parent.")
    marker_path = resolved / _OWNER_FILE
    if marker_path.is_symlink() or not marker_path.is_file():
        raise RuntimeError("Quickstart ownership marker is missing or unsafe.")
    marker = json.loads(marker_path.read_text(encoding="utf-8"))
    if marker != {
        "schema": 1,
        "owner": "agentbus-quickstart",
        "token": owner_token,
        "root": str(resolved),
    }:
        raise RuntimeError("Quickstart ownership marker does not match this process.")
    # Make only entries beneath the validated, marker-owned root removable.
    # POSIX traversal needs owner access on directories; Windows Git objects
    # can require the write bit on files.
    for directory, child_directories, files in os.walk(resolved, followlinks=False):
        directory_path = Path(directory)
        directory_path.chmod(
            directory_path.stat().st_mode
            | stat.S_IREAD
            | stat.S_IWRITE
            | stat.S_IEXEC
        )
        child_directories[:] = [
            name
            for name in child_directories
            if not (directory_path / name).is_symlink()
        ]
        for name in child_directories:
            target = directory_path / name
            target.chmod(
                target.stat().st_mode
                | stat.S_IREAD
                | stat.S_IWRITE
                | stat.S_IEXEC
            )
        for name in files:
            target = directory_path / name
            if not target.is_symlink():
                target.chmod(target.stat().st_mode | stat.S_IWRITE)
    shutil.rmtree(resolved)


def _git(arguments: list[str], cwd: Path) -> str:
    result = subprocess.run(
        ["git", *arguments],
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=30,
        shell=False,
        check=False,
    )
    if result.returncode != 0:
        detail = redact_text(result.stderr.strip(), max_chars=1_000) or "unknown error"
        raise RuntimeError(f"Git command failed: {detail}")
    return redact_text(result.stdout.strip(), max_chars=1_000) or "git available"


def _failure_category(steps: list[QuickstartStep]) -> ProductErrorCategory:
    failed = next(
        (step.name for step in reversed(steps) if step.status == "failed"),
        None,
    )
    if failed == "environment":
        return ProductErrorCategory.INSTALLATION_ERROR
    if failed == "demo_repository":
        return ProductErrorCategory.GIT_ERROR
    if failed == "repository_index":
        return ProductErrorCategory.INDEX_ERROR
    return ProductErrorCategory.INTERNAL_ERROR


def _elapsed_ms(started: float) -> int:
    return max(0, round((time.perf_counter() - started) * 1_000))
