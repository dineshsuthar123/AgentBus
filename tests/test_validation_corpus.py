from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
from pydantic import ValidationError

from agentbus.git.repository import GitRepository
from agentbus.validation.corpus import (
    MAXIMUM_MANIFEST_BYTES,
    download_public_repository,
    generate_validation_repository,
    load_corpus_manifest,
    resolve_repository_checkout,
    run_validation_corpus,
)
from agentbus.validation.failures import (
    ManifestValidationError,
    RepositoryValidationError,
)
from agentbus.validation.models import (
    RepositorySource,
    ValidationRepository,
    ValidationStatus,
)


def test_bundled_corpus_covers_generated_and_real_world_repository_shapes():
    manifest = load_corpus_manifest()
    generated = [
        item for item in manifest.repositories if item.source == RepositorySource.GENERATED
    ]
    public = [
        item for item in manifest.repositories if item.source == RepositorySource.PUBLIC
    ]

    assert manifest.corpus_id == "agentbus-v07"
    assert {item.repository_id for item in generated} == {
        "generated-python-library",
        "generated-mixed-monorepo",
        "generated-deep-tree",
    }
    assert len(public) == 10
    assert all(item.remote_url.startswith("https://") for item in public)
    assert all(item.checkout_environment for item in public)
    assert all(item.enabled_by_default is False for item in public)
    assert all(item.scenarios for item in manifest.repositories)
    assert not any(item.path for item in public)


def test_generated_repository_is_deterministic_and_never_replaces_data(tmp_path):
    first = generate_validation_repository(
        tmp_path / "first",
        "generated-mixed-monorepo",
    )
    second = generate_validation_repository(
        tmp_path / "second",
        "generated-mixed-monorepo",
    )

    assert first.fingerprint == second.fingerprint
    assert first.file_count == second.file_count
    assert first.byte_count == second.byte_count
    assert (first.root / ".env").is_file()
    occupied = tmp_path / "occupied"
    occupied.mkdir()
    (occupied / "user.txt").write_text("preserve", encoding="utf-8")
    with pytest.raises(RepositoryValidationError):
        generate_validation_repository(occupied, "generated-python-library")
    assert (occupied / "user.txt").read_text(encoding="utf-8") == "preserve"


def test_offline_corpus_runs_only_generated_defaults_without_network():
    report = run_validation_corpus(offline=True)

    assert report.status in {
        ValidationStatus.PASS,
        ValidationStatus.PASS_WITH_WARNINGS,
    }
    assert len(report.runs) == 3
    assert all(run.status != ValidationStatus.FAIL for run in report.runs)
    assert report.offline is True
    assert report.network_used is False


def test_local_checkout_can_be_supplied_by_explicit_environment_mapping(tmp_path):
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    repository = ValidationRepository(
        repository_id="local-checkout",
        title="Local checkout",
        source=RepositorySource.LOCAL,
        checkout_environment="AGENTBUS_TEST_CHECKOUT",
    )

    resolved = resolve_repository_checkout(
        repository,
        environ={"AGENTBUS_TEST_CHECKOUT": str(checkout)},
    )

    assert resolved == checkout.resolve()
    assert resolve_repository_checkout(repository, environ={}) is None


def test_public_repository_metadata_rejects_credentials_and_option_revisions():
    with pytest.raises(ValidationError):
        ValidationRepository(
            repository_id="credential-url",
            title="Unsafe URL",
            source=RepositorySource.PUBLIC,
            remote_url="https://token@example.com/repository.git",
        )
    with pytest.raises(ValidationError):
        ValidationRepository(
            repository_id="unsafe-revision",
            title="Unsafe revision",
            source=RepositorySource.PUBLIC,
            remote_url="https://github.com/example/repository.git",
            revision="--upload-pack=malicious",
        )


def test_manifest_loader_bounds_and_redacts_malformed_inputs(tmp_path):
    oversized = tmp_path / "oversized.json"
    oversized.write_bytes(b"{" + b"x" * MAXIMUM_MANIFEST_BYTES + b"}")
    with pytest.raises(ManifestValidationError, match="1 MiB"):
        load_corpus_manifest(oversized)

    malformed = tmp_path / "malformed.json"
    malformed.write_text('{"schema_version": 1, "secret": "do-not-echo"', encoding="utf-8")
    with pytest.raises(ManifestValidationError) as captured:
        load_corpus_manifest(malformed)
    assert "do-not-echo" not in str(captured.value)


def test_optional_public_corpus_is_skipped_offline_when_checkouts_are_absent():
    report = run_validation_corpus(
        offline=True,
        include_optional=True,
        environ={},
    )

    assert len(report.runs) == 3
    assert report.status == ValidationStatus.PASS_WITH_WARNINGS
    assert len(report.warnings) == 10
    assert all("Skipped unavailable optional checkout" in item for item in report.warnings)


def test_public_download_is_explicit_shell_free_and_preserves_partial_state(
    tmp_path,
    monkeypatch,
):
    repository = ValidationRepository(
        repository_id="public-fixture",
        title="Public fixture",
        source=RepositorySource.PUBLIC,
        remote_url="https://github.com/example/repository.git",
        revision="main",
    )
    captured = {}

    def fake_runner(command, **kwargs):
        captured["command"] = tuple(command)
        captured["kwargs"] = kwargs
        Path(command[-1]).mkdir()
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(
        GitRepository,
        "validate_workspace",
        lambda self: self.workspace,
    )
    monkeypatch.setattr(
        GitRepository,
        "head_commit",
        lambda self, short=False: "a" * 40,
    )

    result = download_public_repository(
        repository,
        tmp_path / "checkout",
        runner=fake_runner,
    )

    assert result.commit_sha == "a" * 40
    assert captured["kwargs"]["shell"] is False
    assert captured["kwargs"]["check"] is False
    assert captured["kwargs"]["env"]["GIT_TERMINAL_PROMPT"] == "0"
    assert "--no-recurse-submodules" in captured["command"]
    assert captured["command"][-2:] == (
        repository.remote_url,
        str(result.root),
    )

    (result.root / "partial.txt").write_text("preserve", encoding="utf-8")
    with pytest.raises(RepositoryValidationError, match="preserved"):
        download_public_repository(repository, result.root, runner=fake_runner)
    assert (result.root / "partial.txt").is_file()
