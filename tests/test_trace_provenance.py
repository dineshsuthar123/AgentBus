from datetime import datetime, timezone

import pytest

from agentbus.trace import (
    ProvenanceBuilder,
    ReplayabilityLevel,
    Trace,
    TraceInput,
    TraceIntegrityError,
    TraceOutput,
    TraceRecorder,
    TraceSpan,
    TraceSpanType,
    trace_context,
    verify_provenance,
)


NOW = datetime(2026, 1, 2, tzinfo=timezone.utc)
INPUT_HASH = "1" * 64
OUTPUT_HASH = "2" * 64


def _trace() -> Trace:
    recorder = TraceRecorder("run-1", clock=lambda: NOW)
    root = recorder.start_trace()
    with trace_context(root):
        child = recorder.start_span(
            TraceSpanType.TASK,
            "task",
            input_references=[
                TraceInput(
                    reference_id="input-1",
                    name="task graph",
                    sha256=INPUT_HASH,
                    byte_length=10,
                )
            ],
        )
        recorder.finish_span(
            child.span_id,
            output_references=[
                TraceOutput(
                    reference_id="output-1",
                    name="result",
                    sha256=OUTPUT_HASH,
                    byte_length=20,
                )
            ],
        )
    return recorder.finish_trace()


def _manifest(trace: Trace):
    return ProvenanceBuilder(
        clock=lambda: NOW,
        system_name="TestOS 1",
        python_version="3.11.9",
    ).build(
        trace,
        configuration={"provider": "deterministic", "api_key": "not-stored"},
        policy_version="1",
        policy_document={"default": "deny"},
        task_graph={"tasks": [{"id": "step-1"}]},
        approvals=[{"approval_id": "approval-1", "token": "not-stored"}],
        audit_entries=[{"audit_id": "audit-1", "outcome": "allowed"}],
        artifacts=[{"artifact_id": "artifact-1", "sha256": OUTPUT_HASH}],
        replayability=ReplayabilityLevel.DETERMINISTICALLY_SUBSTITUTABLE,
        replayability_reasons=["captured structured model response"],
    )


def _verification_records():
    return {
        "approvals": [{"approval_id": "approval-1", "token": "different"}],
        "audit_entries": [{"audit_id": "audit-1", "outcome": "allowed"}],
        "artifacts": [{"artifact_id": "artifact-1", "sha256": OUTPUT_HASH}],
    }


def test_provenance_manifest_is_deterministic_and_secret_free() -> None:
    trace = _trace()

    first = _manifest(trace)
    second = _manifest(trace)

    assert first == second
    assert first.input_object_hashes == [INPUT_HASH]
    assert first.output_object_hashes == [OUTPUT_HASH]
    assert first.integrity_root != "0" * 64
    assert "not-stored" not in first.model_dump_json()
    verify_provenance(first, trace, **_verification_records())


def test_provenance_detects_modified_span_or_manifest() -> None:
    trace = _trace()
    manifest = _manifest(trace)
    changed_span = TraceSpan.model_validate(
        trace.spans[1].model_copy(
            update={"attributes": {"changed": True}}
        ).model_dump()
    )
    changed_trace = Trace.model_validate(
        trace.model_copy(
            update={"spans": [trace.spans[0], changed_span]}
        ).model_dump()
    )

    with pytest.raises(TraceIntegrityError, match="entries"):
        verify_provenance(
            manifest,
            changed_trace,
            **_verification_records(),
        )

    changed_manifest = manifest.model_copy(
        update={"policy_version": "tampered"}
    )
    with pytest.raises(TraceIntegrityError, match="entries"):
        verify_provenance(
            changed_manifest,
            trace,
            **_verification_records(),
        )


def test_provenance_validates_referenced_blob_payloads() -> None:
    trace = _trace()
    manifest = _manifest(trace)

    with pytest.raises(TraceIntegrityError, match="failed hash"):
        verify_provenance(
            manifest,
            trace,
            **_verification_records(),
            blob_payloads={
                INPUT_HASH: b"tampered",
                OUTPUT_HASH: b"tampered",
            },
        )
