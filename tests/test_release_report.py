import json
import subprocess
from pathlib import Path

from agentbus.evaluation.models import (
    EvaluationCaseResult,
    EvaluationMetrics,
    EvaluationRun,
    EvaluationScore,
    EvaluationVariant,
)
from agentbus.evaluation.storage import EvaluationStorage
from agentbus.release_report import (
    ReleaseStatus,
    build_release_report,
    main,
    render_markdown,
)


ROOT = Path(__file__).resolve().parents[1]


def _repository(path: Path) -> Path:
    path.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=path, check=True, shell=False)
    subprocess.run(
        ["git", "config", "user.name", "AgentBus Tests"], cwd=path, check=True, shell=False
    )
    subprocess.run(
        ["git", "config", "user.email", "tests@agentbus.invalid"],
        cwd=path,
        check=True,
        shell=False,
    )
    (path / "README.md").write_text("# release\n", encoding="utf-8")
    subprocess.run(["git", "add", "README.md"], cwd=path, check=True, shell=False)
    subprocess.run(
        ["git", "commit", "-q", "-m", "baseline"], cwd=path, check=True, shell=False
    )
    return path


def _evaluation(storage: EvaluationStorage) -> str:
    metrics = EvaluationMetrics()
    metrics.quality.success = True
    case = EvaluationCaseResult(
        case_id="case",
        title="Case",
        passed=True,
        run_status="succeeded",
        score=EvaluationScore(total=100),
        metrics=metrics,
    )
    run = EvaluationRun(
        evaluation_run_id="release-run",
        suite_id="release-offline",
        variant=EvaluationVariant(
            variant_id="durable-parallel-fake",
            title="Fake",
            provider="fake",
            durable=True,
            parallel=True,
            max_workers=2,
        ),
        status="completed",
        agentbus_commit_sha="abc",
        configuration_fingerprint="fingerprint",
        case_results=[case],
        aggregate_metrics=metrics,
        aggregate_score=100,
        passed=True,
    )
    storage.save_run(run)
    return run.evaluation_run_id


def test_release_report_marks_missing_checks_not_run(tmp_path):
    repository = _repository(tmp_path / "repository")
    report = build_release_report(
        repository=repository,
        workspace=repository,
        results_dir=tmp_path / "results",
        dist_dir=tmp_path / "dist",
    )
    checks = {check.name: check for check in report.checks}

    assert checks["tests"].status == ReleaseStatus.NOT_RUN
    assert checks["offline-evaluation"].status == ReleaseStatus.NOT_RUN
    assert checks["package-build"].status == ReleaseStatus.NOT_RUN
    assert checks["installation"].status == ReleaseStatus.NOT_RUN
    assert checks["live-evaluation"].status == ReleaseStatus.NOT_RUN
    assert report.ready is False
    assert "NOT_RUN" in render_markdown(report)


def test_release_report_uses_actual_evidence_and_redacts_sensitive_paths(tmp_path):
    repository = _repository(tmp_path / "repository")
    (repository / ".env").write_text("API_KEY=never-print-this\n", encoding="utf-8")
    subprocess.run(
        ["git", "add", "-f", ".env"], cwd=repository, check=True, shell=False
    )
    storage = EvaluationStorage(tmp_path / "results")
    run_id = _evaluation(storage)
    tests = tmp_path / "tests.json"
    tests.write_text(
        json.dumps({"status": "PASS", "summary": "400 tests passed"}),
        encoding="utf-8",
    )
    install = tmp_path / "install.json"
    install.write_text(
        json.dumps({"status": "PASS", "summary": "fresh wheel smoke passed"}),
        encoding="utf-8",
    )

    report = build_release_report(
        repository=repository,
        workspace=repository,
        results_dir=storage.root,
        offline_run_id=run_id,
        test_evidence=tests,
        install_evidence=install,
        dist_dir=tmp_path / "dist",
    )
    checks = {check.name: check for check in report.checks}
    payload = json.dumps(report.to_dict())

    assert checks["tests"].status == ReleaseStatus.PASS
    assert checks["offline-evaluation"].status == ReleaseStatus.PASS
    assert checks["sensitive-files"].status == ReleaseStatus.FAIL
    assert "never-print-this" not in payload


def test_release_report_cli_writes_markdown_and_json(tmp_path, capsys):
    markdown = tmp_path / "report.md"
    json_output = tmp_path / "report.json"

    assert (
        main(
            [
                "--repository",
                str(ROOT),
                "--workspace",
                str(ROOT),
                "--results-dir",
                str(tmp_path / "results"),
                "--dist-dir",
                str(tmp_path / "dist"),
                "--markdown-output",
                str(markdown),
                "--json-output",
                str(json_output),
                "--json",
            ]
        )
        == 0
    )
    payload = json.loads(json_output.read_text(encoding="utf-8"))
    assert payload["version"] == "0.6.0b1"
    assert payload["ready"] is False
    assert markdown.read_text(encoding="utf-8").startswith("# AgentBus")
    assert json.loads(capsys.readouterr().out)["version"] == "0.6.0b1"
