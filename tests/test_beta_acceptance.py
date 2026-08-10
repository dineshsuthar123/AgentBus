from __future__ import annotations

import json

from agentbus import beta_acceptance
from agentbus.product.acceptance import (
    AcceptanceKind,
    AcceptanceStep,
    CleanInstallAcceptanceReport,
    acceptance_step_names,
)
from agentbus.product.acceptance_probe import run_managed_approval_probe


def test_beta_acceptance_plan_adds_every_final_product_gate() -> None:
    steps = acceptance_step_names(AcceptanceKind.BETA)

    assert steps[0:4] == (
        "package_build",
        "package_audit",
        "fresh_environment",
        "wheel_install",
    )
    assert {
        "version",
        "doctor",
        "setup",
        "quickstart",
        "daemon_start",
        "repository_index",
        "deterministic_task",
        "managed_tool_approval",
        "final_report",
        "offline_replay",
        "support_bundle",
        "benchmark_smoke",
        "cleanup",
        "leak_check",
        "uninstall",
    } <= set(steps)
    assert steps.index("managed_tool_approval") < steps.index("final_report")
    assert steps.index("daemon_stop") < steps.index("leak_check")


def test_managed_approval_probe_executes_exact_persisted_grant(tmp_path) -> None:
    payload = run_managed_approval_probe(tmp_path / "probe")

    assert payload["ok"] is True
    assert payload["tool_name"] == "filesystem.delete"
    assert payload["approval_requested"] is True
    assert payload["approval_approved"] is True
    assert payload["tool_status"] == "succeeded"
    assert payload["target_deleted"] is True
    assert payload["invocation_count"] == 1
    assert payload["audit_count"] >= 1
    assert payload["provider_calls"] == 0
    assert payload["network_used"] is False


def test_beta_acceptance_entrypoint_renders_machine_readable_report(
    monkeypatch,
    capsys,
) -> None:
    report = CleanInstallAcceptanceReport(
        kind=AcceptanceKind.BETA,
        ok=True,
        version="0.6.0b1",
        duration_seconds=0.1,
        steps=(AcceptanceStep("package_audit", "passed", "Audited.", 0.01),),
    )
    monkeypatch.setattr(
        beta_acceptance,
        "run_clean_install_acceptance",
        lambda *_args, **_kwargs: report,
    )

    exit_code = beta_acceptance.main(["--json"])

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["package_audit"] is True
    assert payload["network_used"] is False
    assert payload["published"] is False
