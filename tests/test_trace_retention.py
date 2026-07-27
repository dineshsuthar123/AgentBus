from datetime import datetime, timedelta, timezone

import pytest

from agentbus.trace import (
    ContentAddressedStore,
    RetentionClass,
    TraceFailure,
    TraceNotFoundError,
    TraceRecorder,
    TraceSpanType,
    TraceStatus,
)
from agentbus.trace.errors import TraceIntegrityError
from agentbus.trace.retention import (
    TraceRetentionManager,
    TraceRetentionPolicy,
)


class ControlledClock:
    def __init__(self, start: datetime) -> None:
        self.value = start

    def __call__(self) -> datetime:
        current = self.value
        self.value += timedelta(milliseconds=1)
        return current


def _trace_with_object(
    store,
    run_id,
    *,
    status=TraceStatus.SUCCEEDED,
    clock=None,
):
    recorder = TraceRecorder(
        run_id,
        clock=clock
        or ControlledClock(datetime(2026, 1, 1, tzinfo=timezone.utc)),
    )
    recorder.start_trace()
    span = recorder.start_span(TraceSpanType.CUSTOM, "capture")
    metadata = store.put_json(
        {"run_id": run_id},
        producing_span_id=span.span_id,
    )
    output = store.reference_output(
        metadata,
        reference_id=f"output-{run_id}",
        name="result",
    )
    recorder.finish_span(span.span_id, output_references=[output])
    if status == TraceStatus.RUNNING:
        return recorder.snapshot(), metadata
    failure = (
        TraceFailure(
            category="fixture_failure",
            message="Expected failure.",
        )
        if status == TraceStatus.FAILED
        else None
    )
    return recorder.finish_trace(status=status, failure=failure), metadata


def _orphan(store, name, *, retention=RetentionClass.TRANSIENT):
    return store.put_json(
        {"name": name},
        producing_span_id=f"span-{name}",
        retention_class=retention,
    )


def test_default_gc_deletes_only_orphans_and_preserves_fixture_objects(
    tmp_path,
) -> None:
    store = ContentAddressedStore(tmp_path / "objects")
    trace, referenced = _trace_with_object(store, "run-1")
    orphan = _orphan(store, "orphan")
    fixture = _orphan(
        store,
        "fixture",
        retention=RetentionClass.FIXTURE,
    )
    manager = TraceRetentionManager(store)

    plan = manager.plan([trace])
    report = manager.execute(plan)

    assert plan.deletion_hashes == [orphan.sha256]
    assert referenced.sha256 in plan.protected_hashes
    assert fixture.sha256 in plan.protected_hashes
    assert report.deleted_objects == 1
    assert report.reclaimed_bytes == orphan.byte_size
    assert store.get(referenced.sha256).metadata == referenced
    assert store.get(fixture.sha256).metadata == fixture
    with pytest.raises(TraceNotFoundError):
        store.get(orphan.sha256)


def test_failure_recent_and_active_replay_roots_are_preserved(tmp_path) -> None:
    store = ContentAddressedStore(tmp_path / "objects")
    failed, failed_object = _trace_with_object(
        store,
        "run-failed",
        status=TraceStatus.FAILED,
    )
    replayed, replayed_object = _trace_with_object(store, "run-replayed")
    removable, removable_object = _trace_with_object(store, "run-removable")
    policy = TraceRetentionPolicy(
        keep_referenced=False,
        keep_failures=True,
        keep_recent=0,
    )

    plan = TraceRetentionManager(store).plan(
        [failed, replayed, removable],
        policy=policy,
        active_replay_trace_ids=[replayed.trace_id],
    )

    assert failed_object.sha256 in plan.protected_hashes
    assert replayed_object.sha256 in plan.protected_hashes
    assert plan.deletion_hashes == [removable_object.sha256]


def test_age_and_size_bounds_select_oldest_unprotected_objects(
    tmp_path,
    monkeypatch,
) -> None:
    now = datetime(2026, 1, 10, tzinfo=timezone.utc)
    timestamps = iter(
        [
            now - timedelta(days=10),
            now - timedelta(days=2),
            now,
        ]
    )
    monkeypatch.setattr(
        "agentbus.trace.storage.utc_now",
        lambda: next(timestamps),
    )
    store = ContentAddressedStore(tmp_path / "objects")
    oldest = _orphan(store, "oldest")
    older = _orphan(store, "older")
    newest = _orphan(store, "newest")
    total = oldest.byte_size + older.byte_size + newest.byte_size
    policy = TraceRetentionPolicy(
        keep_referenced=False,
        keep_failures=False,
        keep_recent=0,
        max_age_seconds=5 * 24 * 60 * 60,
        max_total_bytes=total - oldest.byte_size - older.byte_size,
    )

    plan = TraceRetentionManager(
        store,
        clock=lambda: now,
    ).plan([], policy=policy)

    assert plan.deletion_hashes == [oldest.sha256, older.sha256]
    assert plan.reasons[oldest.sha256] == "age_bound"
    assert plan.reasons[older.sha256] == "size_bound"
    assert newest.sha256 not in plan.deletion_hashes


def test_interrupted_gc_resumes_from_atomic_journal(tmp_path) -> None:
    store = ContentAddressedStore(tmp_path / "objects")
    objects = [_orphan(store, f"orphan-{index}") for index in range(3)]
    manager = TraceRetentionManager(store)
    policy = TraceRetentionPolicy(
        keep_referenced=False,
        keep_failures=False,
        keep_recent=0,
    )
    plan = manager.plan([], policy=policy)

    def crash_after_first(_digest, count):
        if count == 1:
            raise RuntimeError("simulated process interruption")

    with pytest.raises(RuntimeError, match="interruption"):
        manager.execute(plan, after_delete=crash_after_first)

    assert manager.pending_plan() == plan
    report = manager.resume()

    assert report.resumed is True
    assert report.deleted_objects == 3
    assert report.reclaimed_bytes == sum(item.byte_size for item in objects)
    assert manager.pending_plan() is None
    for item in objects:
        with pytest.raises(TraceNotFoundError):
            store.get(item.sha256)


def test_gc_rechecks_live_references_before_each_delete(tmp_path) -> None:
    store = ContentAddressedStore(tmp_path / "objects")
    orphan = _orphan(store, "became-live")
    manager = TraceRetentionManager(store)
    plan = manager.plan(
        [],
        policy=TraceRetentionPolicy(
            keep_referenced=False,
            keep_failures=False,
            keep_recent=0,
        ),
    )

    report = manager.execute(
        plan,
        current_references=lambda: {orphan.sha256},
    )

    assert report.deleted_objects == 0
    assert report.skipped_objects == 1
    assert store.get(orphan.sha256).metadata == orphan


def test_tampered_gc_journal_is_rejected_without_deleting_objects(
    tmp_path,
) -> None:
    store = ContentAddressedStore(tmp_path / "objects")
    orphan = _orphan(store, "safe")
    manager = TraceRetentionManager(store)
    manager.journal_path.write_bytes(b'{"schema_version":999}')

    with pytest.raises(TraceIntegrityError, match="journal"):
        manager.resume()

    assert store.get(orphan.sha256).metadata == orphan
