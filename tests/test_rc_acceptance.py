from __future__ import annotations

import json

from agentbus import rc_acceptance
from agentbus.product.acceptance import (
    AcceptanceKind,
    AcceptanceStep,
    CleanInstallAcceptanceReport,
    RC_ACCEPTANCE_STEPS,
    _CleanInstallRunner,
    acceptance_step_names,
)
from agentbus.product.acceptance_probe import run_managed_workflow_probe


def test_rc_acceptance_plan_matches_release_candidate_lifecycle() -> None:
    assert acceptance_step_names(AcceptanceKind.RC) == RC_ACCEPTANCE_STEPS
    assert RC_ACCEPTANCE_STEPS == (
        "package_build",
        "package_audit",
        "clean_install",
        "migration_verification",
        "setup",
        "doctor",
        "deterministic_quickstart",
        "repository_index",
        "managed_tool_workflow",
        "scoped_approval",
        "cancellation",
        "daemon_restart",
        "offline_replay",
        "trace_integrity",
        "hostile_mcp",
        "adversarial_path",
        "support_bundle_privacy",
        "reliability_smoke",
        "performance_smoke",
        "cleanup",
        "uninstall",
        "process_worktree_leak_check",
    )


def test_rc_runner_dispatches_every_gate_in_order(tmp_path, monkeypatch) -> None:
    actions = {
        "_build_package": "package_build",
        "_audit_package": "package_audit",
        "_clean_install": "clean_install",
        "_migration_verification": "migration_verification",
        "_setup": "setup",
        "_rc_doctor": "doctor",
        "_quickstart": "deterministic_quickstart",
        "_build_index": "repository_index",
        "_managed_tool_workflow": "managed_tool_workflow",
        "_managed_tool_approval": "scoped_approval",
        "_cancellation": "cancellation",
        "_restart_daemon": "daemon_restart",
        "_replay": "offline_replay",
        "_trace_integrity": "trace_integrity",
        "_hostile_mcp": "hostile_mcp",
        "_adversarial_path": "adversarial_path",
        "_support_bundle_privacy": "support_bundle_privacy",
        "_reliability_smoke": "reliability_smoke",
        "_performance_smoke": "performance_smoke",
        "_rc_cleanup": "cleanup",
        "_uninstall": "uninstall",
        "_post_uninstall_leak_check": "process_worktree_leak_check",
    }
    invoked: list[str] = []

    for method_name, step_name in actions.items():
        def action(_self, name=step_name):
            invoked.append(name)
            return f"Passed {name}."

        monkeypatch.setattr(_CleanInstallRunner, method_name, action)

    root = tmp_path / "acceptance"
    root.mkdir()
    steps: list[AcceptanceStep] = []
    runner = _CleanInstallRunner(
        AcceptanceKind.RC,
        repository=tmp_path,
        temporary_root=root,
        steps=steps,
    )

    runner.run()

    assert invoked == list(RC_ACCEPTANCE_STEPS)
    assert [step.name for step in steps] == list(RC_ACCEPTANCE_STEPS)
    assert all(step.status == "passed" for step in steps)


def test_managed_workflow_probe_executes_read_write_and_test(tmp_path) -> None:
    payload = run_managed_workflow_probe(tmp_path / "probe")

    assert payload["ok"] is True
    assert payload["tool_names"] == [
        "filesystem.read",
        "filesystem.write",
        "test.execute",
    ]
    assert payload["invocation_count"] == 3
    assert payload["audit_count"] == 3
    assert payload["provider_calls"] == 0
    assert payload["network_used"] is False


def test_rc_acceptance_entrypoint_reports_controlled_local_scope(
    monkeypatch,
    capsys,
) -> None:
    report = CleanInstallAcceptanceReport(
        kind=AcceptanceKind.RC,
        ok=True,
        version="0.7.0",
        duration_seconds=0.1,
        steps=(AcceptanceStep("package_audit", "passed", "Audited.", 0.01),),
    )
    monkeypatch.setattr(
        rc_acceptance,
        "run_clean_install_acceptance",
        lambda *_args, **_kwargs: report,
    )

    exit_code = rc_acceptance.main(["--json"])

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["kind"] == "release-candidate"
    assert payload["release_candidate"] is True
    assert payload["package_audit"] is True
    assert payload["network_used"] is False
    assert payload["external_security_targets_contacted"] == 0
    assert payload["published"] is False


def test_rc_acceptance_entrypoint_returns_failure_for_failed_gate(
    monkeypatch,
    capsys,
) -> None:
    report = CleanInstallAcceptanceReport(
        kind=AcceptanceKind.RC,
        ok=False,
        version="0.7.0",
        duration_seconds=0.1,
        steps=(AcceptanceStep("trace_integrity", "failed", "Rejected.", 0.01),),
        error="Trace integrity failed safely.",
    )
    monkeypatch.setattr(
        rc_acceptance,
        "run_clean_install_acceptance",
        lambda *_args, **_kwargs: report,
    )

    exit_code = rc_acceptance.main([])

    assert exit_code == 1
    output = capsys.readouterr().out
    assert "release-candidate acceptance: FAIL" in output
    assert "[FAILED] trace_integrity" in output
    assert "No package was published" in output
