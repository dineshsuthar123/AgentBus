from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import Field

from agentbus.execution.models import DomainModel, utc_now


class WorktreeStatus(str, Enum):
    CREATING = "creating"
    READY = "ready"
    ACTIVE = "active"
    COMPLETED = "completed"
    CONFLICTED = "conflicted"
    ORPHANED = "orphaned"
    CLEANUP_PENDING = "cleanup_pending"
    REMOVED = "removed"


class WorktreePurpose(str, Enum):
    TASK = "task"
    INTEGRATION = "integration"
    REPLAY = "replay"


class MergeStatus(str, Enum):
    INTEGRATION_PENDING = "integration_pending"
    INTEGRATING = "integrating"
    INTEGRATED = "integrated"
    INTEGRATION_CONFLICT = "integration_conflict"
    INTEGRATION_FAILED = "integration_failed"


class WorktreeRecord(DomainModel):
    worktree_id: str = Field(min_length=1)
    run_id: str = Field(min_length=1)
    task_id: str | None = None
    path: str = Field(min_length=1)
    repository_root: str = Field(min_length=1)
    base_commit: str = Field(min_length=1)
    branch_ref: str = Field(min_length=1)
    purpose: WorktreePurpose
    status: WorktreeStatus = WorktreeStatus.CREATING
    worker_id: str | None = None
    result_commit: str | None = None
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)
    metadata: dict[str, Any] = Field(default_factory=dict)


class TaskCommitRecord(DomainModel):
    run_id: str
    task_id: str
    commit_sha: str
    parent_sha: str
    worktree_id: str
    changed_files: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=utc_now)


class IntegrationRecord(DomainModel):
    integration_id: str
    run_id: str
    task_id: str
    task_commit: str
    base_commit: str
    resulting_commit: str | None = None
    status: MergeStatus
    conflict_files: list[str] = Field(default_factory=list)
    error_message: str | None = None
    created_at: datetime = Field(default_factory=utc_now)
    completed_at: datetime | None = None


class MergeAttempt(IntegrationRecord):
    pass
