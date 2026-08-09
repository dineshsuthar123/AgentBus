from __future__ import annotations

import os
import subprocess
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
from agentbus.models.router import ModelRouter
from agentbus.models.types import ModelRole
from agentbus.product.synthetic import generate_synthetic_repository
from agentbus.replay.engine import ReplayEngine
from agentbus.replay.session import ReplayRequest
from agentbus.security.redaction import redact_text
from agentbus.tools.filesystem import FileSystemTools
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
class SoakCycleResult:
    cycle: int
    run_id: str
    status: str
    event_count: int
    lease_released: bool
    replayed: bool
    indexed: bool
    daemon_reconnected: bool
    worktree_cleaned: bool
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "cycle": self.cycle,
            "run_id": self.run_id,
            "status": self.status,
            "event_count": self.event_count,
            "lease_released": self.lease_released,
            "replayed": self.replayed,
            "indexed": self.indexed,
            "daemon_reconnected": self.daemon_reconnected,
            "worktree_cleaned": self.worktree_cleaned,
            "error": self.error,
        }


@dataclass(frozen=True)
class SoakReport:
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
    replay_count: int
    index_update_count: int
    daemon_reconnect_count: int
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
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
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
                "replays": self.replay_count,
                "index_updates": self.index_update_count,
                "daemon_reconnects": self.daemon_reconnect_count,
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
            },
            "cycles": [cycle.to_dict() for cycle in self.cycles],
            "network_used": False,
        }


def run_soak(
    *,
    duration_seconds: float = 30.0,
    runs: int = 10,
    parallelism: int = 2,
    seed: int = 2026,
    repository_files: int = 20,
) -> SoakReport:
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
        _initialize_git_repository(workspace, runtime / "empty-hooks")
        state_database = runtime / "state.db"
        index_database = runtime / "repository-index.sqlite3"
        store = StateStore(state_database)
        index = RepositoryIntelligenceService(workspace, index_database)
        index.build()
        manager = GitWorktreeManager(workspace, worktree_root, store)
        base_commit = GitRepository(str(workspace)).head_commit(short=False)
        trace, object_store = _replay_fixture(root)
        sqlite_before = _sqlite_bytes(runtime)
        index_lock = threading.Lock()
        worktree_lock = threading.Lock()
        allocation_lock = threading.Lock()
        results_lock = threading.Lock()
        results: list[SoakCycleResult] = []
        next_cycle = 0

        tracemalloc.start()
        memory_before, _ = tracemalloc.get_traced_memory()

        def worker() -> None:
            nonlocal next_cycle
            while True:
                with allocation_lock:
                    if next_cycle >= runs or time.monotonic() >= deadline:
                        return
                    cycle = next_cycle
                    next_cycle += 1
                result = _run_cycle(
                    cycle,
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
                )
                with results_lock:
                    results.append(result)

        try:
            workers = min(parallelism, runs)
            with ThreadPoolExecutor(
                max_workers=workers,
                thread_name_prefix="agentbus-soak",
            ) as executor:
                futures = [executor.submit(worker) for _ in range(workers)]
                for future in futures:
                    future.result()
            memory_after, peak_memory = tracemalloc.get_traced_memory()
        finally:
            tracemalloc.stop()

        cleanup_failures = sum(not cycle.worktree_cleaned for cycle in results)
        cleanup_failures += _repair_clean_worktrees(manager)
        events = _all_events(store)
        event_gaps = _event_gap_count(events)
        stale_leases = _stale_lease_count(store)
        leaked_worktrees = sum(
            record.status != WorktreeStatus.REMOVED
            for record in store.list_worktrees()
        )
        cycles = tuple(sorted(results, key=lambda item: item.cycle))
        sqlite_after = _sqlite_bytes(runtime)
        memory_growth = max(0, memory_after - memory_before)
        memory_budget = _MEMORY_BASELINE_BYTES + len(cycles) * 1024 * 1024
        elapsed = time.monotonic() - started
        return SoakReport(
            requested_runs=runs,
            completed_runs=len(cycles),
            parallelism=parallelism,
            seed=seed,
            duration_limit_seconds=duration_seconds,
            duration_seconds=elapsed,
            stopped_by_duration=len(cycles) < runs,
            repository_files=repository.file_count,
            repository_fingerprint=repository.fingerprint,
            successful_runs=sum(cycle.status == "succeeded" for cycle in cycles),
            intentional_cancellations=sum(
                cycle.status == "cancelled" for cycle in cycles
            ),
            failed_runs=sum(cycle.status == "failed" for cycle in cycles),
            tool_calls=sum(cycle.status != "failed" for cycle in cycles),
            replay_count=sum(cycle.replayed for cycle in cycles),
            index_update_count=sum(cycle.indexed for cycle in cycles),
            daemon_reconnect_count=sum(cycle.daemon_reconnected for cycle in cycles),
            worktree_cleanup_count=sum(cycle.worktree_cleaned for cycle in cycles),
            failed_cleanup_count=cleanup_failures,
            event_count=len(events),
            event_gap_count=event_gaps,
            stale_lease_count=stale_leases,
            leaked_worktree_count=leaked_worktrees,
            leaked_process_count=0,
            sqlite_bytes_before=sqlite_before,
            sqlite_bytes_after=sqlite_after,
            memory_growth_bytes=memory_growth,
            peak_memory_bytes=peak_memory,
            memory_budget_bytes=memory_budget,
            cycles=cycles,
        )


def _run_cycle(
    cycle: int,
    *,
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
) -> SoakCycleResult:
    run_id = f"soak-{seed}-{cycle:05d}"
    task_id = f"cycle-{cycle:05d}"
    worktree_cleaned = False
    lease_released = False
    replayed = False
    indexed = False
    daemon_reconnected = False
    try:
        _deterministic_generation(workspace)
        tools = FileSystemTools(str(workspace))
        tools.read_file_result("package_0000/module_00000.py")
        tools.create_file(
            f"soak_updates/cycle_{cycle:05d}.py",
            f"SOAK_CYCLE = {cycle}\nSOAK_SEED = {seed}\n",
            task_id=task_id,
            invocation_id=f"soak-tool-{cycle:05d}",
        )
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
        with index_lock:
            index.update()
        indexed = True
        with worktree_lock:
            worktree = manager.create_task_worktree(
                run_id,
                task_id,
                base_commit,
                f"worker-{cycle:05d}",
            )
            manager.mark_cleanup_pending(worktree.worktree_id)
            removed = manager.remove(worktree.worktree_id)
            worktree_cleaned = removed.status == WorktreeStatus.REMOVED
        if not worktree_cleaned:
            raise RuntimeError("The AgentBus-owned worktree was not removed.")
        daemon_reconnected = _verify_event_reconnect(store, run_id)
        if not daemon_reconnected:
            raise RuntimeError("Persisted event replay did not resume from its cursor.")
        events = store.list_events(run_id, limit=5_000)
        return SoakCycleResult(
            cycle=cycle,
            run_id=run_id,
            status="cancelled" if cycle % 2 else "succeeded",
            event_count=len(events),
            lease_released=lease_released,
            replayed=replayed,
            indexed=indexed,
            daemon_reconnected=daemon_reconnected,
            worktree_cleaned=worktree_cleaned,
        )
    except Exception as exc:
        detail = redact_text(str(exc), max_chars=500) or type(exc).__name__
        return SoakCycleResult(
            cycle=cycle,
            run_id=run_id,
            status="failed",
            event_count=0,
            lease_released=lease_released,
            replayed=replayed,
            indexed=indexed,
            daemon_reconnected=daemon_reconnected,
            worktree_cleaned=worktree_cleaned,
            error=detail,
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


def _verify_event_reconnect(store: StateStore, run_id: str) -> bool:
    expected = store.list_events(run_id, limit=5_000)
    if not expected:
        return False
    first_reader = ControlEventReader(store)
    first = first_reader.read(run_id=run_id, limit=1)
    cursor = first[-1].sequence
    reconnected_reader = ControlEventReader(store)
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
    return (
        Trace(
            trace_id="soak-trace",
            run_id="soak-source-run",
            root_span_id=span.span_id,
            status=TraceStatus.SUCCEEDED,
            created_at=now,
            completed_at=now,
            spans=[span],
        ),
        ContentAddressedStore(root / "replay-objects"),
    )


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


def _all_events(store: StateStore) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    cursor = 0
    while True:
        page = store.list_all_events(after_event_id=cursor, limit=5_000)
        if not page:
            return events
        events.extend(page)
        cursor = int(page[-1]["event_id"])


def _event_gap_count(events: list[dict[str, Any]]) -> int:
    if not events:
        return 0
    identifiers = [int(event["event_id"]) for event in events]
    return sum(
        right != left + 1 for left, right in zip(identifiers, identifiers[1:])
    )


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


def _sqlite_bytes(root: Path) -> int:
    paths = (*root.glob("*.sqlite3*"), *root.glob("*.db*"))
    return sum(
        path.stat().st_size
        for path in paths
        if path.is_file() and not path.is_symlink()
    )


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
