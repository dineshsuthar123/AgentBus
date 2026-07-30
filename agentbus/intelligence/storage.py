from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Iterator, Sequence

from agentbus.intelligence.errors import (
    IndexBusyError,
    IndexCorruptedError,
    IndexPersistenceError,
    IndexSchemaError,
    IndexUnavailableError,
)
from agentbus.intelligence.migrations import (
    apply_migrations,
    schema_version,
    verify_schema,
)
from agentbus.intelligence.models import (
    DiagnosticSeverity,
    IndexDiagnostic,
    IndexSnapshot,
    IndexState,
    Project,
    RepositoryIdentity,
    SourceFile,
    SourceLanguage,
    WorkspaceIdentity,
)


_DEFAULT_BUSY_TIMEOUT_MS = 5_000
_MAX_LIST_LIMIT = 10_000


class IndexStore:
    """Transactional SQLite storage for portable repository intelligence."""

    def __init__(
        self,
        database_path: str | Path,
        *,
        busy_timeout_ms: int = _DEFAULT_BUSY_TIMEOUT_MS,
    ) -> None:
        if busy_timeout_ms < 1 or busy_timeout_ms > 120_000:
            raise ValueError("busy_timeout_ms must be between 1 and 120000")
        self.database_path = Path(database_path).expanduser().resolve()
        self.busy_timeout_ms = busy_timeout_ms
        try:
            self.database_path.parent.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise IndexUnavailableError(
                f"Unable to create index directory '{self.database_path.parent}'."
            ) from exc
        if self.database_path.exists() and not self.database_path.is_file():
            raise IndexUnavailableError(
                f"Index database path is not a file: '{self.database_path}'."
            )
        self._initialize()

    @property
    def schema_version(self) -> int:
        with self._connection() as connection:
            return schema_version(connection)

    @property
    def journal_mode(self) -> str:
        with self._connection() as connection:
            row = connection.execute("PRAGMA journal_mode").fetchone()
        return str(row[0]).lower() if row else ""

    def publish_snapshot(
        self,
        repository: RepositoryIdentity,
        workspace: WorkspaceIdentity,
        snapshot: IndexSnapshot,
        *,
        projects: Iterable[Project] = (),
        files: Iterable[SourceFile] = (),
    ) -> IndexSnapshot:
        project_records = tuple(sorted(projects, key=lambda item: item.project_id))
        file_records = tuple(sorted(files, key=lambda item: item.file_id))
        self._validate_snapshot_bundle(
            repository,
            workspace,
            snapshot,
            project_records,
            file_records,
        )

        with self._write_transaction() as connection:
            self._register_repository(connection, repository)
            self._register_workspace(connection, workspace)
            existing = connection.execute(
                "SELECT * FROM index_snapshots WHERE snapshot_id = ?",
                (snapshot.snapshot_id,),
            ).fetchone()
            if existing is not None:
                self._verify_idempotent_retry(
                    connection,
                    snapshot,
                    project_records,
                    file_records,
                )
            else:
                self._insert_snapshot(connection, snapshot)
                self._insert_projects(
                    connection,
                    snapshot.snapshot_id,
                    project_records,
                )
                self._insert_files(
                    connection,
                    snapshot.snapshot_id,
                    file_records,
                )
                self._insert_diagnostics(
                    connection,
                    snapshot.snapshot_id,
                    snapshot.diagnostics,
                )
        return self.get_snapshot(snapshot.snapshot_id)

    def get_repository(self, repository_id: str) -> RepositoryIdentity:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT * FROM repositories WHERE repository_id = ?",
                (repository_id,),
            ).fetchone()
        if row is None:
            raise IndexUnavailableError(
                f"Repository intelligence record not found: {repository_id}."
            )
        return self._repository_from_row(row)

    def get_workspace(self, workspace_id: str) -> WorkspaceIdentity:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT * FROM workspaces WHERE workspace_id = ?",
                (workspace_id,),
            ).fetchone()
        if row is None:
            raise IndexUnavailableError(
                f"Repository intelligence workspace not found: {workspace_id}."
            )
        return self._workspace_from_row(row)

    def get_snapshot(self, snapshot_id: str) -> IndexSnapshot:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT * FROM index_snapshots WHERE snapshot_id = ?",
                (snapshot_id,),
            ).fetchone()
            if row is None:
                raise IndexUnavailableError(
                    f"Repository intelligence snapshot not found: {snapshot_id}."
                )
            return self._snapshot_from_row(connection, row)

    def latest_snapshot(self, repository_id: str) -> IndexSnapshot | None:
        with self._connection() as connection:
            row = connection.execute(
                """
                SELECT *
                FROM index_snapshots
                WHERE repository_id = ?
                ORDER BY COALESCE(completed_at, created_at) DESC, snapshot_id DESC
                LIMIT 1
                """,
                (repository_id,),
            ).fetchone()
            return self._snapshot_from_row(connection, row) if row else None

    def list_snapshots(
        self,
        repository_id: str,
        *,
        limit: int = 100,
    ) -> tuple[IndexSnapshot, ...]:
        bounded_limit = _bounded_limit(limit)
        with self._connection() as connection:
            rows = connection.execute(
                """
                SELECT *
                FROM index_snapshots
                WHERE repository_id = ?
                ORDER BY COALESCE(completed_at, created_at) DESC, snapshot_id DESC
                LIMIT ?
                """,
                (repository_id, bounded_limit),
            ).fetchall()
            return tuple(
                self._snapshot_from_row(connection, row)
                for row in rows
            )

    def list_projects(self, snapshot_id: str) -> tuple[Project, ...]:
        with self._connection() as connection:
            return self._projects_for_snapshot(connection, snapshot_id)

    def list_files(self, snapshot_id: str) -> tuple[SourceFile, ...]:
        with self._connection() as connection:
            return self._files_for_snapshot(connection, snapshot_id)

    def verify(self) -> None:
        try:
            with self._connection() as connection:
                verify_schema(connection)
        except IndexSchemaError:
            raise
        except sqlite3.DatabaseError as exc:
            raise IndexCorruptedError(
                f"Repository intelligence index is corrupted: '{self.database_path}'."
            ) from exc

    def _initialize(self) -> None:
        try:
            with self._connection() as connection:
                connection.execute("PRAGMA journal_mode = WAL")
                connection.execute("PRAGMA synchronous = NORMAL")
                apply_migrations(connection)
                verify_schema(connection)
        except (IndexSchemaError, IndexUnavailableError):
            raise
        except sqlite3.DatabaseError as exc:
            raise IndexCorruptedError(
                f"Unable to initialize repository intelligence index "
                f"'{self.database_path}'."
            ) from exc

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        connection: sqlite3.Connection | None = None
        try:
            connection = sqlite3.connect(
                self.database_path,
                timeout=self.busy_timeout_ms / 1_000,
                isolation_level=None,
            )
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA foreign_keys = ON")
            connection.execute(f"PRAGMA busy_timeout = {self.busy_timeout_ms}")
            yield connection
        except (
            IndexBusyError,
            IndexCorruptedError,
            IndexPersistenceError,
            IndexSchemaError,
            IndexUnavailableError,
        ):
            raise
        except sqlite3.OperationalError as exc:
            if _is_busy_error(exc):
                raise IndexBusyError(
                    f"Repository intelligence index is busy: '{self.database_path}'."
                ) from exc
            raise IndexUnavailableError(
                f"Unable to access repository intelligence index "
                f"'{self.database_path}'."
            ) from exc
        except sqlite3.DatabaseError as exc:
            raise IndexCorruptedError(
                f"Repository intelligence index is unreadable: "
                f"'{self.database_path}'."
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
            except (
                IndexBusyError,
                IndexCorruptedError,
                IndexPersistenceError,
                IndexSchemaError,
                IndexUnavailableError,
            ):
                if connection.in_transaction:
                    connection.rollback()
                raise
            except sqlite3.IntegrityError as exc:
                if connection.in_transaction:
                    connection.rollback()
                raise IndexPersistenceError(
                    "Repository intelligence update violates an identity, "
                    "uniqueness, or relationship constraint."
                ) from exc
            except sqlite3.OperationalError as exc:
                if connection.in_transaction:
                    connection.rollback()
                if _is_busy_error(exc):
                    raise IndexBusyError(
                        f"Repository intelligence index is busy: "
                        f"'{self.database_path}'."
                    ) from exc
                raise IndexPersistenceError(
                    "Unable to commit repository intelligence update."
                ) from exc
            except sqlite3.DatabaseError as exc:
                if connection.in_transaction:
                    connection.rollback()
                raise IndexCorruptedError(
                    "Repository intelligence update failed because the index "
                    "is corrupted or unreadable."
                ) from exc

    def _register_repository(
        self,
        connection: sqlite3.Connection,
        repository: RepositoryIdentity,
    ) -> None:
        connection.execute(
            """
            INSERT INTO repositories(
                repository_id, key_hash, display_name, schema_version, created_at
            ) VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(repository_id) DO NOTHING
            """,
            (
                repository.repository_id,
                repository.key_hash,
                repository.display_name,
                repository.schema_version,
                _utc_now(),
            ),
        )
        row = connection.execute(
            "SELECT * FROM repositories WHERE repository_id = ?",
            (repository.repository_id,),
        ).fetchone()
        if (
            row is None
            or row["key_hash"] != repository.key_hash
            or row["schema_version"] != repository.schema_version
        ):
            raise IndexPersistenceError(
                f"Repository identity conflicts with stored record: "
                f"{repository.repository_id}."
            )

    def _register_workspace(
        self,
        connection: sqlite3.Connection,
        workspace: WorkspaceIdentity,
    ) -> None:
        roots_json = _json(workspace.roots)
        connection.execute(
            """
            INSERT INTO workspaces(
                workspace_id, repository_id, roots_json, created_at
            ) VALUES (?, ?, ?, ?)
            ON CONFLICT(workspace_id) DO NOTHING
            """,
            (
                workspace.workspace_id,
                workspace.repository_id,
                roots_json,
                _utc_now(),
            ),
        )
        row = connection.execute(
            "SELECT * FROM workspaces WHERE workspace_id = ?",
            (workspace.workspace_id,),
        ).fetchone()
        if (
            row is None
            or row["repository_id"] != workspace.repository_id
            or row["roots_json"] != roots_json
        ):
            raise IndexPersistenceError(
                f"Workspace identity conflicts with stored record: "
                f"{workspace.workspace_id}."
            )

    def _insert_snapshot(
        self,
        connection: sqlite3.Connection,
        snapshot: IndexSnapshot,
    ) -> None:
        connection.execute(
            """
            INSERT INTO index_snapshots(
                snapshot_id, repository_id, workspace_id, state, created_at,
                completed_at, file_count, symbol_count, reference_count,
                edge_count, project_map_hash, graph_hash, parser_versions_json,
                source_fingerprint
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                snapshot.snapshot_id,
                snapshot.repository_id,
                snapshot.workspace_id,
                snapshot.state.value,
                _datetime_text(snapshot.created_at),
                _datetime_text(snapshot.completed_at),
                snapshot.file_count,
                snapshot.symbol_count,
                snapshot.reference_count,
                snapshot.edge_count,
                snapshot.project_map_hash,
                snapshot.graph_hash,
                _json(snapshot.parser_versions),
                snapshot.source_fingerprint,
            ),
        )

    def _insert_projects(
        self,
        connection: sqlite3.Connection,
        snapshot_id: str,
        projects: Sequence[Project],
    ) -> None:
        connection.executemany(
            """
            INSERT INTO projects(
                project_id, repository_id, snapshot_id, name, kind, root,
                source_roots_json, test_roots_json, generated_roots_json,
                manifest_paths_json, workspace_project_ids_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                (
                    project.project_id,
                    project.repository_id,
                    snapshot_id,
                    project.name,
                    project.kind.value,
                    project.root,
                    _json(project.source_roots),
                    _json(project.test_roots),
                    _json(project.generated_roots),
                    _json(project.manifest_paths),
                    _json(project.workspace_project_ids),
                )
                for project in projects
            ),
        )

    def _insert_files(
        self,
        connection: sqlite3.Connection,
        snapshot_id: str,
        files: Sequence[SourceFile],
    ) -> None:
        now = _utc_now()
        connection.executemany(
            """
            INSERT INTO content_hashes(content_hash, size_bytes, first_seen_at)
            VALUES (?, ?, ?)
            ON CONFLICT(content_hash) DO NOTHING
            """,
            (
                (source.content_hash, source.size_bytes, now)
                for source in files
            ),
        )
        for source in files:
            row = connection.execute(
                "SELECT size_bytes FROM content_hashes WHERE content_hash = ?",
                (source.content_hash,),
            ).fetchone()
            if row is None or row["size_bytes"] != source.size_bytes:
                raise IndexPersistenceError(
                    f"Content hash metadata conflicts for {source.relative_path}."
                )
        connection.executemany(
            """
            INSERT INTO indexed_files(
                file_id, repository_id, project_id, snapshot_id, relative_path,
                language, content_hash, size_bytes, parser_name, parser_version,
                generated, is_test, protected
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                (
                    source.file_id,
                    source.repository_id,
                    source.project_id,
                    snapshot_id,
                    source.relative_path,
                    source.language.value,
                    source.content_hash,
                    source.size_bytes,
                    source.parser_name,
                    source.parser_version,
                    int(source.generated),
                    int(source.test),
                    int(source.protected),
                )
                for source in files
            ),
        )

    def _insert_diagnostics(
        self,
        connection: sqlite3.Connection,
        snapshot_id: str,
        diagnostics: Sequence[IndexDiagnostic],
    ) -> None:
        connection.executemany(
            """
            INSERT INTO index_diagnostics(
                snapshot_id, code, severity, message, relative_path,
                parser_name, recoverable, details_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                (
                    snapshot_id,
                    diagnostic.code,
                    diagnostic.severity.value,
                    diagnostic.message,
                    diagnostic.relative_path,
                    diagnostic.parser_name,
                    int(diagnostic.recoverable),
                    _json(diagnostic.details),
                )
                for diagnostic in diagnostics
            ),
        )

    def _verify_idempotent_retry(
        self,
        connection: sqlite3.Connection,
        snapshot: IndexSnapshot,
        projects: Sequence[Project],
        files: Sequence[SourceFile],
    ) -> None:
        stored_row = connection.execute(
            "SELECT * FROM index_snapshots WHERE snapshot_id = ?",
            (snapshot.snapshot_id,),
        ).fetchone()
        if stored_row is None:
            raise IndexCorruptedError(
                f"Snapshot disappeared during retry: {snapshot.snapshot_id}."
            )
        stored_snapshot = self._snapshot_from_row(connection, stored_row)
        stored_projects = self._projects_for_snapshot(
            connection,
            snapshot.snapshot_id,
        )
        stored_files = self._files_for_snapshot(
            connection,
            snapshot.snapshot_id,
        )
        if (
            stored_snapshot != snapshot
            or stored_projects != tuple(projects)
            or stored_files != tuple(files)
        ):
            raise IndexPersistenceError(
                f"Snapshot identity conflicts with stored content: "
                f"{snapshot.snapshot_id}."
            )

    def _validate_snapshot_bundle(
        self,
        repository: RepositoryIdentity,
        workspace: WorkspaceIdentity,
        snapshot: IndexSnapshot,
        projects: Sequence[Project],
        files: Sequence[SourceFile],
    ) -> None:
        if workspace.repository_id != repository.repository_id:
            raise IndexPersistenceError(
                "Workspace and repository identities do not match."
            )
        if snapshot.repository_id != repository.repository_id:
            raise IndexPersistenceError(
                "Snapshot and repository identities do not match."
            )
        if snapshot.workspace_id != workspace.workspace_id:
            raise IndexPersistenceError(
                "Snapshot and workspace identities do not match."
            )
        if snapshot.file_count != len(files):
            raise IndexPersistenceError(
                "Snapshot file count does not match the supplied files."
            )
        if any(
            count
            for count in (
                snapshot.symbol_count,
                snapshot.reference_count,
                snapshot.edge_count,
            )
        ):
            raise IndexPersistenceError(
                "This snapshot contains graph records that were not supplied."
            )
        project_ids = {project.project_id for project in projects}
        if len(project_ids) != len(projects):
            raise IndexPersistenceError("Snapshot contains duplicate project identities.")
        if any(
            project.repository_id != repository.repository_id
            for project in projects
        ):
            raise IndexPersistenceError(
                "Snapshot contains a project from another repository."
            )
        file_ids = {source.file_id for source in files}
        file_paths = {source.relative_path for source in files}
        if len(file_ids) != len(files) or len(file_paths) != len(files):
            raise IndexPersistenceError(
                "Snapshot contains duplicate file identities or paths."
            )
        for source in files:
            if source.repository_id != repository.repository_id:
                raise IndexPersistenceError(
                    "Snapshot contains a file from another repository."
                )
            if source.project_id and source.project_id not in project_ids:
                raise IndexPersistenceError(
                    f"File references a project outside its snapshot: "
                    f"{source.relative_path}."
                )

    def _snapshot_from_row(
        self,
        connection: sqlite3.Connection,
        row: sqlite3.Row,
    ) -> IndexSnapshot:
        diagnostics = tuple(
            IndexDiagnostic(
                code=item["code"],
                severity=DiagnosticSeverity(item["severity"]),
                message=item["message"],
                relative_path=item["relative_path"],
                parser_name=item["parser_name"],
                recoverable=bool(item["recoverable"]),
                details=_load_json(item["details_json"]),
            )
            for item in connection.execute(
                """
                SELECT *
                FROM index_diagnostics
                WHERE snapshot_id = ?
                ORDER BY diagnostic_id
                """,
                (row["snapshot_id"],),
            ).fetchall()
        )
        return IndexSnapshot(
            snapshot_id=row["snapshot_id"],
            repository_id=row["repository_id"],
            workspace_id=row["workspace_id"],
            state=IndexState(row["state"]),
            created_at=row["created_at"],
            completed_at=row["completed_at"],
            file_count=row["file_count"],
            symbol_count=row["symbol_count"],
            reference_count=row["reference_count"],
            edge_count=row["edge_count"],
            project_map_hash=row["project_map_hash"],
            graph_hash=row["graph_hash"],
            parser_versions=_load_json(row["parser_versions_json"]),
            source_fingerprint=row["source_fingerprint"],
            diagnostics=diagnostics,
        )

    def _projects_for_snapshot(
        self,
        connection: sqlite3.Connection,
        snapshot_id: str,
    ) -> tuple[Project, ...]:
        rows = connection.execute(
            """
            SELECT *
            FROM projects
            WHERE snapshot_id = ?
            ORDER BY project_id
            """,
            (snapshot_id,),
        ).fetchall()
        return tuple(
            Project(
                project_id=row["project_id"],
                repository_id=row["repository_id"],
                name=row["name"],
                kind=row["kind"],
                root=row["root"],
                source_roots=tuple(_load_json(row["source_roots_json"])),
                test_roots=tuple(_load_json(row["test_roots_json"])),
                generated_roots=tuple(_load_json(row["generated_roots_json"])),
                manifest_paths=tuple(_load_json(row["manifest_paths_json"])),
                workspace_project_ids=tuple(
                    _load_json(row["workspace_project_ids_json"])
                ),
            )
            for row in rows
        )

    def _files_for_snapshot(
        self,
        connection: sqlite3.Connection,
        snapshot_id: str,
    ) -> tuple[SourceFile, ...]:
        rows = connection.execute(
            """
            SELECT *
            FROM indexed_files
            WHERE snapshot_id = ?
            ORDER BY file_id
            """,
            (snapshot_id,),
        ).fetchall()
        return tuple(
            SourceFile(
                file_id=row["file_id"],
                repository_id=row["repository_id"],
                project_id=row["project_id"],
                relative_path=row["relative_path"],
                language=SourceLanguage(row["language"]),
                content_hash=row["content_hash"],
                size_bytes=row["size_bytes"],
                parser_name=row["parser_name"],
                parser_version=row["parser_version"],
                generated=bool(row["generated"]),
                test=bool(row["is_test"]),
                protected=bool(row["protected"]),
            )
            for row in rows
        )

    @staticmethod
    def _repository_from_row(row: sqlite3.Row) -> RepositoryIdentity:
        return RepositoryIdentity(
            repository_id=row["repository_id"],
            key_hash=row["key_hash"],
            display_name=row["display_name"],
            schema_version=row["schema_version"],
        )

    @staticmethod
    def _workspace_from_row(row: sqlite3.Row) -> WorkspaceIdentity:
        return WorkspaceIdentity(
            workspace_id=row["workspace_id"],
            repository_id=row["repository_id"],
            roots=tuple(_load_json(row["roots_json"])),
        )


def _bounded_limit(value: int) -> int:
    if value < 1 or value > _MAX_LIST_LIMIT:
        raise ValueError(f"limit must be between 1 and {_MAX_LIST_LIMIT}")
    return value


def _json(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    )


def _load_json(value: str) -> object:
    try:
        return json.loads(value)
    except (TypeError, json.JSONDecodeError) as exc:
        raise IndexCorruptedError("Stored repository intelligence JSON is invalid.") from exc


def _datetime_text(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _utc_now() -> str:
    return _datetime_text(datetime.now(timezone.utc)) or ""


def _is_busy_error(error: sqlite3.Error) -> bool:
    message = str(error).casefold()
    return "locked" in message or "busy" in message
