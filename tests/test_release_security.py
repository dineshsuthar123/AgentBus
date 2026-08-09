from __future__ import annotations

import json
import zipfile
from pathlib import Path

from agentbus.release_security import audit_release_security, main


ROOT = Path(__file__).resolve().parents[1]


def test_release_security_accepts_safe_release_inputs(tmp_path):
    (tmp_path / "agentbus").mkdir()
    (tmp_path / "agentbus" / "module.py").write_text(
        "PROVIDER = 'deterministic'\n",
        encoding="utf-8",
    )
    (tmp_path / ".env.example").write_text(
        "AZURE_OPENAI_API_KEY=\n",
        encoding="utf-8",
    )

    report = audit_release_security(
        tmp_path,
        tracked_paths=("agentbus/module.py", ".env.example"),
        artifacts=(),
    )

    assert report.ok is True
    assert report.scanned_files == 2
    assert report.findings == ()
    assert report.to_dict()["network_used"] is False


def test_release_security_accepts_current_tracked_repository():
    report = audit_release_security(ROOT, artifacts=())

    assert report.ok is True
    assert report.scanned_files > 500


def test_release_security_detects_runtime_files_and_real_secret_shapes(tmp_path):
    token = "sk-" + "Q7mN2xP9vR4tY8kL3cD6sF1hJ5wB0zA"
    private_key = (
        "-----BEGIN "
        + "PRIVATE KEY-----\n"
        + "QUJDREVGR0hJSktMTU5PUFFSU1RVVldYWVo=\n"
        + "-----END "
        + "PRIVATE KEY-----\n"
    )
    (tmp_path / ".env").write_text(f"OPENAI_API_KEY={token}\n", encoding="utf-8")
    (tmp_path / "state.db").write_bytes(b"SQLite format 3\x00")
    (tmp_path / "identity.txt").write_text(private_key, encoding="utf-8")

    report = audit_release_security(
        tmp_path,
        tracked_paths=(".env", "state.db", "identity.txt"),
        artifacts=(),
    )

    codes = {finding.code for finding in report.findings}
    assert report.ok is False
    assert {"CREDENTIAL_FILE", "RUNTIME_OR_KEY_FILE", "PRIVATE_KEY"} <= codes
    assert "OPENAI_TOKEN" in codes
    assert token not in json.dumps(report.to_dict())


def test_release_security_audits_archive_paths_and_content(tmp_path):
    archive = tmp_path / "unsafe.whl"
    token = "ghp_" + "R7mN2xP9vT4kL8cD3sF6hJ1wB5zA0qE2yU9i"
    with zipfile.ZipFile(archive, "w") as output:
        output.writestr("../escape.txt", "unsafe")
        output.writestr("package/.env", f"ACCESS_TOKEN={token}\n")

    report = audit_release_security(
        tmp_path,
        tracked_paths=(),
        artifacts=(archive,),
    )

    codes = {finding.code for finding in report.findings}
    assert report.ok is False
    assert "ARCHIVE_TRAVERSAL" in codes
    assert "CREDENTIAL_FILE" in codes
    assert "GITHUB_TOKEN" in codes
    assert token not in json.dumps(report.to_dict())


def test_release_security_cli_is_machine_readable(tmp_path, capsys):
    (tmp_path / "safe.txt").write_text("safe\n", encoding="utf-8")

    exit_code = main(["--root", str(tmp_path), "--json"])

    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 2
    assert payload["ok"] is False
    assert payload["network_used"] is False
