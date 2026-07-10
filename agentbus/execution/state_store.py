from __future__ import annotations

import json
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime
from functools import wraps
from pathlib import Path
from typing import Any, Callable, Iterator

from agentbus.execution.models import (
    ApprovalDecision,
    ApprovalOutcome,
    AttemptStatus,
    ExecutionArtifact,
    FailureCategory,
    RunRecord,
    RunSnapshot,
    RunStatus,
    TaskAttempt,
    TaskDependency,
    TaskRecord,
    TaskSpec,
    TaskStatus,
    utc_now,
)
from agentbus.execution.schema import SCHEMA_SQL, SCHEMA_VERSION
from agentbus.execution.transitions import (
    InvalidStateTransition,
    validate_attempt_transition,
    validate_run_transition,
    validate_task_transition,
)
from agentbus.security.redaction import is_sensitive_key, redact_text


class StateStoreError(RuntimeError):
    """Base error for durable state operations."""


class RunNotFoundError(StateStoreError):
    pass


class TaskNotFoundError(StateStoreError):
    pass


class AttemptNotFoundError(StateStoreError):
    pass


_MAX_TEXT_CHARS = 20_000


def _domain_decode(description: str) -> Callable:
    def decorate(function: Callable) -> Callable:
        @wraps(function)
        def wrapped(*args, **kwargs):
            try:
                return function(*args, **kwargs)
            except StateStoreError:
                raise
            except (KeyError, TypeError, ValueError) as exc:
                raise StateStoreError(
                    f"Stored {description} is invalid; recovery cannot continue safely."
                ) from exc

        return wrapped

    return decorate


class StateStore:
    """SQLite repository for durable runs.

    A fresh connection is used for each operation. Writes take an immediate
    transaction so transition validation and its audit event see one state.
    """

    def __init__(self, database_path: str | Path):
        self.database_path = Path(database_path).expanduser().resolve()
        try:
            self.database_path.parent.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise StateStoreError(
                f"Unable to create state directory '{self.database_path.parent}'."
            ) from exc
        self._initialize_schema()

    @property
    def schema_version(self) -> int:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT value FROM schema_metadata WHERE key = ?",
                ("schema_version",),
            ).fetchone()
        if row is None:
            raise StateStoreError("State database is missing its schema version.")
        return int(row["value"])

    def _initialize_schema(self) -> None:
        try:
            with self._connection() as connection:
                connection.execute("PRAGMA journal_mode = WAL")
                connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS schema_metadata (
                        key TEXT PRIMARY KEY,
                        value TEXT NOT NULL
                    )
                    """
                )
                row = connection.execute(
                    "SELECT value FROM schema_metadata WHERE key = ?",
                    ("schema_version",),
                ).fetchone()
                if row is None:
                    connection.executescript(SCHEMA_SQL)
                    connection.execute(
                        "INSERT OR IGNORE INTO schema_metadata(key, value) VALUES (?, ?)",
                        ("schema_version", str(SCHEMA_VERSION)),
                    )
                else:
                    existing = int(row["value"])
                    if existing > SCHEMA_VERSION:
                        raise StateStoreError(
                            "State database schema is newer than this AgentBus version: "
                            f"{existing} > {SCHEMA_VERSION}."
                        )
                    if existing < SCHEMA_VERSION:
                        raise StateStoreError(
                            "No migration is registered from state schema "
                            f"{existing} to {SCHEMA_VERSION}."
                        )
                    connection.executescript(SCHEMA_SQL)
                connection.commit()
        except StateStoreError:
            raise
        except (OSError, sqlite3.Error, ValueError) as exc:
            raise StateStoreError(
                f"Unable to initialize state database '{self.database_path}'."
            ) from exc

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        connection: sqlite3.Connection | None = None
        try:
            connection = sqlite3.connect(
                self.database_path,
                timeout=10,
                isolation_level=None,
            )
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA foreign_keys = ON")
            connection.execute("PRAGMA busy_timeout = 5000")
            yield connection
        except StateStoreError:
            raise
        except sqlite3.Error as exc:
            raise StateStoreError(
                f"Unable to access state database '{self.database_path}'."
            ) from exc
        finally:
            if connection is not None:
                connection.close()

    @contextmanager
    def _write_transaction(self) -> Iterator[sqlite3.Connection]:
        with self._connection() as connection:
            try:
                connection.execute("BEGIN IMMEDIATE")
                yield connection
                connection.commit()
            except (StateStoreError, InvalidStateTransition):
                connection.rollback()
                raise
            except sqlite3.IntegrityError as exc:
                connection.rollback()
                raise StateStoreError(
                    "Durable state violates a uniqueness or relationship constraint."
                ) from exc
            except sqlite3.Error as exc:
                connection.rollback()
                raise StateStoreError("Unable to update durable state.") from exc

    def create_run(self, run: RunRecord) -> RunRecord:
        with self._write_transaction() as connection:
            self._insert_run(connection, run)
            self._insert_event(
                connection,
                run.run_id,
                None,
                "durable_run_created",
                {"workflow_type": run.workflow_type, "status": run.status.value},
            )
        return self.get_run(run.run_id)

    def create_run_with_tasks(
        self,
        run: RunRecord,
        tasks: list[TaskSpec],
    ) -> RunSnapshot:
        """Persist a validated run and its graph atomically."""
        with self._write_transaction() as connection:
            self._insert_run(connection, run)
            self._insert_tasks(connection, run.run_id, tasks)
            self._insert_event(
                connection,
                run.run_id,
                None,
                "durable_run_created",
                {"workflow_type": run.workflow_type, "status": run.status.value},
            )
            self._insert_event(
                connection,
                run.run_id,
                None,
                "task_graph_validated",
                {"task_count": len(tasks), "graph_version": 1},
            )
        return self.load_snapshot(run.run_id)

    def _insert_run(self, connection: sqlite3.Connection, run: RunRecord) -> None:
        connection.execute(
            """
            INSERT INTO runs(
                run_id, original_task, workflow_type, status, model, workspace,
                created_at, updated_at, completed_at, planner_output_json,
                context_summary, failure_reason, version, graph_json, metadata_json,
                verifier_status, reviewer_status, changed_files_json,
                commit_identifier, pr_url, finalization_error
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run.run_id,
                _safe_text(run.original_task),
                run.workflow_type,
                run.status.value,
                run.model,
                run.workspace,
                _timestamp(run.created_at),
                _timestamp(run.updated_at),
                _timestamp(run.completed_at),
                _dump_json(run.planner_output),
                _safe_text(run.context_summary),
                _safe_text(run.failure_reason),
                run.version,
                _dump_json(run.graph_data),
                _dump_json(run.metadata),
                run.verifier_status,
                run.reviewer_status,
                _dump_json(run.changed_files),
                run.commit_identifier,
                run.pr_url,
                _safe_text(run.finalization_error),
            ),
        )

    def get_run(self, run_id: str) -> RunRecord:
        _require_id(run_id, "run")
        with self._connection() as connection:
            try:
                row = connection.execute(
                    "SELECT * FROM runs WHERE run_id = ?", (run_id,)
                ).fetchone()
            except sqlite3.Error as exc:
                raise StateStoreError("Unable to read durable run state.") from exc
        if row is None:
            raise RunNotFoundError(f"Run '{run_id}' was not found.")
        return self._run_from_row(row)

    def list_runs(
        self,
        *,
        status: RunStatus | None = None,
        limit: int = 100,
    ) -> list[RunRecord]:
        if limit < 1 or limit > 1000:
            raise StateStoreError("Run list limit must be between 1 and 1000.")
        query = "SELECT * FROM runs"
        parameters: list[Any] = []
        if status is not None:
            query += " WHERE status = ?"
            parameters.append(status.value)
        query += " ORDER BY updated_at DESC, run_id ASC LIMIT ?"
        parameters.append(limit)
        with self._connection() as connection:
            try:
                rows = connection.execute(query, parameters).fetchall()
            except sqlite3.Error as exc:
                raise StateStoreError("Unable to list durable runs.") from exc
        return [self._run_from_row(row) for row in rows]

    def update_run_status(
        self,
        run_id: str,
        status: RunStatus,
        *,
        failure_reason: str | None = None,
        event_type: str | None = None,
        event_payload: dict[str, Any] | None = None,
    ) -> RunRecord:
        _require_id(run_id, "run")
        with self._write_transaction() as connection:
            row = connection.execute(
                "SELECT status FROM runs WHERE run_id = ?", (run_id,)
            ).fetchone()
            if row is None:
                raise RunNotFoundError(f"Run '{run_id}' was not found.")
            current = RunStatus(row["status"])
            validate_run_transition(current, status)
            now = utc_now()
            completed_at = (
                _timestamp(now)
                if status
                in {RunStatus.SUCCEEDED, RunStatus.FAILED, RunStatus.CANCELLED}
                else None
            )
            connection.execute(
                """
                UPDATE runs
                SET status = ?, updated_at = ?, completed_at = ?,
                    failure_reason = COALESCE(?, failure_reason), version = version + 1
                WHERE run_id = ?
                """,
                (
                    status.value,
                    _timestamp(now),
                    completed_at,
                    _safe_text(failure_reason),
                    run_id,
                ),
            )
            payload = {
                "from_status": current.value,
                "to_status": status.value,
                **(event_payload or {}),
            }
            self._insert_event(
                connection,
                run_id,
                None,
                event_type or "run_status_changed",
                payload,
            )
        return self.get_run(run_id)

    def update_run_details(
        self,
        run_id: str,
        *,
        verifier_status: str | None = None,
        reviewer_status: str | None = None,
        changed_files: list[str] | None = None,
        commit_identifier: str | None = None,
        pr_url: str | None = None,
        finalization_error: str | None = None,
        metadata_updates: dict[str, Any] | None = None,
        event_type: str = "durable_run_updated",
        clear_finalization_error: bool = False,
    ) -> RunRecord:
        _require_id(run_id, "run")
        with self._write_transaction() as connection:
            row = connection.execute(
                "SELECT metadata_json FROM runs WHERE run_id = ?", (run_id,)
            ).fetchone()
            if row is None:
                raise RunNotFoundError(f"Run '{run_id}' was not found.")
            metadata = _load_json(row["metadata_json"], "run metadata")
            if metadata_updates:
                metadata.update(_sanitize(metadata_updates))
            now = utc_now()
            connection.execute(
                """
                UPDATE runs SET
                    verifier_status = COALESCE(?, verifier_status),
                    reviewer_status = COALESCE(?, reviewer_status),
                    changed_files_json = COALESCE(?, changed_files_json),
                    commit_identifier = COALESCE(?, commit_identifier),
                    pr_url = COALESCE(?, pr_url),
                    finalization_error = CASE
                        WHEN ? THEN NULL
                        ELSE COALESCE(?, finalization_error)
                    END,
                    metadata_json = ?, updated_at = ?, version = version + 1
                WHERE run_id = ?
                """,
                (
                    verifier_status,
                    reviewer_status,
                    _dump_json(changed_files) if changed_files is not None else None,
                    commit_identifier,
                    pr_url,
                    int(clear_finalization_error),
                    _safe_text(finalization_error),
                    _dump_json(metadata),
                    _timestamp(now),
                    run_id,
                ),
            )
            self._insert_event(
                connection,
                run_id,
                None,
                event_type,
                {
                    "verifier_status": verifier_status,
                    "reviewer_status": reviewer_status,
                    "changed_file_count": len(changed_files or []),
                    "commit_identifier": commit_identifier,
                    "pr_url": pr_url,
                    "finalization_error": finalization_error,
                },
            )
        return self.get_run(run_id)

    def create_tasks(self, run_id: str, tasks: list[TaskSpec]) -> list[TaskRecord]:
        _require_id(run_id, "run")
        with self._write_transaction() as connection:
            self._require_run_row(connection, run_id)
            self._insert_tasks(connection, run_id, tasks)
            self._insert_event(
                connection,
                run_id,
                None,
                "tasks_created",
                {"task_count": len(tasks)},
            )
        return self.list_tasks(run_id)

    def _insert_tasks(
        self,
        connection: sqlite3.Connection,
        run_id: str,
        tasks: list[TaskSpec],
    ) -> None:
        now = utc_now()
        for position, task in enumerate(tasks):
            connection.execute(
                """
                INSERT INTO tasks(
                    run_id, task_id, position, title, description, status, risk,
                    assigned_role, dependencies_json, maximum_attempts,
                    current_attempt_count, expected_outputs_json, done_criteria_json,
                    metadata_json, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    task.task_id,
                    position,
                    task.title,
                    task.description,
                    TaskStatus.PENDING.value,
                    task.risk.value,
                    task.assigned_role,
                    _dump_json(
                        [dependency.model_dump(mode="json") for dependency in task.dependencies]
                    ),
                    task.maximum_attempts,
                    0,
                    _dump_json(task.expected_outputs),
                    _dump_json(task.done_criteria),
                    _dump_json(task.metadata),
                    _timestamp(now),
                    _timestamp(now),
                ),
            )

    def get_task(self, run_id: str, task_id: str) -> TaskRecord:
        _require_id(run_id, "run")
        _require_id(task_id, "task")
        with self._connection() as connection:
            try:
                row = connection.execute(
                    "SELECT * FROM tasks WHERE run_id = ? AND task_id = ?",
                    (run_id, task_id),
                ).fetchone()
            except sqlite3.Error as exc:
                raise StateStoreError("Unable to read durable task state.") from exc
        if row is None:
            raise TaskNotFoundError(
                f"Task '{task_id}' was not found in run '{run_id}'."
            )
        return self._task_from_row(row)

    def list_tasks(self, run_id: str) -> list[TaskRecord]:
        _require_id(run_id, "run")
        with self._connection() as connection:
            self._require_run_row(connection, run_id)
            try:
                rows = connection.execute(
                    "SELECT * FROM tasks WHERE run_id = ? ORDER BY position, task_id",
                    (run_id,),
                ).fetchall()
            except sqlite3.Error as exc:
                raise StateStoreError("Unable to list durable tasks.") from exc
        return [self._task_from_row(row) for row in rows]

    def update_task_status(
        self,
        run_id: str,
        task_id: str,
        status: TaskStatus,
        *,
        event_type: str | None = None,
        event_payload: dict[str, Any] | None = None,
    ) -> TaskRecord:
        _require_id(run_id, "run")
        _require_id(task_id, "task")
        with self._write_transaction() as connection:
            row = connection.execute(
                "SELECT status FROM tasks WHERE run_id = ? AND task_id = ?",
                (run_id, task_id),
            ).fetchone()
            if row is None:
                raise TaskNotFoundError(
                    f"Task '{task_id}' was not found in run '{run_id}'."
                )
            current = TaskStatus(row["status"])
            validate_task_transition(current, status)
            connection.execute(
                """
                UPDATE tasks SET status = ?, updated_at = ?
                WHERE run_id = ? AND task_id = ?
                """,
                (status.value, _timestamp(utc_now()), run_id, task_id),
            )
            self._insert_event(
                connection,
                run_id,
                task_id,
                event_type or "task_status_changed",
                {
                    "from_status": current.value,
                    "to_status": status.value,
                    **(event_payload or {}),
                },
            )
        return self.get_task(run_id, task_id)

    def create_attempt(self, run_id: str, task_id: str) -> TaskAttempt:
        _require_id(run_id, "run")
        _require_id(task_id, "task")
        with self._write_transaction() as connection:
            task_row = connection.execute(
                """
                SELECT status, maximum_attempts FROM tasks
                WHERE run_id = ? AND task_id = ?
                """,
                (run_id, task_id),
            ).fetchone()
            if task_row is None:
                raise TaskNotFoundError(
                    f"Task '{task_id}' was not found in run '{run_id}'."
                )
            if TaskStatus(task_row["status"]) != TaskStatus.RUNNING:
                raise StateStoreError(
                    f"Task '{task_id}' must be running before an attempt is created."
                )
            latest = connection.execute(
                """
                SELECT MAX(attempt_number) AS latest FROM attempts
                WHERE run_id = ? AND task_id = ?
                """,
                (run_id, task_id),
            ).fetchone()
            attempt_number = int(latest["latest"] or 0) + 1
            if attempt_number > int(task_row["maximum_attempts"]):
                raise StateStoreError(
                    f"Task '{task_id}' exhausted its maximum attempts "
                    f"({task_row['maximum_attempts']})."
                )
            attempt = TaskAttempt(
                attempt_id=uuid.uuid4().hex,
                run_id=run_id,
                task_id=task_id,
                attempt_number=attempt_number,
            )
            connection.execute(
                """
                INSERT INTO attempts(
                    attempt_id, run_id, task_id, attempt_number, status,
                    started_at, completed_at, error_category, error_message,
                    observation_summary, metadata_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    attempt.attempt_id,
                    run_id,
                    task_id,
                    attempt_number,
                    attempt.status.value,
                    _timestamp(attempt.started_at),
                    None,
                    None,
                    None,
                    None,
                    _dump_json({}),
                ),
            )
            connection.execute(
                """
                UPDATE tasks SET current_attempt_count = ?, updated_at = ?
                WHERE run_id = ? AND task_id = ?
                """,
                (attempt_number, _timestamp(utc_now()), run_id, task_id),
            )
            self._insert_event(
                connection,
                run_id,
                task_id,
                "task_attempt_started",
                {"attempt_number": attempt_number, "attempt_id": attempt.attempt_id},
            )
        return attempt

    def complete_attempt(
        self,
        attempt_id: str,
        status: AttemptStatus,
        *,
        error_category: FailureCategory | None = None,
        error_message: str | None = None,
        observation_summary: str | None = None,
        metadata: dict[str, Any] | None = None,
        event_type: str | None = None,
    ) -> TaskAttempt:
        _require_id(attempt_id, "attempt")
        with self._write_transaction() as connection:
            row = connection.execute(
                "SELECT * FROM attempts WHERE attempt_id = ?", (attempt_id,)
            ).fetchone()
            if row is None:
                raise AttemptNotFoundError(f"Attempt '{attempt_id}' was not found.")
            current = AttemptStatus(row["status"])
            validate_attempt_transition(current, status)
            completed_at = utc_now()
            connection.execute(
                """
                UPDATE attempts SET
                    status = ?, completed_at = ?, error_category = ?,
                    error_message = ?, observation_summary = ?, metadata_json = ?
                WHERE attempt_id = ?
                """,
                (
                    status.value,
                    _timestamp(completed_at),
                    error_category.value if error_category else None,
                    _safe_text(error_message),
                    _safe_text(observation_summary),
                    _dump_json(metadata or {}),
                    attempt_id,
                ),
            )
            default_event = {
                AttemptStatus.SUCCEEDED: "task_attempt_succeeded",
                AttemptStatus.FAILED: "task_attempt_failed",
                AttemptStatus.INTERRUPTED: "task_attempt_interrupted",
            }.get(status, "task_attempt_updated")
            self._insert_event(
                connection,
                row["run_id"],
                row["task_id"],
                event_type or default_event,
                {
                    "attempt_number": row["attempt_number"],
                    "attempt_id": attempt_id,
                    "status": status.value,
                    "error_category": error_category.value if error_category else None,
                    "error_message": error_message,
                },
            )
        return self.get_attempt(attempt_id)

    def get_attempt(self, attempt_id: str) -> TaskAttempt:
        _require_id(attempt_id, "attempt")
        with self._connection() as connection:
            row = connection.execute(
                "SELECT * FROM attempts WHERE attempt_id = ?", (attempt_id,)
            ).fetchone()
        if row is None:
            raise AttemptNotFoundError(f"Attempt '{attempt_id}' was not found.")
        return self._attempt_from_row(row)

    def list_attempts(
        self,
        run_id: str,
        task_id: str | None = None,
    ) -> list[TaskAttempt]:
        _require_id(run_id, "run")
        parameters: list[Any] = [run_id]
        query = "SELECT * FROM attempts WHERE run_id = ?"
        if task_id is not None:
            _require_id(task_id, "task")
            query += " AND task_id = ?"
            parameters.append(task_id)
        query += " ORDER BY task_id, attempt_number"
        with self._connection() as connection:
            self._require_run_row(connection, run_id)
            rows = connection.execute(query, parameters).fetchall()
        return [self._attempt_from_row(row) for row in rows]

    def record_artifact(self, artifact: ExecutionArtifact) -> ExecutionArtifact:
        with self._write_transaction() as connection:
            self._require_run_row(connection, artifact.run_id)
            if artifact.task_id is not None:
                self._require_task_row(
                    connection,
                    artifact.run_id,
                    artifact.task_id,
                )
            connection.execute(
                """
                INSERT INTO artifacts(
                    artifact_id, run_id, task_id, artifact_type, identifier,
                    metadata_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    artifact.artifact_id,
                    artifact.run_id,
                    artifact.task_id,
                    _safe_text(artifact.artifact_type),
                    _safe_text(artifact.identifier),
                    _dump_json(artifact.metadata),
                    _timestamp(artifact.created_at),
                ),
            )
            self._insert_event(
                connection,
                artifact.run_id,
                artifact.task_id,
                "artifact_recorded",
                {
                    "artifact_id": artifact.artifact_id,
                    "artifact_type": artifact.artifact_type,
                    "identifier": artifact.identifier,
                },
            )
        return artifact

    def record_event(
        self,
        run_id: str,
        event_type: str,
        payload: dict[str, Any] | None = None,
        *,
        task_id: str | None = None,
    ) -> int:
        _require_id(run_id, "run")
        if not event_type.strip():
            raise StateStoreError("Event type must not be empty.")
        with self._write_transaction() as connection:
            self._require_run_row(connection, run_id)
            if task_id is not None:
                self._require_task_row(connection, run_id, task_id)
            return self._insert_event(
                connection,
                run_id,
                task_id,
                event_type,
                payload or {},
            )

    def list_events(self, run_id: str) -> list[dict[str, Any]]:
        _require_id(run_id, "run")
        with self._connection() as connection:
            self._require_run_row(connection, run_id)
            rows = connection.execute(
                "SELECT * FROM events WHERE run_id = ? ORDER BY event_id",
                (run_id,),
            ).fetchall()
        return [
            {
                "event_id": row["event_id"],
                "run_id": row["run_id"],
                "task_id": row["task_id"],
                "event_type": row["event_type"],
                "payload": _load_json(row["payload_json"], "event payload"),
                "created_at": _parse_timestamp(row["created_at"]),
            }
            for row in rows
        ]

    def record_approval(
        self,
        run_id: str,
        task_id: str,
        decision: ApprovalOutcome,
        reason: str | None = None,
    ) -> ApprovalDecision:
        _require_id(run_id, "run")
        _require_id(task_id, "task")
        created_at = utc_now()
        with self._write_transaction() as connection:
            self._require_task_row(connection, run_id, task_id)
            cursor = connection.execute(
                """
                INSERT INTO approvals(run_id, task_id, decision, reason, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    task_id,
                    decision.value,
                    _safe_text(reason),
                    _timestamp(created_at),
                ),
            )
            self._insert_event(
                connection,
                run_id,
                task_id,
                "task_approved"
                if decision == ApprovalOutcome.APPROVED
                else "task_rejected",
                {"decision": decision.value, "reason": reason},
            )
            approval_id = int(cursor.lastrowid)
        return ApprovalDecision(
            approval_id=approval_id,
            run_id=run_id,
            task_id=task_id,
            decision=decision,
            reason=_safe_text(reason),
            created_at=created_at,
        )

    def latest_approval(
        self, run_id: str, task_id: str
    ) -> ApprovalDecision | None:
        _require_id(run_id, "run")
        _require_id(task_id, "task")
        with self._connection() as connection:
            self._require_task_row(connection, run_id, task_id)
            row = connection.execute(
                """
                SELECT * FROM approvals
                WHERE run_id = ? AND task_id = ?
                ORDER BY approval_id DESC LIMIT 1
                """,
                (run_id, task_id),
            ).fetchone()
        return self._approval_from_row(row) if row is not None else None

    def load_snapshot(self, run_id: str) -> RunSnapshot:
        """Read a consistent run snapshot from one SQLite read transaction."""
        _require_id(run_id, "run")
        with self._connection() as connection:
            try:
                connection.execute("BEGIN")
                run_row = connection.execute(
                    "SELECT * FROM runs WHERE run_id = ?", (run_id,)
                ).fetchone()
                if run_row is None:
                    raise RunNotFoundError(f"Run '{run_id}' was not found.")
                task_rows = connection.execute(
                    "SELECT * FROM tasks WHERE run_id = ? ORDER BY position, task_id",
                    (run_id,),
                ).fetchall()
                attempt_rows = connection.execute(
                    """
                    SELECT * FROM attempts WHERE run_id = ?
                    ORDER BY task_id, attempt_number
                    """,
                    (run_id,),
                ).fetchall()
                artifact_rows = connection.execute(
                    "SELECT * FROM artifacts WHERE run_id = ? ORDER BY created_at, artifact_id",
                    (run_id,),
                ).fetchall()
                approval_rows = connection.execute(
                    "SELECT * FROM approvals WHERE run_id = ? ORDER BY approval_id",
                    (run_id,),
                ).fetchall()
                connection.commit()
            except RunNotFoundError:
                connection.rollback()
                raise
            except sqlite3.Error as exc:
                connection.rollback()
                raise StateStoreError("Unable to load durable run snapshot.") from exc
        return RunSnapshot(
            run=self._run_from_row(run_row),
            tasks=[self._task_from_row(row) for row in task_rows],
            attempts=[self._attempt_from_row(row) for row in attempt_rows],
            artifacts=[self._artifact_from_row(row) for row in artifact_rows],
            approvals=[self._approval_from_row(row) for row in approval_rows],
        )

    def _insert_event(
        self,
        connection: sqlite3.Connection,
        run_id: str,
        task_id: str | None,
        event_type: str,
        payload: dict[str, Any],
    ) -> int:
        cursor = connection.execute(
            """
            INSERT INTO events(run_id, task_id, event_type, payload_json, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                run_id,
                task_id,
                event_type,
                _dump_json(payload),
                _timestamp(utc_now()),
            ),
        )
        return int(cursor.lastrowid)

    @staticmethod
    def _require_run_row(
        connection: sqlite3.Connection, run_id: str
    ) -> sqlite3.Row:
        row = connection.execute(
            "SELECT run_id FROM runs WHERE run_id = ?", (run_id,)
        ).fetchone()
        if row is None:
            raise RunNotFoundError(f"Run '{run_id}' was not found.")
        return row

    @staticmethod
    def _require_task_row(
        connection: sqlite3.Connection, run_id: str, task_id: str
    ) -> sqlite3.Row:
        row = connection.execute(
            "SELECT task_id FROM tasks WHERE run_id = ? AND task_id = ?",
            (run_id, task_id),
        ).fetchone()
        if row is None:
            raise TaskNotFoundError(
                f"Task '{task_id}' was not found in run '{run_id}'."
            )
        return row

    @staticmethod
    @_domain_decode("run record")
    def _run_from_row(row: sqlite3.Row) -> RunRecord:
        return RunRecord(
            run_id=row["run_id"],
            original_task=row["original_task"],
            workflow_type=row["workflow_type"],
            status=RunStatus(row["status"]),
            model=row["model"],
            workspace=row["workspace"],
            created_at=_parse_timestamp(row["created_at"]),
            updated_at=_parse_timestamp(row["updated_at"]),
            completed_at=_parse_timestamp(row["completed_at"]),
            planner_output=_load_json(row["planner_output_json"], "planner output"),
            context_summary=row["context_summary"],
            failure_reason=row["failure_reason"],
            version=row["version"],
            graph_data=_load_json(row["graph_json"], "task graph"),
            metadata=_load_json(row["metadata_json"], "run metadata"),
            verifier_status=row["verifier_status"],
            reviewer_status=row["reviewer_status"],
            changed_files=_load_json(row["changed_files_json"], "changed files"),
            commit_identifier=row["commit_identifier"],
            pr_url=row["pr_url"],
            finalization_error=row["finalization_error"],
        )

    @staticmethod
    @_domain_decode("task record")
    def _task_from_row(row: sqlite3.Row) -> TaskRecord:
        dependencies = _load_json(row["dependencies_json"], "task dependencies")
        spec = TaskSpec(
            task_id=row["task_id"],
            title=row["title"],
            description=row["description"],
            dependencies=[TaskDependency.model_validate(item) for item in dependencies],
            assigned_role=row["assigned_role"],
            risk=row["risk"],
            maximum_attempts=row["maximum_attempts"],
            expected_outputs=_load_json(
                row["expected_outputs_json"], "expected outputs"
            ),
            done_criteria=_load_json(row["done_criteria_json"], "done criteria"),
            metadata=_load_json(row["metadata_json"], "task metadata"),
        )
        return TaskRecord(
            run_id=row["run_id"],
            spec=spec,
            status=TaskStatus(row["status"]),
            position=row["position"],
            current_attempt_count=row["current_attempt_count"],
            created_at=_parse_timestamp(row["created_at"]),
            updated_at=_parse_timestamp(row["updated_at"]),
        )

    @staticmethod
    @_domain_decode("attempt record")
    def _attempt_from_row(row: sqlite3.Row) -> TaskAttempt:
        return TaskAttempt(
            attempt_id=row["attempt_id"],
            run_id=row["run_id"],
            task_id=row["task_id"],
            attempt_number=row["attempt_number"],
            status=AttemptStatus(row["status"]),
            started_at=_parse_timestamp(row["started_at"]),
            completed_at=_parse_timestamp(row["completed_at"]),
            error_category=(
                FailureCategory(row["error_category"])
                if row["error_category"]
                else None
            ),
            error_message=row["error_message"],
            observation_summary=row["observation_summary"],
            metadata=_load_json(row["metadata_json"], "attempt metadata"),
        )

    @staticmethod
    @_domain_decode("artifact record")
    def _artifact_from_row(row: sqlite3.Row) -> ExecutionArtifact:
        return ExecutionArtifact(
            artifact_id=row["artifact_id"],
            run_id=row["run_id"],
            task_id=row["task_id"],
            artifact_type=row["artifact_type"],
            identifier=row["identifier"],
            metadata=_load_json(row["metadata_json"], "artifact metadata"),
            created_at=_parse_timestamp(row["created_at"]),
        )

    @staticmethod
    @_domain_decode("approval record")
    def _approval_from_row(row: sqlite3.Row) -> ApprovalDecision:
        return ApprovalDecision(
            approval_id=row["approval_id"],
            run_id=row["run_id"],
            task_id=row["task_id"],
            decision=ApprovalOutcome(row["decision"]),
            reason=row["reason"],
            created_at=_parse_timestamp(row["created_at"]),
        )


def _timestamp(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _parse_timestamp(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value is not None else None


def _require_id(value: str, entity: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise StateStoreError(f"{entity.capitalize()} ID must not be empty.")


def _safe_text(value: str | None) -> str | None:
    if value is None:
        return None
    return redact_text(str(value), max_chars=_MAX_TEXT_CHARS)


def _sanitize(value: Any, key: str | None = None, depth: int = 0) -> Any:
    if key and is_sensitive_key(key):
        return "[REDACTED]"
    if depth > 12:
        return "[maximum depth reached]"
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return _safe_text(value)
    if isinstance(value, dict):
        return {
            str(item_key): _sanitize(item_value, str(item_key), depth + 1)
            for item_key, item_value in value.items()
        }
    if isinstance(value, (list, tuple, set)):
        return [_sanitize(item, None, depth + 1) for item in value]
    raise StateStoreError(
        f"Durable state values must be JSON serializable, got {type(value).__name__}."
    )


def _dump_json(value: Any) -> str:
    try:
        return json.dumps(
            _sanitize(value),
            allow_nan=False,
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        )
    except (TypeError, ValueError) as exc:
        raise StateStoreError("Durable state value is not JSON serializable.") from exc


def _load_json(value: str, description: str) -> Any:
    try:
        return json.loads(value)
    except (TypeError, json.JSONDecodeError) as exc:
        raise StateStoreError(
            f"Stored {description} is not valid JSON; recovery cannot continue safely."
        ) from exc
