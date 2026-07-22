from __future__ import annotations

import hashlib
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
from agentbus.policy.approvals import (
    approval_binding_sha256,
    build_tool_approval_request,
    validate_tool_approval,
)
from agentbus.policy.errors import ToolApprovalBindingError
from agentbus.policy.models import ToolApprovalDisposition, ToolApprovalGrant
from agentbus.security.redaction import is_sensitive_key, redact_text
from agentbus.tools.protocol import (
    ToolApprovalRequest,
    ToolAuditRecord,
    ToolCancellationSnapshot,
    ToolDescriptor,
    ToolError,
    ToolErrorCategory,
    ToolInvocation,
    ToolInvocationStatus,
    ToolPolicyDecision,
    ToolPolicyOutcome,
    ToolResourceUsage,
    ToolResult,
    canonical_json,
)
from agentbus.tools.records import (
    TERMINAL_TOOL_STATUSES,
    ToolApprovalRecord,
    ToolAuditEntry,
    ToolInvocationRecord,
    approval_request_scope_sha256,
    invocation_identity_sha256,
    invocation_record_values,
    policy_decision_sha256,
    safe_persisted_tool_result,
    safe_policy_decision,
    safe_tool_approval_request,
    safe_tool_audit_record,
    tool_audit_scope_sha256,
)
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


class ToolInvocationNotFoundError(StateStoreError):
    pass


class ToolInvocationConflictError(StateStoreError):
    pass


class ToolApprovalNotFoundError(StateStoreError):
    pass


class ToolAuditNotFoundError(StateStoreError):
    pass


class InvalidToolInvocationTransition(StateStoreError):
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

    def record_tool_invocation(
        self,
        invocation: ToolInvocation,
        *,
        anticipated_usage: ToolResourceUsage | None = None,
        process_slot: bool = False,
    ) -> ToolInvocationRecord:
        anticipated = anticipated_usage or ToolResourceUsage()
        values = invocation_record_values(
            invocation,
            anticipated_usage=anticipated,
            process_slot=process_slot,
            updated_at=utc_now(),
        )
        with self._write_transaction() as connection:
            self._require_task_row(connection, invocation.run_id, invocation.task_id)
            existing = self._find_matching_tool_invocation(
                connection,
                invocation,
                values,
                anticipated,
                process_slot,
            )
            if existing is not None:
                return existing

            idempotency_digest = values["idempotency_key_sha256"]
            cursor = connection.execute(
                """
                INSERT INTO tool_invocations(
                    invocation_id, invocation_revision, run_id, task_id,
                    tool_name, tool_version_json, protocol_version, caller_role,
                    workspace_identity, worktree_identity, capabilities_json,
                    capability_fingerprint, arguments_sha256, invocation_sha256,
                    operation_sha256, idempotency_key_sha256, status,
                    resource_budget_json, anticipated_usage_json,
                    resource_usage_json, process_slot, policy_decision_json,
                    approval_id, safe_result_json, cancellation_json,
                    error_category, error_message, requested_at, started_at,
                    completed_at, updated_at
                ) VALUES (
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                    ?, ?, NULL, NULL, NULL, ?, NULL, NULL, ?, NULL, NULL, ?
                )
                """,
                (
                    invocation.invocation_id,
                    invocation.invocation_revision,
                    invocation.run_id,
                    invocation.task_id,
                    invocation.tool_name,
                    _dump_json(invocation.tool_version.model_dump(mode="json")),
                    invocation.protocol_version,
                    invocation.context.caller_role,
                    invocation.context.workspace_identity,
                    invocation.context.worktree_identity,
                    _dump_json(
                        [
                            capability.model_dump(mode="json")
                            for capability in invocation.requested_capabilities
                        ]
                    ),
                    values["capability_fingerprint"],
                    values["arguments_sha256"],
                    values["invocation_sha256"],
                    values["operation_sha256"],
                    idempotency_digest,
                    ToolInvocationStatus.REQUESTED.value,
                    _dump_json(invocation.resource_budget.model_dump(mode="json")),
                    _dump_json(anticipated.model_dump(mode="json")),
                    _dump_json(values["resource_usage"].model_dump(mode="json")),
                    int(process_slot),
                    _dump_json(values["cancellation"].model_dump(mode="json")),
                    _timestamp(invocation.requested_at),
                    _timestamp(values["updated_at"]),
                ),
            )
            self._insert_event(
                connection,
                invocation.run_id,
                invocation.task_id,
                "tool_invocation_requested",
                {
                    "invocation_id": invocation.invocation_id,
                    "invocation_revision": invocation.invocation_revision,
                    "invocation_sequence": int(cursor.lastrowid),
                    "tool_name": invocation.tool_name,
                    "tool_version": invocation.tool_version.model_dump(mode="json"),
                    "capability_fingerprint": values["capability_fingerprint"],
                },
            )
            row = connection.execute(
                "SELECT * FROM tool_invocations WHERE invocation_sequence = ?",
                (int(cursor.lastrowid),),
            ).fetchone()
        return self._tool_invocation_from_row(row)

    def find_tool_invocation_request(
        self,
        invocation: ToolInvocation,
        *,
        anticipated_usage: ToolResourceUsage | None = None,
        process_slot: bool = False,
    ) -> ToolInvocationRecord | None:
        anticipated = anticipated_usage or ToolResourceUsage()
        values = invocation_record_values(
            invocation,
            anticipated_usage=anticipated,
            process_slot=process_slot,
            updated_at=utc_now(),
        )
        with self._connection() as connection:
            self._require_task_row(connection, invocation.run_id, invocation.task_id)
            return self._find_matching_tool_invocation(
                connection,
                invocation,
                values,
                anticipated,
                process_slot,
            )

    def get_tool_invocation(
        self,
        run_id: str,
        invocation_id: str,
    ) -> ToolInvocationRecord:
        _require_id(run_id, "run")
        _require_id(invocation_id, "tool invocation")
        with self._connection() as connection:
            self._require_run_row(connection, run_id)
            row = connection.execute(
                """SELECT * FROM tool_invocations
                WHERE run_id = ? AND invocation_id = ?""",
                (run_id, invocation_id),
            ).fetchone()
        if row is None:
            raise ToolInvocationNotFoundError(
                f"Tool invocation '{invocation_id}' was not found in run '{run_id}'."
            )
        return self._tool_invocation_from_row(row)

    def list_tool_invocations(
        self,
        run_id: str,
        *,
        task_id: str | None = None,
        status: ToolInvocationStatus | None = None,
        after_sequence: int = 0,
        limit: int = 100,
    ) -> list[ToolInvocationRecord]:
        _require_id(run_id, "run")
        if task_id is not None:
            _require_id(task_id, "task")
        if after_sequence < 0:
            raise StateStoreError("Tool invocation cursor must not be negative.")
        if limit < 1 or limit > 1000:
            raise StateStoreError(
                "Tool invocation page limit must be between 1 and 1000."
            )
        clauses = ["run_id = ?", "invocation_sequence > ?"]
        parameters: list[object] = [run_id, after_sequence]
        if task_id is not None:
            clauses.append("task_id = ?")
            parameters.append(task_id)
        if status is not None:
            clauses.append("status = ?")
            parameters.append(status.value)
        parameters.append(limit)
        query = (
            "SELECT * FROM tool_invocations WHERE "
            + " AND ".join(clauses)
            + " ORDER BY invocation_sequence LIMIT ?"
        )
        with self._connection() as connection:
            self._require_run_row(connection, run_id)
            rows = connection.execute(query, parameters).fetchall()
        return [self._tool_invocation_from_row(row) for row in rows]

    def record_tool_policy_decision(
        self,
        run_id: str,
        decision: ToolPolicyDecision,
        *,
        approval_id: str | None = None,
    ) -> ToolInvocationRecord:
        _require_id(run_id, "run")
        if approval_id is not None:
            _require_id(approval_id, "tool approval")
        persisted_decision = safe_policy_decision(decision)
        now = utc_now()
        with self._write_transaction() as connection:
            row = self._require_tool_invocation_row(
                connection,
                run_id,
                decision.invocation_id,
            )
            record = self._tool_invocation_from_row(row)
            self._validate_tool_policy_binding(record, persisted_decision)
            if record.policy_decision is not None and policy_decision_sha256(
                record.policy_decision
            ) == policy_decision_sha256(persisted_decision):
                if approval_id is None or approval_id == record.approval_id:
                    return record
                raise ToolInvocationConflictError(
                    "A persisted tool policy decision cannot change approval scope."
                )

            if record.policy_decision is None:
                if record.status != ToolInvocationStatus.REQUESTED:
                    raise InvalidToolInvocationTransition(
                        "Initial tool policy evaluation requires requested state."
                    )
                if approval_id is not None and (
                    persisted_decision.outcome
                    != ToolPolicyOutcome.REQUIRE_APPROVAL
                ):
                    raise ToolInvocationConflictError(
                        "Only approval-required policy may attach an approval ID."
                    )
                if (
                    persisted_decision.outcome
                    == ToolPolicyOutcome.REQUIRE_APPROVAL
                    and approval_id is None
                ):
                    raise ToolInvocationConflictError(
                        "Approval-required policy must allocate a stable approval ID."
                    )
            else:
                self._validate_approved_policy_transition(
                    record,
                    persisted_decision,
                    approval_id,
                )
                if (
                    persisted_decision.outcome
                    == ToolPolicyOutcome.ALLOW_WITH_CONSTRAINTS
                ):
                    self._require_approved_tool_grant(
                        connection,
                        record,
                        approval_id,
                    )

            status = record.status
            completed_at = record.completed_at
            error_category = record.error_category
            error_message = record.error_message
            if persisted_decision.outcome == ToolPolicyOutcome.REQUIRE_APPROVAL:
                status = ToolInvocationStatus.AWAITING_APPROVAL
            elif persisted_decision.outcome == ToolPolicyOutcome.DENY:
                status = ToolInvocationStatus.DENIED
                completed_at = now
                error_category = ToolErrorCategory.POLICY_DENIED.value
                error_message = _safe_text(persisted_decision.reason)

            connection.execute(
                """UPDATE tool_invocations
                SET status = ?, policy_decision_json = ?, approval_id = ?,
                    error_category = ?, error_message = ?, completed_at = ?,
                    updated_at = ?
                WHERE invocation_id = ?""",
                (
                    status.value,
                    _dump_json(persisted_decision.model_dump(mode="json")),
                    approval_id,
                    error_category,
                    error_message,
                    _timestamp(completed_at),
                    _timestamp(now),
                    record.invocation_id,
                ),
            )
            event_type = {
                ToolPolicyOutcome.DENY: "tool_policy_denied",
                ToolPolicyOutcome.REQUIRE_APPROVAL: "tool_approval_required",
            }.get(persisted_decision.outcome, "tool_policy_allowed")
            self._insert_event(
                connection,
                record.run_id,
                record.task_id,
                event_type,
                {
                    "invocation_id": record.invocation_id,
                    "invocation_revision": record.invocation_revision,
                    "outcome": persisted_decision.outcome.value,
                    "rule_id": persisted_decision.rule_id,
                    "reason": persisted_decision.reason,
                    "approval_id": approval_id,
                },
            )
            updated = self._require_tool_invocation_row(
                connection,
                run_id,
                record.invocation_id,
            )
        return self._tool_invocation_from_row(updated)

    def record_tool_approval_request(
        self,
        request: ToolApprovalRequest,
        invocation: ToolInvocation,
        descriptor: ToolDescriptor,
    ) -> ToolApprovalRecord:
        persisted_request = safe_tool_approval_request(request)
        request_sha256 = approval_request_scope_sha256(persisted_request)
        with self._write_transaction() as connection:
            invocation_row = self._require_tool_invocation_row(
                connection,
                request.run_id,
                request.invocation_id,
            )
            invocation_record = self._tool_invocation_from_row(invocation_row)
            self._validate_tool_approval_request_binding(
                invocation_record,
                persisted_request,
                invocation,
                descriptor,
            )

            row = connection.execute(
                "SELECT * FROM tool_approvals WHERE approval_id = ?",
                (request.approval_id,),
            ).fetchone()
            if row is not None:
                record = self._tool_approval_from_row(row)
                if record.request_sha256 != request_sha256:
                    raise ToolInvocationConflictError(
                        "Tool approval identity was reused with different scope."
                    )
                return record

            row = connection.execute(
                """SELECT * FROM tool_approvals
                WHERE invocation_id = ? AND invocation_revision = ?""",
                (request.invocation_id, request.invocation_revision),
            ).fetchone()
            if row is not None:
                raise ToolInvocationConflictError(
                    "A tool invocation revision already has an approval request."
                )

            cursor = connection.execute(
                """INSERT INTO tool_approvals(
                    approval_id, invocation_id, invocation_revision, run_id,
                    task_id, request_json, request_sha256, binding_sha256,
                    disposition, reason, created_at, decided_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, NULL, NULL, NULL, ?, NULL)""",
                (
                    request.approval_id,
                    request.invocation_id,
                    request.invocation_revision,
                    request.run_id,
                    request.task_id,
                    _dump_json(persisted_request.model_dump(mode="json")),
                    request_sha256,
                    _timestamp(request.created_at),
                ),
            )
            row = connection.execute(
                "SELECT * FROM tool_approvals WHERE approval_sequence = ?",
                (int(cursor.lastrowid),),
            ).fetchone()
        return self._tool_approval_from_row(row)

    def record_tool_approval_grant(
        self,
        grant: ToolApprovalGrant,
        invocation: ToolInvocation,
        descriptor: ToolDescriptor,
    ) -> ToolApprovalRecord:
        persisted_request = safe_tool_approval_request(grant.request)
        request_sha256 = approval_request_scope_sha256(persisted_request)
        with self._write_transaction() as connection:
            row = self._require_tool_approval_row(
                connection,
                grant.request.run_id,
                grant.approval_id,
            )
            record = self._tool_approval_from_row(row)
            invocation_row = self._require_tool_invocation_row(
                connection,
                grant.request.run_id,
                grant.request.invocation_id,
            )
            invocation_record = self._tool_invocation_from_row(invocation_row)
            if invocation_record.invocation_sha256 != invocation_identity_sha256(
                invocation
            ):
                raise ToolInvocationConflictError(
                    "Tool approval grant invocation does not match durable state."
                )
            if record.request_sha256 != request_sha256:
                raise ToolInvocationConflictError(
                    "Tool approval grant does not match its persisted request."
                )
            expected_binding = approval_binding_sha256(grant.request, invocation)
            if grant.binding_sha256 != expected_binding:
                raise ToolInvocationConflictError(
                    "Tool approval grant binding does not match the invocation."
                )
            if record.disposition is not None:
                if (
                    record.disposition == grant.disposition.value
                    and record.binding_sha256 == grant.binding_sha256
                    and record.reason == _safe_text(grant.reason)
                    and record.decided_at == grant.decided_at
                ):
                    return record
                raise ToolInvocationConflictError(
                    "A decided tool approval cannot be replaced."
                )
            if grant.disposition == ToolApprovalDisposition.APPROVED:
                try:
                    validate_tool_approval(grant, invocation, descriptor)
                except ToolApprovalBindingError as exc:
                    raise ToolInvocationConflictError(str(exc)) from exc

            connection.execute(
                """UPDATE tool_approvals
                SET binding_sha256 = ?, disposition = ?, reason = ?, decided_at = ?
                WHERE approval_id = ? AND disposition IS NULL""",
                (
                    grant.binding_sha256,
                    grant.disposition.value,
                    _safe_text(grant.reason),
                    _timestamp(grant.decided_at),
                    grant.approval_id,
                ),
            )
            self._insert_event(
                connection,
                grant.request.run_id,
                grant.request.task_id,
                (
                    "tool_approval_approved"
                    if grant.disposition.value == "approved"
                    else "tool_approval_rejected"
                ),
                {
                    "approval_id": grant.approval_id,
                    "invocation_id": grant.request.invocation_id,
                    "invocation_revision": grant.request.invocation_revision,
                    "disposition": grant.disposition.value,
                    "reason": grant.reason,
                },
            )
            updated = self._require_tool_approval_row(
                connection,
                grant.request.run_id,
                grant.approval_id,
            )
        return self._tool_approval_from_row(updated)

    def get_tool_approval(
        self,
        run_id: str,
        approval_id: str,
    ) -> ToolApprovalRecord:
        _require_id(run_id, "run")
        _require_id(approval_id, "tool approval")
        with self._connection() as connection:
            self._require_run_row(connection, run_id)
            row = connection.execute(
                """SELECT * FROM tool_approvals
                WHERE run_id = ? AND approval_id = ?""",
                (run_id, approval_id),
            ).fetchone()
        if row is None:
            raise ToolApprovalNotFoundError(
                f"Tool approval '{approval_id}' was not found in run '{run_id}'."
            )
        return self._tool_approval_from_row(row)

    def list_tool_approvals(
        self,
        run_id: str,
        *,
        pending_only: bool = False,
        after_sequence: int = 0,
        limit: int = 100,
    ) -> list[ToolApprovalRecord]:
        _require_id(run_id, "run")
        if after_sequence < 0:
            raise StateStoreError("Tool approval cursor must not be negative.")
        if limit < 1 or limit > 1000:
            raise StateStoreError(
                "Tool approval page limit must be between 1 and 1000."
            )
        with self._connection() as connection:
            self._require_run_row(connection, run_id)
            rows = connection.execute(
                """SELECT * FROM tool_approvals
                WHERE run_id = ? AND approval_sequence > ?
                    AND (? = 0 OR disposition IS NULL)
                ORDER BY approval_sequence LIMIT ?""",
                (run_id, after_sequence, int(pending_only), limit),
            ).fetchall()
        return [self._tool_approval_from_row(row) for row in rows]

    def record_tool_audit(self, audit: ToolAuditRecord) -> ToolAuditEntry:
        persisted_audit = safe_tool_audit_record(audit)
        with self._write_transaction() as connection:
            invocation_row = self._require_tool_invocation_row(
                connection,
                persisted_audit.run_id,
                persisted_audit.invocation_id,
            )
            invocation = self._tool_invocation_from_row(invocation_row)
            self._validate_tool_audit_binding(invocation, persisted_audit)
            persisted_audit = persisted_audit.model_copy(
                update={"policy_decision": invocation.policy_decision}
            )
            scope_sha256 = tool_audit_scope_sha256(persisted_audit)

            row = connection.execute(
                "SELECT * FROM tool_audit_records WHERE audit_id = ?",
                (persisted_audit.audit_id,),
            ).fetchone()
            if row is not None:
                existing = self._tool_audit_from_row(row)
                if tool_audit_scope_sha256(existing.record) == scope_sha256:
                    return existing
                raise ToolInvocationConflictError(
                    "Tool audit identity was reused with different content."
                )

            row = connection.execute(
                """SELECT * FROM tool_audit_records
                WHERE invocation_id = ? AND invocation_revision = ?""",
                (
                    persisted_audit.invocation_id,
                    persisted_audit.invocation_revision,
                ),
            ).fetchone()
            if row is not None:
                existing = self._tool_audit_from_row(row)
                if tool_audit_scope_sha256(existing.record) == scope_sha256:
                    return existing
                raise ToolInvocationConflictError(
                    "Tool invocation revision already has a different audit record."
                )

            encoded = canonical_json(persisted_audit.model_dump(mode="json"))
            record_sha256 = _sha256_text(encoded)
            cursor = connection.execute(
                """INSERT INTO tool_audit_records(
                    audit_id, invocation_id, invocation_revision, run_id,
                    task_id, record_sha256, record_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    persisted_audit.audit_id,
                    persisted_audit.invocation_id,
                    persisted_audit.invocation_revision,
                    persisted_audit.run_id,
                    persisted_audit.task_id,
                    record_sha256,
                    encoded,
                    _timestamp(persisted_audit.created_at),
                ),
            )
            self._insert_event(
                connection,
                persisted_audit.run_id,
                persisted_audit.task_id,
                "tool_audit_recorded",
                {
                    "audit_id": persisted_audit.audit_id,
                    "audit_sequence": int(cursor.lastrowid),
                    "invocation_id": persisted_audit.invocation_id,
                    "invocation_revision": persisted_audit.invocation_revision,
                    "outcome": persisted_audit.outcome.value,
                    "record_sha256": record_sha256,
                },
            )
            inserted = connection.execute(
                "SELECT * FROM tool_audit_records WHERE audit_sequence = ?",
                (int(cursor.lastrowid),),
            ).fetchone()
        return self._tool_audit_from_row(inserted)

    def get_tool_audit(self, run_id: str, audit_id: str) -> ToolAuditEntry:
        _require_id(run_id, "run")
        _require_id(audit_id, "tool audit")
        with self._connection() as connection:
            self._require_run_row(connection, run_id)
            row = connection.execute(
                """SELECT * FROM tool_audit_records
                WHERE run_id = ? AND audit_id = ?""",
                (run_id, audit_id),
            ).fetchone()
        if row is None:
            raise ToolAuditNotFoundError(
                f"Tool audit '{audit_id}' was not found in run '{run_id}'."
            )
        return self._tool_audit_from_row(row)

    def list_tool_audits(
        self,
        run_id: str,
        *,
        after_sequence: int = 0,
        limit: int = 100,
    ) -> list[ToolAuditEntry]:
        _require_id(run_id, "run")
        if after_sequence < 0:
            raise StateStoreError("Tool audit cursor must not be negative.")
        if limit < 1 or limit > 1000:
            raise StateStoreError("Tool audit page limit must be between 1 and 1000.")
        with self._connection() as connection:
            self._require_run_row(connection, run_id)
            rows = connection.execute(
                """SELECT * FROM tool_audit_records
                WHERE run_id = ? AND audit_sequence > ?
                ORDER BY audit_sequence LIMIT ?""",
                (run_id, after_sequence, limit),
            ).fetchall()
        return [self._tool_audit_from_row(row) for row in rows]

    def reconcile_running_tool_invocations(
        self,
        run_id: str,
        *,
        reconciled_at: datetime | None = None,
    ) -> list[ToolInvocationRecord]:
        _require_id(run_id, "run")
        cancellation = self.get_cancellation_state(run_id)
        running = self.list_tool_invocations(
            run_id,
            status=ToolInvocationStatus.RUNNING,
            limit=1000,
        )
        now = reconciled_at or utc_now()
        reconciled: list[ToolInvocationRecord] = []
        for record in running:
            if record.policy_decision is None or record.started_at is None:
                raise StateStoreError(
                    "Running tool state is missing policy or start metadata."
                )
            if now < record.started_at:
                raise StateStoreError(
                    "Tool reconciliation time cannot precede its start time."
                )
            was_cancelled = cancellation.requested
            status = (
                ToolInvocationStatus.CANCELLED
                if was_cancelled
                else ToolInvocationStatus.FAILED
            )
            category = (
                ToolErrorCategory.CANCELLED
                if was_cancelled
                else (
                    ToolErrorCategory.PROCESS
                    if record.process_slot
                    else ToolErrorCategory.INTERNAL
                )
            )
            if was_cancelled:
                raw_message = (
                    cancellation.reason
                    or "Tool cancelled before restart reconciliation."
                )
            else:
                raw_message = (
                    "Tool execution was interrupted by a runtime restart."
                )
            message = redact_text(
                raw_message,
                max_chars=2_000,
            )
            result = ToolResult(
                invocation_id=record.invocation_id,
                invocation_revision=record.invocation_revision,
                status=status,
                error=ToolError(
                    category=category,
                    code=(
                        "restart_cancelled"
                        if was_cancelled
                        else "restart_interrupted"
                    ),
                    message=message or "Tool execution was interrupted.",
                    retryable=False,
                    safe_metadata={"restart_reconciled": True},
                ),
                duration_seconds=max(
                    0.0,
                    (now - record.started_at).total_seconds(),
                ),
                cancellation=ToolCancellationSnapshot(
                    requested=was_cancelled,
                    revision=cancellation.revision,
                    requested_at=cancellation.requested_at,
                    signal_sent=False,
                    acknowledged=False,
                    process_terminated=False,
                    operation_completed_after_request=False,
                    cleanup_completed=False,
                    reason=redact_text(cancellation.reason, max_chars=1_000),
                ),
                resource_usage=record.resource_usage,
                policy_decision=record.policy_decision,
                approval_id=record.approval_id,
                safe_diagnostic_metadata={
                    "restart_reconciled": True,
                    "process_cleanup_confirmed": False,
                },
            )
            completed = self.complete_tool_invocation(
                run_id,
                result,
                completed_at=now,
            )
            self.record_event(
                run_id,
                "tool_restart_reconciled",
                {
                    "invocation_id": record.invocation_id,
                    "invocation_revision": record.invocation_revision,
                    "status": status.value,
                    "process_cleanup_confirmed": False,
                    "automatic_retry_allowed": False,
                },
                task_id=record.task_id,
            )
            reconciled.append(completed)
        return reconciled

    def mark_tool_invocation_started(
        self,
        run_id: str,
        invocation_id: str,
        *,
        approval_id: str | None = None,
        started_at: datetime | None = None,
    ) -> ToolInvocationRecord:
        _require_id(run_id, "run")
        _require_id(invocation_id, "tool invocation")
        if approval_id is not None:
            _require_id(approval_id, "tool approval")
        now = started_at or utc_now()
        with self._write_transaction() as connection:
            row = self._require_tool_invocation_row(
                connection,
                run_id,
                invocation_id,
            )
            record = self._tool_invocation_from_row(row)
            if record.status == ToolInvocationStatus.RUNNING:
                if record.approval_id == approval_id:
                    return record
                raise ToolInvocationConflictError(
                    "A running tool invocation cannot change approval scope."
                )
            if record.status in TERMINAL_TOOL_STATUSES:
                raise InvalidToolInvocationTransition(
                    "A terminal tool invocation cannot be started."
                )
            if record.policy_decision is None or (
                record.policy_decision.outcome
                not in {
                    ToolPolicyOutcome.ALLOW,
                    ToolPolicyOutcome.ALLOW_WITH_CONSTRAINTS,
                }
            ):
                raise InvalidToolInvocationTransition(
                    "A tool invocation requires an allowing policy decision before start."
                )
            if record.status == ToolInvocationStatus.AWAITING_APPROVAL:
                if approval_id is None or approval_id != record.approval_id:
                    raise ToolInvocationConflictError(
                        "The exact persisted tool approval is required before start."
                    )
                self._require_approved_tool_grant(
                    connection,
                    record,
                    approval_id,
                )
            elif record.status != ToolInvocationStatus.REQUESTED:
                raise InvalidToolInvocationTransition(
                    f"Tool invocation cannot start from '{record.status.value}'."
                )
            elif approval_id is not None:
                raise ToolInvocationConflictError(
                    "An automatically allowed tool cannot attach an approval."
                )
            if now < record.requested_at:
                raise InvalidToolInvocationTransition(
                    "Tool start time cannot precede its request time."
                )

            connection.execute(
                """UPDATE tool_invocations
                SET status = ?, started_at = ?, updated_at = ?
                WHERE invocation_id = ?""",
                (
                    ToolInvocationStatus.RUNNING.value,
                    _timestamp(now),
                    _timestamp(now),
                    invocation_id,
                ),
            )
            self._insert_event(
                connection,
                record.run_id,
                record.task_id,
                "tool_invocation_started",
                {
                    "invocation_id": invocation_id,
                    "invocation_revision": record.invocation_revision,
                    "approval_id": approval_id,
                },
            )
            updated = self._require_tool_invocation_row(
                connection,
                run_id,
                invocation_id,
            )
        return self._tool_invocation_from_row(updated)

    def complete_tool_invocation(
        self,
        run_id: str,
        result: ToolResult,
        *,
        completed_at: datetime | None = None,
    ) -> ToolInvocationRecord:
        _require_id(run_id, "run")
        if result.status not in TERMINAL_TOOL_STATUSES - {
            ToolInvocationStatus.DENIED
        }:
            raise InvalidToolInvocationTransition(
                "Tool completion requires a runtime terminal result."
            )
        persisted_result = safe_persisted_tool_result(result)
        now = completed_at or utc_now()
        with self._write_transaction() as connection:
            row = self._require_tool_invocation_row(
                connection,
                run_id,
                result.invocation_id,
            )
            record = self._tool_invocation_from_row(row)
            if record.invocation_revision != result.invocation_revision:
                raise ToolInvocationConflictError(
                    "Tool result does not match the persisted invocation revision."
                )
            if record.policy_decision is None or policy_decision_sha256(
                record.policy_decision
            ) != policy_decision_sha256(persisted_result.policy_decision):
                raise ToolInvocationConflictError(
                    "Tool result does not match the persisted policy decision."
                )
            persisted_result = persisted_result.model_copy(
                update={"policy_decision": record.policy_decision}
            )
            if record.approval_id != result.approval_id:
                raise ToolInvocationConflictError(
                    "Tool result does not match the persisted approval scope."
                )
            if record.status in TERMINAL_TOOL_STATUSES:
                if record.safe_result == persisted_result:
                    return record
                raise ToolInvocationConflictError(
                    "A terminal tool result cannot be replaced."
                )
            if record.status != ToolInvocationStatus.RUNNING:
                raise InvalidToolInvocationTransition(
                    "Tool completion requires running state."
                )
            if record.started_at is None or now < record.started_at:
                raise InvalidToolInvocationTransition(
                    "Tool completion time cannot precede its start time."
                )
            error_category = (
                persisted_result.error.category.value
                if persisted_result.error is not None
                else None
            )
            error_message = (
                _safe_text(persisted_result.error.message)
                if persisted_result.error is not None
                else None
            )
            connection.execute(
                """UPDATE tool_invocations
                SET status = ?, safe_result_json = ?, resource_usage_json = ?,
                    cancellation_json = ?, error_category = ?, error_message = ?,
                    completed_at = ?, updated_at = ?
                WHERE invocation_id = ?""",
                (
                    persisted_result.status.value,
                    _dump_json(persisted_result.model_dump(mode="json")),
                    _dump_json(
                        persisted_result.resource_usage.model_dump(mode="json")
                    ),
                    _dump_json(
                        persisted_result.cancellation.model_dump(mode="json")
                    ),
                    error_category,
                    error_message,
                    _timestamp(now),
                    _timestamp(now),
                    result.invocation_id,
                ),
            )
            event_type = {
                ToolInvocationStatus.SUCCEEDED: "tool_succeeded",
                ToolInvocationStatus.FAILED: "tool_failed",
                ToolInvocationStatus.CANCELLED: "tool_cancelled",
                ToolInvocationStatus.TIMED_OUT: "tool_timed_out",
            }[persisted_result.status]
            self._insert_event(
                connection,
                record.run_id,
                record.task_id,
                event_type,
                {
                    "invocation_id": result.invocation_id,
                    "invocation_revision": result.invocation_revision,
                    "status": persisted_result.status.value,
                    "duration_seconds": persisted_result.duration_seconds,
                    "timed_out": persisted_result.timed_out,
                    "cancelled": persisted_result.cancellation.requested,
                    "error_category": error_category,
                },
            )
            updated = self._require_tool_invocation_row(
                connection,
                run_id,
                result.invocation_id,
            )
        return self._tool_invocation_from_row(updated)

    def _require_matching_tool_invocation(
        self,
        row: sqlite3.Row,
        *,
        invocation_sha256: str | None,
        operation_sha256: str,
        anticipated_usage: ToolResourceUsage,
        process_slot: bool,
    ) -> ToolInvocationRecord:
        record = self._tool_invocation_from_row(row)
        if (
            (
                invocation_sha256 is not None
                and record.invocation_sha256 != invocation_sha256
            )
            or record.operation_sha256 != operation_sha256
            or record.anticipated_usage != anticipated_usage
            or record.process_slot != process_slot
        ):
            raise ToolInvocationConflictError(
                "Tool invocation or idempotency identity was reused with different scope."
            )
        return record

    def _find_matching_tool_invocation(
        self,
        connection: sqlite3.Connection,
        invocation: ToolInvocation,
        values: dict[str, object],
        anticipated_usage: ToolResourceUsage,
        process_slot: bool,
    ) -> ToolInvocationRecord | None:
        row = connection.execute(
            "SELECT * FROM tool_invocations WHERE invocation_id = ?",
            (invocation.invocation_id,),
        ).fetchone()
        if row is not None:
            return self._require_matching_tool_invocation(
                row,
                invocation_sha256=str(values["invocation_sha256"]),
                operation_sha256=str(values["operation_sha256"]),
                anticipated_usage=anticipated_usage,
                process_slot=process_slot,
            )

        idempotency_digest = values["idempotency_key_sha256"]
        if idempotency_digest is None:
            return None
        row = connection.execute(
            """SELECT * FROM tool_invocations
            WHERE run_id = ? AND task_id = ?
                AND idempotency_key_sha256 = ?""",
            (invocation.run_id, invocation.task_id, idempotency_digest),
        ).fetchone()
        if row is None:
            return None
        return self._require_matching_tool_invocation(
            row,
            invocation_sha256=None,
            operation_sha256=str(values["operation_sha256"]),
            anticipated_usage=anticipated_usage,
            process_slot=process_slot,
        )

    @staticmethod
    def _validate_tool_policy_binding(
        record: ToolInvocationRecord,
        decision: ToolPolicyDecision,
    ) -> None:
        if (
            decision.invocation_id != record.invocation_id
            or decision.invocation_revision != record.invocation_revision
            or decision.capability_fingerprint != record.capability_fingerprint
            or decision.arguments_sha256 != record.arguments_sha256
        ):
            raise ToolInvocationConflictError(
                "Tool policy decision does not match the invocation binding."
            )

    @staticmethod
    def _validate_approved_policy_transition(
        record: ToolInvocationRecord,
        decision: ToolPolicyDecision,
        approval_id: str | None,
    ) -> None:
        previous = record.policy_decision
        if (
            previous is None
            or previous.outcome != ToolPolicyOutcome.REQUIRE_APPROVAL
            or record.status != ToolInvocationStatus.AWAITING_APPROVAL
            or decision.outcome
            not in {ToolPolicyOutcome.ALLOW_WITH_CONSTRAINTS, ToolPolicyOutcome.DENY}
            or approval_id is None
            or decision.safe_metadata.get("approval_id") != approval_id
        ):
            raise ToolInvocationConflictError(
                "Tool policy may change only after its exact approval is evaluated."
            )

    @staticmethod
    def _validate_tool_approval_request_binding(
        invocation_record: ToolInvocationRecord,
        request: ToolApprovalRequest,
        invocation: ToolInvocation,
        descriptor: ToolDescriptor,
    ) -> None:
        policy = invocation_record.policy_decision
        matches = (
            invocation_record.status == ToolInvocationStatus.AWAITING_APPROVAL
            and invocation_record.approval_id == request.approval_id
            and invocation_record.invocation_id == request.invocation_id
            and invocation_record.invocation_revision == request.invocation_revision
            and invocation_record.run_id == request.run_id
            and invocation_record.task_id == request.task_id
            and invocation_record.tool_name == request.tool_name
            and invocation_record.tool_version == request.tool_version
            and invocation_record.protocol_version == request.protocol_version
            and invocation_record.capabilities == request.requested_capabilities
            and invocation_record.capability_fingerprint
            == request.capability_fingerprint
            and invocation_record.arguments_sha256 == request.arguments_sha256
            and invocation_record.workspace_identity == request.workspace_identity
            and invocation_record.worktree_identity == request.worktree_identity
            and invocation_record.resource_budget == request.resource_budget
            and invocation_record.invocation_sha256
            == invocation_identity_sha256(invocation)
            and descriptor.name == request.tool_name
            and descriptor.version == request.tool_version
            and descriptor.protocol_version == request.protocol_version
            and policy is not None
            and policy.outcome == ToolPolicyOutcome.REQUIRE_APPROVAL
            and policy.rule_id == request.policy_rule
            and policy.reason == request.reason
            and policy.constraints == request.proposed_constraints
        )
        if not matches:
            raise ToolInvocationConflictError(
                "Tool approval request does not match the persisted invocation scope."
            )
        expected = safe_tool_approval_request(
            build_tool_approval_request(
                invocation,
                descriptor,
                policy,
                approval_id=request.approval_id,
                expires_at=request.expires_at,
            )
        )
        if approval_request_scope_sha256(expected) != approval_request_scope_sha256(
            request
        ):
            raise ToolInvocationConflictError(
                "Tool approval request resource summary does not match the invocation."
            )

    def _require_approved_tool_grant(
        self,
        connection: sqlite3.Connection,
        invocation: ToolInvocationRecord,
        approval_id: str | None,
    ) -> ToolApprovalRecord:
        if approval_id is None:
            raise ToolInvocationConflictError(
                "An approved tool policy transition requires an approval ID."
            )
        row = self._require_tool_approval_row(
            connection,
            invocation.run_id,
            approval_id,
        )
        approval = self._tool_approval_from_row(row)
        if (
            approval.disposition != ToolApprovalDisposition.APPROVED.value
            or approval.request.invocation_id != invocation.invocation_id
            or approval.request.invocation_revision
            != invocation.invocation_revision
            or approval.binding_sha256 is None
        ):
            raise ToolInvocationConflictError(
                "The exact persisted tool approval has not been approved."
            )
        return approval

    @staticmethod
    def _validate_tool_audit_binding(
        invocation: ToolInvocationRecord,
        audit: ToolAuditRecord,
    ) -> None:
        result = invocation.safe_result
        expected_artifacts = result.artifacts if result is not None else ()
        expected_timed_out = result.timed_out if result is not None else False
        policy_matches = (
            invocation.policy_decision is not None
            and policy_decision_sha256(invocation.policy_decision)
            == policy_decision_sha256(audit.policy_decision)
        )
        matches = (
            invocation.status in TERMINAL_TOOL_STATUSES
            and audit.invocation_id == invocation.invocation_id
            and audit.invocation_revision == invocation.invocation_revision
            and audit.run_id == invocation.run_id
            and audit.task_id == invocation.task_id
            and audit.tool_name == invocation.tool_name
            and audit.tool_version == invocation.tool_version
            and audit.protocol_version == invocation.protocol_version
            and audit.caller_role == invocation.caller_role
            and audit.capabilities == invocation.capabilities
            and policy_matches
            and audit.approval_id == invocation.approval_id
            and audit.arguments_sha256 == invocation.arguments_sha256
            and audit.started_at == invocation.started_at
            and audit.completed_at == invocation.completed_at
            and audit.cancellation == invocation.cancellation
            and audit.timed_out == expected_timed_out
            and audit.resource_usage == invocation.resource_usage
            and audit.artifacts == expected_artifacts
            and audit.outcome == invocation.status
            and audit.error_category == invocation.error_category
        )
        if not matches:
            raise ToolInvocationConflictError(
                "Tool audit does not match the terminal invocation lifecycle."
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
    def _require_tool_invocation_row(
        connection: sqlite3.Connection,
        run_id: str,
        invocation_id: str,
    ) -> sqlite3.Row:
        row = connection.execute(
            """SELECT * FROM tool_invocations
            WHERE run_id = ? AND invocation_id = ?""",
            (run_id, invocation_id),
        ).fetchone()
        if row is None:
            raise ToolInvocationNotFoundError(
                f"Tool invocation '{invocation_id}' was not found in run '{run_id}'."
            )
        return row

    @staticmethod
    def _require_tool_approval_row(
        connection: sqlite3.Connection,
        run_id: str,
        approval_id: str,
    ) -> sqlite3.Row:
        row = connection.execute(
            """SELECT * FROM tool_approvals
            WHERE run_id = ? AND approval_id = ?""",
            (run_id, approval_id),
        ).fetchone()
        if row is None:
            raise ToolApprovalNotFoundError(
                f"Tool approval '{approval_id}' was not found in run '{run_id}'."
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
    @_domain_decode("tool invocation record")
    def _tool_invocation_from_row(row: sqlite3.Row) -> ToolInvocationRecord:
        return ToolInvocationRecord(
            invocation_sequence=row["invocation_sequence"],
            invocation_id=row["invocation_id"],
            invocation_revision=row["invocation_revision"],
            run_id=row["run_id"],
            task_id=row["task_id"],
            tool_name=row["tool_name"],
            tool_version=_load_json(row["tool_version_json"], "tool version"),
            protocol_version=row["protocol_version"],
            caller_role=row["caller_role"],
            workspace_identity=row["workspace_identity"],
            worktree_identity=row["worktree_identity"],
            capabilities=_load_json(row["capabilities_json"], "tool capabilities"),
            capability_fingerprint=row["capability_fingerprint"],
            arguments_sha256=row["arguments_sha256"],
            invocation_sha256=row["invocation_sha256"],
            operation_sha256=row["operation_sha256"],
            idempotency_key_sha256=row["idempotency_key_sha256"],
            status=row["status"],
            resource_budget=_load_json(
                row["resource_budget_json"], "tool resource budget"
            ),
            anticipated_usage=_load_json(
                row["anticipated_usage_json"], "anticipated tool usage"
            ),
            resource_usage=_load_json(
                row["resource_usage_json"], "tool resource usage"
            ),
            process_slot=bool(row["process_slot"]),
            policy_decision=(
                _load_json(row["policy_decision_json"], "tool policy decision")
                if row["policy_decision_json"] is not None
                else None
            ),
            approval_id=row["approval_id"],
            safe_result=(
                _load_json(row["safe_result_json"], "safe tool result")
                if row["safe_result_json"] is not None
                else None
            ),
            cancellation=_load_json(
                row["cancellation_json"], "tool cancellation snapshot"
            ),
            error_category=row["error_category"],
            error_message=row["error_message"],
            requested_at=_parse_timestamp(row["requested_at"]),
            started_at=_parse_timestamp(row["started_at"]),
            completed_at=_parse_timestamp(row["completed_at"]),
            updated_at=_parse_timestamp(row["updated_at"]),
        )

    @staticmethod
    @_domain_decode("tool approval record")
    def _tool_approval_from_row(row: sqlite3.Row) -> ToolApprovalRecord:
        request = ToolApprovalRequest.model_validate(
            _load_json(row["request_json"], "tool approval request")
        )
        if (
            request.approval_id != row["approval_id"]
            or request.invocation_id != row["invocation_id"]
            or request.invocation_revision != row["invocation_revision"]
            or request.run_id != row["run_id"]
            or request.task_id != row["task_id"]
        ):
            raise ValueError("tool approval columns do not match the request")
        return ToolApprovalRecord(
            approval_sequence=row["approval_sequence"],
            approval_id=row["approval_id"],
            request=request,
            request_sha256=row["request_sha256"],
            binding_sha256=row["binding_sha256"],
            disposition=row["disposition"],
            reason=row["reason"],
            created_at=_parse_timestamp(row["created_at"]),
            decided_at=_parse_timestamp(row["decided_at"]),
        )

    @staticmethod
    @_domain_decode("tool audit record")
    def _tool_audit_from_row(row: sqlite3.Row) -> ToolAuditEntry:
        if _sha256_text(row["record_json"]) != row["record_sha256"]:
            raise ValueError("tool audit record digest does not match its payload")
        record = ToolAuditRecord.model_validate(
            _load_json(row["record_json"], "tool audit payload")
        )
        if (
            record.audit_id != row["audit_id"]
            or record.invocation_id != row["invocation_id"]
            or record.invocation_revision != row["invocation_revision"]
            or record.run_id != row["run_id"]
            or record.task_id != row["task_id"]
            or _timestamp(record.created_at) != row["created_at"]
        ):
            raise ValueError("tool audit columns do not match the payload")
        return ToolAuditEntry(
            audit_sequence=row["audit_sequence"],
            record=record,
            record_sha256=row["record_sha256"],
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


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()
