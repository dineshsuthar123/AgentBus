from __future__ import annotations

import threading
from dataclasses import dataclass, field, replace

from agentbus.tools.protocol import (
    ToolInvocation,
    ToolInvocationStatus,
    ToolResourceBudget,
    ToolResourceUsage,
    canonical_json,
)
from agentbus.tools.records import (
    TERMINAL_TOOL_STATUSES,
    ToolInvocationRecord,
    invocation_identity_sha256,
)


class ToolBudgetError(RuntimeError):
    """Base error for tool resource accounting failures."""


class ToolBudgetExceeded(ToolBudgetError):
    def __init__(self, limit_name: str, message: str) -> None:
        super().__init__(message)
        self.limit_name = limit_name


class ToolBudgetConflict(ToolBudgetError):
    """Raised when an invocation identity is reused with different scope."""


@dataclass(frozen=True)
class ToolBudgetReservation:
    invocation_id: str
    invocation_revision: int
    run_id: str
    task_id: str
    sequence: int
    anticipated_usage: ToolResourceUsage
    process_slot: bool
    duplicate: bool = False


@dataclass(frozen=True)
class ToolBudgetSnapshot:
    run_id: str
    invocation_count: int
    task_invocation_counts: dict[str, int]
    active_invocations: tuple[str, ...]
    active_processes: int
    completed_invocations: tuple[str, ...]
    file_mutations: int
    written_bytes: int
    reserved_file_mutations: int
    reserved_written_bytes: int
    invocation_limit: int
    task_invocation_limits: dict[str, int]
    process_limit: int
    file_mutation_limit: int
    written_bytes_limit: int


@dataclass
class _InvocationState:
    reservation: ToolBudgetReservation
    invocation_fingerprint: str
    budget_json: str
    active: bool = True
    completed: bool = False
    process_active: bool = False
    usage: ToolResourceUsage = field(default_factory=ToolResourceUsage)


@dataclass
class _RunState:
    run_id: str
    invocation_limit: int
    process_limit: int
    file_mutation_limit: int
    written_bytes_limit: int
    task_invocation_limits: dict[str, int] = field(default_factory=dict)
    task_invocation_counts: dict[str, int] = field(default_factory=dict)
    invocations: dict[str, _InvocationState] = field(default_factory=dict)
    sequence: int = 0
    active_processes: int = 0
    file_mutations: int = 0
    written_bytes: int = 0
    reserved_file_mutations: int = 0
    reserved_written_bytes: int = 0


class ToolBudgetLedger:
    """Atomically accounts cumulative limits without resetting duplicate calls."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._runs: dict[str, _RunState] = {}

    def begin(
        self,
        invocation: ToolInvocation,
        *,
        anticipated_usage: ToolResourceUsage | None = None,
        process_slot: bool = False,
    ) -> ToolBudgetReservation:
        anticipated = anticipated_usage or ToolResourceUsage()
        self._validate_usage(invocation.resource_budget, anticipated)
        invocation_fingerprint = invocation_identity_sha256(invocation)
        budget_json = canonical_json(invocation.resource_budget.model_dump(mode="json"))
        with self._lock:
            state = self._runs.get(invocation.run_id)
            if state is None:
                state = _RunState(
                    run_id=invocation.run_id,
                    invocation_limit=invocation.resource_budget.invocations_per_run,
                    process_limit=invocation.resource_budget.concurrent_processes,
                    file_mutation_limit=invocation.resource_budget.file_mutations,
                    written_bytes_limit=invocation.resource_budget.total_written_bytes,
                )
                self._runs[invocation.run_id] = state
            existing = state.invocations.get(invocation.invocation_id)
            if existing is not None:
                self._require_same_invocation(
                    existing,
                    invocation,
                    invocation_fingerprint,
                    budget_json,
                    anticipated,
                    process_slot,
                )
                return replace(existing.reservation, duplicate=True)

            self._tighten_limits(state, invocation.resource_budget, invocation.task_id)
            invocation_count = len(state.invocations) + 1
            if invocation_count > state.invocation_limit:
                raise ToolBudgetExceeded(
                    "invocations_per_run",
                    "Tool invocation count exceeds the run budget.",
                )
            task_count = state.task_invocation_counts.get(invocation.task_id, 0) + 1
            task_limit = state.task_invocation_limits[invocation.task_id]
            if task_count > task_limit:
                raise ToolBudgetExceeded(
                    "invocations_per_task",
                    "Tool invocation count exceeds the task budget.",
                )
            self._require_cumulative_capacity(state, anticipated)

            state.sequence += 1
            reservation = ToolBudgetReservation(
                invocation_id=invocation.invocation_id,
                invocation_revision=invocation.invocation_revision,
                run_id=invocation.run_id,
                task_id=invocation.task_id,
                sequence=state.sequence,
                anticipated_usage=anticipated,
                process_slot=process_slot,
            )
            state.invocations[invocation.invocation_id] = _InvocationState(
                reservation=reservation,
                invocation_fingerprint=invocation_fingerprint,
                budget_json=budget_json,
            )
            state.task_invocation_counts[invocation.task_id] = task_count
            state.reserved_file_mutations += anticipated.file_mutations
            state.reserved_written_bytes += anticipated.written_bytes
            return reservation

    def activate_process(
        self,
        reservation: ToolBudgetReservation,
    ) -> ToolBudgetSnapshot:
        with self._lock:
            state, invocation_state = self._active_state(reservation)
            if not reservation.process_slot:
                raise ToolBudgetConflict(
                    "The invocation did not reserve process execution capability."
                )
            if invocation_state.process_active:
                return self._snapshot(state)
            if state.active_processes + 1 > state.process_limit:
                raise ToolBudgetExceeded(
                    "concurrent_processes",
                    "Concurrent process count exceeds the run budget.",
                )
            invocation_state.process_active = True
            state.active_processes += 1
            return self._snapshot(state)

    def restore(self, record: ToolInvocationRecord) -> ToolBudgetSnapshot:
        if record.status not in TERMINAL_TOOL_STATUSES:
            self._validate_usage(record.resource_budget, record.anticipated_usage)
        budget_json = canonical_json(record.resource_budget.model_dump(mode="json"))
        with self._lock:
            state = self._runs.get(record.run_id)
            if state is None:
                state = _RunState(
                    run_id=record.run_id,
                    invocation_limit=record.resource_budget.invocations_per_run,
                    process_limit=record.resource_budget.concurrent_processes,
                    file_mutation_limit=record.resource_budget.file_mutations,
                    written_bytes_limit=record.resource_budget.total_written_bytes,
                )
                self._runs[record.run_id] = state
            existing = state.invocations.get(record.invocation_id)
            if existing is not None:
                if (
                    existing.invocation_fingerprint != record.invocation_sha256
                    or existing.budget_json != budget_json
                    or existing.reservation.anticipated_usage
                    != record.anticipated_usage
                    or existing.reservation.process_slot != record.process_slot
                ):
                    raise ToolBudgetConflict(
                        "Persisted invocation conflicts with restored budget state."
                    )
                return self._snapshot(state)

            self._tighten_limits(state, record.resource_budget, record.task_id)
            if len(state.invocations) + 1 > state.invocation_limit:
                raise ToolBudgetExceeded(
                    "invocations_per_run",
                    "Persisted invocation count exceeds the run budget.",
                )
            task_count = state.task_invocation_counts.get(record.task_id, 0) + 1
            if task_count > state.task_invocation_limits[record.task_id]:
                raise ToolBudgetExceeded(
                    "invocations_per_task",
                    "Persisted task invocation count exceeds the task budget.",
                )

            active = record.status not in TERMINAL_TOOL_STATUSES
            process_active = (
                active
                and record.status == ToolInvocationStatus.RUNNING
                and record.process_slot
            )
            if active:
                self._require_cumulative_capacity(state, record.anticipated_usage)
            if process_active and state.active_processes + 1 > state.process_limit:
                raise ToolBudgetExceeded(
                    "concurrent_processes",
                    "Persisted active processes exceed the run budget.",
                )

            reservation = ToolBudgetReservation(
                invocation_id=record.invocation_id,
                invocation_revision=record.invocation_revision,
                run_id=record.run_id,
                task_id=record.task_id,
                sequence=record.invocation_sequence,
                anticipated_usage=record.anticipated_usage,
                process_slot=record.process_slot,
            )
            state.invocations[record.invocation_id] = _InvocationState(
                reservation=reservation,
                invocation_fingerprint=record.invocation_sha256,
                budget_json=budget_json,
                active=active,
                completed=not active,
                process_active=process_active,
                usage=record.resource_usage,
            )
            state.sequence = max(state.sequence, record.invocation_sequence)
            state.task_invocation_counts[record.task_id] = task_count
            if active:
                state.reserved_file_mutations += record.anticipated_usage.file_mutations
                state.reserved_written_bytes += record.anticipated_usage.written_bytes
            else:
                state.file_mutations += record.resource_usage.file_mutations
                state.written_bytes += record.resource_usage.written_bytes
            if process_active:
                state.active_processes += 1
            return self._snapshot(state)

    def complete(
        self,
        reservation: ToolBudgetReservation,
        usage: ToolResourceUsage,
    ) -> ToolBudgetSnapshot:
        with self._lock:
            state, invocation_state = self._active_state(reservation)
            budget = ToolResourceBudget.model_validate_json(invocation_state.budget_json)
            violation: ToolBudgetExceeded | None = None
            try:
                self._validate_usage(budget, usage)
                actual_mutations = (
                    state.file_mutations
                    + state.reserved_file_mutations
                    - reservation.anticipated_usage.file_mutations
                    + usage.file_mutations
                )
                actual_written = (
                    state.written_bytes
                    + state.reserved_written_bytes
                    - reservation.anticipated_usage.written_bytes
                    + usage.written_bytes
                )
                if actual_mutations > state.file_mutation_limit:
                    raise ToolBudgetExceeded(
                        "file_mutations",
                        "Completed tool usage exceeds the run mutation budget.",
                    )
                if actual_written > state.written_bytes_limit:
                    raise ToolBudgetExceeded(
                        "total_written_bytes",
                        "Completed tool usage exceeds the run write budget.",
                    )
            except ToolBudgetExceeded as exc:
                violation = exc
            self._finish(state, invocation_state, usage)
            snapshot = self._snapshot(state)
            if violation is not None:
                raise violation
            return snapshot

    def abort(self, reservation: ToolBudgetReservation) -> ToolBudgetSnapshot:
        with self._lock:
            state, invocation_state = self._active_state(reservation)
            self._finish(state, invocation_state, ToolResourceUsage())
            return self._snapshot(state)

    def abandon_active(self, run_id: str) -> ToolBudgetSnapshot | None:
        with self._lock:
            state = self._runs.get(run_id)
            if state is None:
                return None
            for invocation_state in tuple(state.invocations.values()):
                if invocation_state.active:
                    self._finish(state, invocation_state, ToolResourceUsage())
            return self._snapshot(state)

    def snapshot(self, run_id: str) -> ToolBudgetSnapshot | None:
        with self._lock:
            state = self._runs.get(run_id)
            return self._snapshot(state) if state is not None else None

    @staticmethod
    def _tighten_limits(
        state: _RunState,
        budget: ToolResourceBudget,
        task_id: str,
    ) -> None:
        state.invocation_limit = min(
            state.invocation_limit,
            budget.invocations_per_run,
        )
        state.process_limit = min(
            state.process_limit,
            budget.concurrent_processes,
        )
        state.file_mutation_limit = min(
            state.file_mutation_limit,
            budget.file_mutations,
        )
        state.written_bytes_limit = min(
            state.written_bytes_limit,
            budget.total_written_bytes,
        )
        previous = state.task_invocation_limits.get(task_id)
        state.task_invocation_limits[task_id] = (
            budget.invocations_per_task
            if previous is None
            else min(previous, budget.invocations_per_task)
        )

    @staticmethod
    def _require_cumulative_capacity(
        state: _RunState,
        anticipated: ToolResourceUsage,
    ) -> None:
        if (
            state.file_mutations
            + state.reserved_file_mutations
            + anticipated.file_mutations
            > state.file_mutation_limit
        ):
            raise ToolBudgetExceeded(
                "file_mutations",
                "Tool mutation reservation exceeds the run budget.",
            )
        if (
            state.written_bytes
            + state.reserved_written_bytes
            + anticipated.written_bytes
            > state.written_bytes_limit
        ):
            raise ToolBudgetExceeded(
                "total_written_bytes",
                "Tool write reservation exceeds the run budget.",
            )

    @staticmethod
    def _validate_usage(
        budget: ToolResourceBudget,
        usage: ToolResourceUsage,
    ) -> None:
        limits = (
            ("wall_clock_seconds", usage.wall_clock_seconds, budget.wall_clock_seconds),
            ("stdout_bytes", usage.stdout_bytes, budget.stdout_bytes),
            ("stderr_bytes", usage.stderr_bytes, budget.stderr_bytes),
            ("artifact_bytes", usage.artifact_bytes, budget.artifact_bytes),
            ("child_processes", usage.child_processes, budget.child_processes),
            ("file_mutations", usage.file_mutations, budget.file_mutations),
            ("written_bytes", usage.written_bytes, budget.total_written_bytes),
        )
        for name, observed, limit in limits:
            if observed > limit:
                raise ToolBudgetExceeded(
                    name,
                    f"Tool usage exceeds the {name} budget.",
                )
        if usage.stdout_bytes + usage.stderr_bytes > budget.combined_output_bytes:
            raise ToolBudgetExceeded(
                "combined_output_bytes",
                "Tool output exceeds the combined output budget.",
            )
        if (
            budget.memory_bytes is not None
            and usage.memory_bytes is not None
            and usage.memory_bytes > budget.memory_bytes
        ):
            raise ToolBudgetExceeded(
                "memory_bytes",
                "Tool usage exceeds the memory budget.",
            )
        if (
            budget.cpu_seconds is not None
            and usage.cpu_seconds is not None
            and usage.cpu_seconds > budget.cpu_seconds
        ):
            raise ToolBudgetExceeded(
                "cpu_seconds",
                "Tool usage exceeds the CPU budget.",
            )

    @staticmethod
    def _require_same_invocation(
        existing: _InvocationState,
        invocation: ToolInvocation,
        invocation_fingerprint: str,
        budget_json: str,
        anticipated: ToolResourceUsage,
        process_slot: bool,
    ) -> None:
        reservation = existing.reservation
        if (
            reservation.run_id != invocation.run_id
            or reservation.task_id != invocation.task_id
            or reservation.invocation_revision != invocation.invocation_revision
            or existing.invocation_fingerprint != invocation_fingerprint
            or existing.budget_json != budget_json
            or reservation.anticipated_usage != anticipated
            or reservation.process_slot != process_slot
        ):
            raise ToolBudgetConflict(
                "Invocation identity was reused with different budget scope."
            )

    def _active_state(
        self,
        reservation: ToolBudgetReservation,
    ) -> tuple[_RunState, _InvocationState]:
        state = self._runs.get(reservation.run_id)
        invocation_state = (
            state.invocations.get(reservation.invocation_id)
            if state is not None
            else None
        )
        canonical_reservation = replace(reservation, duplicate=False)
        if (
            invocation_state is None
            or invocation_state.reservation != canonical_reservation
        ):
            raise ToolBudgetConflict("Unknown or mismatched budget reservation.")
        if not invocation_state.active:
            raise ToolBudgetConflict("Budget reservation is already terminal.")
        return state, invocation_state

    @staticmethod
    def _finish(
        state: _RunState,
        invocation_state: _InvocationState,
        usage: ToolResourceUsage,
    ) -> None:
        reservation = invocation_state.reservation
        state.reserved_file_mutations -= reservation.anticipated_usage.file_mutations
        state.reserved_written_bytes -= reservation.anticipated_usage.written_bytes
        state.file_mutations += usage.file_mutations
        state.written_bytes += usage.written_bytes
        if invocation_state.process_active:
            state.active_processes -= 1
            invocation_state.process_active = False
        invocation_state.active = False
        invocation_state.completed = True
        invocation_state.usage = usage

    @staticmethod
    def _snapshot(state: _RunState) -> ToolBudgetSnapshot:
        return ToolBudgetSnapshot(
            run_id=state.run_id,
            invocation_count=len(state.invocations),
            task_invocation_counts=dict(sorted(state.task_invocation_counts.items())),
            active_invocations=tuple(
                sorted(
                    invocation_id
                    for invocation_id, invocation_state in state.invocations.items()
                    if invocation_state.active
                )
            ),
            active_processes=state.active_processes,
            completed_invocations=tuple(
                sorted(
                    invocation_id
                    for invocation_id, invocation_state in state.invocations.items()
                    if invocation_state.completed
                )
            ),
            file_mutations=state.file_mutations,
            written_bytes=state.written_bytes,
            reserved_file_mutations=state.reserved_file_mutations,
            reserved_written_bytes=state.reserved_written_bytes,
            invocation_limit=state.invocation_limit,
            task_invocation_limits=dict(sorted(state.task_invocation_limits.items())),
            process_limit=state.process_limit,
            file_mutation_limit=state.file_mutation_limit,
            written_bytes_limit=state.written_bytes_limit,
        )
