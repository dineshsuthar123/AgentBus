from __future__ import annotations

import json
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime
from functools import wraps
from pathlib import Path
from typing import Any, Callable, Iterator

from agentbus.execution.cancellation import (
    CancellationOperation,
    CancellationState,
)
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
from agentbus.execution.schema import MIGRATIONS, SCHEMA_SQL, SCHEMA_VERSION
from agentbus.execution.transitions import (
    InvalidStateTransition,
    validate_attempt_transition,
    validate_run_transition,
    validate_task_transition,
)
from agentbus.security.redaction import is_sensitive_key, redact_text
from agentbus.worktrees.models import (
    IntegrationRecord,
    MergeStatus,
    TaskCommitRecord,
    WorktreePurpose,
    WorktreeRecord,
    WorktreeStatus,
)


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
                        self._apply_migrations(connection, existing)
                    connection.executescript(SCHEMA_SQL)
                connection.commit()
        except StateStoreError:
            raise
        except (OSError, sqlite3.Error, ValueError) as exc:
            raise StateStoreError(
                f"Unable to initialize state database '{self.database_path}'."
            ) from exc

    def _apply_migrations(
        self,
        connection: sqlite3.Connection,
        existing: int,
    ) -> None:
        current = existing
        while current < SCHEMA_VERSION:
            statements = MIGRATIONS.get(current)
            if not statements:
                raise StateStoreError(
                    "No migration is registered from state schema "
                    f"{current} to {current + 1}."
                )
            try:
                connection.execute("BEGIN IMMEDIATE")
                for statement in statements:
                    connection.execute(statement)
                connection.execute(
                    "UPDATE schema_metadata SET value = ? WHERE key = ?",
                    (str(current + 1), "schema_version"),
                )
                connection.commit()
            except sqlite3.Error as exc:
                connection.rollback()
                raise StateStoreError(
                    f"State schema migration {current} -> {current + 1} failed."
                ) from exc
            current += 1

    def backup(self, destination: str | Path) -> Path:
        target = Path(destination).expanduser().resolve()
        if target == self.database_path:
            raise StateStoreError("State database backup must use a different path.")
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            with self._connection() as source:
                backup = sqlite3.connect(target)
                try:
                    source.backup(backup)
                finally:
                    backup.close()
        except (OSError, sqlite3.Error) as exc:
            raise StateStoreError(f"Unable to back up state database to '{target}'.") from exc
        return target

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

    def complete_fenced_task_commit(
        self,
        *,
        attempt_id: str,
        lease_id: str,
        worker_id: str,
        fencing_token: int,
        commit: TaskCommitRecord,
        summary: str,
        metadata: dict[str, Any] | None = None,
        now: datetime | None = None,
    ) -> TaskCommitRecord:
        """Atomically persist worker success only while its lease remains valid."""
        completed_at = now or utc_now()
        with self._write_transaction() as connection:
            lease = connection.execute(
                "SELECT * FROM worker_leases WHERE lease_id = ?", (lease_id,)
            ).fetchone()
            if (
                lease is None
                or lease["run_id"] != commit.run_id
                or lease["task_id"] != commit.task_id
                or lease["worker_id"] != worker_id
                or lease["fencing_token"] != fencing_token
                or lease["status"] != "active"
                or _parse_timestamp(lease["expires_at"]) <= completed_at
            ):
                raise StateStoreError(
                    "Worker lease is stale; task success and commit were not persisted."
                )
            attempt = connection.execute(
                "SELECT * FROM attempts WHERE attempt_id = ?", (attempt_id,)
            ).fetchone()
            if attempt is None:
                raise AttemptNotFoundError(f"Attempt '{attempt_id}' was not found.")
            if (
                attempt["run_id"] != commit.run_id
                or attempt["task_id"] != commit.task_id
                or attempt["status"] != AttemptStatus.RUNNING.value
            ):
                raise StateStoreError("Attempt is not the active fenced task attempt.")
            self._require_task_row(connection, commit.run_id, commit.task_id)
            task = connection.execute(
                "SELECT status FROM tasks WHERE run_id = ? AND task_id = ?",
                (commit.run_id, commit.task_id),
            ).fetchone()
            validate_task_transition(
                TaskStatus(task["status"]), TaskStatus.INTEGRATION_PENDING
            )
            connection.execute(
                """UPDATE attempts SET status = ?, completed_at = ?,
                   observation_summary = ?, metadata_json = ? WHERE attempt_id = ?""",
                (
                    AttemptStatus.SUCCEEDED.value,
                    _timestamp(completed_at),
                    _safe_text(summary),
                    _dump_json(metadata or {}),
                    attempt_id,
                ),
            )
            connection.execute(
                """INSERT INTO task_commits(
                       run_id, task_id, commit_sha, parent_sha, worktree_id,
                       changed_files_json, created_at
                   ) VALUES (?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(run_id, task_id) DO NOTHING""",
                (
                    commit.run_id,
                    commit.task_id,
                    commit.commit_sha,
                    commit.parent_sha,
                    commit.worktree_id,
                    _dump_json(commit.changed_files),
                    _timestamp(commit.created_at),
                ),
            )
            persisted = connection.execute(
                "SELECT commit_sha FROM task_commits WHERE run_id = ? AND task_id = ?",
                (commit.run_id, commit.task_id),
            ).fetchone()
            if persisted["commit_sha"] != commit.commit_sha:
                raise StateStoreError("Task already has a different persisted commit.")
            connection.execute(
                "UPDATE tasks SET status = ?, updated_at = ? WHERE run_id = ? AND task_id = ?",
                (
                    TaskStatus.INTEGRATION_PENDING.value,
                    _timestamp(completed_at),
                    commit.run_id,
                    commit.task_id,
                ),
            )
            self._insert_event(
                connection,
                commit.run_id,
                commit.task_id,
                "task_commit_created",
                {
                    "commit_sha": commit.commit_sha,
                    "worktree_id": commit.worktree_id,
                    "worker_id": worker_id,
                    "lease_id": lease_id,
                    "fencing_token": fencing_token,
                },
            )
        return self.get_task_commit(commit.run_id, commit.task_id)  # type: ignore[return-value]

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

    def persist_cancellation_state(
        self,
        run_id: str,
        state: CancellationState,
    ) -> CancellationState:
        """Persist a full cancellation snapshot and its new lifecycle events.

        Listener callbacks may reach SQLite out of order when multiple runtime
        threads update one token. The monotonic revision check makes the newest
        complete snapshot authoritative while still deriving every missing event.
        """
        _require_id(run_id, "run")
        if not isinstance(state, CancellationState):
            raise StateStoreError("Cancellation state must be a CancellationState.")
        with self._write_transaction() as connection:
            self._require_run_row(connection, run_id)
            row = connection.execute(
                "SELECT * FROM cancellations WHERE run_id = ?",
                (run_id,),
            ).fetchone()
            previous = self._cancellation_from_row(row) if row is not None else None
            if previous is not None and state.revision <= previous.revision:
                return previous

            now = utc_now()
            connection.execute(
                """
                INSERT INTO cancellations(
                    run_id, requested, requested_at, reason, propagated_at,
                    propagation_sources_json, provider_cancellation_requested_at,
                    provider_names_json, acknowledged, acknowledged_at,
                    acknowledgement_source, acknowledgement_stage,
                    provider_cancellation_acknowledged_at,
                    provider_acknowledgement_source, active_operations_json,
                    operations_completed_after_request_json,
                    tasks_prevented_from_starting_json,
                    tasks_completed_after_request_json, scheduling_stopped_at,
                    cleanup_completed_at, resume_eligible, terminal_reason,
                    revision, updated_at
                ) VALUES (
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                    ?, ?, ?, ?
                )
                ON CONFLICT(run_id) DO UPDATE SET
                    requested = excluded.requested,
                    requested_at = excluded.requested_at,
                    reason = excluded.reason,
                    propagated_at = excluded.propagated_at,
                    propagation_sources_json = excluded.propagation_sources_json,
                    provider_cancellation_requested_at =
                        excluded.provider_cancellation_requested_at,
                    provider_names_json = excluded.provider_names_json,
                    acknowledged = excluded.acknowledged,
                    acknowledged_at = excluded.acknowledged_at,
                    acknowledgement_source = excluded.acknowledgement_source,
                    acknowledgement_stage = excluded.acknowledgement_stage,
                    provider_cancellation_acknowledged_at =
                        excluded.provider_cancellation_acknowledged_at,
                    provider_acknowledgement_source =
                        excluded.provider_acknowledgement_source,
                    active_operations_json = excluded.active_operations_json,
                    operations_completed_after_request_json =
                        excluded.operations_completed_after_request_json,
                    tasks_prevented_from_starting_json =
                        excluded.tasks_prevented_from_starting_json,
                    tasks_completed_after_request_json =
                        excluded.tasks_completed_after_request_json,
                    scheduling_stopped_at = excluded.scheduling_stopped_at,
                    cleanup_completed_at = excluded.cleanup_completed_at,
                    resume_eligible = excluded.resume_eligible,
                    terminal_reason = excluded.terminal_reason,
                    revision = excluded.revision,
                    updated_at = excluded.updated_at
                """,
                (
                    run_id,
                    int(state.requested),
                    _timestamp(state.requested_at),
                    _safe_text(state.reason),
                    _timestamp(state.propagated_at),
                    _dump_json(state.propagation_sources),
                    _timestamp(state.provider_cancellation_requested_at),
                    _dump_json(state.provider_names),
                    int(state.acknowledged),
                    _timestamp(state.acknowledged_at),
                    _safe_text(state.acknowledgement_source),
                    _safe_text(state.acknowledgement_stage),
                    _timestamp(state.provider_cancellation_acknowledged_at),
                    _safe_text(state.provider_acknowledgement_source),
                    _dump_json(
                        [
                            operation.model_dump(mode="json")
                            for operation in state.active_operations
                        ]
                    ),
                    _dump_json(state.operations_completed_after_request),
                    _dump_json(state.tasks_prevented_from_starting),
                    _dump_json(state.tasks_completed_after_request),
                    _timestamp(state.scheduling_stopped_at),
                    _timestamp(state.cleanup_completed_at),
                    int(state.resume_eligible),
                    _safe_text(state.terminal_reason),
                    state.revision,
                    _timestamp(now),
                ),
            )
            for event_type, payload in _cancellation_events(previous, state):
                self._insert_event(
                    connection,
                    run_id,
                    None,
                    event_type,
                    payload,
                )
        return self.get_cancellation_state(run_id)

    def get_cancellation_state(self, run_id: str) -> CancellationState:
        _require_id(run_id, "run")
        with self._connection() as connection:
            self._require_run_row(connection, run_id)
            row = connection.execute(
                "SELECT * FROM cancellations WHERE run_id = ?",
                (run_id,),
            ).fetchone()
        if row is None:
            return CancellationState()
        return self._cancellation_from_row(row)

    def list_events(
        self,
        run_id: str,
        *,
        after_event_id: int = 0,
        limit: int = 1000,
    ) -> list[dict[str, Any]]:
        _require_id(run_id, "run")
        _validate_event_page(after_event_id, limit)
        with self._connection() as connection:
            self._require_run_row(connection, run_id)
            rows = connection.execute(
                """
                SELECT * FROM events
                WHERE run_id = ? AND event_id > ?
                ORDER BY event_id
                LIMIT ?
                """,
                (run_id, after_event_id, limit),
            ).fetchall()
        return [_event_from_row(row) for row in rows]

    def list_all_events(
        self,
        *,
        after_event_id: int = 0,
        limit: int = 1000,
    ) -> list[dict[str, Any]]:
        """Return a bounded global event page for control-plane replay."""
        _validate_event_page(after_event_id, limit)
        with self._connection() as connection:
            rows = connection.execute(
                """
                SELECT * FROM events
                WHERE event_id > ?
                ORDER BY event_id
                LIMIT ?
                """,
                (after_event_id, limit),
            ).fetchall()
        return [_event_from_row(row) for row in rows]

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

    def record_worktree(self, record: WorktreeRecord) -> WorktreeRecord:
        with self._write_transaction() as connection:
            self._require_run_row(connection, record.run_id)
            if record.task_id is not None:
                self._require_task_row(connection, record.run_id, record.task_id)
            connection.execute(
                """
                INSERT INTO worktrees(
                    worktree_id, run_id, task_id, path, repository_root, base_commit,
                    branch_ref, purpose, status, worker_id, result_commit, created_at,
                    updated_at, metadata_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record.worktree_id,
                    record.run_id,
                    record.task_id,
                    record.path,
                    record.repository_root,
                    record.base_commit,
                    record.branch_ref,
                    record.purpose.value,
                    record.status.value,
                    record.worker_id,
                    record.result_commit,
                    _timestamp(record.created_at),
                    _timestamp(record.updated_at),
                    _dump_json(record.metadata),
                ),
            )
            self._insert_event(
                connection,
                record.run_id,
                record.task_id,
                "worktree_creation_started",
                {
                    "worktree_id": record.worktree_id,
                    "purpose": record.purpose.value,
                },
            )
        return self.get_worktree(record.worktree_id)

    def get_worktree(self, worktree_id: str) -> WorktreeRecord:
        _require_id(worktree_id, "worktree")
        with self._connection() as connection:
            row = connection.execute(
                "SELECT * FROM worktrees WHERE worktree_id = ?", (worktree_id,)
            ).fetchone()
        if row is None:
            raise StateStoreError(f"Worktree '{worktree_id}' was not found.")
        return self._worktree_from_row(row)

    def list_worktrees(
        self,
        run_id: str | None = None,
        *,
        task_id: str | None = None,
    ) -> list[WorktreeRecord]:
        query = "SELECT * FROM worktrees"
        clauses: list[str] = []
        parameters: list[Any] = []
        if run_id is not None:
            clauses.append("run_id = ?")
            parameters.append(run_id)
        if task_id is not None:
            clauses.append("task_id = ?")
            parameters.append(task_id)
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += " ORDER BY created_at, worktree_id"
        with self._connection() as connection:
            rows = connection.execute(query, parameters).fetchall()
        return [self._worktree_from_row(row) for row in rows]

    def update_worktree(
        self,
        worktree_id: str,
        *,
        status: WorktreeStatus | None = None,
        worker_id: str | None = None,
        result_commit: str | None = None,
        metadata_updates: dict[str, Any] | None = None,
        event_type: str = "worktree_updated",
    ) -> WorktreeRecord:
        with self._write_transaction() as connection:
            row = connection.execute(
                "SELECT * FROM worktrees WHERE worktree_id = ?", (worktree_id,)
            ).fetchone()
            if row is None:
                raise StateStoreError(f"Worktree '{worktree_id}' was not found.")
            metadata = _load_json(row["metadata_json"], "worktree metadata")
            if metadata_updates:
                metadata.update(_sanitize(metadata_updates))
            now = utc_now()
            connection.execute(
                """
                UPDATE worktrees SET status = COALESCE(?, status),
                    worker_id = COALESCE(?, worker_id),
                    result_commit = COALESCE(?, result_commit), metadata_json = ?,
                    updated_at = ? WHERE worktree_id = ?
                """,
                (
                    status.value if status else None,
                    worker_id,
                    result_commit,
                    _dump_json(metadata),
                    _timestamp(now),
                    worktree_id,
                ),
            )
            self._insert_event(
                connection,
                row["run_id"],
                row["task_id"],
                event_type,
                {"worktree_id": worktree_id, "status": status.value if status else row["status"]},
            )
        return self.get_worktree(worktree_id)

    def record_task_commit(self, record: TaskCommitRecord) -> TaskCommitRecord:
        with self._write_transaction() as connection:
            connection.execute(
                """
                INSERT INTO task_commits(
                    run_id, task_id, commit_sha, parent_sha, worktree_id,
                    changed_files_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(run_id, task_id) DO NOTHING
                """,
                (
                    record.run_id,
                    record.task_id,
                    record.commit_sha,
                    record.parent_sha,
                    record.worktree_id,
                    _dump_json(record.changed_files),
                    _timestamp(record.created_at),
                ),
            )
            row = connection.execute(
                "SELECT * FROM task_commits WHERE run_id = ? AND task_id = ?",
                (record.run_id, record.task_id),
            ).fetchone()
            if row["commit_sha"] != record.commit_sha:
                raise StateStoreError(
                    f"Task '{record.task_id}' already has a different persisted commit."
                )
            self._insert_event(
                connection,
                record.run_id,
                record.task_id,
                "task_commit_created",
                {"commit_sha": record.commit_sha, "worktree_id": record.worktree_id},
            )
        return self.get_task_commit(record.run_id, record.task_id)

    def get_task_commit(self, run_id: str, task_id: str) -> TaskCommitRecord | None:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT * FROM task_commits WHERE run_id = ? AND task_id = ?",
                (run_id, task_id),
            ).fetchone()
        return self._task_commit_from_row(row) if row is not None else None

    def list_task_commits(self, run_id: str) -> list[TaskCommitRecord]:
        with self._connection() as connection:
            rows = connection.execute(
                """SELECT c.* FROM task_commits c
                   JOIN tasks t ON t.run_id = c.run_id AND t.task_id = c.task_id
                   WHERE c.run_id = ? ORDER BY t.position, c.task_id""",
                (run_id,),
            ).fetchall()
        return [self._task_commit_from_row(row) for row in rows]

    def record_integration(self, record: IntegrationRecord) -> IntegrationRecord:
        with self._write_transaction() as connection:
            connection.execute(
                """
                INSERT INTO integration_attempts(
                    integration_id, run_id, task_id, task_commit, base_commit,
                    resulting_commit, status, conflict_files_json, error_message,
                    created_at, completed_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record.integration_id,
                    record.run_id,
                    record.task_id,
                    record.task_commit,
                    record.base_commit,
                    record.resulting_commit,
                    record.status.value,
                    _dump_json(record.conflict_files),
                    _safe_text(record.error_message),
                    _timestamp(record.created_at),
                    _timestamp(record.completed_at),
                ),
            )
            self._insert_event(
                connection,
                record.run_id,
                record.task_id,
                "integration_started",
                {"integration_id": record.integration_id, "task_commit": record.task_commit},
            )
        return self.get_integration(record.integration_id)

    def update_integration(
        self,
        integration_id: str,
        *,
        status: MergeStatus,
        resulting_commit: str | None = None,
        conflict_files: list[str] | None = None,
        error_message: str | None = None,
    ) -> IntegrationRecord:
        with self._write_transaction() as connection:
            row = connection.execute(
                "SELECT * FROM integration_attempts WHERE integration_id = ?",
                (integration_id,),
            ).fetchone()
            if row is None:
                raise StateStoreError(f"Integration '{integration_id}' was not found.")
            now = utc_now()
            connection.execute(
                """UPDATE integration_attempts SET status = ?, resulting_commit = ?,
                   conflict_files_json = ?, error_message = ?, completed_at = ?
                   WHERE integration_id = ?""",
                (
                    status.value,
                    resulting_commit,
                    _dump_json(conflict_files or []),
                    _safe_text(error_message),
                    _timestamp(now),
                    integration_id,
                ),
            )
            event = {
                MergeStatus.INTEGRATED: "integration_succeeded",
                MergeStatus.INTEGRATION_CONFLICT: "integration_conflict",
                MergeStatus.INTEGRATION_FAILED: "integration_failed",
            }.get(status, "integration_updated")
            self._insert_event(
                connection,
                row["run_id"],
                row["task_id"],
                event,
                {
                    "integration_id": integration_id,
                    "resulting_commit": resulting_commit,
                    "conflict_files": conflict_files or [],
                },
            )
        return self.get_integration(integration_id)

    def get_integration(self, integration_id: str) -> IntegrationRecord:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT * FROM integration_attempts WHERE integration_id = ?",
                (integration_id,),
            ).fetchone()
        if row is None:
            raise StateStoreError(f"Integration '{integration_id}' was not found.")
        return self._integration_from_row(row)

    def list_integrations(self, run_id: str) -> list[IntegrationRecord]:
        with self._connection() as connection:
            rows = connection.execute(
                "SELECT * FROM integration_attempts WHERE run_id = ? ORDER BY created_at, integration_id",
                (run_id,),
            ).fetchall()
        return [self._integration_from_row(row) for row in rows]

    def list_worker_lease_rows(self, run_id: str | None = None) -> list[dict[str, Any]]:
        query = """SELECT lease_id, run_id, task_id, worker_id, status, heartbeat_at,
                          expires_at, fencing_token
                   FROM worker_leases"""
        parameters: tuple[str, ...] = ()
        if run_id is not None:
            query += " WHERE run_id = ?"
            parameters = (run_id,)
        query += " ORDER BY acquired_at, fencing_token"
        with self._connection() as connection:
            rows = connection.execute(query, parameters).fetchall()
        return [dict(row) for row in rows]

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

    @staticmethod
    def _worktree_from_row(row: sqlite3.Row) -> WorktreeRecord:
        return WorktreeRecord(
            worktree_id=row["worktree_id"],
            run_id=row["run_id"],
            task_id=row["task_id"],
            path=row["path"],
            repository_root=row["repository_root"],
            base_commit=row["base_commit"],
            branch_ref=row["branch_ref"],
            purpose=WorktreePurpose(row["purpose"]),
            status=WorktreeStatus(row["status"]),
            worker_id=row["worker_id"],
            result_commit=row["result_commit"],
            created_at=_parse_timestamp(row["created_at"]),
            updated_at=_parse_timestamp(row["updated_at"]),
            metadata=_load_json(row["metadata_json"], "worktree metadata"),
        )

    @staticmethod
    def _task_commit_from_row(row: sqlite3.Row) -> TaskCommitRecord:
        return TaskCommitRecord(
            run_id=row["run_id"],
            task_id=row["task_id"],
            commit_sha=row["commit_sha"],
            parent_sha=row["parent_sha"],
            worktree_id=row["worktree_id"],
            changed_files=_load_json(row["changed_files_json"], "task commit files"),
            created_at=_parse_timestamp(row["created_at"]),
        )

    @staticmethod
    def _integration_from_row(row: sqlite3.Row) -> IntegrationRecord:
        return IntegrationRecord(
            integration_id=row["integration_id"],
            run_id=row["run_id"],
            task_id=row["task_id"],
            task_commit=row["task_commit"],
            base_commit=row["base_commit"],
            resulting_commit=row["resulting_commit"],
            status=MergeStatus(row["status"]),
            conflict_files=_load_json(row["conflict_files_json"], "conflict files"),
            error_message=row["error_message"],
            created_at=_parse_timestamp(row["created_at"]),
            completed_at=_parse_timestamp(row["completed_at"]),
        )

    @staticmethod
    @_domain_decode("cancellation state")
    def _cancellation_from_row(row: sqlite3.Row) -> CancellationState:
        operations = _load_json(
            row["active_operations_json"],
            "active cancellation operations",
        )
        if not isinstance(operations, list):
            raise ValueError("active cancellation operations must be a list")
        return CancellationState(
            requested=bool(row["requested"]),
            requested_at=_parse_timestamp(row["requested_at"]),
            reason=row["reason"],
            propagated_at=_parse_timestamp(row["propagated_at"]),
            propagation_sources=_load_json(
                row["propagation_sources_json"],
                "cancellation propagation sources",
            ),
            provider_cancellation_requested_at=_parse_timestamp(
                row["provider_cancellation_requested_at"]
            ),
            provider_names=_load_json(
                row["provider_names_json"],
                "cancellation provider names",
            ),
            acknowledged=bool(row["acknowledged"]),
            acknowledged_at=_parse_timestamp(row["acknowledged_at"]),
            acknowledgement_source=row["acknowledgement_source"],
            acknowledgement_stage=row["acknowledgement_stage"],
            provider_cancellation_acknowledged_at=_parse_timestamp(
                row["provider_cancellation_acknowledged_at"]
            ),
            provider_acknowledgement_source=row[
                "provider_acknowledgement_source"
            ],
            active_operations=[
                CancellationOperation.model_validate(operation)
                for operation in operations
            ],
            operations_completed_after_request=_load_json(
                row["operations_completed_after_request_json"],
                "operations completed after cancellation",
            ),
            tasks_prevented_from_starting=_load_json(
                row["tasks_prevented_from_starting_json"],
                "tasks prevented by cancellation",
            ),
            tasks_completed_after_request=_load_json(
                row["tasks_completed_after_request_json"],
                "tasks completed after cancellation",
            ),
            scheduling_stopped_at=_parse_timestamp(
                row["scheduling_stopped_at"]
            ),
            cleanup_completed_at=_parse_timestamp(row["cleanup_completed_at"]),
            resume_eligible=bool(row["resume_eligible"]),
            terminal_reason=row["terminal_reason"],
            revision=int(row["revision"]),
        )


def _cancellation_events(
    previous: CancellationState | None,
    current: CancellationState,
) -> list[tuple[str, dict[str, Any]]]:
    before = previous or CancellationState()
    events: list[tuple[str, dict[str, Any]]] = []
    if current.requested and not before.requested:
        events.append(
            (
                "cancellation_requested",
                {
                    "requested_at": _timestamp(current.requested_at),
                    "has_reason": bool(current.reason),
                    "revision": current.revision,
                },
            )
        )
    if current.requested and current.propagated_at is not None and (
        not before.requested or before.propagated_at is None
    ):
        events.append(
            (
                "cancellation_propagated",
                {
                    "propagated_at": _timestamp(current.propagated_at),
                    "sources": current.propagation_sources,
                    "revision": current.revision,
                },
            )
        )
    if (
        current.provider_cancellation_requested_at is not None
        and before.provider_cancellation_requested_at is None
    ):
        events.append(
            (
                "provider_cancellation_requested",
                {
                    "requested_at": _timestamp(
                        current.provider_cancellation_requested_at
                    ),
                    "providers": current.provider_names,
                    "revision": current.revision,
                },
            )
        )
    if (
        current.provider_cancellation_acknowledged_at is not None
        and before.provider_cancellation_acknowledged_at is None
    ):
        events.append(
            (
                "provider_cancellation_acknowledged",
                {
                    "acknowledged_at": _timestamp(
                        current.provider_cancellation_acknowledged_at
                    ),
                    "source": current.provider_acknowledgement_source,
                    "providers": current.provider_names,
                    "revision": current.revision,
                },
            )
        )

    prior_completed = set(before.operations_completed_after_request)
    for operation in current.operations_completed_after_request:
        if operation not in prior_completed:
            events.append(
                (
                    "operation_completed_after_cancellation",
                    {
                        "operation": operation,
                        "revision": current.revision,
                    },
                )
            )
    if (
        current.scheduling_stopped_at is not None
        and before.scheduling_stopped_at is None
    ):
        events.append(
            (
                "scheduling_stopped",
                {
                    "stopped_at": _timestamp(current.scheduling_stopped_at),
                    "tasks_prevented_from_starting": (
                        current.tasks_prevented_from_starting
                    ),
                    "task_count": len(current.tasks_prevented_from_starting),
                    "revision": current.revision,
                },
            )
        )
    if current.cleanup_completed_at is not None and before.cleanup_completed_at is None:
        events.append(
            (
                "cancellation_cleanup_completed",
                {
                    "completed_at": _timestamp(current.cleanup_completed_at),
                    "resume_eligible": current.resume_eligible,
                    "tasks_completed_after_request": (
                        current.tasks_completed_after_request
                    ),
                    "revision": current.revision,
                },
            )
        )
    return events


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


def _event_from_row(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "event_id": row["event_id"],
        "run_id": row["run_id"],
        "task_id": row["task_id"],
        "event_type": row["event_type"],
        "payload": _load_json(row["payload_json"], "event payload"),
        "created_at": _parse_timestamp(row["created_at"]),
    }


def _validate_event_page(after_event_id: int, limit: int) -> None:
    if after_event_id < 0:
        raise StateStoreError("Event cursor must not be negative.")
    if limit < 1 or limit > 5000:
        raise StateStoreError("Event page limit must be between 1 and 5000.")


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
