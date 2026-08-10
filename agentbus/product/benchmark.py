from __future__ import annotations

import hashlib
import json
import math
import os
import platform
import statistics
import tempfile
import time
import tracemalloc
from dataclasses import dataclass
from datetime import UTC, datetime
from importlib import metadata
from pathlib import Path
from typing import Any, Callable

from agentbus import __version__
from agentbus.agents.planner import PlannerOutput
from agentbus.config import AgentBusConfig
from agentbus.intelligence.service import RepositoryIntelligenceService
from agentbus.models.router import ModelRouter
from agentbus.models.types import ModelRole
from agentbus.product.synthetic import generate_synthetic_repository
from agentbus.replay.engine import ReplayEngine
from agentbus.replay.session import ReplayRequest
from agentbus.tools.filesystem import FileSystemTools
from agentbus.trace.models import (
    ReplayMode,
    Trace,
    TraceSpan,
    TraceSpanType,
    TraceStatus,
)
from agentbus.trace.storage import ContentAddressedStore


BENCHMARK_GROUPS = ("startup", "index", "search", "control", "replay", "tools")
_BASE_BUDGETS_MS = {
    "deterministic_run_startup": 5_000.0,
    "initial_index": 30_000.0,
    "incremental_index": 15_000.0,
    "lexical_search": 5_000.0,
    "graph_traversal": 5_000.0,
    "context_planning": 10_000.0,
    "daemon_app_startup": 20_000.0,
    "protocol_readiness": 10_000.0,
    "offline_replay": 5_000.0,
    "filesystem_tool_read": 2_000.0,
    "trace_recording": 2_000.0,
}


@dataclass(frozen=True)
class OperationMetrics:
    name: str
    group: str
    status: str
    samples_ms: tuple[float, ...]
    median_ms: float | None
    p95_ms: float | None
    max_ms: float | None
    operation_count: int
    budget_ms: float | None
    budget_passed: bool | None
    detail: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "group": self.group,
            "status": self.status,
            "samples_ms": [round(value, 3) for value in self.samples_ms],
            "median_ms": _round(self.median_ms),
            "p95_ms": _round(self.p95_ms),
            "max_ms": _round(self.max_ms),
            "operation_count": self.operation_count,
            "budget_ms": _round(self.budget_ms),
            "budget_passed": self.budget_passed,
            "detail": self.detail,
        }


@dataclass(frozen=True)
class BenchmarkReport:
    selected_group: str
    iterations: int
    repository_profile: str
    repository_files: int
    repository_bytes: int
    repository_fingerprint: str | None
    peak_memory_bytes: int
    memory_budget_bytes: int
    environment: dict[str, Any]
    environment_fingerprint: str
    operations: tuple[OperationMetrics, ...]
    generated_at: str

    @property
    def passed(self) -> bool:
        operations_passed = all(
            operation.status == "skipped" or operation.budget_passed is not False
            for operation in self.operations
        )
        return operations_passed and self.peak_memory_bytes <= self.memory_budget_bytes

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.passed,
            "selected_group": self.selected_group,
            "iterations": self.iterations,
            "repository": {
                "profile": self.repository_profile,
                "file_count": self.repository_files,
                "byte_count": self.repository_bytes,
                "fingerprint": self.repository_fingerprint,
                "generated": self.repository_fingerprint is not None,
            },
            "peak_memory_bytes": self.peak_memory_bytes,
            "memory_budget_bytes": self.memory_budget_bytes,
            "memory_budget_passed": self.peak_memory_bytes <= self.memory_budget_bytes,
            "budget_policy": "broad-regression-v1",
            "environment": self.environment,
            "environment_fingerprint": self.environment_fingerprint,
            "operations": [operation.to_dict() for operation in self.operations],
            "generated_at": self.generated_at,
            "network_used": False,
        }


def run_benchmark(
    group: str = "all",
    *,
    profile: str = "small",
    file_count: int | None = None,
    iterations: int = 5,
    seed: int = 2026,
) -> BenchmarkReport:
    if group not in {*BENCHMARK_GROUPS, "all"}:
        raise ValueError("Unsupported benchmark group.")
    if iterations < 1 or iterations > 50:
        raise ValueError("Benchmark iterations must be between 1 and 50.")
    selected_groups = set(BENCHMARK_GROUPS if group == "all" else (group,))
    operations: list[OperationMetrics] = []
    repository_files = 0
    repository_bytes = 0
    repository_fingerprint: str | None = None
    peak_memory_bytes = 0
    with tempfile.TemporaryDirectory(prefix="agentbus-benchmark-") as temporary:
        root = Path(temporary)
        workspace = root / "repository"
        needs_repository = bool(selected_groups & {"index", "search", "tools"})
        if needs_repository:
            repository = generate_synthetic_repository(
                workspace,
                profile=profile,
                file_count=file_count,
                seed=seed,
            )
            repository_files = repository.file_count
            repository_bytes = repository.byte_count
            repository_fingerprint = repository.fingerprint
        if "startup" in selected_groups:
            operations.append(
                _measure(
                    "deterministic_run_startup",
                    "startup",
                    lambda: _deterministic_startup(workspace or root),
                    iterations,
                )
            )
        service: RepositoryIntelligenceService | None = None
        if selected_groups & {"index", "search"}:
            tracemalloc.start()
            service = RepositoryIntelligenceService(
                workspace,
                root / "repository-index.sqlite3",
            )
            if "index" in selected_groups or "search" in selected_groups:
                operations.append(
                    _measure("initial_index", "index", service.build, 1)
                )
            if "index" in selected_groups:
                _touch_incremental_file(workspace, seed)
                operations.append(
                    _measure(
                        "incremental_index",
                        "index",
                        service.update,
                        iterations,
                    )
                )
            _, peak_memory_bytes = tracemalloc.get_traced_memory()
            tracemalloc.stop()
        if "search" in selected_groups and service is not None:
            subject = f"compute_{max(0, repository_files - 1):05d}"
            operations.extend(
                (
                    _measure(
                        "lexical_search",
                        "search",
                        lambda: service.search(subject, limit=20),
                        iterations,
                    ),
                    _measure(
                        "graph_traversal",
                        "search",
                        lambda: service.dependencies(subject, max_depth=3),
                        iterations,
                    ),
                    _measure(
                        "context_planning",
                        "search",
                        lambda: service.context_plan(
                            "Update the generated compute function",
                            token_budget=4_000,
                            byte_budget=20_000,
                        ),
                        iterations,
                    ),
                )
            )
        if "control" in selected_groups:
            operations.extend(_control_metrics(iterations))
        if "replay" in selected_groups:
            replay = _replay_fixture(root)
            operations.append(
                _measure(
                    "offline_replay",
                    "replay",
                    replay,
                    iterations,
                )
            )
        if "tools" in selected_groups:
            tools = FileSystemTools(str(workspace))
            source = "package_0000/module_00000.py"
            object_store = ContentAddressedStore(root / "trace-objects")
            counter = 0

            def record_trace() -> None:
                nonlocal counter
                counter += 1
                object_store.put_json(
                    {"sequence": counter, "status": "ok"},
                    producing_span_id="benchmark",
                )

            operations.extend(
                (
                    _measure(
                        "filesystem_tool_read",
                        "tools",
                        lambda: tools.read_file_result(source),
                        iterations,
                    ),
                    _measure(
                        "trace_recording",
                        "tools",
                        record_trace,
                        iterations,
                    ),
                )
            )
    environment = _environment()
    environment_payload = json.dumps(
        environment,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return BenchmarkReport(
        selected_group=group,
        iterations=iterations,
        repository_profile=profile,
        repository_files=repository_files,
        repository_bytes=repository_bytes,
        repository_fingerprint=repository_fingerprint,
        peak_memory_bytes=peak_memory_bytes,
        memory_budget_bytes=(
            256 * 1024 * 1024 + repository_files * 128 * 1024
        ),
        environment=environment,
        environment_fingerprint=hashlib.sha256(environment_payload).hexdigest(),
        operations=tuple(operations),
        generated_at=datetime.now(UTC).isoformat(),
    )


def write_benchmark_report(report: BenchmarkReport, output: str | Path) -> Path:
    destination = Path(output).expanduser().absolute()
    if destination.suffix.lower() != ".json":
        raise ValueError("Benchmark report output must use a .json extension.")
    if destination.exists() or destination.is_symlink():
        raise ValueError("Benchmark report output already exists or is a link.")
    destination.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(report.to_dict(), indent=2, sort_keys=True) + "\n"
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{destination.name}.",
        suffix=".tmp",
        dir=destination.parent,
        text=True,
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_name, destination)
    finally:
        Path(temporary_name).unlink(missing_ok=True)
    return destination.resolve()


def _measure(
    name: str,
    group: str,
    operation: Callable[[], Any],
    iterations: int,
) -> OperationMetrics:
    samples: list[float] = []
    try:
        for _ in range(iterations):
            started = time.perf_counter_ns()
            operation()
            samples.append((time.perf_counter_ns() - started) / 1_000_000)
    except ImportError as exc:
        return OperationMetrics(
            name=name,
            group=group,
            status="skipped",
            samples_ms=(),
            median_ms=None,
            p95_ms=None,
            max_ms=None,
            operation_count=0,
            budget_ms=None,
            budget_passed=None,
            detail=f"Optional dependency unavailable: {type(exc).__name__}",
        )
    ordered = sorted(samples)
    p95_index = max(0, math.ceil(len(ordered) * 0.95) - 1)
    budget = _budget(name)
    maximum = max(ordered)
    return OperationMetrics(
        name=name,
        group=group,
        status="passed" if maximum <= budget else "budget_exceeded",
        samples_ms=tuple(samples),
        median_ms=statistics.median(samples),
        p95_ms=ordered[p95_index],
        max_ms=maximum,
        operation_count=len(samples),
        budget_ms=budget,
        budget_passed=maximum <= budget,
    )


def _deterministic_startup(workspace: Path) -> None:
    workspace.mkdir(parents=True, exist_ok=True)
    config = AgentBusConfig(
        workspace_dir=str(workspace),
        provider_name="deterministic",
        fallback_provider_name="deterministic",
        model_max_retries=0,
    )
    ModelRouter(config).generate_json(
        ModelRole.PLANNER,
        "Plan a deterministic benchmark task.",
        schema=PlannerOutput,
    )


def _control_metrics(iterations: int) -> tuple[OperationMetrics, ...]:
    try:
        from agentbus.control.protocol import build_json_schema, build_openapi
    except ImportError as exc:
        skipped = OperationMetrics(
            name="daemon_app_startup",
            group="control",
            status="skipped",
            samples_ms=(),
            median_ms=None,
            p95_ms=None,
            max_ms=None,
            operation_count=0,
            budget_ms=None,
            budget_passed=None,
            detail=(
                "Install the `ide` extra for control benchmarks: "
                f"{type(exc).__name__}"
            ),
        )
        return (skipped,)
    return (
        _measure("daemon_app_startup", "control", build_openapi, iterations),
        _measure("protocol_readiness", "control", build_json_schema, iterations),
    )


def _replay_fixture(root: Path) -> Callable[[], Any]:
    store = ContentAddressedStore(root / "replay-objects")
    now = datetime(2026, 1, 1, tzinfo=UTC)
    span = TraceSpan(
        trace_id="benchmark-trace",
        span_id="benchmark-root",
        parent_span_id=None,
        run_id="benchmark-run",
        span_type=TraceSpanType.RUN,
        name="benchmark run",
        sequence=1,
        started_at=now,
        ended_at=now,
        status=TraceStatus.SUCCEEDED,
    )
    trace = Trace(
        trace_id="benchmark-trace",
        run_id="benchmark-run",
        root_span_id=span.span_id,
        status=TraceStatus.SUCCEEDED,
        created_at=now,
        completed_at=now,
        spans=[span],
    )
    counter = 0

    def replay() -> Any:
        nonlocal counter
        counter += 1
        request = ReplayRequest(
            replay_id=f"benchmark-replay-{counter}",
            source_trace_id=trace.trace_id,
            source_run_id=trace.run_id,
            mode=ReplayMode.OFFLINE,
        )
        return ReplayEngine(store).replay(trace, request)

    return replay


def _touch_incremental_file(workspace: Path, seed: int) -> None:
    path = workspace / "package_0000" / "module_00000.py"
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(f"\nINCREMENTAL_MARKER = {seed}\n")


def _budget(name: str) -> float:
    return _BASE_BUDGETS_MS[name]


def _environment() -> dict[str, Any]:
    dependencies: dict[str, str] = {}
    for package in ("pydantic", "jsonschema"):
        try:
            dependencies[package] = metadata.version(package)
        except metadata.PackageNotFoundError:
            dependencies[package] = "not-installed"
    return {
        "agentbus_version": __version__,
        "python": platform.python_version(),
        "implementation": platform.python_implementation(),
        "system": platform.system(),
        "machine": platform.machine(),
        "processor_count": os.cpu_count(),
        "dependencies": dependencies,
    }


def _round(value: float | None) -> float | None:
    return round(value, 3) if value is not None else None
