from datetime import datetime, timezone
from uuid import UUID

import pytest
from pydantic import ValidationError

from agentbus.replay import (
    DeterministicInputProvider,
    EnvironmentFingerprint,
    NondeterminismDetector,
    NondeterminismDisposition,
    NondeterminismFinding,
    NondeterminismSource,
    RecordedValueUnavailableError,
    ReplayabilityClassifier,
    ReplayabilityLevel,
    annotate_span_nondeterminism,
    environment_drift,
    environment_fingerprint,
)
from agentbus.trace import (
    Trace,
    TraceOutput,
    TraceSpan,
    TraceSpanType,
    TraceStatus,
)

NOW = datetime(2026, 1, 2, tzinfo=timezone.utc)
OUTPUT = "2" * 64


def _span(
    span_id: str,
    span_type: TraceSpanType,
    sequence: int,
    *,
    parent: str | None = "root",
    worker_id: str | None = None,
    outputs: tuple[TraceOutput, ...] = (),
    attributes: dict | None = None,
) -> TraceSpan:
    return TraceSpan(
        trace_id="trace-1",
        span_id=span_id,
        parent_span_id=parent,
        run_id="run-1",
        worker_id=worker_id,
        span_type=span_type,
        name=span_id,
        sequence=sequence,
        started_at=NOW,
        ended_at=NOW,
        status=TraceStatus.SUCCEEDED,
        output_references=list(outputs),
        attributes=attributes or {},
    )


def _trace(*spans: TraceSpan) -> Trace:
    root = _span("root", TraceSpanType.RUN, 1, parent=None)
    return Trace(
        trace_id="trace-1",
        run_id="run-1",
        root_span_id="root",
        status=TraceStatus.SUCCEEDED,
        created_at=NOW,
        completed_at=NOW,
        spans=[root, *spans],
    )


def test_detects_captured_provider_variation_and_observed_scheduling() -> None:
    response = _span(
        "response",
        TraceSpanType.PROVIDER_RESPONSE,
        2,
        worker_id="worker-1",
        outputs=(
            TraceOutput(
                reference_id="response-output",
                name="response",
                sha256=OUTPUT,
                byte_length=10,
            ),
        ),
        attributes={"provider": "azure"},
    )
    task = _span(
        "task",
        TraceSpanType.TASK,
        3,
        worker_id="worker-2",
    )

    report = NondeterminismDetector().detect_trace(_trace(response, task))

    by_source = {finding.source: finding for finding in report.findings}
    assert (
        by_source[NondeterminismSource.PROVIDER_VARIATION].disposition
        == NondeterminismDisposition.CAPTURED
    )
    assert (
        by_source[NondeterminismSource.PROCESS_SCHEDULING].disposition
        == NondeterminismDisposition.OBSERVED_ONLY
    )
    assert report.unresolved_sources == []


def test_unresolved_ordering_annotation_limits_replayability() -> None:
    span = _span(
        "policy",
        TraceSpanType.TOOL_POLICY,
        2,
        attributes={"mapping_order_unstable": True},
    )
    findings = NondeterminismDetector().detect_span(span)
    annotated = annotate_span_nondeterminism(span, findings)

    result = ReplayabilityClassifier().classify_span(
        annotated,
        available_object_hashes=set(),
    )

    assert findings[0].source == NondeterminismSource.MAPPING_ORDER
    assert findings[0].disposition == NondeterminismDisposition.UNRESOLVED
    assert result.level == ReplayabilityLevel.PARTIALLY_REPLAYABLE


def test_environment_fingerprint_is_stable_sanitized_and_detects_drift() -> None:
    left = environment_fingerprint(
        environment={
            "NORMAL": "value",
            "API_TOKEN": "real-secret",
            "HOME": r"C:\Users\private\workspace",
        },
        git_configuration={"core.autocrlf": "true"},
        operating_system={"system": "test-os"},
        python={"version": "3.11"},
        locale_name="en_US.UTF-8",
        timezone_names=("UTC",),
        line_ending="\r\n",
    )
    reordered = environment_fingerprint(
        environment={
            "HOME": r"C:\Users\private\workspace",
            "API_TOKEN": "different-secret",
            "NORMAL": "value",
        },
        git_configuration={"core.autocrlf": "true"},
        operating_system={"system": "test-os"},
        python={"version": "3.11"},
        locale_name="en_US.UTF-8",
        timezone_names=("UTC",),
        line_ending="\r\n",
    )
    changed = environment_fingerprint(
        environment={"NORMAL": "changed"},
        git_configuration={"core.autocrlf": "false"},
        operating_system={"system": "test-os"},
        python={"version": "3.11"},
        locale_name="fr_FR.UTF-8",
        timezone_names=("UTC",),
        line_ending="\n",
    )

    assert left == reordered
    assert "real-secret" not in left.model_dump_json()
    assert r"C:\Users\private" not in left.model_dump_json()
    assert environment_drift(left, changed) == [
        NondeterminismSource.ENVIRONMENT,
        NondeterminismSource.GIT_CONFIGURATION,
        NondeterminismSource.LOCALE,
        NondeterminismSource.LINE_ENDINGS,
    ]


def test_environment_fingerprint_rejects_modified_component_hash() -> None:
    fingerprint = environment_fingerprint()
    payload = fingerprint.model_dump()
    payload["locale_sha256"] = "0" * 64

    with pytest.raises(ValidationError, match="component hash mismatch"):
        EnvironmentFingerprint.model_validate(payload)


def test_recorded_inputs_are_injected_without_global_monkeypatches() -> None:
    expected_uuid = UUID("00000000-0000-4000-8000-000000000001")
    inputs = DeterministicInputProvider(
        wall_clock_values=[NOW],
        uuid_values=[expected_uuid],
        random_values=[0.25],
    )

    assert inputs.now() == NOW
    assert inputs.uuid4() == expected_uuid
    assert inputs.random() == 0.25
    with pytest.raises(RecordedValueUnavailableError, match="exhausted"):
        inputs.now()


def test_finding_sanitizes_evidence_and_reason() -> None:
    finding = NondeterminismFinding(
        source=NondeterminismSource.ENVIRONMENT,
        disposition=NondeterminismDisposition.CAPTURED,
        reason="authorization: Bearer abcdefghijklmnop",
        evidence={"password": "do-not-store"},
    )

    serialized = finding.model_dump_json()
    assert "abcdefghijklmnop" not in serialized
    assert "do-not-store" not in serialized
    assert "REDACTED" in serialized
