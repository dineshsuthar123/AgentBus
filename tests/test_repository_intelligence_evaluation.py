from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from agentbus.eval import main as evaluation_main
from agentbus.evaluation.fixtures import OWNERSHIP_FILE
from agentbus.evaluation.runner import EvaluationRunner
from agentbus.evaluation.suites import builtin_suites, builtin_variants


FIXTURE_ROOT = (
    Path(__file__).parents[1] / "agentbus" / "evaluation" / "fixtures_data"
)


class _UnexpectedProviderBackend:
    def execute(self, *args, **kwargs):
        raise AssertionError("normal agent/provider backend must not run")


def test_repository_intelligence_suite_and_variant_are_offline() -> None:
    suite = builtin_suites()["repository-intelligence"]
    variant = builtin_variants()["deterministic"]

    assert suite.default_variant == variant.variant_id
    assert suite.metadata["repository_intelligence_backend"] is True
    assert {case.case_id for case in suite.cases} == {
        "graph-retrieval-impact",
        "incremental-staleness-and-rename",
        "large-repository-budget",
        "multilingual-index-correctness",
    }
    assert {"ci", "offline", "repository-intelligence"} <= suite.tags
    assert variant.provider == "fake"
    assert variant.live is False
    assert variant.durable is False
    assert variant.metadata["providerless"] is True


def test_repository_intelligence_evaluation_uses_real_providerless_service(
    tmp_path: Path,
) -> None:
    source_before = _tree_fingerprint(FIXTURE_ROOT)
    fixture_root = tmp_path / "owned-fixtures"
    runner = EvaluationRunner(
        results_dir=tmp_path / "results",
        owned_fixture_root=fixture_root,
        offline_backend=_UnexpectedProviderBackend(),
    )

    run = runner.run(
        "repository-intelligence",
        variant_id="deterministic",
        max_requests=1,
        max_tokens=1,
        timeout_seconds=180,
    )

    assert run.passed is True
    assert len(run.case_results) == 4
    assert run.aggregate_metrics.provider.requests == 0
    assert run.aggregate_metrics.provider.total_tokens == 0
    assert source_before == _tree_fingerprint(FIXTURE_ROOT)
    assert not list(fixture_root.rglob(OWNERSHIP_FILE))

    by_case = {item.case_id: item for item in run.case_results}
    multilingual = _metrics(by_case["multilingual-index-correctness"])
    planning = _metrics(by_case["graph-retrieval-impact"])
    incremental = _metrics(by_case["incremental-staleness-and-rename"])
    large = _metrics(by_case["large-repository-budget"])

    assert multilingual["indexed_files"] == 17
    assert multilingual["indexing_correctness"] == 1
    assert multilingual["symbol_precision"] == pytest.approx(8 / 9)
    assert multilingual["reference_precision"] == 1
    assert multilingual["protected_generated_exclusion"] == 1
    assert planning["retrieval_precision"] == 1
    assert planning["impact_recall"] == 1
    assert planning["test_impact_recall"] == 1
    assert planning["context_budget_adherence"] == 1
    assert incremental["stale_index_detected"] is True
    assert incremental["incremental_invalidation_correctness"] == 1
    assert 0 < incremental["incremental_reuse_ratio"] < 1
    assert large["indexed_files"] == 132
    assert large["retrieval_precision"] == 1
    assert large["context_budget_adherence"] == 1

    planning_context = by_case["graph-retrieval-impact"].raw_metrics[
        "details"
    ]["context_plan"]
    assert planning_context["candidate_count"] == 26
    assert planning_context["selected_candidate_count"] == 11
    assert planning_context["exclusion_counts"] == {
        "duplicate_content": 15,
    }
    assert planning_context["selected_source_hash_duplicates"] == 0
    assert planning_context["stale_warning_present"] is True

    large_context = by_case["large-repository-budget"].raw_metrics[
        "details"
    ]["context_plan"]
    assert large_context["candidate_count"] == 61
    assert large_context["selected_candidate_count"] == 42
    assert large_context["exclusion_counts"] == {
        "budget_exceeded": 10,
        "duplicate_content": 9,
    }
    assert large_context["selected_bytes"] == 3901
    assert large_context["selected_tokens"] == 996
    assert large_context["selected_source_hash_duplicates"] == 0
    assert large_context["stale_warning_present"] is False

    search_details = by_case["graph-retrieval-impact"].raw_metrics[
        "details"
    ]["search_probes"]
    positive_categories = {
        "architecture_boundary",
        "configuration",
        "dependency_related_symbol",
        "endpoint",
        "exact_identifier",
        "fuzzy_identifier",
        "implementation_location",
        "test_location",
    }
    assert set(search_details) == {
        *positive_categories,
        "protected_file_exclusion",
    }
    for category in positive_categories:
        detail = search_details[category]
        assert detail["precision_at_k"] == 1
        assert detail["required_paths_present"] is True
        assert detail["expected_components_present"] is True
        assert detail["explanations_correct"] is True
        assert detail["stale_state_correct"] is True
    protected = search_details["protected_file_exclusion"]
    assert protected["expected_empty"] is True
    assert protected["result_paths"] == []
    assert "synthetic-fixture" in planning["interpretation_note"]
    assert "statistical significance" in planning["interpretation_note"]

    for result in run.case_results:
        metrics = _metrics(result)
        assert metrics["provider_calls"] == 0
        assert metrics["network_calls"] == 0
        assert result.artifacts[0].identifier == f"fixture:{result.case_id}"
        serialized = json.dumps(result.raw_metrics, sort_keys=True)
        assert str(tmp_path) not in serialized
        assert "source_content" not in serialized
        assert "api_key" not in serialized.casefold()
    assert str(tmp_path) not in run.model_dump_json()


def test_repository_intelligence_cli_renders_bounded_metrics(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    monkeypatch.setenv("AGENTBUS_EVAL_FIXTURE_ROOT", str(tmp_path / "fixtures"))

    exit_code = evaluation_main(
        [
            "--results-dir",
            str(tmp_path / "results"),
            "run",
            "--suite",
            "repository-intelligence",
            "--variant",
            "deterministic",
            "--case",
            "multilingual-index-correctness",
        ]
    )
    output = capsys.readouterr().out

    assert exit_code == 0
    assert "Cases: 1/1 passed" in output
    assert "Intelligence multilingual-index-correctness" in output
    assert "provider/network=0/0" in output
    assert "do not establish statistical significance" in output


def _metrics(result) -> dict:
    metrics = result.raw_metrics["repository_intelligence"]
    assert isinstance(metrics, dict)
    return metrics


def _tree_fingerprint(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(
        (item for item in root.rglob("*") if item.is_file()),
        key=lambda item: item.relative_to(root).as_posix(),
    ):
        digest.update(path.relative_to(root).as_posix().encode("utf-8"))
        digest.update(b"\0")
        digest.update(hashlib.sha256(path.read_bytes()).digest())
    return digest.hexdigest()
