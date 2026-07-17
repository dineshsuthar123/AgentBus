import json

import pytest

from agentbus.execution.models import (
    ApprovalOutcome,
    AttemptStatus,
    ExecutionArtifact,
    FailureCategory,
    RunRecord,
    RunStatus,
    TaskSpec,
    TaskStatus,
)
from agentbus.execution.state_store import (
    RunNotFoundError,
    StateStore,
    StateStoreError,
    TaskNotFoundError,
)
from agentbus.execution.transitions import InvalidStateTransition


def make_run(run_id="run-1"):
    return RunRecord(
        run_id=run_id,
        original_task="Build feature",
        model="fake-model",
        workspace="workspace",
        planner_output={"goal": "Build", "steps": []},
        graph_data={"version": 1, "tasks": []},
    )


def make_task(task_id="step-1", maximum_attempts=2):
    return TaskSpec(
        task_id=task_id,
        title="Implement",
        description="Implement feature",
        maximum_attempts=maximum_attempts,
    )


def test_schema_initializes_and_enables_foreign_keys(tmp_path):
    store = StateStore(tmp_path / "state.db")

    assert store.schema_version == 2
    with store._connection() as connection:
        assert connection.execute("PRAGMA foreign_keys").fetchone()[0] == 1


def test_run_and_tasks_persist_across_store_instances(tmp_path):
    path = tmp_path / "state.db"
    first = StateStore(path)
    first.create_run_with_tasks(make_run(), [make_task()])

    second = StateStore(path)
    snapshot = second.load_snapshot("run-1")

    assert snapshot.run.original_task == "Build feature"
    assert snapshot.tasks[0].task_id == "step-1"
    assert snapshot.tasks[0].status == TaskStatus.PENDING


def test_attempts_are_independent_and_numbering_survives_reload(tmp_path):
    path = tmp_path / "state.db"
    store = StateStore(path)
    store.create_run_with_tasks(make_run(), [make_task(maximum_attempts=3)])
    store.update_task_status("run-1", "step-1", TaskStatus.READY)
    store.update_task_status("run-1", "step-1", TaskStatus.RUNNING)
    first = store.create_attempt("run-1", "step-1")
    store.complete_attempt(
        first.attempt_id,
        AttemptStatus.FAILED,
        error_category=FailureCategory.MODEL_OUTPUT_ERROR,
    )
    store.update_task_status("run-1", "step-1", TaskStatus.RETRYABLE)
    store.update_task_status("run-1", "step-1", TaskStatus.READY)
    store.update_task_status("run-1", "step-1", TaskStatus.RUNNING)

    second_store = StateStore(path)
    second = second_store.create_attempt("run-1", "step-1")

    assert first.attempt_number == 1
    assert second.attempt_number == 2
    assert len(second_store.list_attempts("run-1", "step-1")) == 2


def test_events_are_json_and_sensitive_values_are_redacted(tmp_path):
    store = StateStore(tmp_path / "state.db")
    store.create_run(make_run())
    store.record_event(
        "run-1",
        "security_test",
        {
            "token": "top-secret",
            "message": (
                "password=hunter2 "
                "https://example.test/path?sig=credential-value"
            ),
        },
    )

    event = store.list_events("run-1")[-1]

    assert event["payload"]["token"] == "[REDACTED]"
    assert "hunter2" not in json.dumps(event["payload"])
    assert "credential-value" not in json.dumps(event["payload"])


def test_event_pages_support_global_monotonic_replay(tmp_path):
    store = StateStore(tmp_path / "state.db")
    store.create_run(make_run("run-1"))
    store.create_run(make_run("run-2"))
    first = store.record_event("run-1", "first")
    second = store.record_event("run-2", "second")

    replay = store.list_all_events(after_event_id=first, limit=10)

    assert [event["event_id"] for event in replay] == [second]
    assert replay[0]["run_id"] == "run-2"


def test_run_event_pages_are_bounded_and_filtered(tmp_path):
    store = StateStore(tmp_path / "state.db")
    store.create_run(make_run("run-1"))
    store.create_run(make_run("run-2"))
    store.record_event("run-1", "one")
    store.record_event("run-2", "other")
    cursor = store.record_event("run-1", "two")
    store.record_event("run-1", "three")

    replay = store.list_events("run-1", after_event_id=cursor, limit=1)

    assert len(replay) == 1
    assert replay[0]["event_type"] == "three"
    assert replay[0]["run_id"] == "run-1"


@pytest.mark.parametrize(
    ("after_event_id", "limit"),
    [(-1, 10), (0, 0), (0, 5001)],
)
def test_event_page_rejects_unbounded_or_invalid_queries(
    tmp_path,
    after_event_id,
    limit,
):
    store = StateStore(tmp_path / "state.db")

    with pytest.raises(StateStoreError):
        store.list_all_events(after_event_id=after_event_id, limit=limit)


def test_approval_persists(tmp_path):
    store = StateStore(tmp_path / "state.db")
    store.create_run_with_tasks(make_run(), [make_task()])

    approval = store.record_approval(
        "run-1",
        "step-1",
        ApprovalOutcome.APPROVED,
        "Reviewed locally",
    )
    restored = StateStore(tmp_path / "state.db").latest_approval(
        "run-1", "step-1"
    )

    assert approval.approval_id is not None
    assert restored.decision == ApprovalOutcome.APPROVED
    assert restored.reason == "Reviewed locally"


def test_artifact_persists_in_snapshot(tmp_path):
    store = StateStore(tmp_path / "state.db")
    store.create_run_with_tasks(make_run(), [make_task()])
    artifact = ExecutionArtifact(
        artifact_id="artifact-1",
        run_id="run-1",
        task_id="step-1",
        artifact_type="file",
        identifier="app.py",
        metadata={"purpose": "implementation"},
    )

    store.record_artifact(artifact)
    restored = StateStore(tmp_path / "state.db").load_snapshot("run-1")

    assert restored.artifacts == [artifact]


def test_status_updates_validate_transitions_and_write_events(tmp_path):
    store = StateStore(tmp_path / "state.db")
    store.create_run_with_tasks(make_run(), [make_task()])

    store.update_run_status("run-1", RunStatus.RUNNING)
    store.update_task_status("run-1", "step-1", TaskStatus.READY)
    store.update_task_status("run-1", "step-1", TaskStatus.RUNNING)
    store.update_task_status("run-1", "step-1", TaskStatus.SUCCEEDED)

    with pytest.raises(InvalidStateTransition):
        store.update_task_status("run-1", "step-1", TaskStatus.RUNNING)
    assert any(
        event["event_type"] == "task_status_changed"
        for event in store.list_events("run-1")
    )


def test_invalid_ids_and_missing_records_have_domain_errors(tmp_path):
    store = StateStore(tmp_path / "state.db")

    with pytest.raises(StateStoreError, match="Run ID must not be empty"):
        store.get_run("")
    with pytest.raises(RunNotFoundError, match="missing"):
        store.get_run("missing")
    store.create_run(make_run())
    with pytest.raises(TaskNotFoundError, match="unknown"):
        store.get_task("run-1", "unknown")


def test_multiple_store_instances_can_write_same_database(tmp_path):
    path = tmp_path / "state.db"
    first = StateStore(path)
    second = StateStore(path)
    first.create_run(make_run("one"))
    second.create_run(make_run("two"))

    assert {run.run_id for run in first.list_runs()} == {"one", "two"}


def test_successful_recovery_can_clear_stale_finalization_error(tmp_path):
    store = StateStore(tmp_path / "state.db")
    store.create_run(make_run())
    store.update_run_details("run-1", finalization_error="temporary failure")

    restored = store.update_run_details(
        "run-1",
        commit_identifier="abc1234",
        clear_finalization_error=True,
    )

    assert restored.commit_identifier == "abc1234"
    assert restored.finalization_error is None
