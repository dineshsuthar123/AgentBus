from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import Barrier

import pytest
from pydantic import ValidationError

from agentbus.intelligence import (
    IndexBusyError,
    IndexOperation,
    IndexOperationKind,
    IndexOperationState,
    IndexSnapshot,
    IndexState,
    IndexStore,
    RepositoryIdentity,
    WorkspaceIdentity,
    repository_identity,
    snapshot_id,
    stable_hash,
    workspace_identity,
)
from agentbus.intelligence.fingerprints import (
    parser_versions_fingerprint,
)


_FIRST_OPERATION = "indexop_" + ("a" * 32)
_SECOND_OPERATION = "indexop_" + ("b" * 32)
_NOW = datetime(2030, 1, 1, tzinfo=timezone.utc)


def _store_and_repository(
    tmp_path: Path,
) -> tuple[IndexStore, RepositoryIdentity]:
    return (
        IndexStore(tmp_path / "repository.sqlite3"),
        repository_identity("fixtures/index-operations"),
    )


def _empty_snapshot(
    repository: RepositoryIdentity,
) -> tuple[WorkspaceIdentity, IndexSnapshot]:
    repository_id = repository.repository_id
    workspace = workspace_identity(repository_id, [""])
    source_hash = stable_hash({"files": []})
    project_hash = stable_hash([])
    graph_hash = stable_hash([])
    identity = snapshot_id(
        repository_id,
        source_hash,
        parser_versions_fingerprint({}),
        project_hash,
        graph_hash,
    )
    return (
        workspace,
        IndexSnapshot(
            snapshot_id=identity,
            repository_id=repository_id,
            workspace_id=workspace.workspace_id,
            state=IndexState.CURRENT,
            created_at=_NOW,
            completed_at=_NOW,
            project_map_hash=project_hash,
            graph_hash=graph_hash,
            source_fingerprint=source_hash,
        ),
    )


def test_only_one_live_operation_can_own_repository(
    tmp_path: Path,
) -> None:
    store, repository = _store_and_repository(tmp_path)
    barrier = Barrier(2)

    def acquire(operation_id: str) -> str:
        barrier.wait(timeout=5)
        try:
            operation = store.acquire_index_operation(
                repository,
                operation_id,
                IndexOperationKind.UPDATE,
                100,
                now=_NOW,
                stale_after=timedelta(minutes=1),
            )
            return operation.operation_id
        except IndexBusyError:
            return "busy"

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = tuple(
            executor.map(acquire, (_FIRST_OPERATION, _SECOND_OPERATION))
        )

    assert outcomes.count("busy") == 1
    assert len(set(outcomes).difference({"busy"})) == 1


def test_stale_operation_is_reclaimed_and_old_owner_is_fenced(
    tmp_path: Path,
) -> None:
    store, repository = _store_and_repository(tmp_path)
    first = store.acquire_index_operation(
        repository,
        _FIRST_OPERATION,
        IndexOperationKind.BUILD,
        101,
        now=_NOW,
        stale_after=timedelta(seconds=30),
    )

    with pytest.raises(IndexBusyError, match="already active"):
        store.acquire_index_operation(
            repository,
            _SECOND_OPERATION,
            IndexOperationKind.UPDATE,
            202,
            now=_NOW + timedelta(seconds=20),
            stale_after=timedelta(seconds=30),
        )

    reclaimed = store.acquire_index_operation(
        repository,
        _SECOND_OPERATION,
        IndexOperationKind.UPDATE,
        202,
        now=_NOW + timedelta(seconds=31),
        stale_after=timedelta(seconds=30),
    )

    assert reclaimed.operation_id == _SECOND_OPERATION
    assert reclaimed.owner_pid == 202
    with pytest.raises(IndexBusyError, match="stale owner"):
        store.heartbeat_index_operation(
            repository.repository_id,
            first.operation_id,
            first.owner_pid,
            at=_NOW + timedelta(seconds=32),
        )


def test_cancellation_prevents_successful_completion(
    tmp_path: Path,
) -> None:
    store, repository = _store_and_repository(tmp_path)
    operation = store.acquire_index_operation(
        repository,
        _FIRST_OPERATION,
        IndexOperationKind.UPDATE,
        101,
        now=_NOW,
        stale_after=timedelta(minutes=1),
    )

    assert store.request_index_cancellation(repository.repository_id) is True
    heartbeat = store.heartbeat_index_operation(
        repository.repository_id,
        operation.operation_id,
        operation.owner_pid,
        at=_NOW + timedelta(seconds=1),
    )

    assert heartbeat.cancellation_requested is True
    with pytest.raises(IndexBusyError, match="cancelled"):
        store.validate_index_operation(
            repository.repository_id,
            operation.operation_id,
            operation.owner_pid,
        )
    with pytest.raises(IndexBusyError, match="cannot be completed"):
        store.finish_index_operation(
            repository.repository_id,
            operation.operation_id,
            operation.owner_pid,
            IndexOperationState.COMPLETED,
            at=_NOW + timedelta(seconds=2),
        )
    paused = store.finish_index_operation(
        repository.repository_id,
        operation.operation_id,
        operation.owner_pid,
        IndexOperationState.PAUSED,
        at=_NOW + timedelta(seconds=2),
    )
    assert paused.state == IndexOperationState.PAUSED


def test_terminal_operation_can_be_replaced(tmp_path: Path) -> None:
    store, repository = _store_and_repository(tmp_path)
    first = store.acquire_index_operation(
        repository,
        _FIRST_OPERATION,
        IndexOperationKind.BUILD,
        101,
        now=_NOW,
        stale_after=timedelta(minutes=1),
    )
    store.finish_index_operation(
        repository.repository_id,
        first.operation_id,
        first.owner_pid,
        IndexOperationState.COMPLETED,
        at=_NOW + timedelta(seconds=1),
    )

    second = store.acquire_index_operation(
        repository,
        _SECOND_OPERATION,
        IndexOperationKind.UPDATE,
        202,
        now=_NOW + timedelta(seconds=2),
        stale_after=timedelta(minutes=1),
    )

    assert second.operation_id == _SECOND_OPERATION
    assert second.state == IndexOperationState.RUNNING


def test_snapshot_publish_rejects_reclaimed_owner(tmp_path: Path) -> None:
    store, repository = _store_and_repository(tmp_path)
    first = store.acquire_index_operation(
        repository,
        _FIRST_OPERATION,
        IndexOperationKind.BUILD,
        101,
        now=_NOW,
        stale_after=timedelta(seconds=30),
    )
    second = store.acquire_index_operation(
        repository,
        _SECOND_OPERATION,
        IndexOperationKind.UPDATE,
        202,
        now=_NOW + timedelta(seconds=31),
        stale_after=timedelta(seconds=30),
    )
    workspace, snapshot = _empty_snapshot(repository)

    with pytest.raises(IndexBusyError, match="stale owner"):
        store.publish_snapshot(
            repository,
            workspace,
            snapshot,
            operation_id=first.operation_id,
            operation_owner_pid=first.owner_pid,
        )

    published = store.publish_snapshot(
        repository,
        workspace,
        snapshot,
        operation_id=second.operation_id,
        operation_owner_pid=second.owner_pid,
    )
    assert published == snapshot


def test_operation_model_rejects_unportable_or_naive_metadata() -> None:
    with pytest.raises(ValidationError, match="portable indexop"):
        IndexOperation(
            repository_id="repo_" + ("a" * 32),
            operation_id="../unsafe",
            operation_kind=IndexOperationKind.BUILD,
            state=IndexOperationState.RUNNING,
            owner_pid=1,
            started_at=_NOW,
            heartbeat_at=_NOW,
        )
    with pytest.raises(ValidationError, match="timezone"):
        IndexOperation(
            repository_id="repo_" + ("a" * 32),
            operation_id=_FIRST_OPERATION,
            operation_kind=IndexOperationKind.BUILD,
            state=IndexOperationState.RUNNING,
            owner_pid=1,
            started_at=datetime(2030, 1, 1),
            heartbeat_at=datetime(2030, 1, 1),
        )
