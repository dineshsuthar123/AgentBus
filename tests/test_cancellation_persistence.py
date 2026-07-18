from __future__ import annotations

import json

from agentbus.execution.cancellation import CancellationState
from agentbus.execution.cancellation_registry import CancellationRegistry
from agentbus.execution.models import RunRecord
from agentbus.execution.state_store import StateStore


def make_run(run_id: str = "run-1") -> RunRecord:
    return RunRecord(
        run_id=run_id,
        original_task="Exercise cancellation",
        model="deterministic",
        workspace="workspace",
    )


def cancellation_event_types(store: StateStore, run_id: str) -> list[str]:
    lifecycle_types = {
        "cancellation_requested",
        "cancellation_propagated",
        "provider_cancellation_requested",
        "provider_cancellation_acknowledged",
        "operation_completed_after_cancellation",
        "scheduling_stopped",
        "run_cancelled",
        "cancellation_cleanup_completed",
    }
    return [
        event["event_type"]
        for event in store.list_events(run_id)
        if event["event_type"] in lifecycle_types
    ]


def test_cancellation_state_round_trips_and_events_are_monotonic(tmp_path):
    database = tmp_path / "state.db"
    store = StateStore(database)
    store.create_run(make_run())
    registry = CancellationRegistry(store)
    token = registry.get("run-1")

    with token.operation(
        "deterministic.generate_json",
        source="provider:deterministic",
        interruptible=True,
        provider="deterministic",
        task_id="step-1",
    ):
        active = StateStore(database).get_cancellation_state("run-1")
        assert active.active_operations[0].task_id == "step-1"
        assert token.request("Stop the slow provider") is True
        assert token.acknowledge(
            "provider:deterministic",
            stage="latency-wait",
            provider="deterministic",
        )

    token.mark_scheduling_stopped(["step-2"])
    store.record_event("run-1", "run_cancelled")
    token.complete_cleanup(
        terminal_reason="Cancellation completed safely.",
        resume_eligible=False,
    )

    restored = StateStore(database).get_cancellation_state("run-1")
    assert restored.requested is True
    assert restored.acknowledged is True
    assert restored.acknowledgement_stage == "latency-wait"
    assert restored.provider_names == ["deterministic"]
    assert restored.active_operations == []
    assert restored.operations_completed_after_request == [
        "deterministic.generate_json"
    ]
    assert restored.tasks_prevented_from_starting == ["step-2"]
    assert restored.cleanup_completed_at is not None
    assert restored.resume_eligible is False
    assert cancellation_event_types(store, "run-1") == [
        "cancellation_requested",
        "cancellation_propagated",
        "provider_cancellation_requested",
        "provider_cancellation_acknowledged",
        "operation_completed_after_cancellation",
        "scheduling_stopped",
        "run_cancelled",
        "cancellation_cleanup_completed",
    ]


def test_newest_complete_snapshot_wins_when_callbacks_arrive_out_of_order(
    tmp_path,
):
    store = StateStore(tmp_path / "state.db")
    store.create_run(make_run())
    newest = CancellationState(
        requested=True,
        reason="stop",
        propagation_sources=["supervisor"],
        acknowledged=True,
        acknowledgement_source="worker",
        revision=4,
    )
    stale = CancellationState(
        requested=True,
        reason="stale reason",
        propagation_sources=["supervisor"],
        revision=2,
    )

    assert store.persist_cancellation_state("run-1", newest) == newest
    persisted = store.persist_cancellation_state("run-1", stale)

    assert persisted == newest
    assert store.get_cancellation_state("run-1") == newest
    assert cancellation_event_types(store, "run-1") == [
        "cancellation_requested"
    ]


def test_pre_run_request_is_flushed_after_durable_run_creation(tmp_path):
    store = StateStore(tmp_path / "state.db")
    registry = CancellationRegistry(store)
    token = registry.prepare("run-before-persist")

    assert token.request("cancel immediately") is True
    store.create_run(make_run("run-before-persist"))
    registry.synchronize("run-before-persist")

    restored = store.get_cancellation_state("run-before-persist")
    assert restored.requested is True
    assert restored.reason == "cancel immediately"
    assert registry.get("run-before-persist") is token


def test_cancellation_reason_and_event_payloads_do_not_persist_secrets(tmp_path):
    database = tmp_path / "state.db"
    store = StateStore(database)
    store.create_run(make_run())
    token = CancellationRegistry(store).get("run-1")

    token.request("api_key=real-secret-value")

    state = store.get_cancellation_state("run-1")
    events = store.list_events("run-1")
    database_text = database.read_bytes().decode("utf-8", errors="ignore")
    assert "real-secret-value" not in state.reason
    assert "real-secret-value" not in json.dumps(events, default=str)
    assert "real-secret-value" not in database_text


def test_registry_rehydrates_persisted_state_without_duplicate_events(tmp_path):
    database = tmp_path / "state.db"
    store = StateStore(database)
    store.create_run(make_run())
    first = CancellationRegistry(store)
    first.get("run-1").request("persist across restart")
    event_count = len(store.list_events("run-1"))

    second = CancellationRegistry(StateStore(database))
    restored = second.get("run-1")
    second.synchronize("run-1")

    assert restored.snapshot().requested is True
    assert restored.snapshot().reason == "persist across restart"
    assert len(store.list_events("run-1")) == event_count

