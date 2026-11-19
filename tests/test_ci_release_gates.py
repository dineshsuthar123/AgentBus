from __future__ import annotations

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "ci.yml"


def test_ci_exposes_every_named_v07_gate() -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")
    required = (
        "Python 3.11 Core",
        "Python 3.12 Core",
        "Python 3.13 Core",
        "Python 3.14 Core",
        "Windows Core",
        "Linux Core",
        "Windows Security Boundaries",
        "Linux Security Boundaries",
        "Property Tests",
        "Parser Robustness",
        "Protocol Robustness",
        "Sandbox Stress",
        "MCP Adversarial Local",
        "SQLite Contention",
        "Repository Scale",
        "Package Audit",
        "Product Acceptance",
        "RC Acceptance",
        "Control Acceptance",
        "Repository Intelligence",
        "Replay Integrity",
        "Security Audit",
        "Release Security",
        "Performance Smoke",
        "VS Code Compile",
        "VS Code Unit",
        "VS Code Electron",
        "VSIX Audit",
        "Documentation Checks",
        "Release Readiness",
    )

    for gate in required:
        assert f"name: {gate}" in workflow


def test_ci_runs_product_acceptance_on_both_platforms_and_beta_readiness() -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")

    assert "python -m syndra.product_acceptance" in workflow
    assert "python -m syndra.beta_acceptance" in workflow
    assert "python -m syndra.rc_acceptance" in workflow
    assert "ubuntu-latest" in workflow
    assert "windows-latest" in workflow
    assert "xvfb-run -a npm run test:product" in workflow
    assert "python -m syndra.release_security" in workflow
    assert "continue-on-error" not in workflow


def test_windows_full_suite_has_bounded_budget_without_skipping_checks() -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")
    windows_job = workflow.split("  windows-core:", 1)[1].split(
        "  package-audit:", 1
    )[0]
    timeout = re.search(r"(?m)^    timeout-minutes: (\d+)$", windows_job)

    assert timeout is not None
    # The growing Windows suite exceeded 25 minutes before completing all tests.
    assert 30 <= int(timeout.group(1)) <= 45
    assert 'python: ["3.11", "3.12", "3.13", "3.14"]' in windows_job
    assert "fail-fast: false" in windows_job
    assert "run: python -m pytest\n" in windows_job
    assert "python -m compileall -q syndra agentbus" in windows_job
    assert "--suite core-offline --variant durable-parallel-fake" in windows_job
    assert "git diff --check" in windows_job
    assert "continue-on-error" not in windows_job


def test_v07_specialized_ci_gates_are_focused_and_mandatory() -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")

    expected_commands = (
        "tests/property",
        "tests/fuzz/test_python_parser_fuzz.py",
        "tests/fuzz/test_control_protocol_fuzz.py",
        "tests/test_sandbox_process_stress.py",
        "tests/test_mcp_adversarial.py",
        "tests/test_sqlite_contention.py",
        "tests/test_index_scale_benchmark.py",
        "tests/test_trace_sealing.py",
        "python -m syndra.rc_acceptance",
        "python -m syndra.release_security",
        "python -m syndra.cli benchmark all",
    )
    for command in expected_commands:
        assert command in workflow

    assert 'python: "3.12"' in workflow
    assert "continue-on-error" not in workflow


def test_ci_references_only_existing_test_paths() -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")
    referenced = set(re.findall(r"tests/[A-Za-z0-9_./-]+", workflow))

    assert referenced
    assert not [path for path in sorted(referenced) if not (ROOT / path).exists()]


def test_required_ci_never_invokes_live_provider_or_publication() -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")

    assert "--live" not in workflow
    assert "AZURE_OPENAI_API_KEY" not in workflow
    assert "npm publish" not in workflow
    assert "twine" not in workflow
    assert "vsce publish" not in workflow


def test_vscode_ci_installs_protocol_and_electron_python_dependencies() -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")
    compile_job = workflow.split("  vscode-compile:", 1)[1].split(
        "  vscode-unit:", 1
    )[0]
    electron_job = workflow.split("  vscode-electron:", 1)[1].split(
        "  vsix-audit:", 1
    )[0]

    assert re.search(r"uses: actions/setup-python@[a-f0-9]{40}\b", compile_job)
    assert 'python -m pip install -e ".[ide]"' in compile_job
    assert 'python -m pip install -e ".[dev,ide]"' in electron_job


def test_workflows_pin_official_actions_and_preserve_read_only_permissions() -> None:
    for path in (ROOT / ".github" / "workflows").glob("*.yml"):
        workflow = path.read_text(encoding="utf-8")
        uses = re.findall(r"uses:\s+(actions/[^\s#]+)", workflow)
        assert uses
        assert all(re.fullmatch(r"actions/[a-z-]+@[a-f0-9]{40}", action) for action in uses)
        assert "contents: read" in workflow
        assert "pull_request_target:" not in workflow


def test_dependency_audits_are_required_and_updates_are_configured() -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")
    assert "name: Dependency Security" in workflow
    assert "python -m pip_audit --local --skip-editable" in workflow
    assert "python -m bandit -r agentbus syndra" in workflow
    assert workflow.count("npm audit --audit-level=high") == 2
    assert 'exit "$status"' in workflow
    updates = (ROOT / ".github" / "dependabot.yml").read_text(encoding="utf-8")
    for ecosystem in ("pip", "npm", "github-actions"):
        assert f"package-ecosystem: {ecosystem}" in updates


def test_release_security_consumes_the_audited_builds() -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")
    job = workflow.split("  release-security:", 1)[1].split(
        "  performance-smoke:", 1
    )[0]
    assert "needs: [package-audit, vsix-audit]" in job
    assert "syndra-python-release-candidate" in job
    assert "syndra-vsix-release-candidate" in job
    for artifact in ("*.whl", "*.tar.gz", "syndra-vscode.vsix"):
        assert f"--artifact .tmp/release-security/{artifact}" in job
