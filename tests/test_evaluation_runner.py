from pathlib import Path

from agentbus.evaluation.assertions import RuntimeObservation
from agentbus.evaluation.models import (
    EvaluationCase,
    EvaluationMetrics,
    EvaluationSuite,
    EvaluationVariant,
    RunStatus,
)
from agentbus.evaluation.runner import BackendResult, EvaluationRunner
from agentbus.evaluation.storage import EvaluationStorage


class StubBackend:
    def __init__(self, failing_cases=()):
        self.failing_cases = set(failing_cases)
        self.repositories = []

    def execute(self, case, variant, fixture, budget):
        self.repositories.append(fixture.repository)
        failed = case.case_id in self.failing_cases
        (fixture.repository / "result.txt").write_text(
            "bad\n" if failed else "ok\n", encoding="utf-8"
        )
        return BackendResult(
            observation=RuntimeObservation(
                repository=fixture.repository,
                run_status=(RunStatus.FAILED if failed else RunStatus.SUCCEEDED).value,
                verifier_passed=not failed,
                reviewer_approved=not failed,
                changed_files=["result.txt"],
                relevant_changed_files=["result.txt"],
                source_unchanged=True,
                sanitized_diagnostic_text="api_key=[REDACTED]",
            ),
            metrics=EvaluationMetrics(),
            runtime_run_id=f"runtime-{case.case_id}",
            failure_category="stub_failure" if failed else None,
            failure_message="expected stub failure" if failed else None,
            raw_metrics={"authorization": "real-secret"},
        )


def make_runner(tmp_path, *, backend=None, case_count=2):
    fixture_root = tmp_path / "sources"
    source = fixture_root / "fixture"
    source.mkdir(parents=True)
    (source / "seed.txt").write_text("unchanged\n", encoding="utf-8")
    cases = []
    for index in range(case_count):
        case_id = f"case-{index}"
        cases.append(
            EvaluationCase(
                case_id=case_id,
                title=case_id,
                task_prompt="write result",
                fixture_repository_source="fixture",
                expected_files=["result.txt"],
                expected_run_status=(
                    RunStatus.FAILED if case_id == "case-1" else RunStatus.SUCCEEDED
                ),
                expected_verifier_passed=case_id != "case-1",
                expected_reviewer_approved=case_id != "case-1",
                metadata={
                    "expected_changed_files": ["result.txt"],
                    "expected_relevant_files": ["result.txt"],
                    "expect_source_unchanged": True,
                },
            )
        )
    suite = EvaluationSuite(
        suite_id="test-suite",
        title="Test",
        description="runner tests",
        cases=cases,
        default_variant="fake",
    )
    variant = EvaluationVariant(variant_id="fake", title="Fake", provider="fake")
    storage = EvaluationStorage(tmp_path / "results")
    selected_backend = backend or StubBackend({"case-1"})
    runner = EvaluationRunner(
        storage=storage,
        fixture_root=fixture_root,
        owned_fixture_root=tmp_path / "owned",
        suites={suite.suite_id: suite},
        variants={variant.variant_id: variant},
        offline_backend=selected_backend,
    )
    return runner, storage, selected_backend, source


def test_runner_executes_cases_in_fresh_repositories_and_persists_result(tmp_path):
    runner, storage, backend, source = make_runner(tmp_path)

    run = runner.run("test-suite")

    assert run.passed is True
    assert len(run.case_results) == 2
    assert backend.repositories[0] != backend.repositories[1]
    assert storage.load_run(run.evaluation_run_id) == run
    assert (source / "seed.txt").read_text(encoding="utf-8") == "unchanged\n"
    assert not (source / "result.txt").exists()
    assert not any((runner.fixture_manager.owned_root).rglob("repo"))


def test_runner_fail_fast_persists_partial_suite(tmp_path):
    runner, storage, backend, _ = make_runner(tmp_path)
    backend.failing_cases.add("case-0")

    run = runner.run("test-suite", fail_fast=True)

    assert run.partial is True
    assert run.status == "failed"
    assert len(run.case_results) == 1
    assert storage.load_run(run.evaluation_run_id).partial is True


def test_runner_filters_cases_and_tags(tmp_path):
    runner, _, _, _ = make_runner(tmp_path)
    runner.suites["test-suite"].cases[0] = runner.suites["test-suite"].cases[
        0
    ].model_copy(update={"tags": {"fast"}})

    by_case = runner.run("test-suite", case_ids={"case-0"})
    by_tag = runner.run("test-suite", tags={"fast"})

    assert [item.case_id for item in by_case.case_results] == ["case-0"]
    assert [item.case_id for item in by_tag.case_results] == ["case-0"]


def test_runner_preserves_owned_fixture_only_when_requested(tmp_path):
    runner, _, _, _ = make_runner(tmp_path, case_count=1)

    run = runner.run("test-suite", preserve_fixtures=True)
    result = run.case_results[0]

    assert result.retained_fixture_path is not None
    assert Path(result.retained_fixture_path).is_dir()
    assert Path(result.retained_fixture_path).is_relative_to(
        runner.fixture_manager.owned_root
    )


def test_runner_sanitizes_backend_metadata_and_never_persists_prompt(tmp_path):
    runner, storage, _, _ = make_runner(tmp_path, case_count=1)

    run = runner.run("test-suite")
    payload = storage.load_run(run.evaluation_run_id).model_dump_json()

    assert "real-secret" not in payload
    assert "write result" not in payload
    assert run.metadata["prompt_content_persisted"] is False


def test_offline_runner_never_selects_live_backend(tmp_path):
    offline = StubBackend()

    class NetworkSentinel:
        def execute(self, *args, **kwargs):
            raise AssertionError("live backend must not be called")

    runner, _, _, _ = make_runner(tmp_path, backend=offline, case_count=1)
    runner.live_backend = NetworkSentinel()

    run = runner.run("test-suite")

    assert run.passed is True
    assert len(offline.repositories) == 1
