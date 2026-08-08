from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from agentbus.execution.cancellation import CancellationToken
from agentbus.intelligence import (
    IndexBusyError,
    IndexOperationKind,
    IndexOperationLease,
    IndexOperationState,
    IndexStore,
    repository_identity,
)


_NOW = datetime(2030, 1, 1, tzinfo=timezone.utc)


class _ControlledTime:
    def __init__(self) -> None:
        self.seconds = 0.0

    def clock(self) -> datetime:
        return _NOW + timedelta(seconds=self.seconds)

    def monotonic(self) -> float:
        return self.seconds


def _lease(
    tmp_path: Path,
    controlled: _ControlledTime,
    *,
    operation_id: str = "indexop_" + ("d" * 32),
    cancellation: CancellationToken | None = None,
) -> IndexOperationLease:
    store = IndexStore(tmp_path / "repository.sqlite3")
    repository = repository_identity("fixtures/index-operation-lease")
    return IndexOperationLease(
        store,
        repository,
        IndexOperationKind.UPDATE,
        operation_id=operation_id,
        owner_pid=101,
        stale_after=timedelta(seconds=30),
        heartbeat_interval_seconds=5,
        cancellation=cancellation,
        clock=controlled.clock,
        monotonic=controlled.monotonic,
    )


def test_lease_acquires_and_finishes_fenced_operation(
    tmp_path: Path,
) -> None:
    controlled = _ControlledTime()
    lease = _lease(tmp_path, controlled)

    running = lease.acquire()
    controlled.seconds = 1
    completed = lease.finish(IndexOperationState.COMPLETED)

    assert running.state == IndexOperationState.RUNNING
    assert completed.state == IndexOperationState.COMPLETED
    assert lease.closed is True
    assert lease.finish(IndexOperationState.COMPLETED) == completed


def test_lease_throttles_heartbeats_and_observes_persisted_cancel(
    tmp_path: Path,
) -> None:
    controlled = _ControlledTime()
    lease = _lease(tmp_path, controlled)
    operation = lease.acquire()
    assert lease.store.request_index_cancellation(
        operation.repository_id
    )

    controlled.seconds = 4
    assert lease.is_set() is False
    controlled.seconds = 5
    assert lease.is_set() is True


def test_external_cancellation_is_immediate(tmp_path: Path) -> None:
    controlled = _ControlledTime()
    cancellation = CancellationToken()
    lease = _lease(
        tmp_path,
        controlled,
        cancellation=cancellation,
    )
    lease.acquire()

    cancellation.request("test")

    assert lease.is_set() is True


def test_publish_guard_detects_reclaimed_operation(
    tmp_path: Path,
) -> None:
    controlled = _ControlledTime()
    first = _lease(tmp_path, controlled)
    first.acquire()
    controlled.seconds = 31
    second = _lease(
        tmp_path,
        controlled,
        operation_id="indexop_" + ("e" * 32),
    )
    second.acquire()

    with pytest.raises(IndexBusyError, match="stale owner"):
        first.publish_guard()


@pytest.mark.parametrize(
    "heartbeat_interval",
    (0.0, float("nan"), 5.0),
)
def test_lease_rejects_unsafe_heartbeat_configuration(
    tmp_path: Path,
    heartbeat_interval: float,
) -> None:
    store = IndexStore(tmp_path / "repository.sqlite3")
    repository = repository_identity("fixtures/index-operation-lease")

    with pytest.raises(ValueError):
        IndexOperationLease(
            store,
            repository,
            IndexOperationKind.BUILD,
            stale_after=timedelta(seconds=10),
            heartbeat_interval_seconds=heartbeat_interval,
        )
