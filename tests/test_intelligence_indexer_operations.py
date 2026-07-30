from __future__ import annotations

from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import Event
from typing import Any

import pytest

from agentbus.intelligence import (
    IndexBusyError,
    IndexOperationKind,
    IndexOperationState,
    IndexProgressEvent,
    IndexProgressPhase,
    IndexState,
    IndexStore,
    RepositoryIndexer,
    repository_identity,
    workspace_identity,
)
from agentbus.intelligence.parsers import (
    ParseRequest,
    ParseResult,
    ParserLimits,
    ParserRegistry,
    PythonAstParser,
)
from agentbus.intelligence.parsers.base import (
    CancellationSignal,
    LanguageParser,
)


class _HookParser:
    descriptor = PythonAstParser.descriptor

    def __init__(
        self,
        *,
        before: Callable[[ParseRequest], None] | None = None,
        after: Callable[[ParseRequest], None] | None = None,
        failure: Exception | None = None,
    ) -> None:
        self.delegate = PythonAstParser()
        self.before = before
        self.after = after
        self.failure = failure

    def parse(
        self,
        request: ParseRequest,
        *,
        limits: ParserLimits | None = None,
        cancellation: CancellationSignal | None = None,
    ) -> ParseResult:
        if self.before is not None:
            self.before(request)
        if self.failure is not None:
            raise self.failure
        result = self.delegate.parse(
            request,
            limits=limits,
            cancellation=cancellation,
        )
        if self.after is not None:
            self.after(request)
        return result


class _ControlledOperationTime:
    def __init__(self) -> None:
        self.seconds = 0.0

    def clock(self) -> datetime:
        return datetime(2030, 1, 1, tzinfo=timezone.utc) + timedelta(
            seconds=self.seconds
        )

    def monotonic(self) -> float:
        return self.seconds


def _indexer(
    tmp_path: Path,
    store: IndexStore,
    parser: LanguageParser,
    **kwargs: Any,
) -> RepositoryIndexer:
    repository = repository_identity("fixtures/indexer-operations")
    workspace = workspace_identity(repository.repository_id, [""])
    return RepositoryIndexer(
        tmp_path,
        repository,
        workspace,
        store,
        registry=ParserRegistry((parser,)),
        **kwargs,
    )


def _write_source(tmp_path: Path) -> None:
    (tmp_path / "service.py").write_text(
        "def source_only_marker_27fd():\n    return True\n",
        encoding="utf-8",
    )


def test_build_persists_operation_and_bounded_progress(
    tmp_path: Path,
) -> None:
    _write_source(tmp_path)
    store = IndexStore(tmp_path / ".agentbus-test" / "index.sqlite3")
    streamed: list[IndexProgressEvent] = []
    operation_id = "indexop_" + ("1" * 32)

    result = _indexer(
        tmp_path,
        store,
        _HookParser(),
    ).build(
        operation_id=operation_id,
        progress_sink=streamed.append,
    )

    assert result.operation is not None
    assert result.operation.operation_id == operation_id
    assert result.operation.operation_kind == IndexOperationKind.BUILD
    assert result.operation.state == IndexOperationState.COMPLETED
    assert store.get_index_operation(
        result.snapshot.repository_id
    ) == result.operation
    assert tuple(streamed) == result.progress_events
    assert result.progress_events[0].phase == IndexProgressPhase.DISCOVERY
    assert (
        result.progress_events[-1].phase
        == IndexProgressPhase.COMPLETED
    )
    assert [
        event.sequence for event in result.progress_events
    ] == list(range(1, len(result.progress_events) + 1))
    serialized = "".join(
        event.model_dump_json() for event in result.progress_events
    )
    assert "source_only_marker_27fd" not in serialized
    assert str(tmp_path) not in serialized

    updated = _indexer(
        tmp_path,
        store,
        _HookParser(),
    ).update(operation_id="indexop_" + ("7" * 32))
    assert updated.operation is not None
    assert updated.operation.operation_kind == IndexOperationKind.UPDATE


def test_concurrent_index_build_is_rejected_before_duplicate_parse(
    tmp_path: Path,
) -> None:
    _write_source(tmp_path)
    store = IndexStore(tmp_path / ".agentbus-test" / "index.sqlite3")
    parser_started = Event()
    release_parser = Event()

    def block_parser(_request: ParseRequest) -> None:
        parser_started.set()
        assert release_parser.wait(timeout=5)

    first = _indexer(
        tmp_path,
        store,
        _HookParser(before=block_parser),
    )
    second = _indexer(tmp_path, store, _HookParser())

    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(
            first.build,
            operation_id="indexop_" + ("2" * 32),
        )
        assert parser_started.wait(timeout=5)
        try:
            with pytest.raises(IndexBusyError, match="already active"):
                second.build(
                    operation_id="indexop_" + ("3" * 32)
                )
        finally:
            release_parser.set()
        first_result = future.result(timeout=5)

    assert first_result.operation is not None
    assert first_result.operation.state == IndexOperationState.COMPLETED


def test_persisted_cancellation_publishes_paused_checkpoint(
    tmp_path: Path,
) -> None:
    _write_source(tmp_path)
    store = IndexStore(tmp_path / ".agentbus-test" / "index.sqlite3")
    controlled = _ControlledOperationTime()

    def request_cancel(_request: ParseRequest) -> None:
        repository = repository_identity("fixtures/indexer-operations")
        assert store.request_index_cancellation(
            repository.repository_id
        )
        controlled.seconds = 2

    indexer = _indexer(
        tmp_path,
        store,
        _HookParser(after=request_cancel),
        operation_stale_after=timedelta(seconds=30),
        operation_heartbeat_seconds=1,
        operation_owner_pid=101,
        operation_clock=controlled.clock,
        operation_monotonic=controlled.monotonic,
    )

    result = indexer.build(
        operation_id="indexop_" + ("4" * 32)
    )

    assert result.snapshot.state == IndexState.PAUSED
    assert result.operation is not None
    assert result.operation.state == IndexOperationState.PAUSED
    assert result.operation.cancellation_requested is True
    assert (
        result.progress_events[-1].phase
        == IndexProgressPhase.PAUSED
    )


def test_unexpected_index_failure_marks_operation_failed(
    tmp_path: Path,
) -> None:
    _write_source(tmp_path)
    store = IndexStore(tmp_path / ".agentbus-test" / "index.sqlite3")
    indexer = _indexer(
        tmp_path,
        store,
        _HookParser(failure=RuntimeError("injected parser crash")),
    )

    with pytest.raises(RuntimeError, match="injected parser crash"):
        indexer.build(operation_id="indexop_" + ("5" * 32))

    operation = store.get_index_operation(
        repository_identity(
            "fixtures/indexer-operations"
        ).repository_id
    )
    assert operation is not None
    assert operation.state == IndexOperationState.FAILED


def test_progress_sink_failure_does_not_fail_index_build(
    tmp_path: Path,
) -> None:
    _write_source(tmp_path)
    store = IndexStore(tmp_path / ".agentbus-test" / "index.sqlite3")

    def fail_progress(_event: IndexProgressEvent) -> None:
        raise RuntimeError("progress observer failed")

    result = _indexer(
        tmp_path,
        store,
        _HookParser(),
        maximum_progress_events=4,
    ).build(
        operation_id="indexop_" + ("6" * 32),
        progress_sink=fail_progress,
    )

    assert result.operation is not None
    assert result.operation.state == IndexOperationState.COMPLETED
    assert len(result.progress_events) == 4
    assert (
        result.progress_events[-1].phase
        == IndexProgressPhase.COMPLETED
    )
