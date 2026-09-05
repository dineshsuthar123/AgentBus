from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from scripts.audit_branding import audit_branding


ROOT = Path(__file__).resolve().parents[1]


def test_public_product_surfaces_use_syndra_branding() -> None:
    assert audit_branding(ROOT) == []


def test_branding_audit_detects_stale_product_and_event_copy(tmp_path: Path) -> None:
    (tmp_path / "README.md").write_text(
        "AgentBus Studio was built for a payment hackathon submission.\n",
        encoding="utf-8",
    )

    findings = audit_branding(tmp_path)

    assert {finding.category for finding in findings} == {
        "event-specific wording",
        "legacy product name",
    }
    assert all(finding.path == "README.md" for finding in findings)


def test_compatibility_policy_may_name_legacy_product(tmp_path: Path) -> None:
    compatibility = tmp_path / "docs" / "reference" / "compatibility.md"
    compatibility.parent.mkdir(parents=True)
    compatibility.write_text(
        "Legacy AgentBus adapters remain readable.\n",
        encoding="utf-8",
    )

    assert audit_branding(tmp_path) == []


def test_canonical_cli_help_and_protocol_metadata_use_syndra() -> None:
    for module in ("syndra", "syndra.cli"):
        completed = subprocess.run(
            [sys.executable, "-m", module, "--help"],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
            shell=False,
        )
        assert "Syndra" in completed.stdout
        assert "AgentBus" not in completed.stdout

    openapi = json.loads(
        (ROOT / "protocol" / "agentbus-v1.openapi.json").read_text(encoding="utf-8")
    )
    schema = json.loads(
        (ROOT / "protocol" / "agentbus-v1.schema.json").read_text(encoding="utf-8")
    )
    assert openapi["info"]["title"] == "Syndra Control Protocol"
    assert schema["title"] == "Syndra Control Protocol v1"
    assert schema["$id"].endswith("/agentbus-v1.schema.json")
