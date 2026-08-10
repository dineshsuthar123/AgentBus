from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "ci.yml"


def test_ci_exposes_every_named_public_beta_gate() -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")
    required = (
        "Python Core",
        "Windows Core",
        "Linux Core",
        "Package Build",
        "Clean Installation",
        "Control Acceptance",
        "Repository Intelligence",
        "Replay",
        "Security Audit",
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

    assert "python -m agentbus.product_acceptance" in workflow
    assert "python -m agentbus.beta_acceptance" in workflow
    assert "ubuntu-latest" in workflow
    assert "windows-latest" in workflow
    assert "xvfb-run -a npm run test:product" in workflow
    assert "python -m agentbus.release_security" in workflow
    assert "continue-on-error" not in workflow


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

    assert "actions/setup-python@v5" in compile_job
    assert "python -m pip install -e ." in compile_job
    assert 'python -m pip install -e ".[dev,ide]"' in electron_job
