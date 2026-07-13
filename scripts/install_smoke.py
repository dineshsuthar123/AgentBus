from __future__ import annotations

import argparse
import json
import os
import runpy
import subprocess
import sys
import tempfile
import venv
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
__version__ = runpy.run_path(str(ROOT / "agentbus" / "_version.py"))["__version__"]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("wheel")
    parser.add_argument("--output")
    parser.add_argument("--skip-evaluation", action="store_true")
    args = parser.parse_args()
    wheel = Path(args.wheel).expanduser().resolve(strict=True)
    if wheel.is_dir():
        candidates = sorted(wheel.glob("agentbus-*.whl"))
        if len(candidates) != 1:
            parser.error("wheel directory must contain exactly one AgentBus wheel")
        wheel = candidates[0]
    try:
        with tempfile.TemporaryDirectory(prefix="agentbus-install-smoke-") as raw_root:
            root = Path(raw_root)
            environment = root / "venv"
            venv.EnvBuilder(with_pip=True).create(environment)
            python = _venv_executable(environment, "python")
            agentbus = _venv_executable(environment, "agentbus")
            evaluation = _venv_executable(environment, "agentbus-eval")
            _run([str(python), "-m", "pip", "install", str(wheel)], cwd=root)
            imported = _run(
                [
                    str(python),
                    "-c",
                    "import agentbus; print(agentbus.__version__)",
                ],
                cwd=root,
            ).strip()
            if imported != __version__:
                raise RuntimeError("Installed package version did not match the wheel build.")
            _run([str(agentbus), "--version"], cwd=root, contains=__version__)
            _run([str(agentbus), "--help"], cwd=root, contains="release-report")
            repository = root / "repository"
            repository.mkdir()
            (repository / "README.md").write_text("# install smoke\n", encoding="utf-8")
            _git(repository, "init", "-q")
            _git(repository, "config", "user.name", "AgentBus Install Smoke")
            _git(repository, "config", "user.email", "smoke@agentbus.invalid")
            _git(repository, "add", "README.md")
            _git(repository, "commit", "-q", "-m", "baseline")
            _run(
                [str(agentbus), "doctor", "--workspace", str(repository), "--json"],
                cwd=root,
                contains='"network_used": false',
            )
            _run([str(evaluation), "list", "--json"], cwd=root, contains="release-offline")
            evaluation_summary = "skipped by explicit flag"
            if not args.skip_evaluation:
                output = _run(
                    [
                        str(evaluation),
                        "--results-dir",
                        str(root / "evaluation-results"),
                        "run",
                        "--suite",
                        "release-offline",
                        "--variant",
                        "durable-parallel-fake",
                    ],
                    cwd=root,
                    contains="Result: PASS",
                    timeout=240,
                )
                evaluation_summary = next(
                    line for line in output.splitlines() if line.startswith("Evaluation run:")
                )
        result = {
            "status": "PASS",
            "summary": "Fresh virtual-environment wheel install and console smoke passed.",
            "version": __version__,
            "evaluation": evaluation_summary,
            "repository_pythonpath_used": False,
        }
    except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
        result = {
            "status": "FAIL",
            "summary": f"{type(exc).__name__}: {exc}",
            "version": __version__,
        }
    payload = json.dumps(result, indent=2, sort_keys=True) + "\n"
    if args.output:
        output = Path(args.output).expanduser().resolve()
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(payload, encoding="utf-8")
    print(payload, end="")
    return 0 if result["status"] == "PASS" else 1


def _venv_executable(environment: Path, name: str) -> Path:
    if os.name == "nt":
        suffix = ".exe" if name != "python" or sys.executable.endswith(".exe") else ""
        return environment / "Scripts" / f"{name}{suffix}"
    return environment / "bin" / name


def _run(
    arguments: list[str],
    *,
    cwd: Path,
    contains: str | None = None,
    timeout: float = 120,
) -> str:
    completed = subprocess.run(
        arguments,
        cwd=cwd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        shell=False,
        check=False,
        timeout=timeout,
        env={key: value for key, value in os.environ.items() if key.upper() != "PYTHONPATH"},
    )
    output = completed.stdout + completed.stderr
    if completed.returncode != 0:
        raise RuntimeError(
            f"Command failed ({completed.returncode}): {Path(arguments[0]).name}\n"
            + output[-4000:]
        )
    if contains and contains not in output:
        raise RuntimeError(f"Command output did not contain {contains!r}.")
    return output


def _git(repository: Path, *arguments: str) -> str:
    return _run(["git", *arguments], cwd=repository)


if __name__ == "__main__":
    raise SystemExit(main())
