from __future__ import annotations

import json
from pathlib import Path

import pytest

from agentbus.cli import main
from agentbus.product.benchmark import BENCHMARK_GROUPS, write_benchmark_report
from agentbus.product.index_scale import (
    INDEX_SCALE_GROUP,
    run_index_scale_benchmark,
)
from agentbus.product.synthetic import SYNTHETIC_SIZES


_SCENARIO_NAMES = [
    "full_index",
    "one_file_incremental",
    "hundred_file_update",
    "rename_storm",
    "delete_storm",
    "parser_version_invalidation",
    "configuration_invalidation",
    "watcher_overflow_recovery",
    "cancellation",
    "restart_recovery",
]


def test_index_scale_runner_exercises_bounded_recovery_matrix(
    tmp_path: Path,
) -> None:
    report = run_index_scale_benchmark(
        profile="small",
        file_count=104,
        seed=42,
        temporary_parent=tmp_path,
    )
    payload = report.to_dict()
    operations = {operation.name: operation for operation in report.operations}

    assert report.passed is True
    assert payload["selected_group"] == INDEX_SCALE_GROUP
    assert payload["iterations"] == 1
    assert payload["repository"] == {
        "profile": "small",
        "file_count": 104,
        "byte_count": report.repository_bytes,
        "fingerprint": report.repository_fingerprint,
        "generated": True,
        "retained": False,
    }
    assert [operation.name for operation in report.operations] == _SCENARIO_NAMES
    assert operations["full_index"].indexed_files == 104
    assert operations["one_file_incremental"].indexed_files == 1
    assert operations["hundred_file_update"].changed_files == 100
    assert operations["hundred_file_update"].indexed_files == 100
    assert operations["rename_storm"].renamed_files == 10
    assert operations["delete_storm"].deleted_files == 10
    assert operations["parser_version_invalidation"].indexed_files == 94
    assert operations["configuration_invalidation"].indexed_files == 94
    assert operations["watcher_overflow_recovery"].indexed_files == 1
    assert operations["cancellation"].snapshot_state == "paused"
    assert operations["restart_recovery"].snapshot_state == "current"
    assert report.final_file_count == 94
    assert report.final_snapshot_state == "current"
    assert report.database_bytes > 0
    assert payload["provider_calls"] == 0
    assert payload["network_calls"] == 0
    assert payload["network_used"] is False
    assert all(operation.passed for operation in report.operations)
    assert all(
        operation.unnecessarily_reindexed_files == 0
        and operation.invalidation_efficiency == 1.0
        for operation in report.operations
    )
    if payload["peak_memory_measured"]:
        assert report.peak_memory_bytes is not None
        assert report.peak_memory_bytes > 0

    serialized = json.dumps(payload, sort_keys=True)
    assert str(tmp_path) not in serialized
    assert list(tmp_path.iterdir()) == []

    output = write_benchmark_report(report, tmp_path / "index-scale.json")
    persisted = json.loads(output.read_text(encoding="utf-8"))
    assert persisted["ok"] is True
    assert persisted["repository"]["retained"] is False
    assert not list(tmp_path.glob("*.tmp"))


def test_index_scale_profiles_match_manual_repository_dimensions() -> None:
    assert SYNTHETIC_SIZES == {
        "small": 100,
        "medium": 1_000,
        "large": 10_000,
        "very-large": 50_000,
    }
    assert INDEX_SCALE_GROUP not in BENCHMARK_GROUPS


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"profile": "unknown"}, "profile must be one of"),
        ({"file_count": 3}, "at least 4"),
        ({"file_count": "10"}, "at least 4"),
        ({"file_count": 50_001}, "between 1 and 50000"),
    ],
)
def test_index_scale_rejects_unbounded_or_unsupported_profiles(
    kwargs: dict[str, object],
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        run_index_scale_benchmark(**kwargs)  # type: ignore[arg-type]


def test_index_scale_cli_defaults_to_medium_without_joining_all(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    calls: list[dict[str, object]] = []

    class _Report:
        operations: tuple[object, ...] = ()
        passed = True

        def to_dict(self) -> dict[str, object]:
            return {
                "ok": True,
                "selected_group": INDEX_SCALE_GROUP,
                "iterations": 1,
                "repository": {
                    "profile": "medium",
                    "file_count": 1_000,
                    "generated": True,
                    "retained": False,
                },
                "network_used": False,
            }

    def fake_run(**kwargs: object) -> _Report:
        calls.append(kwargs)
        return _Report()

    monkeypatch.setattr(
        "agentbus.product.index_scale.run_index_scale_benchmark",
        fake_run,
    )

    assert main(["benchmark", "index-scale", "--json"]) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["selected_group"] == INDEX_SCALE_GROUP
    assert calls == [
        {
            "profile": "medium",
            "file_count": None,
            "seed": 2026,
        }
    ]


def test_index_scale_cli_rejects_repeated_stateful_lifecycle(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(
        "agentbus.product.index_scale.run_index_scale_benchmark",
        lambda **_kwargs: pytest.fail("invalid iteration count must fail first"),
    )

    assert (
        main(
            [
                "benchmark",
                "index-scale",
                "--iterations",
                "2",
                "--json",
            ]
        )
        == 2
    )
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False
    assert "stateful lifecycle" in payload["error"]


def test_legacy_benchmark_cli_preserves_zero_iteration_validation(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert (
        main(
            [
                "benchmark",
                "startup",
                "--iterations",
                "0",
                "--json",
            ]
        )
        == 2
    )
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False
    assert "between 1 and 50" in payload["error"]
