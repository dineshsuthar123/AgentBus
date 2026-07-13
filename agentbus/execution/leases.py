from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from enum import Enum
from typing import Any, Callable

from pydantic import Field

from agentbus.execution.models import DomainModel, TaskStatus, utc_now
from agentbus.execution.transitions import validate_task_transition
from agentbus.execution.state_store import (
    StateStore,
    StateStoreError,
    _dump_json,
    _load_json,
    _parse_timestamp,
    _timestamp,
)


class LeaseError(RuntimeError):
    """Base error for transactional worker lease operations."""


class LeaseUnavailableError(LeaseError):
    pass


class LeaseOwnershipError(LeaseError):
    pass


class LeaseExpiredError(LeaseError):
    pass


class LeaseStatus(str, Enum):
    ACTIVE = "active"
    RELEASED = "released"
    EXPIRED = "expired"


class WorkerLease(DomainModel):
    lease_id: str
    run_id: str
    task_id: str
    worker_id: str
    status: LeaseStatus
    acquired_at: datetime
    heartbeat_at: datetime
    expires_at: datetime
    released_at: datetime | None = None
    fencing_token: int = Field(ge=1)
    metadata: dict[str, Any] = Field(default_factory=dict)


class LeaseService:
    def __init__(
        self,
        store: StateStore,
        *,
        lease_seconds: float = 120,
        clock: Callable[[], datetime] = utc_now,
    ):
        if lease_seconds <= 0:
            raise ValueError("lease_seconds must be greater than zero")
        self.store = store
        self.lease_seconds = lease_seconds
        self.clock = clock

    def acquire_lease(
        self,
        run_id: str,
        task_id: str,
        worker_id: str,
        metadata: dict[str, Any] | None = None,
        *,
        activate_task: bool = False,
    ) -> WorkerLease:
        now = self.clock()
        expires = now + timedelta(seconds=self.lease_seconds)
        lease_id = uuid.uuid4().hex
        try:
            with self.store._write_transaction() as connection:
                self.store._require_task_row(connection, run_id, task_id)
                self._expire_stale_in_transaction(connection, now, run_id, task_id)
                active = connection.execute(
                    """SELECT lease_id FROM worker_leases
                       WHERE run_id = ? AND task_id = ? AND status = 'active'""",
                    (run_id, task_id),
                ).fetchone()
                if active is not None:
                    raise LeaseUnavailableError(
                        f"Task '{task_id}' already has an active worker lease."
                    )
                row = connection.execute(
                    """SELECT COALESCE(MAX(fencing_token), 0) AS token
                       FROM worker_leases WHERE run_id = ? AND task_id = ?""",
                    (run_id, task_id),
                ).fetchone()
                token = int(row["token"]) + 1
                if activate_task:
                    task = connection.execute(
                        "SELECT status FROM tasks WHERE run_id = ? AND task_id = ?",
                        (run_id, task_id),
                    ).fetchone()
                    current = TaskStatus(task["status"])
                    validate_task_transition(current, TaskStatus.RUNNING)
                    connection.execute(
                        """UPDATE tasks SET status = ?, updated_at = ?
                           WHERE run_id = ? AND task_id = ?""",
                        (TaskStatus.RUNNING.value, _timestamp(now), run_id, task_id),
                    )
                connection.execute(
                    """INSERT INTO worker_leases(
                           lease_id, run_id, task_id, worker_id, status, acquired_at,
                           heartbeat_at, expires_at, released_at, fencing_token, metadata_json
                       ) VALUES (?, ?, ?, ?, 'active', ?, ?, ?, NULL, ?, ?)""",
                    (
                        lease_id,
                        run_id,
                        task_id,
                        worker_id,
                        _timestamp(now),
                        _timestamp(now),
                        _timestamp(expires),
                        token,
                        _dump_json(metadata or {}),
                    ),
                )
                self.store._insert_event(
                    connection,
                    run_id,
                    task_id,
                    "lease_acquired" if token == 1 else "lease_reclaimed",
                    {
                        "lease_id": lease_id,
                        "worker_id": worker_id,
                        "fencing_token": token,
                        "expires_at": _timestamp(expires),
                    },
                )
        except LeaseError:
            raise
        except StateStoreError as exc:
            raise LeaseError(str(exc)) from exc
        return self.get_lease(lease_id)

    def renew_lease(
        self,
        lease_id: str,
        worker_id: str,
        fencing_token: int,
    ) -> WorkerLease:
        now = self.clock()
        expires = now + timedelta(seconds=self.lease_seconds)
        with self.store._write_transaction() as connection:
            row = self._require_owned_active(
                connection, lease_id, worker_id, fencing_token, now
            )
            connection.execute(
                "UPDATE worker_leases SET heartbeat_at = ?, expires_at = ? WHERE lease_id = ?",
                (_timestamp(now), _timestamp(expires), lease_id),
            )
            self.store._insert_event(
                connection,
                row["run_id"],
                row["task_id"],
                "lease_renewed",
                {
                    "lease_id": lease_id,
                    "worker_id": worker_id,
                    "fencing_token": fencing_token,
                    "expires_at": _timestamp(expires),
                },
            )
        return self.get_lease(lease_id)

    def release_lease(
        self,
        lease_id: str,
        worker_id: str,
        fencing_token: int,
    ) -> WorkerLease:
        now = self.clock()
        with self.store._write_transaction() as connection:
            row = connection.execute(
                "SELECT * FROM worker_leases WHERE lease_id = ?", (lease_id,)
            ).fetchone()
            if row is None:
                raise LeaseOwnershipError(f"Lease '{lease_id}' was not found.")
            if row["worker_id"] != worker_id or row["fencing_token"] != fencing_token:
                raise LeaseOwnershipError("Lease worker or fencing token does not match.")
            if row["status"] == LeaseStatus.RELEASED.value:
                return self._from_row(row)
            if row["status"] == LeaseStatus.ACTIVE.value:
                connection.execute(
                    """UPDATE worker_leases SET status = 'released', released_at = ?
                       WHERE lease_id = ?""",
                    (_timestamp(now), lease_id),
                )
                self.store._insert_event(
                    connection,
                    row["run_id"],
                    row["task_id"],
                    "lease_released",
                    {
                        "lease_id": lease_id,
                        "worker_id": worker_id,
                        "fencing_token": fencing_token,
                    },
                )
        return self.get_lease(lease_id)

    def expire_stale_leases(self, run_id: str | None = None) -> list[WorkerLease]:
        now = self.clock()
        with self.store._write_transaction() as connection:
            expired_ids = self._expire_stale_in_transaction(connection, now, run_id)
        return [self.get_lease(lease_id) for lease_id in expired_ids]

    def validate_fencing_token(
        self,
        lease_id: str,
        worker_id: str,
        fencing_token: int,
    ) -> WorkerLease:
        now = self.clock()
        with self.store._connection() as connection:
            row = self._require_owned_active(
                connection, lease_id, worker_id, fencing_token, now
            )
        return self._from_row(row)

    def get_active_lease(self, run_id: str, task_id: str) -> WorkerLease | None:
        self.expire_stale_leases(run_id)
        with self.store._connection() as connection:
            row = connection.execute(
                """SELECT * FROM worker_leases WHERE run_id = ? AND task_id = ?
                   AND status = 'active'""",
                (run_id, task_id),
            ).fetchone()
        return self._from_row(row) if row is not None else None

    def get_lease(self, lease_id: str) -> WorkerLease:
        with self.store._connection() as connection:
            row = connection.execute(
                "SELECT * FROM worker_leases WHERE lease_id = ?", (lease_id,)
            ).fetchone()
        if row is None:
            raise LeaseOwnershipError(f"Lease '{lease_id}' was not found.")
        return self._from_row(row)

    def list_leases(self, run_id: str | None = None) -> list[WorkerLease]:
        query = "SELECT * FROM worker_leases"
        parameters: tuple[str, ...] = ()
        if run_id is not None:
            query += " WHERE run_id = ?"
            parameters = (run_id,)
        query += " ORDER BY acquired_at, fencing_token"
        with self.store._connection() as connection:
            rows = connection.execute(query, parameters).fetchall()
        return [self._from_row(row) for row in rows]

    def _expire_stale_in_transaction(
        self,
        connection,
        now: datetime,
        run_id: str | None = None,
        task_id: str | None = None,
    ) -> list[str]:
        query = "SELECT * FROM worker_leases WHERE status = 'active' AND expires_at <= ?"
        parameters: list[str] = [_timestamp(now)]
        if run_id is not None:
            query += " AND run_id = ?"
            parameters.append(run_id)
        if task_id is not None:
            query += " AND task_id = ?"
            parameters.append(task_id)
        rows = connection.execute(query, parameters).fetchall()
        for row in rows:
            connection.execute(
                "UPDATE worker_leases SET status = 'expired', released_at = ? WHERE lease_id = ?",
                (_timestamp(now), row["lease_id"]),
            )
            self.store._insert_event(
                connection,
                row["run_id"],
                row["task_id"],
                "lease_expired",
                {
                    "lease_id": row["lease_id"],
                    "worker_id": row["worker_id"],
                    "fencing_token": row["fencing_token"],
                },
            )
        return [row["lease_id"] for row in rows]

    @staticmethod
    def _require_owned_active(connection, lease_id, worker_id, fencing_token, now):
        row = connection.execute(
            "SELECT * FROM worker_leases WHERE lease_id = ?", (lease_id,)
        ).fetchone()
        if row is None or row["worker_id"] != worker_id or row["fencing_token"] != fencing_token:
            raise LeaseOwnershipError("Lease worker or fencing token does not match.")
        if row["status"] != LeaseStatus.ACTIVE.value or _parse_timestamp(row["expires_at"]) <= now:
            raise LeaseExpiredError("Lease is no longer active.")
        return row

    @staticmethod
    def _from_row(row) -> WorkerLease:
        return WorkerLease(
            lease_id=row["lease_id"],
            run_id=row["run_id"],
            task_id=row["task_id"],
            worker_id=row["worker_id"],
            status=LeaseStatus(row["status"]),
            acquired_at=_parse_timestamp(row["acquired_at"]),
            heartbeat_at=_parse_timestamp(row["heartbeat_at"]),
            expires_at=_parse_timestamp(row["expires_at"]),
            released_at=_parse_timestamp(row["released_at"]),
            fencing_token=row["fencing_token"],
            metadata=_load_json(row["metadata_json"], "worker lease metadata"),
        )
