from __future__ import annotations

import re
from collections.abc import Mapping
from enum import Enum
from pathlib import Path
from typing import Any

from pydantic import Field, field_validator

from agentbus.execution.state_store import StateStore
from agentbus.replay.errors import ReplayIncompatibleError, ReplayIsolationError
from agentbus.trace.models import (
    Trace,
    TraceCheckpoint,
    TraceIdentifier,
    TraceInput,
    TraceModel,
)
from agentbus.trace.recorder import TraceRecorder
from agentbus.trace.redaction import sanitize_document
from agentbus.trace.storage import ContentAddressedStore
from agentbus.trace.version import TRACE_SCHEMA_VERSION
from agentbus.worktrees.manager import GitWorktreeManager
from agentbus.worktrees.models import WorktreeRecord

CHECKPOINT_STATE_SCHEMA_VERSION = 1
CHECKPOINT_MEDIA_TYPE = "application/vnd.agentbus.checkpoint+json"
_SAFE_COMPONENT = re.compile(r"[^a-zA-Z0-9._-]+")


class CheckpointKind(str, Enum):
    PLAN_CREATED = "plan_created"
    GRAPH_PERSISTED = "graph_persisted"
    TASK_COMPLETED = "task_completed"
    APPROVAL_DECIDED = "approval_decided"
    TOOL_COMPLETED = "tool_completed"
    VERIFIER_COMPLETED = "verifier_completed"
    INTEGRATION_COMPLETED = "integration_completed"


class ReplayCheckpointState(TraceModel):
    schema_version: int = CHECKPOINT_STATE_SCHEMA_VERSION
    trace_schema_version: int = TRACE_SCHEMA_VERSION
    checkpoint_id: TraceIdentifier
    trace_id: TraceIdentifier
    run_id: TraceIdentifier
    kind: CheckpointKind
    parent_checkpoint_id: TraceIdentifier | None = None
    task_id: TraceIdentifier | None = None
    completed_task_ids: list[TraceIdentifier] = Field(default_factory=list)
    required_task_ids: list[TraceIdentifier] = Field(default_factory=list)
    durable_state: dict[str, Any] = Field(default_factory=dict)
    base_commit: str | None = Field(
        default=None,
        pattern=r"^[0-9a-fA-F]{7,64}$",
    )
    repository_tree_sha256: str | None = Field(
        default=None,
        pattern=r"^[0-9a-f]{64}$",
    )
    protocol_versions: dict[str, int] = Field(default_factory=dict)

    @field_validator("schema_version")
    @classmethod
    def state_schema_is_supported(cls, value: int) -> int:
        if value != CHECKPOINT_STATE_SCHEMA_VERSION:
            raise ValueError(f"unsupported checkpoint state version: {value}")
        return value

    @field_validator("trace_schema_version")
    @classmethod
    def trace_schema_is_supported(cls, value: int) -> int:
        if value != TRACE_SCHEMA_VERSION:
            raise ValueError(f"unsupported trace schema version: {value}")
        return value

    @field_validator("completed_task_ids", "required_task_ids")
    @classmethod
    def task_ids_are_unique(cls, value: list[str]) -> list[str]:
        if len(value) != len(set(value)):
            raise ValueError("checkpoint task IDs must be unique")
        return value

    @field_validator("durable_state")
    @classmethod
    def durable_state_is_safe(cls, value: dict[str, Any]) -> dict[str, Any]:
        return sanitize_document(value).value


class ReplayIsolation(TraceModel):
    replay_id: TraceIdentifier
    root: str
    database_path: str
    worktree: WorktreeRecord | None = None
    cleanup_recommendation: str


class CheckpointManager:
    def __init__(self, store: ContentAddressedStore):
        self.store = store

    def capture(
        self,
        recorder: TraceRecorder,
        *,
        kind: CheckpointKind,
        label: str,
        parent_checkpoint_id: str | None = None,
        task_id: str | None = None,
        completed_task_ids: list[str] | None = None,
        required_task_ids: list[str] | None = None,
        durable_state: Mapping[str, Any] | None = None,
        base_commit: str | None = None,
        repository_tree_sha256: str | None = None,
        protocol_versions: Mapping[str, int] | None = None,
        span_id: str | None = None,
    ) -> TraceCheckpoint:
        def create_references(checkpoint_id: str) -> list[TraceInput]:
            state = ReplayCheckpointState(
                checkpoint_id=checkpoint_id,
                trace_id=recorder.trace_id,
                run_id=recorder.run_id,
                kind=kind,
                parent_checkpoint_id=parent_checkpoint_id,
                task_id=task_id,
                completed_task_ids=completed_task_ids or [],
                required_task_ids=required_task_ids or [],
                durable_state=dict(durable_state or {}),
                base_commit=base_commit,
                repository_tree_sha256=repository_tree_sha256,
                protocol_versions=dict(protocol_versions or {}),
            )
            metadata = self.store.put_json(
                state.model_dump(mode="json"),
                producing_span_id=(
                    span_id or recorder.root_span_id or "checkpoint"
                ),
                media_type=CHECKPOINT_MEDIA_TYPE,
            )
            return [
                TraceInput(
                    reference_id=f"{checkpoint_id}-state",
                    name="checkpoint state",
                    sha256=metadata.sha256,
                    media_type=metadata.media_type,
                    byte_length=metadata.byte_size,
                    redacted=metadata.redaction.applied,
                    required_for_replay=True,
                )
            ]

        return recorder.checkpoint(
            label,
            span_id=span_id,
            state_reference_factory=create_references,
            replayable=True,
        )

    def load_state(self, checkpoint: TraceCheckpoint) -> ReplayCheckpointState:
        references = [
            item
            for item in checkpoint.state_references
            if item.media_type == CHECKPOINT_MEDIA_TYPE
        ]
        if len(references) != 1:
            raise ReplayIncompatibleError(
                "Replay checkpoint must contain exactly one state document."
            )
        try:
            state = ReplayCheckpointState.model_validate(
                self.store.get_json(references[0].sha256)
            )
        except Exception as exc:
            raise ReplayIncompatibleError(
                "Replay checkpoint state is unavailable or incompatible."
            ) from exc
        if (
            state.checkpoint_id != checkpoint.checkpoint_id
            or state.trace_id != checkpoint.trace_id
            or state.run_id != checkpoint.run_id
        ):
            raise ReplayIncompatibleError(
                "Replay checkpoint state identity does not match the trace."
            )
        missing = set(state.required_task_ids) - set(state.completed_task_ids)
        if missing:
            raise ReplayIncompatibleError(
                "Replay checkpoint is missing completed task dependencies: "
                + ", ".join(sorted(missing))
            )
        return state

    def validate_ancestry(
        self,
        trace: Trace,
        checkpoint_id: str,
    ) -> list[ReplayCheckpointState]:
        checkpoints = {
            item.checkpoint_id: item for item in trace.checkpoints
        }
        current: str | None = checkpoint_id
        visited: set[str] = set()
        ancestry: list[ReplayCheckpointState] = []
        previous_sequence: int | None = None
        while current is not None:
            if current in visited:
                raise ReplayIncompatibleError(
                    "Replay checkpoint ancestry contains a cycle."
                )
            visited.add(current)
            checkpoint = checkpoints.get(current)
            if checkpoint is None:
                raise ReplayIncompatibleError(
                    f"Replay checkpoint ancestor '{current}' is missing."
                )
            if (
                previous_sequence is not None
                and checkpoint.sequence >= previous_sequence
            ):
                raise ReplayIncompatibleError(
                    "Replay checkpoint ancestry is not causally ordered."
                )
            state = self.load_state(checkpoint)
            ancestry.append(state)
            previous_sequence = checkpoint.sequence
            current = state.parent_checkpoint_id
        ancestry.reverse()
        return ancestry


class ReplayIsolationManager:
    """Allocate replay-only databases and Git worktrees under one owned root."""

    def __init__(
        self,
        replay_root: str | Path,
        source_store: StateStore,
        *,
        repository_root: str | Path | None = None,
    ):
        self.replay_root = Path(replay_root).expanduser().resolve()
        self.source_store = source_store
        self.repository_root = (
            Path(repository_root).expanduser().resolve()
            if repository_root is not None
            else None
        )
        if self.repository_root is not None and _overlaps(
            self.replay_root,
            self.repository_root,
        ):
            raise ReplayIsolationError(
                "Replay root must be outside the source repository."
            )
        self.replay_root.mkdir(parents=True, exist_ok=True)
        _reject_link(self.replay_root)

    def reconstruct(
        self,
        replay_id: str,
        *,
        run_id: str,
        base_commit: str | None = None,
    ) -> ReplayIsolation:
        safe_id = _safe_component(replay_id)
        root = (self.replay_root / safe_id).resolve()
        if root.parent != self.replay_root or root.exists():
            raise ReplayIsolationError(
                "Replay isolation path already exists or escaped its root."
            )
        root.mkdir(parents=False)
        _reject_link(root)
        database_path = root / "state.db"
        self.source_store.backup(database_path)
        isolated_store = StateStore(database_path)
        worktree = None
        if base_commit is not None:
            if self.repository_root is None:
                raise ReplayIsolationError(
                    "Checkpoint requires a repository, but none is configured."
                )
            manager = GitWorktreeManager(
                self.repository_root,
                root / "worktrees",
                isolated_store,
            )
            worktree = manager.create_replay_worktree(
                run_id,
                replay_id,
                base_commit,
            )
            if _overlaps(Path(worktree.path), self.repository_root):
                raise ReplayIsolationError(
                    "Replay worktree overlaps the source repository."
                )
        return ReplayIsolation(
            replay_id=replay_id,
            root="[ISOLATED_REPLAY_ROOT]",
            database_path="[ISOLATED_REPLAY_ROOT]/state.db",
            worktree=(
                worktree.model_copy(
                    update={
                        "path": "[ISOLATED_REPLAY_WORKTREE]",
                        "repository_root": "[SOURCE_REPOSITORY]",
                    }
                )
                if worktree is not None
                else None
            ),
            cleanup_recommendation=(
                "Inspect the replay result, then explicitly request cleanup of "
                f"the AgentBus-owned replay '{replay_id}'."
            ),
        )

    def actual_database_path(self, replay_id: str) -> Path:
        return self.replay_root / _safe_component(replay_id) / "state.db"

    def actual_worktree_path(self, replay_id: str) -> Path | None:
        root = self.replay_root / _safe_component(replay_id) / "worktrees"
        if not root.exists():
            return None
        candidates = [
            path
            for path in root.rglob("replay-*")
            if path.is_dir() and (path / ".git").exists()
        ]
        return candidates[0] if candidates else None


def _safe_component(value: str) -> str:
    normalized = _SAFE_COMPONENT.sub("-", value).strip("-.")
    if not normalized:
        raise ReplayIsolationError("Replay ID cannot form a safe path.")
    return normalized[:128]


def _overlaps(left: Path, right: Path) -> bool:
    left = left.resolve()
    right = right.resolve()
    return left == right or left in right.parents or right in left.parents


def _reject_link(path: Path) -> None:
    try:
        details = path.lstat()
    except OSError as exc:
        raise ReplayIsolationError(
            "Unable to inspect replay isolation path."
        ) from exc
    if path.is_symlink() or getattr(details, "st_file_attributes", 0) & 0x400:
        raise ReplayIsolationError(
            "Replay isolation refuses symbolic links, junctions, and reparse points."
        )


__all__ = [
    "CHECKPOINT_MEDIA_TYPE",
    "CHECKPOINT_STATE_SCHEMA_VERSION",
    "CheckpointKind",
    "CheckpointManager",
    "ReplayCheckpointState",
    "ReplayIsolation",
    "ReplayIsolationManager",
]
