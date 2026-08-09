from __future__ import annotations

import json
import os
import re
import secrets
import shutil
import site
import subprocess
import sys
import tempfile
import time
import venv
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any, Callable

from agentbus import __version__
from agentbus.security.redaction import redact_text


_TEMP_PREFIX = "agentbus-product-acceptance-"
_OWNER_FILE = ".agentbus-product-acceptance.json"
_PRODUCT_EXTRAS = ("ide", "mcp")


class AcceptanceKind(StrEnum):
    PRODUCT = "product"
    BETA = "beta"


@dataclass(frozen=True)
class AcceptanceStep:
    name: str
    status: str
    detail: str
    duration_seconds: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "status": self.status,
            "detail": self.detail,
            "duration_seconds": round(self.duration_seconds, 3),
        }


@dataclass(frozen=True)
class CleanInstallAcceptanceReport:
    kind: AcceptanceKind
    ok: bool
    version: str
    duration_seconds: float
    steps: tuple[AcceptanceStep, ...]
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind.value,
            "ok": self.ok,
            "version": self.version,
            "duration_seconds": round(self.duration_seconds, 3),
            "steps": [step.to_dict() for step in self.steps],
            "error": self.error,
            "fresh_virtual_environment": True,
            "wheel_install": True,
            "editable_install": False,
            "dependency_extras": list(_PRODUCT_EXTRAS),
            "repository_pythonpath_used": False,
            "provider": "deterministic",
            "provider_calls": 0,
            "network_used": False,
            "published": False,
        }


class AcceptanceCommandError(RuntimeError):
    pass


def acceptance_step_names(kind: AcceptanceKind | str) -> tuple[str, ...]:
    selected = AcceptanceKind(kind)
    common = (
        "package_build",
        "fresh_environment",
        "wheel_install",
        "installed_origin",
        "version",
        "doctor",
        "setup",
        "demo_repository",
        "daemon_start",
        "repository_index",
        "quickstart",
        "deterministic_task",
        "final_report",
        "offline_replay",
        "daemon_stop",
        "cleanup",
        "leak_check",
        "uninstall",
    )
    if selected == AcceptanceKind.PRODUCT:
        return common
    return common


def run_clean_install_acceptance(
    kind: AcceptanceKind | str = AcceptanceKind.PRODUCT,
    *,
    root: str | Path = ".",
) -> CleanInstallAcceptanceReport:
    selected = AcceptanceKind(kind)
    started = time.monotonic()
    steps: list[AcceptanceStep] = []
    runner: _CleanInstallRunner | None = None
    error: str | None = None
    try:
        repository = _repository_root(Path(root).expanduser().resolve())
        with tempfile.TemporaryDirectory(prefix=_TEMP_PREFIX) as temporary:
            runner = _CleanInstallRunner(
                selected,
                repository=repository,
                temporary_root=Path(temporary).resolve(),
                steps=steps,
            )
            try:
                runner.run()
            except Exception:
                runner.stop_daemon_after_failure()
                raise
    except Exception as exc:
        error = _safe_detail(exc)
    return CleanInstallAcceptanceReport(
        kind=selected,
        ok=error is None,
        version=__version__,
        duration_seconds=time.monotonic() - started,
        steps=tuple(steps),
        error=error,
    )


class _CleanInstallRunner:
    def __init__(
        self,
        kind: AcceptanceKind,
        *,
        repository: Path,
        temporary_root: Path,
        steps: list[AcceptanceStep],
    ) -> None:
        self.kind = kind
        self.repository = repository
        self.root = temporary_root
        self.steps = steps
        self.private_marker = secrets.token_urlsafe(32)
        self.environment = _offline_environment(
            temporary_root,
            private_marker=self.private_marker,
        )
        self.distributions = temporary_root / "distributions"
        self.environment_root = temporary_root / "venv"
        self.workspace = temporary_root / "workspace"
        self.demo = temporary_root / "demo-python"
        self.product_state = temporary_root / "product-state"
        self.config_file = self.product_state / "config.toml"
        self.registry = temporary_root / "daemon-registry.json"
        self.wheel: Path | None = None
        self.python: Path | None = None
        self.agentbus: Path | None = None
        self.daemon_id: str | None = None
        self.daemon_pid: int | None = None
        self.run_id: str | None = None
        self.root.joinpath(_OWNER_FILE).write_text(
            json.dumps({"owner": "agentbus", "kind": kind.value}) + "\n",
            encoding="utf-8",
        )

    def run(self) -> None:
        actions: dict[str, Callable[[], str]] = {
            "package_build": self._build_package,
            "fresh_environment": self._create_environment,
            "wheel_install": self._install_wheel,
            "installed_origin": self._verify_installed_origin,
            "version": self._version,
            "doctor": self._doctor,
            "setup": self._setup,
            "demo_repository": self._create_demo,
            "daemon_start": self._start_daemon,
            "repository_index": self._build_index,
            "quickstart": self._quickstart,
            "deterministic_task": self._run_task,
            "final_report": self._show_report,
            "offline_replay": self._replay,
            "daemon_stop": self._stop_daemon,
            "cleanup": self._cleanup,
            "leak_check": self._leak_check,
            "uninstall": self._uninstall,
        }
        for name in acceptance_step_names(self.kind):
            self._step(name, actions[name])

    def stop_daemon_after_failure(self) -> None:
        if self.daemon_id is None:
            return
        try:
            self._stop_daemon()
        except Exception:
            from agentbus.product.daemon import stop_daemon

            stop_daemon(
                registry_path=self.registry,
                daemon_id=self.daemon_id,
            )
            self.daemon_id = None

    def _step(self, name: str, action: Callable[[], str]) -> None:
        started = time.monotonic()
        try:
            detail = action()
        except Exception as exc:
            self.steps.append(
                AcceptanceStep(
                    name=name,
                    status="failed",
                    detail=_safe_detail(exc),
                    duration_seconds=time.monotonic() - started,
                )
            )
            raise
        self.steps.append(
            AcceptanceStep(
                name=name,
                status="passed",
                detail=detail,
                duration_seconds=time.monotonic() - started,
            )
        )

    def _build_package(self) -> str:
        self.distributions.mkdir()
        arguments = [
            sys.executable,
            "-m",
            "build",
            "--no-isolation",
            "--wheel",
            "--outdir",
            str(self.distributions),
        ]
        self._command(arguments, cwd=self.repository, timeout=600)
        wheels = tuple(self.distributions.glob("agentbus-*.whl"))
        if len(wheels) != 1:
            raise RuntimeError("Package build did not produce exactly one wheel.")
        self.wheel = wheels[0].resolve(strict=True)
        return "Built one local wheel without publishing."

    def _create_environment(self) -> str:
        venv.EnvBuilder(with_pip=True).create(self.environment_root)
        self.python = _venv_executable(self.environment_root, "python")
        self.agentbus = _venv_executable(self.environment_root, "agentbus")
        dependency_paths = _host_dependency_paths()
        if not dependency_paths:
            raise RuntimeError("Offline host dependencies are unavailable.")
        bridge = _venv_site_packages(self.environment_root) / "agentbus-host-dependencies.pth"
        bridge.write_text(
            "".join(f"{path}\n" for path in dependency_paths),
            encoding="utf-8",
            newline="\n",
        )
        return "Created an isolated virtual environment with an offline dependency bridge."

    def _install_wheel(self) -> str:
        python = self._required(self.python, "fresh Python")
        wheel = self._required(self.wheel, "wheel")
        requirement = str(wheel) + "[" + ",".join(_PRODUCT_EXTRAS) + "]"
        arguments = install_arguments(python, requirement)
        self._command(arguments, cwd=self.root, timeout=300)
        return "Installed the wheel with IDE and MCP extras; no editable install was used."

    def _verify_installed_origin(self) -> str:
        python = self._required(self.python, "fresh Python")
        payload = self._json_command(
            [
                str(python),
                "-m",
                "agentbus.product_acceptance",
                "--verify-install",
                str(self.environment_root),
            ],
            cwd=self.root,
        )
        if not payload.get("ok") or payload.get("version") != __version__:
            raise RuntimeError("Fresh Python did not import the expected AgentBus wheel.")
        return "Verified AgentBus imports from fresh-environment site-packages."

    def _version(self) -> str:
        payload = self._agentbus_json("version", "--json")
        if payload.get("version") != __version__:
            raise RuntimeError("Installed version metadata does not match the built wheel.")
        return f"Verified AgentBus {__version__} compatibility metadata."

    def _doctor(self) -> str:
        payload = self._agentbus_json(
            "doctor",
            "--workspace",
            str(self.root),
            "--provider",
            "deterministic",
            "--registry-path",
            str(self.registry),
            "--json",
            allowed_returncodes={0, 1},
        )
        if payload.get("network_used") is not False:
            raise RuntimeError("Doctor did not prove its offline execution mode.")
        return "Ran pre-setup product diagnostics without a live provider request."

    def _setup(self) -> str:
        _create_repository(self.workspace, self.root / "empty-hooks")
        payload = self._agentbus_json(
            "setup",
            "--workspace",
            str(self.workspace),
            "--provider",
            "deterministic",
            "--scope",
            "user",
            "--root",
            str(self.product_state),
            "--non-interactive",
            "--cleanup-worktrees",
            "--json",
        )
        if not payload.get("ok") or payload.get("credentials_written") is not False:
            raise RuntimeError("Noninteractive setup did not meet its safe output contract.")
        if Path(payload["config_file"]).resolve() != self.config_file.resolve():
            raise RuntimeError("Setup wrote configuration outside the acceptance state root.")
        return "Configured deterministic execution and local state noninteractively."

    def _create_demo(self) -> str:
        created = self._agentbus_json(
            "demo",
            "create",
            "python",
            "--output",
            str(self.demo),
            "--json",
        )
        preflight = self._agentbus_json(
            "demo",
            "run",
            "python",
            "--workspace",
            str(self.demo),
            "--json",
        )
        if created.get("language") != "python" or preflight.get("ready") is not True:
            raise RuntimeError("The generated Python demo did not expose its expected task.")
        return "Generated and preflighted the intentionally failing Python demo."

    def _start_daemon(self) -> str:
        payload = self._agentbus_json(
            "daemon",
            "--registry-path",
            str(self.registry),
            "--json",
            "start",
            "--config",
            str(self.config_file),
            "--workspace",
            str(self.workspace),
            "--idle-timeout",
            "300",
        )
        daemon = payload.get("daemon", {})
        if daemon.get("lifecycle") != "active" or not daemon.get("process_matches"):
            raise RuntimeError("The fresh-install daemon did not prove process ownership.")
        self.daemon_id = str(daemon["daemon_id"])
        self.daemon_pid = int(daemon["pid"])
        status = self._agentbus_json(
            "daemon",
            "--registry-path",
            str(self.registry),
            "--json",
            "status",
        )
        if status.get("count") != 1:
            raise RuntimeError("Daemon status did not report exactly one owned daemon.")
        return "Started one authenticated loopback daemon and verified its identity."

    def _build_index(self) -> str:
        payload = self._agentbus_json(
            "index",
            "build",
            "--config",
            str(self.config_file),
            "--workspace",
            str(self.workspace),
            "--json",
        )
        if payload.get("provider_calls") != 0 or payload.get("network_calls") != 0:
            raise RuntimeError("Repository indexing did not remain providerless.")
        return "Built the repository index with zero provider and network calls."

    def _quickstart(self) -> str:
        payload = self._agentbus_json("quickstart", "--json", timeout=180)
        if not payload.get("ok") or not payload.get("cleaned"):
            raise RuntimeError("Deterministic quickstart failed or retained temporary state.")
        if not payload.get("reviewer_approved") or not payload.get("verifier_passed"):
            raise RuntimeError("Quickstart verification or final review did not pass.")
        if sorted(payload.get("changed_files", [])) != [
            "agentbus_result.py",
            "test_agentbus_result.py",
        ]:
            raise RuntimeError("Quickstart changed-file evidence was incomplete.")
        return "Completed the reviewed deterministic first-task workflow and cleanup."

    def _run_task(self) -> str:
        output = self._agentbus_text(
            "run",
            "--config",
            str(self.config_file),
            "--workflow",
            "multi",
            "--provider",
            "deterministic",
            "--workspace",
            str(self.workspace),
            "--durable",
            "Create and verify the deterministic AgentBus calculator.",
            timeout=300,
        )
        match = re.search(r"^Run ID:\s+(\S+)$", output, flags=re.MULTILINE)
        if match is None or "Status: succeeded" not in output:
            raise RuntimeError("The durable deterministic task did not succeed.")
        self.run_id = match.group(1)
        return "Executed a durable deterministic implementation through final review."

    def _show_report(self) -> str:
        run_id = self._required(self.run_id, "durable run ID")
        output = self._agentbus_text(
            "show-run",
            str(run_id),
            "--config",
            str(self.config_file),
        )
        required = (
            "Status: succeeded",
            "Reviewer: approved",
            "agentbus_result.py",
            "test_agentbus_result.py",
        )
        if not all(value in output for value in required):
            raise RuntimeError("The durable final report omitted required task evidence.")
        return "Inspected the persisted final report, review, and scoped file evidence."

    def _replay(self) -> str:
        run_id = self._required(self.run_id, "durable run ID")
        payload = self._agentbus_json(
            "replay",
            str(run_id),
            "--mode",
            "offline",
            "--config",
            str(self.config_file),
            "--json",
            timeout=180,
        )
        session = payload.get("session", {})
        if not isinstance(session, dict) or session.get("status") != "succeeded":
            raise RuntimeError("Offline replay did not reach a successful terminal state.")
        if session.get("provider_calls") != 0 or session.get("network_calls") != 0:
            raise RuntimeError("Offline replay attempted provider or network access.")
        return "Replayed the persisted run offline with zero provider and network calls."

    def _stop_daemon(self) -> str:
        if self.daemon_id is None:
            return "No daemon required stopping."
        daemon_id = self.daemon_id
        payload = self._agentbus_json(
            "daemon",
            "--registry-path",
            str(self.registry),
            "--json",
            "stop",
            daemon_id,
        )
        if payload.get("stopped") != daemon_id:
            raise RuntimeError("The owned daemon did not acknowledge shutdown.")
        self.daemon_id = None
        return "Stopped the exact registry-bound daemon process."

    def _cleanup(self) -> str:
        payload = self._agentbus_json(
            "cleanup",
            "--config",
            str(self.config_file),
            "--registry-path",
            str(self.registry),
            "--all-runtime-state",
            "--yes",
            "--json",
        )
        if payload.get("ok") is not True:
            raise RuntimeError("Explicit cleanup did not complete safely.")
        return "Removed only validated AgentBus-owned runtime state after confirmation."

    def _leak_check(self) -> str:
        registry = self._agentbus_json(
            "daemon",
            "--registry-path",
            str(self.registry),
            "--json",
            "status",
        )
        if registry.get("count") != 0:
            raise RuntimeError("An AgentBus daemon registration remained after shutdown.")
        if self.daemon_pid is not None and _process_exists(self.daemon_pid):
            raise RuntimeError("The owned AgentBus daemon process remained active.")
        worktrees = self._command(
            ["git", "worktree", "list", "--porcelain"],
            cwd=self.workspace,
        )
        if sum(line.startswith("worktree ") for line in worktrees.splitlines()) != 1:
            raise RuntimeError("A temporary Git worktree remained after acceptance.")
        _assert_private_marker_absent(self.root, self.private_marker)
        return "Verified no daemon, worktree, or persisted credential marker leaked."

    def _uninstall(self) -> str:
        python = self._required(self.python, "fresh Python")
        self._command(
            [
                str(python),
                "-m",
                "pip",
                "uninstall",
                "--yes",
                "agentbus",
            ],
            cwd=self.root,
            timeout=180,
        )
        site_packages = _venv_site_packages(self.environment_root)
        local_artifacts = [
            site_packages / "agentbus",
            *site_packages.glob("agentbus-*.dist-info"),
        ]
        if any(path.exists() for path in local_artifacts):
            raise RuntimeError("AgentBus wheel files remained after uninstall.")
        result = self._command(
            [
                str(python),
                "-I",
                "-c",
                (
                    "import importlib.util; "
                    "print('present' if importlib.util.find_spec('agentbus') "
                    "else 'absent')"
                ),
            ],
            cwd=self.root,
        )
        if result.strip() != "absent":
            raise RuntimeError("AgentBus remained importable after uninstall.")
        return "Uninstalled the wheel from the fresh virtual environment."

    def _agentbus_json(
        self,
        *arguments: str,
        timeout: float = 120,
        allowed_returncodes: set[int] | None = None,
    ) -> dict[str, Any]:
        output = self._agentbus_text(
            *arguments,
            timeout=timeout,
            allowed_returncodes=allowed_returncodes,
        )
        try:
            payload = json.loads(output)
        except json.JSONDecodeError as exc:
            raise RuntimeError("AgentBus command did not return valid JSON.") from exc
        if not isinstance(payload, dict):
            raise RuntimeError("AgentBus JSON output must be an object.")
        return payload

    def _agentbus_text(
        self,
        *arguments: str,
        timeout: float = 120,
        allowed_returncodes: set[int] | None = None,
    ) -> str:
        executable = self._required(self.agentbus, "AgentBus console script")
        return self._command(
            [str(executable), *arguments],
            cwd=self.root,
            timeout=timeout,
            allowed_returncodes=allowed_returncodes,
        )

    def _json_command(
        self,
        arguments: list[str],
        *,
        cwd: Path,
    ) -> dict[str, Any]:
        output = self._command(arguments, cwd=cwd)
        try:
            payload = json.loads(output)
        except json.JSONDecodeError as exc:
            raise RuntimeError("Acceptance helper did not return valid JSON.") from exc
        if not isinstance(payload, dict):
            raise RuntimeError("Acceptance helper JSON must be an object.")
        return payload

    def _command(
        self,
        arguments: list[str],
        *,
        cwd: Path,
        timeout: float = 120,
        allowed_returncodes: set[int] | None = None,
    ) -> str:
        completed = subprocess.run(
            arguments,
            cwd=cwd,
            env=self.environment,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            shell=False,
            check=False,
        )
        allowed = {0} if allowed_returncodes is None else allowed_returncodes
        output = completed.stdout + completed.stderr
        if self.private_marker in output:
            raise RuntimeError("A private acceptance marker reached command output.")
        if completed.returncode not in allowed:
            detail = redact_text(output[-4_000:], max_chars=1_000) or "no safe output"
            raise AcceptanceCommandError(
                f"Command {Path(arguments[0]).name} failed with "
                f"exit code {completed.returncode}: {detail}"
            )
        return completed.stdout.strip()

    @staticmethod
    def _required(value: Any, label: str) -> Any:
        if value is None:
            raise RuntimeError(f"Acceptance {label} is unavailable.")
        return value


def install_arguments(python: Path, requirement: str) -> list[str]:
    return [
        str(python),
        "-m",
        "pip",
        "install",
        "--disable-pip-version-check",
        "--no-index",
        "--no-deps",
        requirement,
    ]


def installed_origin_payload(environment_root: str | Path) -> dict[str, Any]:
    import agentbus

    environment = Path(environment_root).expanduser().resolve(strict=True)
    package = Path(agentbus.__file__).resolve(strict=True)
    site_packages = _venv_site_packages(environment).resolve(strict=True)
    ok = package.is_relative_to(site_packages)
    return {
        "ok": ok,
        "version": agentbus.__version__,
        "wheel_origin": ok,
        "editable_install": False,
        "repository_pythonpath_used": False,
    }


def _create_repository(workspace: Path, hooks: Path) -> None:
    workspace.mkdir()
    hooks.mkdir()
    workspace.joinpath("README.md").write_text(
        "# AgentBus clean-install acceptance\n",
        encoding="utf-8",
        newline="\n",
    )
    workspace.joinpath("baseline.py").write_text(
        "VALUE = 1\n",
        encoding="utf-8",
        newline="\n",
    )
    workspace.joinpath("test_baseline.py").write_text(
        "import unittest\n\n\n"
        "class BaselineTest(unittest.TestCase):\n"
        "    def test_baseline(self):\n"
        "        self.assertEqual(1, 1)\n",
        encoding="utf-8",
        newline="\n",
    )
    _local_git(workspace, "init", "-q", "--initial-branch=main")
    _local_git(workspace, "add", "--all")
    _local_git(
        workspace,
        "-c",
        f"core.hooksPath={hooks}",
        "-c",
        "commit.gpgSign=false",
        "-c",
        "user.name=AgentBus Acceptance",
        "-c",
        "user.email=acceptance@agentbus.invalid",
        "commit",
        "-q",
        "-m",
        "test: initialize clean-install repository",
    )


def _local_git(workspace: Path, *arguments: str) -> str:
    completed = subprocess.run(
        ["git", *arguments],
        cwd=workspace,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
        shell=False,
        check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError("Local acceptance Git operation failed.")
    return completed.stdout.strip()


def _repository_root(root: Path) -> Path:
    completed = subprocess.run(
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
    if completed.returncode != 0:
        raise ValueError("Product acceptance root must be a Git repository.")
    discovered = Path(completed.stdout.strip()).resolve()
    if os.path.normcase(str(discovered)) != os.path.normcase(str(root)):
        raise ValueError("Product acceptance root must equal the Git top-level directory.")
    return discovered


def _offline_environment(
    root: Path,
    *,
    private_marker: str | None = None,
) -> dict[str, str]:
    blocked = ("CREDENTIAL", "KEY", "PASSWORD", "SECRET", "TOKEN")
    environment = {
        key: value
        for key, value in os.environ.items()
        if not any(marker in key.upper() for marker in blocked)
        and key.upper() != "PYTHONPATH"
        and not key.upper().startswith("GIT_CONFIG_")
    }
    home = root / "home"
    appdata = root / "appdata"
    local_appdata = root / "local-appdata"
    temporary = root / "temporary"
    for directory in (home, appdata, local_appdata, temporary):
        directory.mkdir(exist_ok=True)
    environment.update(
        {
            "AGENTBUS_PROVIDER": "deterministic",
            "AGENTBUS_STATE_DIR": str(root / "preflight-state"),
            "AGENTBUS_RUNS_DIR": str(root / "preflight-runs"),
            "AGENTBUS_MODEL_MAX_RETRIES": "0",
            "AGENTBUS_KEEP_WORKTREES": "false",
            "AGENTBUS_ACCEPTANCE": "1",
            "AZURE_OPENAI_ENDPOINT": "https://acceptance.invalid",
            "AZURE_OPENAI_API_KEY": private_marker or secrets.token_urlsafe(32),
            "AZURE_OPENAI_DEFAULT_DEPLOYMENT": "acceptance-only",
            "ALL_PROXY": "http://127.0.0.1:9",
            "HTTP_PROXY": "http://127.0.0.1:9",
            "HTTPS_PROXY": "http://127.0.0.1:9",
            "NO_PROXY": "127.0.0.1,localhost,::1",
            "PIP_NO_INDEX": "1",
            "PIP_DISABLE_PIP_VERSION_CHECK": "1",
            "PYTHONNOUSERSITE": "1",
            "PYTHONSAFEPATH": "1",
            "HOME": str(home),
            "USERPROFILE": str(home),
            "APPDATA": str(appdata),
            "LOCALAPPDATA": str(local_appdata),
            "XDG_CONFIG_HOME": str(home / ".config"),
            "XDG_STATE_HOME": str(home / ".local" / "state"),
            "TEMP": str(temporary),
            "TMP": str(temporary),
            "TMPDIR": str(temporary),
        }
    )
    return environment


def _host_dependency_paths() -> tuple[Path, ...]:
    paths: list[Path] = []
    for value in (*site.getsitepackages(), *sys.path):
        if not value:
            continue
        path = Path(value).expanduser().resolve()
        if path.is_dir() and (path / "pydantic").is_dir() and path not in paths:
            paths.append(path)
    return tuple(paths)


def _venv_executable(environment: Path, name: str) -> Path:
    if os.name == "nt":
        return environment / "Scripts" / f"{name}.exe"
    return environment / "bin" / name


def _venv_site_packages(environment: Path) -> Path:
    if os.name == "nt":
        return environment / "Lib" / "site-packages"
    version = f"python{sys.version_info.major}.{sys.version_info.minor}"
    return environment / "lib" / version / "site-packages"


def _assert_private_marker_absent(root: Path, private_marker: str) -> None:
    marker = private_marker.encode("utf-8")
    inspected = 0
    for path in root.rglob("*"):
        if not path.is_file() or path.is_symlink():
            continue
        inspected += 1
        if inspected > 100_000:
            raise RuntimeError("Acceptance credential scan exceeded its file bound.")
        try:
            size = path.stat().st_size
            if size > 32 * 1024 * 1024:
                continue
            if marker in path.read_bytes():
                raise RuntimeError("A private acceptance marker was persisted to disk.")
        except PermissionError as exc:
            raise RuntimeError("Acceptance credential scan could not read a file.") from exc


def _process_exists(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name == "nt":
        import ctypes

        process = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)
        if not process:
            return False
        ctypes.windll.kernel32.CloseHandle(process)
        return True
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _safe_detail(error: BaseException) -> str:
    detail = redact_text(str(error), max_chars=1_000)
    return detail or type(error).__name__
