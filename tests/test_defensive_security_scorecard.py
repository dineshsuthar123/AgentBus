from __future__ import annotations

import json
from pathlib import Path
from zipfile import ZipFile

from agentbus.release_security import (
    ReleaseSecurityReport,
    audit_release_security,
    main,
)
from agentbus.security.validation import (
    DEFENSIVE_SECURITY_DISCLAIMER,
    DefensiveSecurityStatus,
    run_defensive_security_validation,
)


ROOT = Path(__file__).resolve().parents[1]
EXPECTED_BOUNDARIES = {
    "filesystem_containment",
    "approval_capability_scope",
    "git_safety",
    "malformed_protocol_handling",
    "hostile_mcp_peer",
    "trace_archive_integrity",
    "diagnostic_privacy",
    "package_contents",
    "vsix_contents",
}


def test_defensive_scorecard_reports_nine_local_security_boundaries(
    tmp_path: Path,
) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()

    scorecard = run_defensive_security_validation(repository)

    assert scorecard.ok is True
    assert scorecard.classification in {
        DefensiveSecurityStatus.PASS,
        DefensiveSecurityStatus.PASS_WITH_WARNINGS,
    }
    assert {item.boundary_id for item in scorecard.evidence} == EXPECTED_BOUNDARIES
    assert all(item.passed and item.tested_boundaries for item in scorecard.evidence)
    assert scorecard.offline is True
    assert scorecard.network_used is False
    assert scorecard.provider_calls == 0
    assert scorecard.external_targets_contacted == 0
    assert scorecard.formal_penetration_test_certification is False
    assert scorecard.disclaimer == DEFENSIVE_SECURITY_DISCLAIMER
    assert "not formal penetration-test certification" in scorecard.disclaimer
    assert len(scorecard.tested_boundaries) >= len(EXPECTED_BOUNDARIES)
    assert any("package" in item.casefold() for item in scorecard.unresolved_limitations)
    payload = json.dumps(scorecard.to_dict(), sort_keys=True)
    assert str(tmp_path) not in payload


def test_selected_real_vsix_is_included_in_scorecard_evidence(
    tmp_path: Path,
) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()
    vsix = tmp_path / "agentbus-vscode.vsix"
    _write_selected_vsix(vsix)

    scorecard = run_defensive_security_validation(
        repository,
        artifacts=(vsix,),
    )

    evidence = _evidence(scorecard, "vsix_contents")
    assert evidence.status == DefensiveSecurityStatus.PASS
    assert not evidence.limitations
    assert "selected real artifacts=1" in evidence.observation


def test_unsafe_selected_vsix_fails_without_exposing_artifact_path(
    tmp_path: Path,
) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()
    unsafe = tmp_path / "private-location" / "unsafe.vsix"
    unsafe.parent.mkdir()
    with ZipFile(unsafe, mode="w") as archive:
        archive.writestr("extension/src/extension.ts", "export const unsafe = true;\n")

    report = audit_release_security(
        repository,
        tracked_paths=(),
        artifacts=(unsafe,),
        include_validation=True,
    )
    scorecard = report.defensive_validation

    assert scorecard is not None
    evidence = _evidence(scorecard, "vsix_contents")
    assert report.findings == ()
    assert report.ok is False
    assert scorecard.ok is False
    assert scorecard.classification == DefensiveSecurityStatus.FAIL
    assert evidence.status == DefensiveSecurityStatus.FAIL
    assert "bounded content check" in evidence.observation
    assert str(unsafe) not in json.dumps(scorecard.to_dict(), sort_keys=True)


def test_release_security_report_optionally_includes_defensive_evidence(
    tmp_path: Path,
) -> None:
    (tmp_path / "safe.txt").write_text("safe\n", encoding="utf-8")

    static = audit_release_security(
        tmp_path,
        tracked_paths=("safe.txt",),
        artifacts=(),
    )
    validated = audit_release_security(
        tmp_path,
        tracked_paths=("safe.txt",),
        artifacts=(),
        include_validation=True,
    )

    assert static.ok is True
    assert static.defensive_validation is None
    assert "defensive_security_scorecard" not in static.to_dict()
    assert validated.ok is True
    assert validated.defensive_validation is not None
    payload = validated.to_dict()
    assert payload["defensive_security_scorecard"]["evidence_count"] == 9
    assert payload["network_used"] is False


def test_release_security_cli_defaults_to_validation_and_supports_static_only(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    scorecard = run_defensive_security_validation(tmp_path)
    observed: list[bool] = []

    def fake_audit(root, *, artifacts=None, include_validation=False):
        del root, artifacts
        observed.append(include_validation)
        return ReleaseSecurityReport(
            scanned_files=3,
            scanned_artifacts=(),
            findings=(),
            defensive_validation=scorecard if include_validation else None,
        )

    monkeypatch.setattr(
        "agentbus.release_security.audit_release_security",
        fake_audit,
    )

    assert main(["--root", str(tmp_path)]) == 0
    output = capsys.readouterr().out
    assert "Defensive security scorecard" in output
    assert "Tested:" in output
    assert DEFENSIVE_SECURITY_DISCLAIMER in output
    assert main(["--root", str(tmp_path), "--static-only", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)

    assert observed == [True, False]
    assert "defensive_security_scorecard" not in payload


def _evidence(scorecard, boundary_id: str):
    return next(
        item for item in scorecard.evidence if item.boundary_id == boundary_id
    )


def _write_selected_vsix(path: Path) -> None:
    extension = ROOT / "extensions" / "vscode"
    package_bytes = (extension / "package.json").read_bytes()
    package = json.loads(package_bytes)
    entries = {
        "[Content_Types].xml": b"<Types />\n",
        "extension.vsixmanifest": (
            f'<PackageManifest Version="{package["version"]}" />\n'.encode()
        ),
        "extension/LICENSE.txt": (extension / "LICENSE").read_bytes(),
        "extension/readme.md": (extension / "README.md").read_bytes(),
        "extension/media/agentbus.svg": (
            extension / "media" / "agentbus.svg"
        ).read_bytes(),
        "extension/out/extension.js": b"exports.activate = () => {};\n",
        "extension/package.json": package_bytes,
    }
    with ZipFile(path, mode="w") as archive:
        for name, content in entries.items():
            archive.writestr(name, content)
