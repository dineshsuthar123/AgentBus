from __future__ import annotations

import json
import os
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any

from agentbus.config import AgentBusConfig
from agentbus.execution.schema import SCHEMA_VERSION
from agentbus.execution.state_store import StateStore
from agentbus.intelligence.migrations import (
    apply_migrations as apply_index_migrations,
    verify_schema as verify_index_schema,
)
from agentbus.intelligence.schema import LATEST_SCHEMA_VERSION


class MigrationState(StrEnum):
    ABSENT = "absent"
    CURRENT = "current"
    REQUIRED = "required"
    NEWER = "newer"
    INVALID = "invalid"


@dataclass(frozen=True)
class MigrationTarget:
    name: str
    path: Path
    current_version: int | None
    target_version: int
    state: MigrationState
    safe_forward: bool
    message: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "path": str(self.path),
            "current_version": self.current_version,
            "target_version": self.target_version,
            "state": self.state.value,
            "safe_forward": self.safe_forward,
            "message": self.message,
        }


@dataclass(frozen=True)
class MigrationReport:
    operation: str
    targets: tuple[MigrationTarget, ...]
    backups: tuple[Path, ...] = ()
    dry_run: bool = False
    recovered_interrupted_operation: bool = False

    @property
    def ok(self) -> bool:
        return all(
            target.state not in {MigrationState.NEWER, MigrationState.INVALID}
            for target in self.targets
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "operation": self.operation,
            "dry_run": self.dry_run,
            "recovered_interrupted_operation": self.recovered_interrupted_operation,
            "targets": [target.to_dict() for target in self.targets],
            "backups": [str(path) for path in self.backups],
        }


class MigrationCoordinator:
    def __init__(self, config: AgentBusConfig):
        self.config = config
        self.state_path = config.state_database_path.expanduser().resolve()
        self.index_path = self.state_path.parent / "repository-index.sqlite3"
        self.journal_path = self.state_path.parent / "migration-journal.json"

    def status(self) -> MigrationReport:
        return MigrationReport(operation="status", targets=self._inspect())

    def plan(self) -> MigrationReport:
        return MigrationReport(operation="plan", targets=self._inspect(), dry_run=True)

    def apply(self, *, dry_run: bool = False) -> MigrationReport:
        targets = self._inspect()
        self._raise_for_unsafe(targets)
        if dry_run:
            return MigrationReport(operation="apply", targets=targets, dry_run=True)
        recovered = self._journal_in_progress()
        required = tuple(target for target in targets if target.state == MigrationState.REQUIRED)
        if not required:
            return MigrationReport(
                operation="apply",
                targets=targets,
                recovered_interrupted_operation=recovered,
            )
        self._write_journal("in_progress", required)
        backups: list[Path] = []
        try:
            for target in required:
                if target.path.exists() and target.current_version not in {None, 0}:
                    backups.append(self._backup(target))
                if target.name == "execution-state":
                    StateStore(target.path)
                else:
                    target.path.parent.mkdir(parents=True, exist_ok=True)
                    with sqlite3.connect(target.path) as connection:
                        apply_index_migrations(connection)
            verified = self.verify()
            if not verified.ok:
                raise RuntimeError("AgentBus migration verification failed")
        except BaseException:
            self._write_journal("failed", required)
            raise
        self._write_journal("complete", required)
        return MigrationReport(
            operation="apply",
            targets=self._inspect(),
            backups=tuple(backups),
            recovered_interrupted_operation=recovered,
        )

    def verify(self) -> MigrationReport:
        targets = self._inspect()
        self._raise_for_unsafe(targets)
        verified: list[MigrationTarget] = []
        for target in targets:
            if target.state == MigrationState.ABSENT:
                verified.append(target)
                continue
            try:
                if target.name == "execution-state":
                    with sqlite3.connect(target.path) as connection:
                        result = connection.execute("PRAGMA quick_check").fetchone()
                        if not result or result[0] != "ok":
                            raise sqlite3.DatabaseError("quick_check failed")
                    StateStore(target.path)
                else:
                    with sqlite3.connect(target.path) as connection:
                        verify_index_schema(connection)
            except (OSError, RuntimeError, sqlite3.Error, ValueError) as exc:
                verified.append(
                    MigrationTarget(
                        name=target.name,
                        path=target.path,
                        current_version=target.current_version,
                        target_version=target.target_version,
                        state=MigrationState.INVALID,
                        safe_forward=False,
                        message=f"Verification failed ({type(exc).__name__}).",
                    )
                )
            else:
                verified.append(target)
        return MigrationReport(operation="verify", targets=tuple(verified))

    def _inspect(self) -> tuple[MigrationTarget, ...]:
        return (
            self._inspect_state(),
            self._inspect_index(),
        )

    def _inspect_state(self) -> MigrationTarget:
        return _inspect_database(
            name="execution-state",
            path=self.state_path,
            target=SCHEMA_VERSION,
            version_reader=_state_version,
        )

    def _inspect_index(self) -> MigrationTarget:
        return _inspect_database(
            name="repository-intelligence",
            path=self.index_path,
            target=LATEST_SCHEMA_VERSION,
            version_reader=_index_version,
        )

    def _backup(self, target: MigrationTarget) -> Path:
        backup_root = self.state_path.parent / "migration-backups"
        backup_root.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        destination = backup_root / (
            f"{target.name}-v{target.current_version}-{timestamp}-{uuid.uuid4().hex[:8]}.sqlite3"
        )
        with sqlite3.connect(target.path) as source:
            with sqlite3.connect(destination) as backup:
                source.backup(backup)
        return destination

    def _journal_in_progress(self) -> bool:
        if not self.journal_path.is_file():
            return False
        try:
            payload = json.loads(self.journal_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return False
        return isinstance(payload, dict) and payload.get("status") == "in_progress"

    def _write_journal(
        self,
        status: str,
        targets: tuple[MigrationTarget, ...],
    ) -> None:
        self.journal_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "status": status,
            "updated_at": datetime.now(UTC).isoformat(),
            "targets": [
                {
                    "name": target.name,
                    "from": target.current_version,
                    "to": target.target_version,
                }
                for target in targets
            ],
        }
        temporary = self.journal_path.with_name(
            f".{self.journal_path.name}.{uuid.uuid4().hex}.tmp"
        )
        try:
            temporary.write_text(
                json.dumps(payload, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            os.replace(temporary, self.journal_path)
        finally:
            if temporary.exists():
                temporary.unlink()

    @staticmethod
    def _raise_for_unsafe(targets: tuple[MigrationTarget, ...]) -> None:
        unsafe = [
            target
            for target in targets
            if target.state in {MigrationState.NEWER, MigrationState.INVALID}
        ]
        if unsafe:
            detail = "; ".join(f"{item.name}: {item.message}" for item in unsafe)
            raise ValueError(f"Unsafe AgentBus migration state: {detail}")


def _inspect_database(
    *,
    name: str,
    path: Path,
    target: int,
    version_reader: Any,
) -> MigrationTarget:
    if not path.exists():
        return MigrationTarget(
            name=name,
            path=path,
            current_version=None,
            target_version=target,
            state=MigrationState.ABSENT,
            safe_forward=True,
            message="Database has not been created.",
        )
    if not path.is_file():
        return MigrationTarget(
            name=name,
            path=path,
            current_version=None,
            target_version=target,
            state=MigrationState.INVALID,
            safe_forward=False,
            message="Database path is not a file.",
        )
    try:
        with sqlite3.connect(path) as connection:
            version = int(version_reader(connection))
    except (sqlite3.Error, TypeError, ValueError):
        return MigrationTarget(
            name=name,
            path=path,
            current_version=None,
            target_version=target,
            state=MigrationState.INVALID,
            safe_forward=False,
            message="Database schema metadata is unreadable.",
        )
    if version > target:
        state = MigrationState.NEWER
        message = f"Database schema {version} is newer than supported schema {target}."
    elif version < target:
        state = MigrationState.REQUIRED
        message = f"Safe forward migration is available ({version} -> {target})."
    else:
        state = MigrationState.CURRENT
        message = f"Database schema {version} is current."
    return MigrationTarget(
        name=name,
        path=path,
        current_version=version,
        target_version=target,
        state=state,
        safe_forward=state in {MigrationState.CURRENT, MigrationState.REQUIRED},
        message=message,
    )


def _state_version(connection: sqlite3.Connection) -> int:
    row = connection.execute(
        "SELECT value FROM schema_metadata WHERE key = 'schema_version'"
    ).fetchone()
    if row is None:
        raise ValueError("missing state schema version")
    return int(row[0])


def _index_version(connection: sqlite3.Connection) -> int:
    row = connection.execute("PRAGMA user_version").fetchone()
    return int(row[0]) if row else 0
