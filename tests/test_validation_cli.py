from __future__ import annotations

import json
from datetime import UTC, datetime

from agentbus.cli import main
from agentbus.validation import commands
from agentbus.validation.commands import validation_command
from agentbus.validation.corpus import generate_validation_repository
from agentbus.validation.models import ValidationReport, ValidationStatus
from agentbus.validation.reports import render_validation_report


def _passing_report(*, offline: bool = True, network_used: bool = False):
    return ValidationReport(
        status=ValidationStatus.PASS,
        generated_at=datetime.now(UTC),
        offline=offline,
        network_used=network_used,
    )


class _ScorecardStub:
    def __init__(self, *, ok: bool = True):
        self.ok = ok

    def to_dict(self):
        return {
            "classification": "PASS" if self.ok else "FAIL",
            "failures": [] if self.ok else [{"summary": "synthetic failure"}],
            "network_used": False,
            "ok": self.ok,
            "scenarios_run": 2,
            "status": "PASS" if self.ok else "FAIL",
        }


def test_root_cli_dispatches_validation_commands(monkeypatch):
    captured = {}

    def fake_command(arguments):
        captured["arguments"] = arguments
        return 7

    monkeypatch.setattr(commands, "validation_command", fake_command)

    assert main(["validate", "corpus", "--offline"]) == 7
    assert captured["arguments"] == ["corpus", "--offline"]


def test_validate_repo_runs_locally_and_writes_atomic_json_report(
    tmp_path,
    capsys,
):
    repository = generate_validation_repository(
        tmp_path / "Repository With Spaces",
        "generated-python-library",
    )
    report_path = tmp_path / "reports" / "validation.json"

    exit_code = validation_command(
        [
            "repo",
            "--path",
            str(repository.root),
            "--output",
            str(report_path),
            "--json",
        ]
    )

    payload = json.loads(capsys.readouterr().out)
    persisted = json.loads(report_path.read_text(encoding="utf-8"))
    assert exit_code == 0
    assert payload["ok"] is True
    assert payload["network_used"] is False
    assert payload["repositories"]["total"] == 1
    assert payload["runs"][0]["repository_id"] == "repository-with-spaces"
    assert payload["report_path"] == str(report_path.resolve())
    assert persisted["runs"][0]["provider_calls"] == 0
    assert persisted["runs"][0]["network_calls"] == 0


def test_corpus_defaults_offline_and_download_requires_explicit_mode(
    tmp_path,
    monkeypatch,
    capsys,
):
    captured = []

    def fake_corpus(manifest, **kwargs):
        captured.append((manifest, kwargs))
        return _passing_report(
            offline=kwargs["offline"],
            network_used=kwargs["allow_download"],
        )

    monkeypatch.setattr(commands, "run_validation_corpus", fake_corpus)

    assert validation_command(["corpus", "--manifest", "corpus.json"]) == 0
    assert (
        validation_command(
            [
                "corpus",
                "--manifest",
                "corpus.json",
                "--include-optional",
                "--download-public",
                "--cache-directory",
                str(tmp_path),
            ]
        )
        == 0
    )
    capsys.readouterr()
    assert captured[0][1]["offline"] is True
    assert captured[0][1]["allow_download"] is False
    assert captured[1][1]["offline"] is False
    assert captured[1][1]["allow_download"] is True
    assert captured[1][1]["include_optional"] is True
    assert captured[1][1]["cache_directory"] == str(tmp_path)


def test_reliability_cli_forwards_bounded_options_and_repeated_repositories(
    tmp_path,
    monkeypatch,
    capsys,
):
    captured = {}

    def fake_reliability(**kwargs):
        captured.update(kwargs)
        return _ScorecardStub()

    monkeypatch.setattr(commands, "run_reliability_validation", fake_reliability)
    first = tmp_path / "first"
    second = tmp_path / "second"

    exit_code = validation_command(
        [
            "reliability",
            "--repository",
            str(first),
            "--repo",
            str(second),
            "--duration",
            "12.5",
            "--runs",
            "4",
            "--parallelism",
            "2",
            "--repository-files",
            "16",
            "--seed",
            "32",
            "--json",
        ]
    )

    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert payload["status"] == "PASS"
    assert payload["report_path"] is None
    assert captured == {
        "repository_paths": [str(first), str(second)],
        "duration_seconds": 12.5,
        "runs": 4,
        "parallelism": 2,
        "repository_files": 16,
        "seed": 32,
    }


def test_reliability_cli_returns_failure_exit_for_failed_scorecard(
    monkeypatch,
    capsys,
):
    monkeypatch.setattr(
        commands,
        "run_reliability_validation",
        lambda **_kwargs: _ScorecardStub(ok=False),
    )

    exit_code = validation_command(["reliability", "--runs", "2", "--json"])

    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 1
    assert payload["status"] == "FAIL"
    assert payload["failures"]


def test_malformed_manifest_error_does_not_echo_payload(tmp_path, capsys):
    manifest = tmp_path / "unsafe.json"
    manifest.write_text(
        '{"schema_version": 1, "api_key": "super-sensitive-value"',
        encoding="utf-8",
    )

    exit_code = validation_command(
        ["corpus", "--manifest", str(manifest), "--json"]
    )

    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 2
    assert payload["ok"] is False
    assert payload["network_used"] is False
    assert payload["error_type"] == "ManifestValidationError"
    assert "super-sensitive-value" not in json.dumps(payload)


def test_download_without_explicit_cache_fails_before_network(capsys):
    exit_code = validation_command(["corpus", "--download-public", "--json"])

    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 2
    assert payload == {
        "error": "validation setup failed (ValueError)",
        "error_type": "RuntimeError",
        "network_used": False,
        "ok": False,
    }


def test_text_report_records_network_mode_and_corpus_warnings():
    report = ValidationReport(
        status=ValidationStatus.PASS_WITH_WARNINGS,
        generated_at=datetime.now(UTC),
        offline=False,
        network_used=True,
        warnings=("Optional checkout was unavailable.",),
    )

    rendered = render_validation_report(report)

    assert "network used: yes" in rendered
    assert "warning: Optional checkout was unavailable." in rendered
