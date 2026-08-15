from __future__ import annotations

import json

from agentbus.cli import main
from agentbus.product.benchmark import (
    BENCHMARK_SCHEMA_VERSION,
    run_benchmark,
    write_benchmark_report,
)


def test_startup_tools_and_replay_benchmarks_are_offline_and_budgeted():
    startup = run_benchmark("startup", iterations=2)
    replay = run_benchmark("replay", iterations=2)
    tools = run_benchmark("tools", file_count=10, iterations=2)

    for report in (startup, replay, tools):
        payload = report.to_dict()
        assert payload["network_used"] is False
        assert payload["environment_fingerprint"]
        assert payload["operations"]
        for operation in payload["operations"]:
            assert operation["operation_count"] == 2
            assert operation["median_ms"] is not None
            assert operation["p95_ms"] is not None
            assert operation["max_ms"] is not None
            assert operation["budget_ms"] >= operation["max_ms"]
            assert operation["budget_passed"] is True


def test_index_and_search_benchmark_uses_generated_repository():
    report = run_benchmark("search", file_count=20, iterations=2, seed=42)
    payload = report.to_dict()

    assert payload["ok"] is True
    assert payload["repository"]["file_count"] == 20
    assert payload["repository"]["byte_count"] > 0
    assert payload["repository"]["fingerprint"]
    assert payload["peak_memory_bytes"] > 0
    assert payload["memory_budget_passed"] is True
    assert payload["persistent_storage_bytes"] > 0
    assert payload["budget_policy"] == "broad-regression-v1"
    assert [item["name"] for item in payload["operations"]] == [
        "initial_index",
        "lexical_search",
        "graph_traversal",
        "context_planning",
    ]


def test_environment_fingerprint_is_stable_for_same_machine():
    first = run_benchmark("startup", iterations=1)
    second = run_benchmark("startup", iterations=1)

    assert first.environment == second.environment
    assert first.environment_fingerprint == second.environment_fingerprint


def test_control_benchmark_measures_app_and_protocol_readiness():
    report = run_benchmark("control", iterations=1)

    operations = report.to_dict()["operations"]
    if operations[0]["status"] == "skipped":
        assert "ide" in operations[0]["detail"]
        assert report.daemon_peak_memory_bytes is None
    else:
        assert [item["name"] for item in operations] == [
            "daemon_app_startup",
            "protocol_readiness",
        ]
        assert all(item["budget_passed"] is True for item in operations)
        assert report.daemon_peak_memory_bytes is not None
        assert report.daemon_peak_memory_bytes > 0


def test_benchmark_report_write_is_atomic_and_machine_readable(tmp_path):
    report = run_benchmark("tools", file_count=5, iterations=1)
    output = write_benchmark_report(report, tmp_path / "benchmark.json")

    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["schema_version"] == BENCHMARK_SCHEMA_VERSION
    assert payload["selected_group"] == "tools"
    assert payload["network_used"] is False
    assert payload["provider_calls"] == 0
    assert not list(tmp_path.glob("*.tmp"))


def test_benchmark_cli_reports_offline_metrics_and_output(tmp_path, capsys):
    output = tmp_path / "startup-benchmark.json"

    exit_code = main(
        [
            "benchmark",
            "startup",
            "--iterations",
            "1",
            "--output",
            str(output),
            "--json",
        ]
    )

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["selected_group"] == "startup"
    assert payload["operations"][0]["operation_count"] == 1
    assert payload["network_used"] is False
    assert payload["report_path"] == str(output)
    assert output.is_file()
