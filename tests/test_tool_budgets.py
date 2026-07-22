from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from pathlib import Path

import pytest

from agentbus.tools.budget import (
    ToolBudgetConflict,
    ToolBudgetExceeded,
    ToolBudgetLedger,
)
from agentbus.tools.protocol import (
    CapabilityScope,
    ToolCapability,
    ToolCapabilityName,
    ToolInvocation,
    ToolInvocationContext,
    ToolInvocationStatus,
    ToolResourceBudget,
    ToolResourceUsage,
    ToolVersion,
)
from agentbus.tools.records import ToolInvocationRecord, invocation_record_values


def invocation(
    root: Path,
    invocation_id: str,
    *,
    task_id: str = "task-1",
    budget: ToolResourceBudget | None = None,
) -> ToolInvocation:
    capability = ToolCapability(
        name=ToolCapabilityName.FILESYSTEM_WRITE,
        scope=CapabilityScope(roots=(str(root.resolve()),)),
    )
    return ToolInvocation(
        invocation_id=invocation_id,
        run_id="run-1",
        task_id=task_id,
        tool_name="filesystem.write",
        tool_version=ToolVersion(major=1),
        arguments={"path": "module.py", "content": "value = 1\n"},
        requested_capabilities=(capability,),
        context=ToolInvocationContext(
            workspace_identity=str(root.resolve()),
            worktree_identity=str(root.resolve()),
            caller_role="coder",
            workspace_trusted=True,
            provider_consented=True,
        ),
        resource_budget=budget or ToolResourceBudget(),
    )


def test_duplicate_invocation_does_not_reset_or_increment_budget(tmp_path: Path) -> None:
    ledger = ToolBudgetLedger()
    current = invocation(
        tmp_path,
        "invocation-1",
        budget=ToolResourceBudget(invocations_per_task=1, invocations_per_run=1),
    )

    first = ledger.begin(current)
    duplicate = ledger.begin(current)
    delayed_duplicate = ledger.begin(
        current.model_copy(update={"requested_at": current.requested_at + timedelta(1)})
    )

    assert duplicate.duplicate is True
    assert delayed_duplicate.duplicate is True
    assert duplicate.sequence == first.sequence
    assert ledger.snapshot("run-1").invocation_count == 1
    with pytest.raises(ToolBudgetExceeded) as captured:
        ledger.begin(
            invocation(
                tmp_path,
                "invocation-2",
                budget=ToolResourceBudget(
                    invocations_per_task=2,
                    invocations_per_run=2,
                ),
            )
        )
    assert captured.value.limit_name == "invocations_per_run"


def test_duplicate_identity_cannot_change_revision_or_budget(tmp_path: Path) -> None:
    ledger = ToolBudgetLedger()
    original = invocation(tmp_path, "invocation-1")
    ledger.begin(original)

    with pytest.raises(ToolBudgetConflict):
        ledger.begin(
            original.model_copy(
                update={
                    "invocation_revision": 2,
                    "resource_budget": ToolResourceBudget(
                        stdout_bytes=1,
                        combined_output_bytes=65_537,
                    ),
                }
            )
        )

    with pytest.raises(ToolBudgetConflict):
        ledger.begin(
            original.model_copy(
                update={"arguments": {"path": "other.py", "content": "changed\n"}}
            )
        )


def test_mutation_and_written_byte_capacity_is_reserved_atomically(
    tmp_path: Path,
) -> None:
    ledger = ToolBudgetLedger()
    budget = ToolResourceBudget(
        file_mutations=1,
        total_written_bytes=5,
        maximum_file_bytes=5,
    )
    first = ledger.begin(
        invocation(tmp_path, "invocation-1", budget=budget),
        anticipated_usage=ToolResourceUsage(file_mutations=1, written_bytes=5),
    )

    with pytest.raises(ToolBudgetExceeded, match="mutation reservation"):
        ledger.begin(
            invocation(tmp_path, "invocation-2", budget=budget),
            anticipated_usage=ToolResourceUsage(file_mutations=1, written_bytes=1),
        )
    snapshot = ledger.complete(
        first,
        ToolResourceUsage(file_mutations=1, written_bytes=5),
    )
    assert snapshot.file_mutations == 1
    assert snapshot.written_bytes == 5
    assert snapshot.reserved_file_mutations == 0


def test_completion_records_actual_usage_before_reporting_overrun(tmp_path: Path) -> None:
    ledger = ToolBudgetLedger()
    budget = ToolResourceBudget(
        stdout_bytes=5,
        stderr_bytes=5,
        combined_output_bytes=5,
    )
    reserved = ledger.begin(invocation(tmp_path, "invocation-1", budget=budget))

    with pytest.raises(ToolBudgetExceeded) as captured:
        ledger.complete(
            reserved,
            ToolResourceUsage(stdout_bytes=4, stderr_bytes=2),
        )

    assert captured.value.limit_name == "combined_output_bytes"
    snapshot = ledger.snapshot("run-1")
    assert snapshot.active_invocations == ()
    assert snapshot.completed_invocations == ("invocation-1",)


def test_process_concurrency_limit_is_atomic_across_threads(tmp_path: Path) -> None:
    ledger = ToolBudgetLedger()
    budget = ToolResourceBudget(concurrent_processes=2)
    reservations = [
        ledger.begin(
            invocation(tmp_path, f"invocation-{index}", budget=budget),
            process_slot=True,
        )
        for index in range(12)
    ]

    def activate(reservation):
        try:
            ledger.activate_process(reservation)
            return reservation
        except ToolBudgetExceeded:
            return None

    with ThreadPoolExecutor(max_workers=12) as pool:
        activated = list(pool.map(activate, reservations))

    accepted = [reservation for reservation in activated if reservation is not None]
    assert len(accepted) == 2
    assert ledger.snapshot("run-1").active_processes == 2
    for reservation in reservations:
        ledger.abort(reservation)
    assert ledger.snapshot("run-1").active_processes == 0


def test_process_slot_requires_process_capability_and_is_idempotent(
    tmp_path: Path,
) -> None:
    ledger = ToolBudgetLedger()
    ordinary = ledger.begin(invocation(tmp_path, "invocation-1"))
    process = ledger.begin(
        invocation(tmp_path, "invocation-2"),
        process_slot=True,
    )
    duplicate_process = ledger.begin(
        invocation(tmp_path, "invocation-2"),
        process_slot=True,
    )

    with pytest.raises(ToolBudgetConflict, match="process execution"):
        ledger.activate_process(ordinary)
    first = ledger.activate_process(duplicate_process)
    duplicate = ledger.activate_process(process)

    assert first.active_processes == duplicate.active_processes == 1
    ledger.abort(ordinary)
    assert ledger.abort(process).active_processes == 0


def test_persisted_budget_state_restores_usage_reservations_and_processes(
    tmp_path: Path,
) -> None:
    budget = ToolResourceBudget(
        concurrent_processes=1,
        file_mutations=3,
        total_written_bytes=30,
    )
    completed_invocation = invocation(
        tmp_path,
        "invocation-1",
        budget=budget,
    )
    active_invocation = invocation(
        tmp_path,
        "invocation-2",
        budget=budget,
    )
    completed_values = invocation_record_values(
        completed_invocation,
        anticipated_usage=ToolResourceUsage(file_mutations=1, written_bytes=10),
        updated_at=completed_invocation.requested_at,
    )
    active_values = invocation_record_values(
        active_invocation,
        anticipated_usage=ToolResourceUsage(file_mutations=1, written_bytes=5),
        process_slot=True,
        updated_at=active_invocation.requested_at,
    )
    completed = ToolInvocationRecord(
        invocation_sequence=4,
        **completed_values,
    ).model_copy(
        update={
            "status": ToolInvocationStatus.SUCCEEDED,
            "resource_usage": ToolResourceUsage(
                file_mutations=1,
                written_bytes=10,
            ),
        }
    )
    running = ToolInvocationRecord(
        invocation_sequence=9,
        **active_values,
    ).model_copy(update={"status": ToolInvocationStatus.RUNNING})
    ledger = ToolBudgetLedger()

    ledger.restore(completed)
    restored = ledger.restore(running)
    duplicate = ledger.restore(running)

    assert duplicate == restored
    assert restored.invocation_count == 2
    assert restored.file_mutations == 1
    assert restored.written_bytes == 10
    assert restored.reserved_file_mutations == 1
    assert restored.reserved_written_bytes == 5
    assert restored.active_processes == 1
    with pytest.raises(ToolBudgetExceeded, match="Concurrent process"):
        next_process = ledger.begin(
            invocation(tmp_path, "invocation-3", budget=budget),
            process_slot=True,
        )
        ledger.activate_process(next_process)


def test_task_limits_are_independent_and_aborts_still_consume_ids(
    tmp_path: Path,
) -> None:
    ledger = ToolBudgetLedger()
    budget = ToolResourceBudget(invocations_per_task=1, invocations_per_run=3)
    first = ledger.begin(invocation(tmp_path, "invocation-1", budget=budget))
    ledger.abort(first)

    with pytest.raises(ToolBudgetExceeded) as captured:
        ledger.begin(invocation(tmp_path, "invocation-2", budget=budget))
    assert captured.value.limit_name == "invocations_per_task"

    other = ledger.begin(
        invocation(
            tmp_path,
            "invocation-3",
            task_id="task-2",
            budget=budget,
        )
    )
    assert other.task_id == "task-2"
