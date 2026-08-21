from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_changelog_documents_every_product_milestone() -> None:
    changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    for version in ("0.1.0", "0.2.0", "0.2.1", "0.3.0", "0.4.0", "0.5.0", "0.6.0"):
        assert f"## [{version}" in changelog
    assert "Unreleased" in changelog
    assert "Known beta limitations" in changelog


def test_contributor_guide_supports_offline_development() -> None:
    guide = (ROOT / "CONTRIBUTING.md").read_text(encoding="utf-8")
    for required in (
        ".[dev,ide]",
        "deterministic provider",
        "Adding a provider",
        "Adding a tool",
        "Adding a repository parser",
        "control-schema export",
        "npm run protocol:check",
        "Conventional Commits",
    ):
        assert required in guide


def test_public_issue_forms_cover_required_support_routes() -> None:
    templates = ROOT / ".github" / "ISSUE_TEMPLATE"
    for name in (
        "bug.yml",
        "feature.yml",
        "installation.yml",
        "provider.yml",
        "repository-intelligence.yml",
        "config.yml",
    ):
        assert (templates / name).is_file()
    bug = (templates / "bug.yml").read_text(encoding="utf-8")
    for field in ("AgentBus version", "Operating system", "Python version"):
        assert field in bug
    assert "Never include API keys" in bug
    config = (templates / "config.yml").read_text(encoding="utf-8")
    assert "/security/advisories/new" in config


def test_security_and_pull_request_guidance_forbid_sensitive_artifacts() -> None:
    security = (ROOT / "SECURITY.md").read_text(encoding="utf-8")
    pull_request = (ROOT / ".github" / "PULL_REQUEST_TEMPLATE.md").read_text(
        encoding="utf-8"
    )
    assert "latest `0.6`" in security
    assert "private security advisory" in security
    assert "shell=False" in security
    assert "No credentials" in pull_request
    assert "No automatic destructive rollback" in pull_request


def test_release_checklist_matches_executable_public_beta_gates() -> None:
    checklist = (ROOT / "RELEASE_CHECKLIST.md").read_text(encoding="utf-8")
    for command in (
        "python -m agentbus.product_acceptance",
        "python -m agentbus.beta_acceptance",
        "python -m agentbus.control.acceptance",
        "python -m agentbus.release_security",
        "agentbus release-check --full",
        "npm run test:product",
        "npm run package:audit",
    ):
        assert command in checklist
    assert "never publish, tag, push, merge" in checklist
    assert "not reset or roll back repository files" in checklist


def test_v07_rc_checklist_covers_evidence_and_publication_hold() -> None:
    checklist_path = ROOT / "docs" / "release" / "v0.7-rc-checklist.md"
    checklist = checklist_path.read_text(encoding="utf-8")

    for heading in (
        "## Python tests",
        "## TypeScript tests",
        "## Clean installation",
        "## Migration and upgrade compatibility",
        "## Package audit",
        "## VSIX audit",
        "## Security validation",
        "## Reliability validation",
        "## Daemon recovery and cancellation",
        "## Replay integrity",
        "## Performance validation",
        "## Repository scale",
        "## Support bundle",
        "## Documentation",
        "## Known issues",
        "## Publication hold",
    ):
        assert heading in checklist

    for command in (
        "python -m pytest",
        "python -m compileall agentbus",
        "python -m agentbus.rc_acceptance",
        "python -m agentbus.eval run --suite release-offline",
        "npm run protocol:check",
        "npm run test:integration",
        "npm run package:audit",
        "agentbus migrate verify",
        "agentbus upgrade-check --json",
        "python -m agentbus.release_packaging",
        "python -m agentbus.release_security",
        "agentbus validate reliability --json",
        "agentbus benchmark all",
        "agentbus benchmark index-scale",
        "agentbus support-bundle",
        "agentbus release-check --full",
    ):
        assert command in checklist

    for evidence_status in ("PASS", "PASS_WITH_WARNINGS", "FAIL", "NOT_RUN"):
        assert f"`{evidence_status}`" in checklist
    normalized_checklist = " ".join(checklist.split())
    for boundary in (
        "Exact candidate commit",
        "zero external security targets",
        "are not automatically reset, cleaned, deleted, or rolled back",
        "Do not tag or publish this candidate",
        "Do not push, merge, rebase",
    ):
        assert boundary in normalized_checklist
    assert "- [x]" not in checklist.lower()

    acceptance = (ROOT / "docs" / "validation" / "release-candidate.md").read_text(
        encoding="utf-8"
    )
    assert "../release/v0.7-rc-checklist.md" in acceptance
