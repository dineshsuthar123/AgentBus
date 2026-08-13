from __future__ import annotations

import json
from pathlib import Path

import pytest

from agentbus.product.acceptance import (
    AcceptanceKind,
    AcceptanceStep,
    CleanInstallAcceptanceReport,
    RepeatedCleanInstallAcceptanceReport,
    _offline_environment,
    acceptance_step_names,
    install_arguments,
    run_repeated_clean_install_acceptance,
)
from agentbus import product_acceptance
from agentbus.product import acceptance as acceptance_module


def test_product_acceptance_plan_covers_clean_machine_lifecycle() -> None:
    steps = acceptance_step_names(AcceptanceKind.PRODUCT)

    assert steps == (
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


def test_clean_install_environment_is_offline_and_credential_bounded(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("GITHUB_TOKEN", "must-not-propagate")
    monkeypatch.setenv("AZURE_OPENAI_API_KEY", "must-not-propagate")
    monkeypatch.setenv("PYTHONPATH", "must-not-propagate")
    monkeypatch.setenv("GIT_CONFIG_COUNT", "1")
    monkeypatch.setenv("GIT_CONFIG_KEY_0", "safe.directory")
    monkeypatch.setenv("GIT_CONFIG_VALUE_0", "must-not-propagate")

    environment = _offline_environment(tmp_path)

    assert environment["AGENTBUS_PROVIDER"] == "deterministic"
    assert environment["PIP_NO_INDEX"] == "1"
    assert environment["HTTPS_PROXY"] == "http://127.0.0.1:9"
    assert "localhost" in environment["NO_PROXY"]
    assert environment["AZURE_OPENAI_API_KEY"] != "must-not-propagate"
    assert "GITHUB_TOKEN" not in environment
    assert "PYTHONPATH" not in environment
    assert not any(key.startswith("GIT_CONFIG_") for key in environment)
    assert Path(environment["HOME"]).is_relative_to(tmp_path)


def test_wheel_install_is_never_editable_or_network_resolved(tmp_path: Path) -> None:
    arguments = install_arguments(
        tmp_path / "venv" / "python",
        str(tmp_path / "agentbus.whl") + "[ide,mcp]",
    )

    assert "--no-index" in arguments
    assert "--no-deps" in arguments
    assert "-e" not in arguments
    assert "--editable" not in arguments
    assert arguments[-1].endswith("agentbus.whl[ide,mcp]")


def test_product_acceptance_entrypoint_renders_machine_readable_report(
    monkeypatch,
    capsys,
) -> None:
    report = CleanInstallAcceptanceReport(
        kind=AcceptanceKind.PRODUCT,
        ok=True,
        version="0.6.0b1",
        duration_seconds=0.1,
        steps=(AcceptanceStep("wheel_install", "passed", "Installed.", 0.01),),
    )
    monkeypatch.setattr(
        product_acceptance,
        "run_clean_install_acceptance",
        lambda *_args, **_kwargs: report,
    )

    exit_code = product_acceptance.main(["--json"])

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["wheel_install"] is True
    assert payload["editable_install"] is False
    assert payload["network_used"] is False
    assert payload["published"] is False


def test_repeated_clean_install_uses_fresh_roots_and_completes_five_runs(
    tmp_path: Path,
    monkeypatch,
) -> None:
    parents: list[Path] = []

    def run_once(*_args, temp_parent=None, **_kwargs):
        parents.append(Path(temp_parent))
        return CleanInstallAcceptanceReport(
            kind=AcceptanceKind.PRODUCT,
            ok=True,
            version="0.7.0",
            duration_seconds=0.1,
            steps=(AcceptanceStep("uninstall", "passed", "Removed.", 0.01),),
        )

    monkeypatch.setattr(
        acceptance_module,
        "run_clean_install_acceptance",
        run_once,
    )

    report = run_repeated_clean_install_acceptance(
        5,
        temp_parent=tmp_path,
    )

    assert report.ok is True
    assert len(report.reports) == 5
    assert len(set(parents)) == 1
    assert all(not parent.exists() for parent in parents)
    assert report.to_dict()["cross_run_state_leak_detected"] is False


def test_repeated_clean_install_detects_cross_run_residue(
    tmp_path: Path,
    monkeypatch,
) -> None:
    def leak_once(*_args, temp_parent=None, **_kwargs):
        Path(temp_parent, "unexpected-state").mkdir()
        return CleanInstallAcceptanceReport(
            kind=AcceptanceKind.PRODUCT,
            ok=True,
            version="0.7.0",
            duration_seconds=0.1,
            steps=(),
        )

    monkeypatch.setattr(
        acceptance_module,
        "run_clean_install_acceptance",
        leak_once,
    )

    report = run_repeated_clean_install_acceptance(
        5,
        temp_parent=tmp_path,
    )

    assert report.ok is False
    assert len(report.reports) == 1
    assert report.state_leak_iterations == (1,)
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize("repetitions", [0, 11])
def test_repeated_clean_install_rejects_unbounded_counts(
    repetitions: int,
) -> None:
    with pytest.raises(ValueError, match="between 1 and 10"):
        run_repeated_clean_install_acceptance(repetitions)


def test_product_acceptance_entrypoint_reports_repetitions(
    monkeypatch,
    capsys,
) -> None:
    run = CleanInstallAcceptanceReport(
        kind=AcceptanceKind.PRODUCT,
        ok=True,
        version="0.7.0",
        duration_seconds=0.1,
        steps=(),
    )
    report = RepeatedCleanInstallAcceptanceReport(
        kind=AcceptanceKind.PRODUCT,
        requested_repetitions=2,
        reports=(run, run),
        duration_seconds=0.2,
    )
    monkeypatch.setattr(
        product_acceptance,
        "run_repeated_clean_install_acceptance",
        lambda *_args, **_kwargs: report,
    )

    exit_code = product_acceptance.main(["--repeat", "2", "--json"])

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["repetitions_requested"] == 2
    assert payload["repetitions_completed"] == 2
    assert payload["cross_run_state_leak_detected"] is False
