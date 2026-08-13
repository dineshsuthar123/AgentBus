from __future__ import annotations

import ctypes
import os
import subprocess
import sys
import tempfile
import threading
import time
import tracemalloc
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from agentbus.agents.planner import PlannerOutput
from agentbus.config import AgentBusConfig
from agentbus.control.event_stream import ControlEventReader
from agentbus.execution.engine import DurableExecutionEngine
from agentbus.execution.leases import LeaseService
from agentbus.execution.models import RunStatus, TaskExecutionResult
from agentbus.execution.state_store import StateStore
from agentbus.git.repository import GitRepository
from agentbus.intelligence.service import RepositoryIntelligenceService
from agentbus.mcp import McpServerConfig, mcp_server_capabilities
from agentbus.models.router import ModelRouter
from agentbus.models.types import ModelRole
from agentbus.policy import ToolApprovalDisposition, decide_tool_approval
from agentbus.product.synthetic import generate_synthetic_repository
from agentbus.replay.engine import ReplayEngine
from agentbus.replay.session import ReplayRequest
from agentbus.sandbox.platform import ExecutableCatalog
from agentbus.security.redaction import redact_text
from agentbus.tools.capabilities import derive_required_capabilities
from agentbus.tools.protocol import StructuredToolCall, ToolInvocationStatus
from agentbus.tools.runtime import ManagedToolRuntime, build_managed_tool_runtime
from agentbus.trace.models import (
    ReplayMode,
    Trace,
    TraceSpan,
    TraceSpanType,
    TraceStatus,
)
from agentbus.trace.storage import ContentAddressedStore
from agentbus.worktrees.manager import GitWorktreeManager
from agentbus.worktrees.models import WorktreeStatus


_MEMORY_BASELINE_BYTES = 128 * 1024 * 1024
_MAX_RUNS = 10_000
_MAX_PARALLELISM = 32
_MAX_DURATION_SECONDS = 86_400.0


@dataclass(frozen=True)
class SoakProfile:
    name: str
    duration_seconds: float
    runs: int
    parallelism: int
    repository_files: int


SOAK_PROFILES = {
    "quick": SoakProfile(
        name="quick",
        duration_seconds=30.0,
        runs=10,
        parallelism=2,
        repository_files=20,
    ),
    "release-candidate": SoakProfile(
        name="release-candidate",
        duration_seconds=600.0,
        runs=10_000,
        parallelism=2,
        repository_files=100,
    ),
}
SOAK_PROFILE_NAMES = tuple(SOAK_PROFILES)


@dataclass(frozen=True)
class ResourceTrend:
    name: str
    unit: str
    before: int | None
    peak: int | None
    after: int | None
    scope: str

    @property
    def measurable(self) -> bool:
        return (
            self.before is not None
            and self.peak is not None
            and self.after is not None
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "unit": self.unit,
            "before": self.before,
            "peak": self.peak,
            "after": self.after,
            "delta": (
                self.after - self.before
                if self.after is not None and self.before is not None
                else None
            ),
            "measurable": self.measurable,
            "scope": self.scope,
        }


class _SoakCleanupError(RuntimeError):
    pass


class _ResourceTracker:
    _METRICS = (
        (
            "process_count",
            "processes",
            "AgentBus-owned synthetic child processes",
        ),
        (
            "owned_worktree_count",
            "worktrees",
            "non-removed AgentBus-owned worktrees",
        ),
        ("state_database_bytes", "bytes", "durable state database files"),
        ("index_database_bytes", "bytes", "repository index database files"),
        ("trace_bytes", "bytes", "content-addressed trace storage"),
        ("memory_bytes", "bytes", "Python allocations measured by tracemalloc"),
        ("handle_count", "handles", "current AgentBus process"),
        ("thread_count", "threads", "current AgentBus process"),
    )

    def __init__(
        self,
        *,
        state_database: Path,
        index_database: Path,
        trace_root: Path,
        worktree_manager: GitWorktreeManager,
    ) -> None:
        self.state_database = state_database
        self.index_database = index_database
        self.trace_root = trace_root
        self.worktree_manager = worktree_manager
        self._lock = threading.RLock()
        self._processes: dict[int, subprocess.Popen[bytes]] = {}
        self._worktree_count = _active_worktree_count(worktree_manager)
        self._trace_bytes = _directory_bytes(trace_root)
        self._before = self._observe_locked()
        self._peaks = dict(self._before)

    def process_started(self, process: subprocess.Popen[bytes]) -> None:
        with self._lock:
            self._processes[process.pid] = process
            self._sample_locked()

    def process_stopped(self, process: subprocess.Popen[bytes]) -> None:
        with self._lock:
            if process.poll() is not None:
                self._processes.pop(process.pid, None)
            self._sample_locked()

    def worktree_started(self) -> None:
        with self._lock:
            self._worktree_count += 1
            self._sample_locked()

    def worktree_stopped(self) -> None:
        with self._lock:
            self._worktree_count = max(0, self._worktree_count - 1)
            self._sample_locked()

    def sample(self) -> None:
        with self._lock:
            self._sample_locked()

    def finish(self) -> tuple[ResourceTrend, ...]:
        with self._lock:
            self._processes = {
                pid: process
                for pid, process in self._processes.items()
                if process.poll() is None
            }
            self._worktree_count = _active_worktree_count(self.worktree_manager)
            self._trace_bytes = _directory_bytes(self.trace_root)
            after = self._sample_locked()
            return tuple(
                ResourceTrend(
                    name=name,
                    unit=unit,
                    before=self._before[name],
                    peak=self._peaks[name],
                    after=after[name],
                    scope=scope,
                )
                for name, unit, scope in self._METRICS
            )

    def _sample_locked(self) -> dict[str, int | None]:
        values = self._observe_locked()
        for name, value in values.items():
            previous = self._peaks.get(name)
            if value is not None and (previous is None or value > previous):
                self._peaks[name] = value
        if tracemalloc.is_tracing():
            _, traced_peak = tracemalloc.get_traced_memory()
            previous_memory = self._peaks.get("memory_bytes")
            if previous_memory is None or traced_peak > previous_memory:
                self._peaks["memory_bytes"] = traced_peak
        return values

    def _observe_locked(self) -> dict[str, int | None]:
        current_memory = (
            tracemalloc.get_traced_memory()[0] if tracemalloc.is_tracing() else None
        )
        return {
            "process_count": sum(
                process.poll() is None for process in self._processes.values()
            ),
            "owned_worktree_count": self._worktree_count,
            "state_database_bytes": _database_bytes(self.state_database),
            "index_database_bytes": _database_bytes(self.index_database),
            "trace_bytes": self._trace_bytes,
            "memory_bytes": current_memory,
            "handle_count": _current_handle_count(),
            "thread_count": threading.active_count(),
        }


@dataclass(frozen=True)
class SoakCycleResult:
    cycle: int
    run_id: str
    status: str
    event_count: int
    managed_tool_calls: int
    approval_count: int
    mcp_call_count: int
    lease_released: bool
    replayed: bool
    trace_written: bool
    indexed: bool
    daemon_reconnected: bool
    worktree_cleaned: bool
    cleanup_failure_count: int
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "cycle": self.cycle,
            "run_id": self.run_id,
            "status": self.status,
            "event_count": self.event_count,
            "managed_tool_calls": self.managed_tool_calls,
            "approval_count": self.approval_count,
            "mcp_call_count": self.mcp_call_count,
            "lease_released": self.lease_released,
            "replayed": self.replayed,
            "trace_written": self.trace_written,
            "indexed": self.indexed,
            "daemon_reconnected": self.daemon_reconnected,
            "daemon_restarted": self.daemon_reconnected,
            "worktree_cleaned": self.worktree_cleaned,
            "cleanup_failure_count": self.cleanup_failure_count,
            "error": self.error,
        }


@dataclass(frozen=True)
class SoakReport:
    profile: str
    requested_runs: int
    completed_runs: int
    parallelism: int
    seed: int
    duration_limit_seconds: float
    duration_seconds: float
    stopped_by_duration: bool
    repository_files: int
    repository_fingerprint: str
    successful_runs: int
    intentional_cancellations: int
    failed_runs: int
    tool_calls: int
    approval_count: int
    mcp_call_count: int
    replay_count: int
    trace_write_count: int
    index_update_count: int
    daemon_reconnect_count: int
    daemon_restart_count: int
    worktree_cleanup_count: int
    failed_cleanup_count: int
    event_count: int
    event_gap_count: int
    stale_lease_count: int
    leaked_worktree_count: int
    leaked_process_count: int
    sqlite_bytes_before: int
    sqlite_bytes_after: int
    memory_growth_bytes: int
    peak_memory_bytes: int
    memory_budget_bytes: int
    resource_trends: tuple[ResourceTrend, ...]
    cycles: tuple[SoakCycleResult, ...]

    @property
    def ok(self) -> bool:
        return (
            self.completed_runs > 0
            and self.failed_runs == 0
            and self.failed_cleanup_count == 0
            and self.event_gap_count == 0
            and self.stale_lease_count == 0
            and self.leaked_worktree_count == 0
            and self.leaked_process_count == 0
            and self.memory_growth_bytes <= self.memory_budget_bytes
            and all(
                trend.measurable
                for trend in self.resource_trends
                if trend.name != "handle_count"
            )
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "profile": self.profile,
            "requested_runs": self.requested_runs,
            "completed_runs": self.completed_runs,
            "parallelism": self.parallelism,
            "seed": self.seed,
            "duration_limit_seconds": self.duration_limit_seconds,
            "duration_seconds": round(self.duration_seconds, 3),
            "stopped_by_duration": self.stopped_by_duration,
            "repository": {
                "file_count": self.repository_files,
                "fingerprint": self.repository_fingerprint,
                "generated": True,
            },
            "runs": {
                "successful": self.successful_runs,
                "intentional_cancellations": self.intentional_cancellations,
                "failed": self.failed_runs,
            },
            "operations": {
                "tool_calls": self.tool_calls,
                "approvals": self.approval_count,
                "mcp_calls": self.mcp_call_count,
                "replays": self.replay_count,
                "trace_writes": self.trace_write_count,
                "index_updates": self.index_update_count,
                "daemon_reconnects": self.daemon_reconnect_count,
                "daemon_restarts": self.daemon_restart_count,
                "worktree_cleanups": self.worktree_cleanup_count,
            },
            "resources": {
                "failed_cleanup_count": self.failed_cleanup_count,
                "event_count": self.event_count,
                "event_gap_count": self.event_gap_count,
                "stale_lease_count": self.stale_lease_count,
                "leaked_worktree_count": self.leaked_worktree_count,
                "leaked_process_count": self.leaked_process_count,
                "sqlite_bytes_before": self.sqlite_bytes_before,
                "sqlite_bytes_after": self.sqlite_bytes_after,
                "memory_growth_bytes": self.memory_growth_bytes,
                "peak_memory_bytes": self.peak_memory_bytes,
                "memory_budget_bytes": self.memory_budget_bytes,
                "process_tracking": "synchronous_children_only",
                "trends": {
                    trend.name: trend.to_dict() for trend in self.resource_trends
                },
            },
            "cycles": [cycle.to_dict() for cycle in self.cycles],
            "network_used": False,
        }


def soak_profile(name: str) -> SoakProfile:
    try:
        return SOAK_PROFILES[name]
    except KeyError as exc:
        supported = ", ".join(SOAK_PROFILE_NAMES)
        raise ValueError(
            f"Unsupported soak profile '{name}'. Choose one of: {supported}."
        ) from exc


def run_soak(
    *,
    profile: str = "quick",
    duration_seconds: float | None = None,
    runs: int | None = None,
    parallelism: int | None = None,
    seed: int = 2026,
    repository_files: int | None = None,
) -> SoakReport:
    selected_profile = soak_profile(profile)
    duration_seconds = (
        selected_profile.duration_seconds
        if duration_seconds is None
        else duration_seconds
    )
    runs = selected_profile.runs if runs is None else runs
    parallelism = (
        selected_profile.parallelism if parallelism is None else parallelism
    )
    repository_files = (
        selected_profile.repository_files
        if repository_files is None
        else repository_files
    )
    _validate_options(
        duration_seconds=duration_seconds,
        runs=runs,
        parallelism=parallelism,
        seed=seed,
        repository_files=repository_files,
    )
    started = time.monotonic()
    deadline = started + duration_seconds
    with tempfile.TemporaryDirectory(prefix="agentbus-soak-") as temporary:
        root = Path(temporary).resolve()
        workspace = root / "repository"
        runtime = root / "runtime"
        worktree_root = root / "worktrees"
        runtime.mkdir()
        repository = generate_synthetic_repository(
            workspace,
            profile="small",
            file_count=repository_files,
            seed=seed,
        )
        _prepare_worker_files(workspace, parallelism)
        _initialize_git_repository(workspace, runtime / "empty-hooks")
        state_database = runtime / "state.db"
        index_database = runtime / "repository-index.sqlite3"
        store = StateStore(state_database)
        index = RepositoryIntelligenceService(workspace, index_database)
        index.build()
        manager = GitWorktreeManager(workspace, worktree_root, store)
        base_commit = GitRepository(str(workspace)).head_commit(short=False)
        trace, object_store = _replay_fixture(root)
        mcp_config, executable_catalog = _soak_mcp_configuration()
        index_lock = threading.Lock()
        worktree_lock = threading.Lock()
        allocation_lock = threading.Lock()
        results_lock = threading.Lock()
        results: list[SoakCycleResult] = []
        next_cycle = 0

        tracemalloc.start()
        try:
            resources = _ResourceTracker(
                state_database=state_database,
                index_database=index_database,
                trace_root=object_store.root,
                worktree_manager=manager,
            )
        except BaseException:
            tracemalloc.stop()
            raise

        def worker(worker_id: int) -> None:
            nonlocal next_cycle
            while True:
                with allocation_lock:
                    if next_cycle >= runs or time.monotonic() >= deadline:
                        return
                    cycle = next_cycle
                    next_cycle += 1
                result = _run_cycle(
                    cycle,
                    worker_id=worker_id,
                    seed=seed,
                    workspace=workspace,
                    store=store,
                    index=index,
                    index_lock=index_lock,
                    manager=manager,
                    worktree_lock=worktree_lock,
                    base_commit=base_commit,
                    trace=trace,
                    object_store=object_store,
                    executable_catalog=executable_catalog,
                    mcp_config=mcp_config,
                    resources=resources,
                )
                with results_lock:
                    results.append(result)
                resources.sample()

        try:
            workers = min(parallelism, runs)
            with ThreadPoolExecutor(
                max_workers=workers,
                thread_name_prefix="agentbus-soak",
            ) as executor:
                futures = [
                    executor.submit(worker, index) for index in range(workers)
                ]
                for future in futures:
                    future.result()
            cleanup_failures = sum(
                cycle.cleanup_failure_count for cycle in results
            )
            cleanup_failures += _repair_clean_worktrees(manager)
            event_count, event_gaps = _event_summary(store)
            stale_leases = _stale_lease_count(store)
            leaked_worktrees = _active_worktree_count(manager)
            resource_trends = resources.finish()
            cycles = tuple(sorted(results, key=lambda item: item.cycle))
            trends = {trend.name: trend for trend in resource_trends}
            memory = trends["memory_bytes"]
            state_database_trend = trends["state_database_bytes"]
            index_database_trend = trends["index_database_bytes"]
            sqlite_before = (state_database_trend.before or 0) + (
                index_database_trend.before or 0
            )
            sqlite_after = (state_database_trend.after or 0) + (
                index_database_trend.after or 0
            )
            memory_growth = max(0, (memory.after or 0) - (memory.before or 0))
            memory_budget = _MEMORY_BASELINE_BYTES + len(cycles) * 1024 * 1024
            elapsed = time.monotonic() - started
            return SoakReport(
                profile=selected_profile.name,
                requested_runs=runs,
                completed_runs=len(cycles),
                parallelism=parallelism,
                seed=seed,
                duration_limit_seconds=duration_seconds,
                duration_seconds=elapsed,
                stopped_by_duration=len(cycles) < runs,
                repository_files=repository.file_count,
                repository_fingerprint=repository.fingerprint,
                successful_runs=sum(
                    cycle.status == "succeeded" for cycle in cycles
                ),
                intentional_cancellations=sum(
                    cycle.status == "cancelled" for cycle in cycles
                ),
                failed_runs=sum(cycle.status == "failed" for cycle in cycles),
                tool_calls=sum(cycle.managed_tool_calls for cycle in cycles),
                approval_count=sum(cycle.approval_count for cycle in cycles),
                mcp_call_count=sum(cycle.mcp_call_count for cycle in cycles),
                replay_count=sum(cycle.replayed for cycle in cycles),
                trace_write_count=sum(cycle.trace_written for cycle in cycles),
                index_update_count=sum(cycle.indexed for cycle in cycles),
                daemon_reconnect_count=sum(
                    cycle.daemon_reconnected for cycle in cycles
                ),
                daemon_restart_count=sum(
                    cycle.daemon_reconnected for cycle in cycles
                ),
                worktree_cleanup_count=sum(
                    cycle.worktree_cleaned for cycle in cycles
                ),
                failed_cleanup_count=cleanup_failures,
                event_count=event_count,
                event_gap_count=event_gaps,
                stale_lease_count=stale_leases,
                leaked_worktree_count=leaked_worktrees,
                leaked_process_count=(trends["process_count"].after or 0),
                sqlite_bytes_before=sqlite_before,
                sqlite_bytes_after=sqlite_after,
                memory_growth_bytes=memory_growth,
                peak_memory_bytes=memory.peak or 0,
                memory_budget_bytes=memory_budget,
                resource_trends=resource_trends,
                cycles=cycles,
            )
        finally:
            tracemalloc.stop()


def _run_cycle(
    cycle: int,
    *,
    worker_id: int,
    seed: int,
    workspace: Path,
    store: StateStore,
    index: RepositoryIntelligenceService,
    index_lock: threading.Lock,
    manager: GitWorktreeManager,
    worktree_lock: threading.Lock,
    base_commit: str,
    trace: Trace,
    object_store: ContentAddressedStore,
    executable_catalog: ExecutableCatalog,
    mcp_config: McpServerConfig,
    resources: _ResourceTracker,
) -> SoakCycleResult:
    run_id = f"soak-{seed}-{cycle:05d}"
    task_id = f"cycle-{cycle:05d}"
    worktree_cleaned = False
    lease_released = False
    replayed = False
    trace_written = False
    indexed = False
    daemon_reconnected = False
    managed_tool_calls = 0
    approval_count = 0
    mcp_call_count = 0
    cleanup_failure_count = 0
    status = "failed"
    error: str | None = None
    try:
        _deterministic_generation(workspace)
        plan = _cycle_plan(task_id)
        engine = DurableExecutionEngine(
            store,
            lambda _context: TaskExecutionResult(
                succeeded=True,
                summary="Deterministic soak task completed.",
                verifier_status="passed",
                reviewer_status="approved",
            ),
        )
        engine.create_run(
            "Exercise deterministic AgentBus reliability.",
            plan,
            model="deterministic",
            workspace=str(workspace),
            run_id=run_id,
        )
        (
            managed_tool_calls,
            approval_count,
            mcp_call_count,
        ) = _exercise_managed_runtime(
            cycle=cycle,
            worker_id=worker_id,
            seed=seed,
            run_id=run_id,
            task_id=task_id,
            workspace=workspace,
            store=store,
            executable_catalog=executable_catalog,
            mcp_config=mcp_config,
            resources=resources,
        )
        leases = LeaseService(store, lease_seconds=60)
        lease = leases.acquire_lease(run_id, task_id, f"worker-{cycle:05d}")
        lease = leases.release_lease(
            lease.lease_id,
            lease.worker_id,
            lease.fencing_token,
        )
        lease_released = lease.status.value == "released"
        if not lease_released:
            raise RuntimeError("The worker lease did not reach released status.")
        if cycle % 2:
            report = engine.cancel_run(run_id, "Intentional soak cancellation.")
        else:
            report = engine.run_until_blocked(run_id)
        expected = RunStatus.CANCELLED if cycle % 2 else RunStatus.SUCCEEDED
        if report.status != expected:
            raise RuntimeError(
                f"Durable cycle ended as {report.status.value}; expected {expected.value}."
            )
        request = ReplayRequest(
            replay_id=f"soak-replay-{seed}-{cycle:05d}",
            source_trace_id=trace.trace_id,
            source_run_id=trace.run_id,
            mode=ReplayMode.OFFLINE,
        )
        ReplayEngine(object_store).replay(trace, request)
        replayed = True
        metadata = object_store.put_json(
            {
                "cycle": cycle,
                "run_id": run_id,
                "status": expected.value,
            },
            producing_span_id=trace.root_span_id,
        )
        trace_written = metadata.byte_size > 0
        with index_lock:
            index.update()
        indexed = True
        with worktree_lock:
            worktree = None
            try:
                worktree = manager.create_task_worktree(
                    run_id,
                    task_id,
                    base_commit,
                    f"worker-{cycle:05d}",
                )
                resources.worktree_started()
                manager.mark_cleanup_pending(worktree.worktree_id)
                removed = manager.remove(worktree.worktree_id)
                worktree_cleaned = removed.status == WorktreeStatus.REMOVED
                if worktree_cleaned:
                    resources.worktree_stopped()
            except Exception as exc:
                if worktree is not None:
                    raise _SoakCleanupError(
                        "The AgentBus-owned worktree cleanup failed."
                    ) from exc
                raise
        if not worktree_cleaned:
            raise _SoakCleanupError(
                "The AgentBus-owned worktree was not removed."
            )
        daemon_reconnected = _verify_daemon_restart(
            store.database_path,
            run_id,
            expected,
        )
        if not daemon_reconnected:
            raise RuntimeError(
                "Persisted daemon state did not recover from its event cursor."
            )
        status = "cancelled" if cycle % 2 else "succeeded"
    except Exception as exc:
        if isinstance(exc, _SoakCleanupError):
            cleanup_failure_count += 1
        error = redact_text(str(exc), max_chars=500) or type(exc).__name__
    try:
        event_count = len(store.list_events(run_id, limit=5_000))
    except Exception:
        event_count = 0
    return SoakCycleResult(
        cycle=cycle,
        run_id=run_id,
        status=status,
        event_count=event_count,
        managed_tool_calls=managed_tool_calls,
        approval_count=approval_count,
        mcp_call_count=mcp_call_count,
        lease_released=lease_released,
        replayed=replayed,
        trace_written=trace_written,
        indexed=indexed,
        daemon_reconnected=daemon_reconnected,
        worktree_cleaned=worktree_cleaned,
        cleanup_failure_count=cleanup_failure_count,
        error=error,
    )


def _cycle_plan(task_id: str) -> dict[str, Any]:
    return {
        "goal": "Exercise one bounded deterministic soak cycle.",
        "steps": [
            {
                "id": task_id,
                "title": "Complete soak cycle",
                "description": "Perform an offline deterministic operation.",
                "dependencies": [],
                "risk": "low",
                "expected_outputs": [],
                "done_criteria": ["The deterministic operation completes."],
            }
        ],
        "test_strategy": "offline",
        "done_criteria": ["The cycle reaches an expected terminal state."],
    }


def _deterministic_generation(workspace: Path) -> None:
    config = AgentBusConfig(
        workspace_dir=str(workspace),
        provider_name="deterministic",
        fallback_provider_name="deterministic",
        enable_provider_fallback=False,
        model_max_retries=0,
    )
    ModelRouter(config).generate_json(
        ModelRole.PLANNER,
        "Plan one deterministic offline soak operation.",
        schema=PlannerOutput,
    )


def _prepare_worker_files(workspace: Path, parallelism: int) -> None:
    update_root = workspace / "soak_updates"
    update_root.mkdir()
    for worker_id in range(parallelism):
        (update_root / f"worker_{worker_id:02d}.py").write_text(
            "SOAK_CYCLE = -1\nSOAK_SEED = 0\n",
            encoding="utf-8",
        )


def _soak_mcp_configuration() -> tuple[McpServerConfig, ExecutableCatalog]:
    server_id = "soak-peer"
    alias = "agentbus-soak-peer"
    peer = Path(__file__).with_name("soak_mcp_peer.py").resolve(strict=True)
    config = McpServerConfig(
        server_id=server_id,
        transport="stdio",
        executable_alias=alias,
        startup_timeout_seconds=10,
        request_timeout_seconds=10,
        capability_map={"echo": mcp_server_capabilities(server_id)},
    )
    catalog = ExecutableCatalog(
        {alias: (sys.executable, "-u", str(peer))}
    )
    return config, catalog


def _exercise_managed_runtime(
    *,
    cycle: int,
    worker_id: int,
    seed: int,
    run_id: str,
    task_id: str,
    workspace: Path,
    store: StateStore,
    executable_catalog: ExecutableCatalog,
    mcp_config: McpServerConfig,
    resources: _ResourceTracker,
) -> tuple[int, int, int]:
    runtime = build_managed_tool_runtime(
        workspace=workspace,
        state_store=store,
        executable_catalog=executable_catalog,
        source_environment={},
        mcp_server_configs=(mcp_config,),
        mcp_run_id=run_id,
    )
    transport = runtime._mcp_sessions[mcp_config.server_id].client.transport
    process = getattr(transport, "_process", None)
    if not isinstance(process, subprocess.Popen):
        runtime.close()
        raise RuntimeError("The synthetic MCP peer did not start a managed process.")
    resources.process_started(process)
    try:
        read_call = _scoped_tool_call(
            runtime,
            tool_name="filesystem.read",
            arguments={"path": "package_0000/module_00000.py"},
            run_id=run_id,
            task_id=task_id,
            invocation_id=f"soak-read-{cycle:05d}",
        )
        read = runtime.invoke(
            read_call,
            run_id=run_id,
            task_id=task_id,
            caller_role="coder",
            workspace_trusted=True,
            provider_consented=True,
            invocation_id=f"soak-read-{cycle:05d}",
        )
        _require_tool_success(read.result.status, "managed filesystem read")

        write_call = _scoped_tool_call(
            runtime,
            tool_name="filesystem.write",
            arguments={
                "path": f"soak_updates/worker_{worker_id:02d}.py",
                "content": f"SOAK_CYCLE = {cycle}\nSOAK_SEED = {seed}\n",
            },
            run_id=run_id,
            task_id=task_id,
            invocation_id=f"soak-write-{cycle:05d}",
        )
        write = runtime.invoke(
            write_call,
            run_id=run_id,
            task_id=task_id,
            caller_role="coder",
            workspace_trusted=True,
            provider_consented=True,
            invocation_id=f"soak-write-{cycle:05d}",
        )
        _require_tool_success(write.result.status, "managed filesystem write")

        mcp_name = f"mcp.{mcp_config.server_id}.echo"
        mcp_call = _scoped_tool_call(
            runtime,
            tool_name=mcp_name,
            arguments={"message": f"soak-{seed}-{cycle:05d}"},
            run_id=run_id,
            task_id=task_id,
            invocation_id=f"soak-mcp-{cycle:05d}",
        )
        pending = runtime.invoke(
            mcp_call,
            run_id=run_id,
            task_id=task_id,
            caller_role="coder",
            workspace_trusted=True,
            provider_consented=True,
            invocation_id=f"soak-mcp-{cycle:05d}",
        )
        if not pending.awaiting_approval or pending.approval_request is None:
            raise RuntimeError(
                "The synthetic MCP invocation did not require exact approval."
            )
        approval = decide_tool_approval(
            pending.approval_request,
            pending.invocation,
            disposition=ToolApprovalDisposition.APPROVED,
            reason="Approve the configured offline soak peer.",
        )
        completed = runtime.dispatch(pending.invocation, approval=approval)
        _require_tool_success(completed.result.status, "approved MCP invocation")
        structured = completed.result.structured_output or {}
        echoed = structured.get("structured_content", {}).get("echo")
        if echoed != f"soak-{seed}-{cycle:05d}":
            raise RuntimeError("The synthetic MCP peer returned inconsistent output.")
        return 3, 1, 1
    finally:
        try:
            runtime.close()
            if process.poll() is None:
                raise _SoakCleanupError(
                    "The managed tool runtime left its MCP peer running."
                )
        except Exception as exc:
            if isinstance(exc, _SoakCleanupError):
                raise
            raise _SoakCleanupError(
                "The managed tool runtime did not close its MCP peer cleanly."
            ) from exc
        finally:
            resources.process_stopped(process)


def _scoped_tool_call(
    runtime: ManagedToolRuntime,
    *,
    tool_name: str,
    arguments: dict[str, Any],
    run_id: str,
    task_id: str,
    invocation_id: str,
) -> StructuredToolCall:
    descriptor = runtime.registry.descriptor(tool_name)
    broad = StructuredToolCall(
        tool_name=tool_name,
        arguments=arguments,
        expected_capabilities=descriptor.capabilities,
    )
    provisional = runtime.invocation_from_call(
        broad,
        run_id=run_id,
        task_id=task_id,
        caller_role="coder",
        workspace_trusted=True,
        provider_consented=True,
        invocation_id=invocation_id,
    )
    required = derive_required_capabilities(provisional, descriptor)
    return broad.model_copy(update={"expected_capabilities": required})


def _require_tool_success(status: ToolInvocationStatus, label: str) -> None:
    if status != ToolInvocationStatus.SUCCEEDED:
        raise RuntimeError(f"The {label} ended as {status.value}.")


def _verify_daemon_restart(
    database_path: Path,
    run_id: str,
    expected_status: RunStatus,
) -> bool:
    initial_store = StateStore(database_path)
    expected = initial_store.list_events(run_id, limit=5_000)
    if not expected:
        return False
    first_reader = ControlEventReader(initial_store)
    first = first_reader.read(run_id=run_id, limit=1)
    cursor = first[-1].sequence
    task_executed = False

    def unexpected_execution(_context) -> TaskExecutionResult:
        nonlocal task_executed
        task_executed = True
        raise RuntimeError("A terminal task was rerun after daemon restart.")

    restarted_store = StateStore(database_path)
    restarted_engine = DurableExecutionEngine(restarted_store, unexpected_execution)
    report = restarted_engine.resume(run_id)
    if task_executed or report.status != expected_status:
        return False
    reconnected_reader = ControlEventReader(restarted_store)
    remaining = reconnected_reader.read(
        run_id=run_id,
        after_sequence=cursor,
        limit=5_000,
    )
    observed = [event.sequence for event in (*first, *remaining)]
    return observed == [int(event["event_id"]) for event in expected]


def _replay_fixture(root: Path) -> tuple[Trace, ContentAddressedStore]:
    now = datetime(2026, 1, 1, tzinfo=UTC)
    span = TraceSpan(
        trace_id="soak-trace",
        span_id="soak-root",
        parent_span_id=None,
        run_id="soak-source-run",
        span_type=TraceSpanType.RUN,
        name="soak source run",
        sequence=1,
        started_at=now,
        ended_at=now,
        status=TraceStatus.SUCCEEDED,
    )
    trace = Trace(
        trace_id="soak-trace",
        run_id="soak-source-run",
        root_span_id=span.span_id,
        status=TraceStatus.SUCCEEDED,
        created_at=now,
        completed_at=now,
        spans=[span],
    )
    object_store = ContentAddressedStore(root / "replay-objects")
    object_store.put_json(
        trace.model_dump(mode="json"),
        producing_span_id=span.span_id,
    )
    return trace, object_store


def _initialize_git_repository(workspace: Path, hooks: Path) -> None:
    hooks.mkdir()
    _git(workspace, "init", "-q", "--initial-branch=main")
    _git(workspace, "add", "--all")
    _git(
        workspace,
        "-c",
        f"core.hooksPath={hooks}",
        "-c",
        "commit.gpgSign=false",
        "-c",
        "user.name=AgentBus Soak",
        "-c",
        "user.email=soak@agentbus.invalid",
        "commit",
        "-q",
        "-m",
        "chore: initialize soak fixture",
    )


def _git(workspace: Path, *arguments: str) -> str:
    result = subprocess.run(
        ["git", *arguments],
        cwd=workspace,
        capture_output=True,
        text=True,
        timeout=60,
        shell=False,
        env={**os.environ, "GIT_CONFIG_NOSYSTEM": "1"},
        check=False,
    )
    if result.returncode != 0:
        detail = redact_text(result.stderr.strip(), max_chars=500) or "unknown error"
        raise RuntimeError(f"Git soak operation failed: {detail}")
    return result.stdout.strip()


def _repair_clean_worktrees(manager: GitWorktreeManager) -> int:
    failures = 0
    for record in manager.list_owned():
        if record.status == WorktreeStatus.REMOVED:
            continue
        try:
            if not manager.is_clean(record):
                failures += 1
                continue
            if record.status != WorktreeStatus.CLEANUP_PENDING:
                manager.mark_cleanup_pending(record.worktree_id)
            manager.remove(record.worktree_id)
        except Exception:
            failures += 1
    return failures


def _event_summary(store: StateStore) -> tuple[int, int]:
    cursor = 0
    count = 0
    gaps = 0
    previous: int | None = None
    while True:
        page = store.list_all_events(after_event_id=cursor, limit=5_000)
        if not page:
            return count, gaps
        for event in page:
            identifier = int(event["event_id"])
            if previous is not None and identifier != previous + 1:
                gaps += 1
            previous = identifier
            count += 1
        cursor = int(page[-1]["event_id"])


def _stale_lease_count(store: StateStore) -> int:
    now = datetime.now(UTC)
    stale = 0
    for lease in store.list_worker_lease_rows():
        if lease["status"] != "active":
            continue
        value = str(lease["expires_at"]).replace("Z", "+00:00")
        expires = datetime.fromisoformat(value)
        if expires.tzinfo is None:
            expires = expires.replace(tzinfo=UTC)
        stale += expires <= now
    return stale


def _active_worktree_count(manager: GitWorktreeManager) -> int:
    return sum(
        record.status != WorktreeStatus.REMOVED for record in manager.list_owned()
    )


def _database_bytes(path: Path) -> int | None:
    candidates = (
        path,
        Path(f"{path}-wal"),
        Path(f"{path}-shm"),
        Path(f"{path}-journal"),
    )
    total = 0
    try:
        for candidate in candidates:
            if candidate.is_file() and not candidate.is_symlink():
                total += candidate.stat().st_size
    except OSError:
        return None
    return total


def _directory_bytes(root: Path) -> int | None:
    total = 0
    try:
        for directory, directories, files in os.walk(root, followlinks=False):
            current = Path(directory)
            directories[:] = [
                name for name in directories if not (current / name).is_symlink()
            ]
            for name in files:
                candidate = current / name
                if candidate.is_file() and not candidate.is_symlink():
                    total += candidate.stat().st_size
    except OSError:
        return None
    return total


def _current_handle_count() -> int | None:
    if os.name == "nt":
        try:
            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel32.GetCurrentProcess.restype = ctypes.c_void_p
            kernel32.GetProcessHandleCount.argtypes = [
                ctypes.c_void_p,
                ctypes.POINTER(ctypes.c_ulong),
            ]
            count = ctypes.c_ulong()
            process = kernel32.GetCurrentProcess()
            if not kernel32.GetProcessHandleCount(process, ctypes.byref(count)):
                return None
            return int(count.value)
        except (AttributeError, OSError):
            return None
    descriptor_root = Path("/proc/self/fd")
    if not descriptor_root.is_dir():
        return None
    try:
        with os.scandir(descriptor_root) as entries:
            return sum(1 for _ in entries)
    except OSError:
        return None


def _validate_options(
    *,
    duration_seconds: float,
    runs: int,
    parallelism: int,
    seed: int,
    repository_files: int,
) -> None:
    if duration_seconds <= 0 or duration_seconds > _MAX_DURATION_SECONDS:
        raise ValueError(
            "Soak duration must be greater than zero and at most 86400 seconds."
        )
    if runs < 1 or runs > _MAX_RUNS:
        raise ValueError(f"Soak runs must be between 1 and {_MAX_RUNS}.")
    if parallelism < 1 or parallelism > _MAX_PARALLELISM:
        raise ValueError(
            f"Soak parallelism must be between 1 and {_MAX_PARALLELISM}."
        )
    if seed < 0 or seed > 2**31 - 1:
        raise ValueError("Soak seed must be between 0 and 2147483647.")
    if repository_files < 1 or repository_files > 1_000:
        raise ValueError("Soak repository files must be between 1 and 1000.")
