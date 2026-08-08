from __future__ import annotations

import math
import os
import threading
import time
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from agentbus.intelligence.models import (
    IndexOperation,
    IndexOperationKind,
    IndexOperationState,
    RepositoryIdentity,
)
from agentbus.intelligence.parsers.base import CancellationSignal
from agentbus.intelligence.storage import IndexStore


class IndexOperationLease:
    """Own and heartbeat one fenced repository-index operation."""

    def __init__(
        self,
        store: IndexStore,
        repository: RepositoryIdentity,
        operation_kind: IndexOperationKind,
        *,
        operation_id: str | None = None,
        owner_pid: int | None = None,
        stale_after: timedelta = timedelta(minutes=5),
        heartbeat_interval_seconds: float = 5.0,
        cancellation: CancellationSignal | None = None,
        clock: Callable[[], datetime] | None = None,
        monotonic: Callable[[], float] | None = None,
    ) -> None:
        if (
            not math.isfinite(heartbeat_interval_seconds)
            or heartbeat_interval_seconds <= 0
            or heartbeat_interval_seconds > 3_600
        ):
            raise ValueError(
                "heartbeat_interval_seconds must be greater than 0 "
                "and at most 3600"
            )
        if heartbeat_interval_seconds >= stale_after.total_seconds() / 2:
            raise ValueError(
                "heartbeat interval must be less than half the stale timeout"
            )
        self.store = store
        self.repository = RepositoryIdentity.model_validate(
            repository.model_dump(mode="python")
        )
        self.operation_kind = IndexOperationKind(operation_kind)
        self.operation_id = operation_id or f"indexop_{uuid4().hex}"
        self.owner_pid = owner_pid if owner_pid is not None else os.getpid()
        self.stale_after = stale_after
        self.heartbeat_interval_seconds = heartbeat_interval_seconds
        self.cancellation = cancellation
        self.clock = clock or _utc_now
        self.monotonic = monotonic or time.monotonic
        self._lock = threading.RLock()
        self._operation: IndexOperation | None = None
        self._last_heartbeat = 0.0
        self._closed = False

    @property
    def operation(self) -> IndexOperation:
        with self._lock:
            if self._operation is None:
                raise RuntimeError("index operation lease has not been acquired")
            return self._operation

    @property
    def closed(self) -> bool:
        with self._lock:
            return self._closed

    def acquire(self) -> IndexOperation:
        with self._lock:
            if self._operation is not None:
                return self._operation
            now = self.clock()
            self._operation = self.store.acquire_index_operation(
                self.repository,
                self.operation_id,
                self.operation_kind,
                self.owner_pid,
                now=now,
                stale_after=self.stale_after,
            )
            self._last_heartbeat = self.monotonic()
            return self._operation

    def checkpoint(self, *, force: bool = False) -> IndexOperation:
        with self._lock:
            if self._closed:
                raise RuntimeError("index operation lease is closed")
            operation = self.acquire()
            tick = self.monotonic()
            if (
                not force
                and tick - self._last_heartbeat
                < self.heartbeat_interval_seconds
            ):
                return operation
            self._operation = self.store.heartbeat_index_operation(
                self.repository.repository_id,
                operation.operation_id,
                operation.owner_pid,
                at=self.clock(),
            )
            self._last_heartbeat = tick
            return self._operation

    def is_set(self) -> bool:
        if self.cancellation is not None and self.cancellation.is_set():
            return True
        return self.checkpoint().cancellation_requested

    def publish_guard(self) -> dict[str, str | int]:
        operation = self.checkpoint(force=True)
        return {
            "operation_id": operation.operation_id,
            "operation_owner_pid": operation.owner_pid,
        }

    def finish(
        self,
        state: IndexOperationState,
    ) -> IndexOperation:
        with self._lock:
            if self._closed:
                return self.operation
            operation = self.checkpoint(force=True)
            self._operation = self.store.finish_index_operation(
                self.repository.repository_id,
                operation.operation_id,
                operation.owner_pid,
                state,
                at=self.clock(),
            )
            self._closed = True
            return self._operation

    def fail(self) -> IndexOperation | None:
        try:
            return self.finish(IndexOperationState.FAILED)
        except Exception:
            return None


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)
