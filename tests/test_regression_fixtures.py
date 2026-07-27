from pathlib import Path
from zipfile import ZipFile

import pytest

from agentbus.execution.models import RunRecord
from agentbus.execution.state_store import StateStore
from agentbus.models.types import ModelResult, ModelRole
from agentbus.replay import (
    FixtureAssertions,
    RegressionFixtureAssertionError,
    RegressionFixtureError,
    RegressionFixtureSpec,
    capture_regression_fixture,
    replay_regression_fixture,
)
from agentbus.replay.substitutions import capture_model_envelope
from agentbus.trace import (
    ContentAddressedStore,
    RuntimeTrace,
    TraceArchiveConsentRequiredError,
    TraceFailure,
    TraceSpanType,
    TraceStatus,
)
from agentbus.trace.sealing import seal_run_provenance


def _completed_run(tmp_path, *, status=TraceStatus.SUCCEEDED, source=False):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    state_store = StateStore(tmp_path / "state.db")
    state_store.create_run(
        RunRecord(
            run_id="run-fixture",
            original_task="Capture a regression fixture",
            model="deterministic",
            workspace=str(workspace),
            graph_data={"version": 1, "tasks": []},
        )
    )
    runtime = RuntimeTrace.open(
        state_store,
        "run-fixture",
        object_root=tmp_path / "source-objects",
        workspace=workspace,
    )
    with runtime.scope(runtime.root_context):
        runtime.call(
            TraceSpanType.VERIFIER,
            "final verifier",
            lambda: {"passed": True, "exit_code": 0},
            capture="json",
        )
        runtime.call(
            TraceSpanType.REVIEWER,
            "final reviewer",
            lambda: {"approved": True, "issues": []},
            capture="json",
        )
        if source:
            span = runtime.start_span(
                TraceSpanType.PROVIDER_RESPONSE,
                "captured provider response",
                attributes={
                    "provider": "deterministic",
                    "model": "fixture-v1",
                },
            )
            output = capture_model_envelope(
                runtime.object_store,
                ModelResult(
                    value={"code": "result = 42"},
                    provider="deterministic",
                    model="fixture-v1",
                    role=ModelRole.CODER,
                ),
                prompt="Create a deterministic fixture",
                producing_span_id=span.span_id,
                reference_id="captured-model",
            )
            runtime.finish_span(
                span,
                output_references=[output],
            )
    trace = runtime.finish(
        status=status,
        failure=(
            TraceFailure(category="FixtureFailure", message="fixture failed")
            if status == TraceStatus.FAILED
            else None
        ),
        attributes={
            "score": 100,
            "file_scope_violations": [
                str(Path.home() / "private" / "outside.py")
            ],
        },
    )
    assert trace is not None
    provenance = seal_run_provenance(
        trace,
        state_store=state_store,
        object_store=runtime.object_store,
        configuration={"provider": "deterministic"},
        task_graph={"version": 1, "tasks": []},
        final_repository_tree_sha256="d" * 64,
    )
    return trace, provenance, runtime.object_store


def test_capture_and_replay_fixture_offline_without_provider_calls(
    tmp_path,
) -> None:
    trace, provenance, source_store = _completed_run(tmp_path)
    archive = tmp_path / "fixture.agentbus-trace"

    captured = capture_regression_fixture(
        trace,
        provenance,
        source_store,
        archive,
    )

    assert captured.spec.assertions.verifier_passed is True
    assert captured.spec.assertions.reviewer_approved is True
    assert captured.spec.assertions.score == 100
    assert captured.spec.assertions.file_scope_violations == [
        "[PRIVATE_PATH]"
    ]
    assert captured.archive.source_content_included is False
    imported_store = ContentAddressedStore(tmp_path / "fixture-objects")
    result = replay_regression_fixture(archive, imported_store)
    assert result.assertions.passed is True
    assert result.replay.session.provider_calls == 0
    assert result.replay.session.network_calls == 0
    assert result.replay.session.status.value == "succeeded"


def test_capture_rejects_assertions_that_contradict_evidence(tmp_path) -> None:
    trace, provenance, store = _completed_run(tmp_path)

    with pytest.raises(
        RegressionFixtureAssertionError,
        match="reviewer_approved",
    ):
        capture_regression_fixture(
            trace,
            provenance,
            store,
            tmp_path / "contradiction.agentbus-trace",
            assertions=FixtureAssertions(
                final_status=TraceStatus.SUCCEEDED,
                score=100,
                verifier_passed=True,
                reviewer_approved=False,
                file_scope_violations=["[PRIVATE_PATH]"],
            ),
        )


def test_capture_refuses_failed_runs(tmp_path) -> None:
    trace, provenance, store = _completed_run(
        tmp_path,
        status=TraceStatus.FAILED,
    )

    with pytest.raises(RegressionFixtureError, match="successful terminal"):
        capture_regression_fixture(
            trace,
            provenance,
            store,
            tmp_path / "failed.agentbus-trace",
        )


def test_source_fixture_has_warning_and_requires_import_consent(
    tmp_path,
) -> None:
    trace, provenance, source_store = _completed_run(tmp_path, source=True)
    archive = tmp_path / "source-fixture.agentbus-trace"

    captured = capture_regression_fixture(
        trace,
        provenance,
        source_store,
        archive,
        include_source_content=True,
    )

    assert captured.spec.source_warning
    assert captured.spec.license_warning
    assert "--allow-source-content" in captured.spec.replay_command
    with ZipFile(archive) as fixture_zip:
        spec = RegressionFixtureSpec.model_validate_json(
            fixture_zip.read("assertions.json")
        )
    assert spec.source_content_requested is True
    with pytest.raises(
        TraceArchiveConsentRequiredError,
        match="consent",
    ):
        replay_regression_fixture(
            archive,
            ContentAddressedStore(tmp_path / "rejected-objects"),
        )

    result = replay_regression_fixture(
        archive,
        ContentAddressedStore(tmp_path / "accepted-objects"),
        allow_source_content=True,
    )
    assert result.assertions.passed is True
    assert result.replay.session.provider_calls == 0
    assert result.replay.session.network_calls == 0
