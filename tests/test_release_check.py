from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

from agentbus.cli import main
from agentbus.product.release_check import (
    CommandOutcome,
    GateStatus,
    ReleaseGate,
    ReleaseReadinessReport,
    _offline_environment,
    release_check_commands,
    run_release_check,
)


def test_fast_release_check_is_offline_and_machine_readable(tmp_path):
    root = _release_repository(tmp_path)
    observed = []

    def runner(spec):
        observed.append(spec)
        return CommandOutcome(returncode=0, duration_seconds=0.01)

    report = run_release_check(mode="fast", root=root, runner=runner)

    assert report.ok is True
    assert report.mode == "fast"
    assert report.to_dict()["network_used"] is False
    assert report.to_dict()["published"] is False
    assert {spec.gate_id for spec in observed} >= {
        "git-cleanliness",
        "git-untracked",
        "python-compile",
        "protocol-freshness",
        "benchmark-smoke",
    }


def test_full_release_plan_contains_every_public_beta_gate(tmp_path):
    commands = release_check_commands(
        "full",
        tmp_path,
        tmp_path / "temporary",
    )
    identifiers = {command.gate_id for command in commands}

    assert {
        "python-tests",
        "control-acceptance",
        "product-acceptance",
        "beta-acceptance",
        "release-evaluation",
        "intelligence-evaluation",
        "reliability-soak",
        "benchmark-full",
        "vscode-compile",
        "vscode-lint",
        "vscode-tests",
        "vscode-electron",
        "vsix-package",
        "vsix-audit",
    } <= identifiers
    assert all("publish" not in part for command in commands for part in command.command)
    assert all(command.timeout_seconds > 0 for command in commands)
    vsix = next(command for command in commands if command.gate_id == "vsix-package")
    assert vsix.command[0] == "node"
    assert vsix.command[1].replace("\\", "/").endswith("/@vscode/vsce/vsce")


def test_release_check_records_safe_command_failure(tmp_path):
    root = _release_repository(tmp_path)

    def runner(spec):
        if spec.gate_id == "benchmark-smoke":
            return CommandOutcome(returncode=1, stderr="API_KEY=never-print")
        return CommandOutcome(returncode=0)

    report = run_release_check(mode="fast", root=root, runner=runner)

    assert report.ok is False
    failed = next(gate for gate in report.gates if gate.gate_id == "benchmark-smoke")
    assert failed.status == GateStatus.FAILED
    assert "never-print" not in failed.summary


def test_release_check_cli_renders_json(monkeypatch, capsys):
    report = ReleaseReadinessReport(
        mode="fast",
        version="0.6.0b1",
        duration_seconds=0.1,
        gates=(
            ReleaseGate("version", "Version", GateStatus.PASSED, "Current."),
        ),
    )
    monkeypatch.setattr(
        "agentbus.product.release_check.run_release_check",
        lambda **_kwargs: report,
    )

    exit_code = main(["release-check", "--fast", "--json"])

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["published"] is False


def test_release_check_environment_strips_credentials_and_blocks_remote_network(
    monkeypatch,
):
    monkeypatch.setenv("AZURE_OPENAI_API_KEY", "not-for-child-processes")
    monkeypatch.setenv("GITHUB_TOKEN", "not-for-child-processes")
    monkeypatch.setenv("GIT_CONFIG_KEY_0", "safe.directory")

    environment = _offline_environment()

    assert "AZURE_OPENAI_API_KEY" not in environment
    assert "GITHUB_TOKEN" not in environment
    assert environment["GIT_CONFIG_KEY_0"] == "safe.directory"
    assert environment["AGENTBUS_PROVIDER"] == "deterministic"
    assert environment["HTTPS_PROXY"] == "http://127.0.0.1:9"
    assert "localhost" in environment["NO_PROXY"]


def _release_repository(root: Path) -> Path:
    files = {
        "CHANGELOG.md": "# Changelog\n\n## 0.6\n",
        "CONTRIBUTING.md": "# Contributing\n",
        "LICENSE": "MIT\n",
        "README.md": "# AgentBus\n\n[Install](docs/getting-started/install.md)\n",
        "RELEASE_CHECKLIST.md": "# Release checklist\n",
        "SECURITY.md": "# Security\n",
        "docs/getting-started/install.md": "# Install\n",
        "docs/getting-started/quickstart.md": "# Quickstart\n",
        "docs/reference/cli.md": "# CLI\n",
        "docs/troubleshooting/install.md": "# Troubleshooting\n",
    }
    for relative, content in files.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    package = root / "extensions" / "vscode" / "package.json"
    package.parent.mkdir(parents=True)
    package.write_text(
        json.dumps(
            {
                "version": "0.6.0-beta.1",
                "agentbusCompatibility": {
                    "python": ">=0.6.0b1,<0.7.0",
                    "controlProtocol": "1.0",
                    "stateSchema": 6,
                },
            }
        ),
        encoding="utf-8",
    )
    subprocess.run(
        ["git", "init", "-q", "--initial-branch=main"],
        cwd=root,
        capture_output=True,
        shell=False,
        check=True,
        env={**os.environ, "GIT_CONFIG_NOSYSTEM": "1"},
    )
    subprocess.run(
        ["git", "add", "--all"],
        cwd=root,
        capture_output=True,
        shell=False,
        check=True,
        env={**os.environ, "GIT_CONFIG_NOSYSTEM": "1"},
    )
    return root.resolve()
