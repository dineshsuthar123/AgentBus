from __future__ import annotations

import threading
from datetime import datetime, timedelta, timezone

import pytest

from agentbus.execution.engine import DurableExecutionEngine
from agentbus.execution.leases import (
    LeaseExpiredError,
    LeaseOwnershipError,
    LeaseService,
    LeaseStatus,
    LeaseUnavailableError,
)
from agentbus.execution.models import RunStatus, TaskStatus
from agentbus.execution.state_store import StateStore, StateStoreError
from agentbus.worktrees.models import (
    TaskCommitRecord,
    WorktreePurpose,
    WorktreeRecord,
    WorktreeStatus,
)


PLAN = {
    "goal": "Lease test",
    "steps": [
        {
            "id": "task-a",
            "title": "Task A",
            "description": "Execute once",
            "risk": "low",
        }
    ],
    "test_strategy": "offline",
    "done_criteria": ["done"],
}


class FakeClock:
    def __init__(self):
        self.value = datetime(2026, 1, 1, tzinfo=timezone.utc)

    def __call__(self):
        return self.value

    def advance(self, seconds):
        self.value += timedelta(seconds=seconds)


def create_store(path):
    store = StateStore(path)
    DurableExecutionEngine(store).create_run(
        "Lease test",
        PLAN,
        model="fake",
        workspace="C:/repo",
        run_id="run-1",
    )
    store.update_run_status("run-1", RunStatus.RUNNING)
    store.update_task_status("run-1", "task-a", TaskStatus.READY)
    return store


def test_only_one_active_lease_and_release_is_idempotent(tmp_path):
    store = create_store(tmp_path / "state.db")
    clock = FakeClock()
    leases = LeaseService(store, lease_seconds=30, clock=clock)

    first = leases.acquire_lease("run-1", "task-a", "worker-1")

    with pytest.raises(LeaseUnavailableError):
        leases.acquire_lease("run-1", "task-a", "worker-2")
    released = leases.release_lease(
        first.lease_id, first.worker_id, first.fencing_token
    )
    repeated = leases.release_lease(
        first.lease_id, first.worker_id, first.fencing_token
    )
    assert released.status == LeaseStatus.RELEASED
    assert repeated.status == LeaseStatus.RELEASED


def test_heartbeat_and_ownership_validation(tmp_path):
    store = create_store(tmp_path / "state.db")
    clock = FakeClock()
    leases = LeaseService(store, lease_seconds=30, clock=clock)
    lease = leases.acquire_lease("run-1", "task-a", "worker-1")
    clock.advance(10)

    renewed = leases.renew_lease(
        lease.lease_id, lease.worker_id, lease.fencing_token
    )

    assert renewed.expires_at == clock.value + timedelta(seconds=30)
    with pytest.raises(LeaseOwnershipError):
        leases.renew_lease(lease.lease_id, "wrong-worker", lease.fencing_token)
    with pytest.raises(LeaseOwnershipError):
        leases.renew_lease(lease.lease_id, lease.worker_id, lease.fencing_token + 1)


def test_expired_lease_is_reclaimed_with_higher_fencing_token(tmp_path):
    store = create_store(tmp_path / "state.db")
    clock = FakeClock()
    leases = LeaseService(store, lease_seconds=10, clock=clock)
    first = leases.acquire_lease("run-1", "task-a", "worker-1")
    clock.advance(11)

    second = leases.acquire_lease("run-1", "task-a", "worker-2")

    assert leases.get_lease(first.lease_id).status == LeaseStatus.EXPIRED
    assert second.fencing_token == first.fencing_token + 1
    with pytest.raises(LeaseExpiredError):
        leases.validate_fencing_token(
            first.lease_id, first.worker_id, first.fencing_token
        )


def test_stale_worker_cannot_persist_success_after_reclamation(tmp_path):
    store = create_store(tmp_path / "state.db")
    clock = FakeClock()
    leases = LeaseService(store, lease_seconds=10, clock=clock)
    first = leases.acquire_lease(
        "run-1", "task-a", "worker-1", activate_task=True
    )
    attempt = store.create_attempt("run-1", "task-a")
    worktree = store.record_worktree(
        WorktreeRecord(
            worktree_id="worktree-1",
            run_id="run-1",
            task_id="task-a",
            path="C:/worktree",
            repository_root="C:/repo",
            base_commit="a" * 40,
            branch_ref="agentbus/run/run-1/task/task-a",
            purpose=WorktreePurpose.TASK,
            status=WorktreeStatus.ACTIVE,
            worker_id="worker-1",
        )
    )
    clock.advance(11)
    second = leases.acquire_lease("run-1", "task-a", "worker-2")
    commit = TaskCommitRecord(
        run_id="run-1",
        task_id="task-a",
        commit_sha="b" * 40,
        parent_sha="a" * 40,
        worktree_id=worktree.worktree_id,
        changed_files=["module.py"],
        created_at=clock.value,
    )

    with pytest.raises(StateStoreError, match="stale"):
        store.complete_fenced_task_commit(
            attempt_id=attempt.attempt_id,
            lease_id=first.lease_id,
            worker_id=first.worker_id,
            fencing_token=first.fencing_token,
            commit=commit,
            summary="stale success",
            now=clock.value,
        )
    store.complete_fenced_task_commit(
        attempt_id=attempt.attempt_id,
        lease_id=second.lease_id,
        worker_id=second.worker_id,
        fencing_token=second.fencing_token,
        commit=commit,
        summary="fenced success",
        now=clock.value,
    )
    assert store.get_task("run-1", "task-a").status == TaskStatus.INTEGRATION_PENDING


def test_two_store_instances_contend_safely(tmp_path):
    database = tmp_path / "state.db"
    create_store(database)
    clock = FakeClock()
    services = [
        LeaseService(StateStore(database), lease_seconds=30, clock=clock),
        LeaseService(StateStore(database), lease_seconds=30, clock=clock),
    ]
    barrier = threading.Barrier(2)
    outcomes = []

    def acquire(index):
        barrier.wait()
        try:
            outcomes.append(
                services[index].acquire_lease(
                    "run-1", "task-a", f"worker-{index}"
                ).worker_id
            )
        except LeaseUnavailableError:
            outcomes.append("unavailable")

    threads = [threading.Thread(target=acquire, args=(index,)) for index in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert outcomes.count("unavailable") == 1
    assert len([item for item in outcomes if item.startswith("worker-")]) == 1
    active = services[0].get_active_lease("run-1", "task-a")
    assert active is not None
