from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path
from typing import Any

from agentbus.product.benchmark import (
    BENCHMARK_SCHEMA_VERSION,
    BenchmarkReport,
)


PERFORMANCE_SCORECARD_SCHEMA_VERSION = 1
PERFORMANCE_THRESHOLD_POLICY = "broad-release-comparison-v1"
PERFORMANCE_REGRESSION_RATIO = 1.75
PERFORMANCE_IMPROVEMENT_RATIO = 0.60
_MAX_BASELINE_BYTES = 2 * 1024 * 1024
_MAX_OPERATIONS = 100


class PerformanceClassification(str, Enum):
    REGRESSION = "regression"
    IMPROVEMENT = "improvement"
    NEUTRAL = "neutral"


class PerformanceScorecardStatus(str, Enum):
    PASS = "pass"
    PASS_WITH_WARNINGS = "pass_with_warnings"
    FAIL = "fail"


class PerformanceMetricStatus(str, Enum):
    COMPARED = "compared"
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True)
class PerformanceMetricComparison:
    metric_id: str
    title: str
    unit: str
    status: PerformanceMetricStatus
    classification: PerformanceClassification | None
    baseline_value: float | None
    current_value: float | None
    ratio: float | None
    relative_change_percent: float | None
    regression_boundary: float | None
    improvement_boundary: float | None
    regression_ratio: float
    improvement_ratio: float
    absolute_tolerance: float
    observation: str

    @property
    def available(self) -> bool:
        return self.status == PerformanceMetricStatus.COMPARED

    def to_dict(self) -> dict[str, Any]:
        return {
            "metric_id": self.metric_id,
            "title": self.title,
            "unit": self.unit,
            "status": self.status.value,
            "available": self.available,
            "classification": (
                self.classification.value if self.classification is not None else None
            ),
            "baseline_value": _round(self.baseline_value),
            "current_value": _round(self.current_value),
            "ratio": _round(self.ratio),
            "relative_change_percent": _round(self.relative_change_percent),
            "regression_boundary": _round(self.regression_boundary),
            "improvement_boundary": _round(self.improvement_boundary),
            "regression_ratio": self.regression_ratio,
            "improvement_ratio": self.improvement_ratio,
            "absolute_tolerance": _round(self.absolute_tolerance),
            "observation": self.observation,
        }


@dataclass(frozen=True)
class PerformanceScorecard:
    generated_at: datetime
    classification: PerformanceClassification
    status: PerformanceScorecardStatus
    baseline_fingerprint: str
    current_fingerprint: str
    baseline_environment_fingerprint: str
    current_environment_fingerprint: str
    metrics: tuple[PerformanceMetricComparison, ...]
    warnings: tuple[str, ...] = ()
    offline: bool = True
    network_used: bool = False
    provider_calls: int = 0

    @property
    def ok(self) -> bool:
        return self.status != PerformanceScorecardStatus.FAIL

    @property
    def regressions(self) -> tuple[PerformanceMetricComparison, ...]:
        return tuple(
            metric
            for metric in self.metrics
            if metric.classification == PerformanceClassification.REGRESSION
        )

    def to_dict(self) -> dict[str, Any]:
        unavailable = sum(not metric.available for metric in self.metrics)
        improvements = sum(
            metric.classification == PerformanceClassification.IMPROVEMENT
            for metric in self.metrics
        )
        neutral = sum(
            metric.classification == PerformanceClassification.NEUTRAL
            for metric in self.metrics
        )
        return {
            "schema_version": PERFORMANCE_SCORECARD_SCHEMA_VERSION,
            "ok": self.ok,
            "status": self.status.value,
            "classification": self.classification.value,
            "generated_at": self.generated_at.isoformat(),
            "threshold_policy": PERFORMANCE_THRESHOLD_POLICY,
            "thresholds": {
                "regression_ratio": PERFORMANCE_REGRESSION_RATIO,
                "improvement_ratio": PERFORMANCE_IMPROVEMENT_RATIO,
                "rule": (
                    "A classification requires both the ratio boundary and the "
                    "metric-specific absolute tolerance."
                ),
            },
            "baseline_fingerprint": self.baseline_fingerprint,
            "current_fingerprint": self.current_fingerprint,
            "baseline_environment_fingerprint": (
                self.baseline_environment_fingerprint
            ),
            "current_environment_fingerprint": self.current_environment_fingerprint,
            "metric_count": len(self.metrics),
            "compared_metric_count": len(self.metrics) - unavailable,
            "unavailable_metric_count": unavailable,
            "regression_count": len(self.regressions),
            "improvement_count": improvements,
            "neutral_count": neutral,
            "warnings": list(self.warnings),
            "metrics": [metric.to_dict() for metric in self.metrics],
            "offline": self.offline,
            "network_used": self.network_used,
            "provider_calls": self.provider_calls,
            "opaque_numerical_score": None,
        }


@dataclass(frozen=True)
class _OperationSnapshot:
    name: str
    status: str
    median_ms: float | None


@dataclass(frozen=True)
class _BenchmarkSnapshot:
    selected_group: str
    iterations: int
    repository_profile: str
    repository_files: int
    repository_bytes: int
    repository_fingerprint: str | None
    environment: dict[str, Any]
    environment_fingerprint: str
    operations: tuple[_OperationSnapshot, ...]
    daemon_peak_memory_bytes: float | None
    persistent_storage_bytes: float | None
    fingerprint: str


@dataclass(frozen=True)
class _MetricSpec:
    metric_id: str
    title: str
    unit: str
    absolute_tolerance: float
    operation_name: str | None = None
    field_name: str | None = None


_METRICS = (
    _MetricSpec(
        "daemon_startup",
        "Daemon startup",
        "milliseconds",
        250.0,
        operation_name="daemon_app_startup",
    ),
    _MetricSpec(
        "initial_index",
        "Initial index",
        "milliseconds",
        250.0,
        operation_name="initial_index",
    ),
    _MetricSpec(
        "incremental_index",
        "Incremental index",
        "milliseconds",
        100.0,
        operation_name="incremental_index",
    ),
    _MetricSpec(
        "search",
        "Search",
        "milliseconds",
        25.0,
        operation_name="lexical_search",
    ),
    _MetricSpec(
        "graph_traversal",
        "Graph traversal",
        "milliseconds",
        25.0,
        operation_name="graph_traversal",
    ),
    _MetricSpec(
        "context_planning",
        "Context planning",
        "milliseconds",
        50.0,
        operation_name="context_planning",
    ),
    _MetricSpec(
        "deterministic_run",
        "Deterministic run",
        "milliseconds",
        10.0,
        operation_name="deterministic_run_startup",
    ),
    _MetricSpec(
        "tool_invocation",
        "Tool invocation",
        "milliseconds",
        10.0,
        operation_name="filesystem_tool_read",
    ),
    _MetricSpec(
        "replay",
        "Replay",
        "milliseconds",
        10.0,
        operation_name="offline_replay",
    ),
    _MetricSpec(
        "daemon_memory",
        "Daemon memory",
        "bytes",
        float(8 * 1024 * 1024),
        field_name="daemon_peak_memory_bytes",
    ),
    _MetricSpec(
        "persistent_storage_size",
        "Persistent-storage size",
        "bytes",
        float(256 * 1024),
        field_name="persistent_storage_bytes",
    ),
)


def load_benchmark_report(path: str | Path) -> dict[str, Any]:
    source = Path(path).expanduser().absolute()
    if source.is_symlink():
        raise ValueError("Performance baseline must not be a symbolic link.")
    try:
        size = source.stat().st_size
    except FileNotFoundError as exc:
        raise ValueError("Performance baseline does not exist.") from exc
    if not source.is_file():
        raise ValueError("Performance baseline must be a regular JSON file.")
    if size > _MAX_BASELINE_BYTES:
        raise ValueError("Performance baseline exceeds the 2 MiB input limit.")
    try:
        with source.open("r", encoding="utf-8") as handle:
            payload = json.load(handle)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("Performance baseline is not valid UTF-8 JSON.") from exc
    if not isinstance(payload, dict):
        raise ValueError("Performance baseline must contain a JSON object.")
    _snapshot(payload)
    return payload


def compare_benchmark_reports(
    baseline: BenchmarkReport | Mapping[str, Any],
    current: BenchmarkReport | Mapping[str, Any],
) -> PerformanceScorecard:
    baseline_snapshot = _snapshot(_payload(baseline))
    current_snapshot = _snapshot(_payload(current))
    _validate_compatibility(baseline_snapshot, current_snapshot)
    warnings = list(_compatibility_warnings(baseline_snapshot, current_snapshot))
    metrics = tuple(
        _compare_metric(spec, baseline_snapshot, current_snapshot)
        for spec in _METRICS
    )
    for metric in metrics:
        if not metric.available:
            warnings.append(f"{metric.title}: {metric.observation}")
    regressions = tuple(
        metric
        for metric in metrics
        if metric.classification == PerformanceClassification.REGRESSION
    )
    if regressions:
        classification = PerformanceClassification.REGRESSION
        status = PerformanceScorecardStatus.FAIL
    elif any(
        metric.classification == PerformanceClassification.IMPROVEMENT
        for metric in metrics
    ):
        classification = PerformanceClassification.IMPROVEMENT
        status = (
            PerformanceScorecardStatus.PASS_WITH_WARNINGS
            if warnings
            else PerformanceScorecardStatus.PASS
        )
    else:
        classification = PerformanceClassification.NEUTRAL
        status = (
            PerformanceScorecardStatus.PASS_WITH_WARNINGS
            if warnings
            else PerformanceScorecardStatus.PASS
        )
    return PerformanceScorecard(
        generated_at=datetime.now(UTC),
        classification=classification,
        status=status,
        baseline_fingerprint=baseline_snapshot.fingerprint,
        current_fingerprint=current_snapshot.fingerprint,
        baseline_environment_fingerprint=(
            baseline_snapshot.environment_fingerprint
        ),
        current_environment_fingerprint=current_snapshot.environment_fingerprint,
        metrics=metrics,
        warnings=tuple(dict.fromkeys(warnings)),
    )


def _compare_metric(
    spec: _MetricSpec,
    baseline: _BenchmarkSnapshot,
    current: _BenchmarkSnapshot,
) -> PerformanceMetricComparison:
    baseline_value = _metric_value(baseline, spec)
    current_value = _metric_value(current, spec)
    if baseline_value is None or current_value is None:
        missing = []
        if baseline_value is None:
            missing.append("baseline")
        if current_value is None:
            missing.append("current")
        return PerformanceMetricComparison(
            metric_id=spec.metric_id,
            title=spec.title,
            unit=spec.unit,
            status=PerformanceMetricStatus.UNAVAILABLE,
            classification=None,
            baseline_value=baseline_value,
            current_value=current_value,
            ratio=None,
            relative_change_percent=None,
            regression_boundary=None,
            improvement_boundary=None,
            regression_ratio=PERFORMANCE_REGRESSION_RATIO,
            improvement_ratio=PERFORMANCE_IMPROVEMENT_RATIO,
            absolute_tolerance=spec.absolute_tolerance,
            observation=(
                f"Comparable measurement unavailable in {' and '.join(missing)} "
                "report."
            ),
        )
    regression_boundary = max(
        baseline_value * PERFORMANCE_REGRESSION_RATIO,
        baseline_value + spec.absolute_tolerance,
    )
    improvement_boundary = max(
        0.0,
        min(
            baseline_value * PERFORMANCE_IMPROVEMENT_RATIO,
            baseline_value - spec.absolute_tolerance,
        ),
    )
    ratio = current_value / baseline_value if baseline_value else None
    relative_change = (
        ((current_value - baseline_value) / baseline_value) * 100.0
        if baseline_value
        else None
    )
    if current_value >= regression_boundary and current_value > baseline_value:
        classification = PerformanceClassification.REGRESSION
        observation = (
            "Current measurement exceeds both the broad ratio boundary and "
            "the absolute noise floor."
        )
    elif current_value <= improvement_boundary and current_value < baseline_value:
        classification = PerformanceClassification.IMPROVEMENT
        observation = (
            "Current measurement improves beyond both the broad ratio boundary "
            "and the absolute noise floor."
        )
    else:
        classification = PerformanceClassification.NEUTRAL
        observation = (
            "Change remains inside the broad ratio or absolute noise boundary."
        )
    return PerformanceMetricComparison(
        metric_id=spec.metric_id,
        title=spec.title,
        unit=spec.unit,
        status=PerformanceMetricStatus.COMPARED,
        classification=classification,
        baseline_value=baseline_value,
        current_value=current_value,
        ratio=ratio,
        relative_change_percent=relative_change,
        regression_boundary=regression_boundary,
        improvement_boundary=improvement_boundary,
        regression_ratio=PERFORMANCE_REGRESSION_RATIO,
        improvement_ratio=PERFORMANCE_IMPROVEMENT_RATIO,
        absolute_tolerance=spec.absolute_tolerance,
        observation=observation,
    )


def _metric_value(snapshot: _BenchmarkSnapshot, spec: _MetricSpec) -> float | None:
    if spec.operation_name is not None:
        operation = next(
            (
                item
                for item in snapshot.operations
                if item.name == spec.operation_name
            ),
            None,
        )
        if operation is None or operation.status == "skipped":
            return None
        return operation.median_ms
    if spec.field_name == "daemon_peak_memory_bytes":
        return snapshot.daemon_peak_memory_bytes
    if spec.field_name == "persistent_storage_bytes":
        return snapshot.persistent_storage_bytes
    raise RuntimeError(f"Unsupported performance metric source: {spec.metric_id}")


def _payload(source: BenchmarkReport | Mapping[str, Any]) -> dict[str, Any]:
    if isinstance(source, BenchmarkReport):
        return source.to_dict()
    if not isinstance(source, Mapping):
        raise ValueError("Performance report must be a benchmark report or mapping.")
    return dict(source)


def _snapshot(payload: Mapping[str, Any]) -> _BenchmarkSnapshot:
    schema_version = _integer(payload.get("schema_version"), "schema_version")
    if schema_version != BENCHMARK_SCHEMA_VERSION:
        raise ValueError(
            "Unsupported performance baseline schema; generate a fresh baseline."
        )
    if payload.get("network_used") is not False:
        raise ValueError("Performance baselines must be offline benchmark reports.")
    if payload.get("provider_calls") != 0:
        raise ValueError("Performance baselines must report zero provider calls.")
    selected_group = _string(payload.get("selected_group"), "selected_group")
    iterations = _integer(payload.get("iterations"), "iterations")
    if iterations < 1 or iterations > 50:
        raise ValueError("Performance report iterations must be between 1 and 50.")
    repository = _mapping(payload.get("repository"), "repository")
    repository_profile = _string(repository.get("profile"), "repository.profile")
    repository_files = _nonnegative_integer(
        repository.get("file_count"), "repository.file_count"
    )
    repository_bytes = _nonnegative_integer(
        repository.get("byte_count"), "repository.byte_count"
    )
    repository_fingerprint_value = repository.get("fingerprint")
    repository_fingerprint = (
        None
        if repository_fingerprint_value is None
        else _string(repository_fingerprint_value, "repository.fingerprint")
    )
    if selected_group == "all" and repository_fingerprint is None:
        raise ValueError("Full performance reports require a generated fixture.")
    environment = _safe_environment(
        _mapping(payload.get("environment"), "environment")
    )
    for key in ("implementation", "machine", "python", "system"):
        _string(environment.get(key), f"environment.{key}")
    _nonnegative_integer(
        environment.get("processor_count"), "environment.processor_count"
    )
    if _python_series(environment.get("python")) is None:
        raise ValueError("Performance report environment.python is not a version.")
    environment_fingerprint = _string(
        payload.get("environment_fingerprint"), "environment_fingerprint"
    )
    raw_operations = payload.get("operations")
    if not isinstance(raw_operations, list) or len(raw_operations) > _MAX_OPERATIONS:
        raise ValueError("Performance report operations must be a bounded array.")
    operations: list[_OperationSnapshot] = []
    operation_names: set[str] = set()
    for index, raw_operation in enumerate(raw_operations):
        operation = _mapping(raw_operation, f"operations[{index}]")
        name = _string(operation.get("name"), f"operations[{index}].name")
        if name in operation_names:
            raise ValueError("Performance report operation names must be unique.")
        operation_names.add(name)
        operations.append(
            _OperationSnapshot(
                name=name,
                status=_string(
                    operation.get("status"), f"operations[{index}].status"
                ),
                median_ms=_optional_nonnegative_number(
                    operation.get("median_ms"),
                    f"operations[{index}].median_ms",
                ),
            )
        )
    required_operations = {
        spec.operation_name
        for spec in _METRICS
        if spec.operation_name is not None
    }
    missing_operations = required_operations - operation_names
    if missing_operations:
        raise ValueError(
            "Full performance report is missing required operation measurements."
        )
    for field in ("daemon_peak_memory_bytes", "persistent_storage_bytes"):
        if field not in payload:
            raise ValueError(f"Full performance report is missing {field}.")
    daemon_peak_memory_bytes = _optional_nonnegative_number(
        payload.get("daemon_peak_memory_bytes"), "daemon_peak_memory_bytes"
    )
    persistent_storage_bytes = _optional_nonnegative_number(
        payload.get("persistent_storage_bytes"), "persistent_storage_bytes"
    )
    canonical = {
        "schema_version": schema_version,
        "selected_group": selected_group,
        "iterations": iterations,
        "repository": {
            "profile": repository_profile,
            "file_count": repository_files,
            "byte_count": repository_bytes,
            "fingerprint": repository_fingerprint,
        },
        "environment": environment,
        "environment_fingerprint": environment_fingerprint,
        "operations": [
            {
                "name": operation.name,
                "status": operation.status,
                "median_ms": operation.median_ms,
            }
            for operation in operations
        ],
        "daemon_peak_memory_bytes": daemon_peak_memory_bytes,
        "persistent_storage_bytes": persistent_storage_bytes,
    }
    fingerprint = hashlib.sha256(
        json.dumps(
            canonical,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()
    return _BenchmarkSnapshot(
        selected_group=selected_group,
        iterations=iterations,
        repository_profile=repository_profile,
        repository_files=repository_files,
        repository_bytes=repository_bytes,
        repository_fingerprint=repository_fingerprint,
        environment=environment,
        environment_fingerprint=environment_fingerprint,
        operations=tuple(operations),
        daemon_peak_memory_bytes=daemon_peak_memory_bytes,
        persistent_storage_bytes=persistent_storage_bytes,
        fingerprint=fingerprint,
    )


def _validate_compatibility(
    baseline: _BenchmarkSnapshot,
    current: _BenchmarkSnapshot,
) -> None:
    if baseline.selected_group != "all" or current.selected_group != "all":
        raise ValueError(
            "Performance scorecards require baseline and current `all` benchmarks."
        )
    baseline_fixture = (
        baseline.repository_profile,
        baseline.repository_files,
        baseline.repository_bytes,
        baseline.repository_fingerprint,
    )
    current_fixture = (
        current.repository_profile,
        current.repository_files,
        current.repository_bytes,
        current.repository_fingerprint,
    )
    if baseline_fixture != current_fixture:
        raise ValueError(
            "Performance baseline repository fixture does not match the current run."
        )
    for key, title in (
        ("implementation", "Python implementation"),
        ("system", "operating system"),
        ("machine", "machine architecture"),
    ):
        if baseline.environment.get(key) != current.environment.get(key):
            raise ValueError(
                f"Performance baseline {title} does not match the current run."
            )
    if _python_series(baseline.environment.get("python")) != _python_series(
        current.environment.get("python")
    ):
        raise ValueError(
            "Performance baseline Python major/minor does not match the current run."
        )


def _compatibility_warnings(
    baseline: _BenchmarkSnapshot,
    current: _BenchmarkSnapshot,
) -> tuple[str, ...]:
    warnings: list[str] = []
    if baseline.iterations != current.iterations:
        warnings.append(
            "Iteration counts differ; comparisons still use each report's median."
        )
    if baseline.environment.get("python") != current.environment.get("python"):
        warnings.append(
            "Python patch versions differ within the compatible major/minor series."
        )
    if baseline.environment.get("processor_count") != current.environment.get(
        "processor_count"
    ):
        warnings.append(
            "Reported processor counts differ; interpret timing changes cautiously."
        )
    if baseline.environment.get("dependencies") != current.environment.get(
        "dependencies"
    ):
        warnings.append(
            "Measured dependency versions differ between baseline and current run."
        )
    return tuple(warnings)


def _safe_environment(value: Mapping[str, Any]) -> dict[str, Any]:
    safe: dict[str, Any] = {}
    for key in (
        "agentbus_version",
        "implementation",
        "machine",
        "processor_count",
        "python",
        "system",
    ):
        item = value.get(key)
        if isinstance(item, (str, int)) and not isinstance(item, bool):
            safe[key] = item
    dependencies = value.get("dependencies")
    if isinstance(dependencies, Mapping) and len(dependencies) <= 64:
        safe["dependencies"] = {
            str(key)[:128]: str(item)[:128]
            for key, item in dependencies.items()
            if isinstance(key, str) and isinstance(item, str)
        }
    return safe


def _python_series(value: Any) -> tuple[int, int] | None:
    if not isinstance(value, str):
        return None
    parts = value.split(".")
    if len(parts) < 2:
        return None
    try:
        return int(parts[0]), int(parts[1])
    except ValueError:
        return None


def _mapping(value: Any, field: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"Performance report {field} must be an object.")
    return value


def _string(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value or len(value) > 512:
        raise ValueError(f"Performance report {field} must be a bounded string.")
    return value


def _integer(value: Any, field: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError(f"Performance report {field} must be an integer.")
    return value


def _nonnegative_integer(value: Any, field: str) -> int:
    number = _integer(value, field)
    if number < 0:
        raise ValueError(f"Performance report {field} must be non-negative.")
    return number


def _optional_nonnegative_number(value: Any, field: str) -> float | None:
    if value is None:
        return None
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise ValueError(f"Performance report {field} must be numeric or null.")
    number = float(value)
    if number < 0 or not math.isfinite(number):
        raise ValueError(f"Performance report {field} must be finite and non-negative.")
    return number


def _round(value: float | None) -> float | None:
    return round(value, 3) if value is not None else None


__all__ = [
    "PERFORMANCE_IMPROVEMENT_RATIO",
    "PERFORMANCE_REGRESSION_RATIO",
    "PERFORMANCE_SCORECARD_SCHEMA_VERSION",
    "PERFORMANCE_THRESHOLD_POLICY",
    "PerformanceClassification",
    "PerformanceMetricComparison",
    "PerformanceMetricStatus",
    "PerformanceScorecard",
    "PerformanceScorecardStatus",
    "compare_benchmark_reports",
    "load_benchmark_report",
]
