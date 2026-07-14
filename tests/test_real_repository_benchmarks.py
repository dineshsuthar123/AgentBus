import json
import os
import subprocess
from pathlib import Path

import pytest
from pydantic import ValidationError

from agentbus import eval as eval_cli
from agentbus.evaluation.benchmarks import (
    RealRepositoryBenchmark,
    RealRepositoryManager,
    RealRepositoryManifest,
    load_manifest,
    suite_from_manifest,
)
from agentbus.evaluation.errors import EvaluationConfigurationError
from agentbus.evaluation.suites import builtin_suites


def _benchmark_data():
    return load_manifest().repositories[0].model_dump(mode="json")


def test_builtin_manifest_is_exact_sha_license_reviewed_and_bounded():
    manifest = load_manifest()
    benchmark = manifest.repositories[0]

    assert benchmark.repository_url == "https://github.com/pallets/click.git"
    assert benchmark.commit_sha == "934813e4d421071a1b3db3973c02fe2721359a6e"
    assert len(benchmark.commit_sha) == 40
    assert benchmark.spdx_license == "BSD-3-Clause"
    assert benchmark.license_review.status == "approved"
    assert benchmark.setup_command == []
    assert benchmark.budget.max_requests == 16


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("commit_sha", "main", "exact 40-character"),
        ("commit_sha", "v8.1.8", "exact 40-character"),
        ("spdx_license", "GPL-3.0", "not approved"),
        ("repository_url", "git@github.com:pallets/click.git", "HTTPS"),
    ],
)
def test_manifest_rejects_mutable_refs_licenses_and_unsafe_urls(field, value, message):
    data = _benchmark_data()
    data[field] = value

    with pytest.raises(ValidationError, match=message):
        RealRepositoryBenchmark.model_validate(data)


def test_manifest_rejects_unreviewed_or_shell_setup_commands():
    data = _benchmark_data()
    data["setup_command"] = ["python", "-m", "pip", "install", "."]

    with pytest.raises(ValidationError, match="setup_reviewed"):
        RealRepositoryBenchmark.model_validate(data)

    data["setup_reviewed"] = True
    data["setup_command"] = ["bash", "-c", "echo unsafe"]
    with pytest.raises(ValidationError, match="shell interpreters"):
        RealRepositoryBenchmark.model_validate(data)


def test_manifest_requires_unique_ids():
    data = _benchmark_data()
    with pytest.raises(ValidationError, match="unique"):
        RealRepositoryManifest.model_validate(
            {"manifest_version": 1, "repositories": [data, data]}
        )


def test_real_repository_clone_uses_shell_false_and_verifies_remote_and_sha(
    tmp_path, monkeypatch
):
    benchmark = load_manifest().repositories[0]
    calls = []
    monkeypatch.setenv("AZURE_OPENAI_API_KEY", "must-not-reach-git")

    def fake_run(arguments, **kwargs):
        calls.append((arguments, kwargs))
        assert kwargs["shell"] is False
        if arguments[1] == "clone":
            Path(arguments[-1]).mkdir()
            stdout = ""
        elif arguments[1:4] == ["remote", "get-url", "origin"]:
            stdout = benchmark.repository_url + "\n"
        elif arguments[1:3] == ["rev-parse", "HEAD"]:
            stdout = benchmark.commit_sha + "\n"
        else:
            stdout = ""
        return subprocess.CompletedProcess(arguments, 0, stdout=stdout, stderr="")

    monkeypatch.setattr("agentbus.evaluation.benchmarks.subprocess.run", fake_run)
    manager = RealRepositoryManager(tmp_path / "owned")
    source = manager.clone(benchmark)

    assert source.is_dir()
    assert any(call[0][1] == "clone" for call in calls)
    assert any("--detach" in call[0] for call in calls)
    assert all(call[1]["shell"] is False for call in calls)
    assert all(call[1]["env"]["GIT_CONFIG_GLOBAL"] == os.devnull for call in calls)
    assert all("AZURE_OPENAI_API_KEY" not in call[1]["env"] for call in calls)
    manager.cleanup()
    assert not manager.session_root.exists()


def test_setup_is_never_automatic_and_requires_explicit_consent(tmp_path):
    data = _benchmark_data()
    data["setup_command"] = ["python", "-m", "pip", "install", "--no-deps", "."]
    data["setup_reviewed"] = True
    benchmark = RealRepositoryBenchmark.model_validate(data)
    manager = RealRepositoryManager(tmp_path / "owned")
    workspace = manager.session_root / "source"
    workspace.mkdir()

    with pytest.raises(EvaluationConfigurationError, match="disabled"):
        manager.run_reviewed_setup(workspace, benchmark)
    manager.cleanup()


def test_real_suite_requires_explicit_download_before_any_clone(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(
        "agentbus.evaluation.benchmarks.RealRepositoryManager.clone",
        lambda *args, **kwargs: pytest.fail("clone must require explicit consent"),
    )

    assert (
        eval_cli.main(
            [
                "--results-dir",
                str(tmp_path / "results"),
                "run",
                "--suite",
                "real-repos",
                "--variant",
                "durable-azure",
                "--live",
            ]
        )
        == 2
    )
    assert "--allow-repository-download" in capsys.readouterr().out


def test_real_suite_requires_live_consent_before_manager_creation(
    tmp_path,
    monkeypatch,
    capsys,
):
    monkeypatch.setattr(
        "agentbus.evaluation.benchmarks.RealRepositoryManager.__init__",
        lambda *args, **kwargs: pytest.fail("manager creation must require live consent"),
    )

    assert (
        eval_cli.main(
            [
                "--results-dir",
                str(tmp_path / "results"),
                "run",
                "--suite",
                "real-repos",
                "--variant",
                "durable-azure",
                "--allow-repository-download",
            ]
        )
        == 2
    )
    assert "explicit --live" in capsys.readouterr().out


def test_dynamic_real_suite_reports_provenance_and_forces_worktrees(tmp_path):
    manifest = load_manifest()
    source = tmp_path / "source"
    source.mkdir()
    suite = suite_from_manifest(
        manifest,
        {manifest.repositories[0].benchmark_id: source},
    )
    case = suite.cases[0]

    assert case.parallel_mode is True
    assert case.metadata["expect_source_unchanged"] is True
    assert case.metadata["benchmark_provenance"]["spdx_license"] == "BSD-3-Clause"
    assert "real-repository" in suite.tags


def test_release_suites_are_offline_complete_and_live_minimal():
    suites = builtin_suites()

    assert len(suites["release-offline"].cases) == 9
    assert suites["release-offline"].metadata["release_surface_checks"] is True
    assert len(suites["release-azure-smoke"].cases) == 1
    assert suites["release-azure-smoke"].metadata["fallback_required"] is False
