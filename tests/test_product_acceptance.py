from __future__ import annotations

import json
from pathlib import Path

from agentbus.product.acceptance import (
    AcceptanceKind,
    AcceptanceStep,
    CleanInstallAcceptanceReport,
    _offline_environment,
    acceptance_step_names,
    install_arguments,
)
from agentbus import product_acceptance


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
