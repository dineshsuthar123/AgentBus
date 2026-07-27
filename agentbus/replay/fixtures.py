from __future__ import annotations

import math
from datetime import datetime
from pathlib import Path
from typing import Any

from pydantic import Field, field_validator, model_validator

from agentbus.replay.engine import ReplayEngine
from agentbus.replay.errors import (
    RegressionFixtureAssertionError,
    RegressionFixtureError,
)
from agentbus.replay.session import (
    ReplayRequest,
    ReplayResult,
    ReplaySessionStatus,
)
from agentbus.trace.archive import (
    ImportedTraceArchive,
    TraceArchiveExporter,
    TraceArchiveImporter,
    TraceArchiveManifest,
)
from agentbus.trace.models import (
    ReplayMode,
    Sha256Digest,
    Trace,
    TraceIdentifier,
    TraceModel,
    TraceSpanType,
    TraceStatus,
)
from agentbus.trace.provenance import ProvenanceManifest
from agentbus.trace.redaction import sanitize_document
from agentbus.trace.storage import ContentAddressedStore

REGRESSION_FIXTURE_SCHEMA_VERSION = 1
REGRESSION_FIXTURE_FORMAT = "agentbus.regression-fixture"
FIXTURE_SOURCE_WARNING = (
    "This fixture includes sanitized source-like content. Review its origin "
    "and license before sharing or importing it."
)
FIXTURE_LICENSE_WARNING = (
    "Captured source-like material remains subject to its original license."
)


class FixtureAssertions(TraceModel):
    final_status: TraceStatus
    replay_session_status: ReplaySessionStatus = ReplaySessionStatus.SUCCEEDED
    score: float | None = None
    verifier_passed: bool | None = None
    reviewer_approved: bool | None = None
    file_scope_violations: list[str] = Field(default_factory=list, max_length=256)
    policy_decisions: dict[str, str] = Field(default_factory=dict, max_length=1_024)
    tool_outcomes: dict[str, str] = Field(default_factory=dict, max_length=1_024)
    expected_patch_hashes: list[Sha256Digest] = Field(default_factory=list)
    safety_failures: list[str] = Field(default_factory=list, max_length=256)

    @field_validator(
        "file_scope_violations",
        "policy_decisions",
        "tool_outcomes",
        "safety_failures",
    )
    @classmethod
    def assertion_values_are_safe(cls, value):
        return sanitize_document(value).value

    @field_validator("expected_patch_hashes")
    @classmethod
    def hashes_are_sorted_unique(cls, value: list[str]) -> list[str]:
        if value != sorted(set(value)):
            raise ValueError("fixture patch hashes must be sorted and unique")
        return value

    @field_validator("score")
    @classmethod
    def score_is_finite(cls, value: float | None) -> float | None:
        if value is not None and not math.isfinite(value):
            raise ValueError("fixture score must be finite")
        return value


class RegressionFixtureSpec(TraceModel):
    schema_version: int = REGRESSION_FIXTURE_SCHEMA_VERSION
    format_name: str = REGRESSION_FIXTURE_FORMAT
    trace_id: TraceIdentifier
    run_id: TraceIdentifier
    provenance_root: Sha256Digest
    created_at: datetime
    assertions: FixtureAssertions
    source_content_requested: bool = False
    source_warning: str | None = Field(default=None, max_length=512)
    license_warning: str | None = Field(default=None, max_length=512)
    replay_command: str = Field(min_length=1, max_length=1_024)

    @field_validator("schema_version")
    @classmethod
    def schema_is_supported(cls, value: int) -> int:
        if value != REGRESSION_FIXTURE_SCHEMA_VERSION:
            raise ValueError(
                f"unsupported regression fixture schema version: {value}"
            )
        return value

    @field_validator("format_name")
    @classmethod
    def format_is_supported(cls, value: str) -> str:
        if value != REGRESSION_FIXTURE_FORMAT:
            raise ValueError(
                f"unsupported regression fixture format: {value}"
            )
        return value

    @field_validator("source_warning", "license_warning", "replay_command")
    @classmethod
    def fixture_text_is_safe(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return str(sanitize_document(value).value)

    @model_validator(mode="after")
    def source_warning_is_explicit(self) -> "RegressionFixtureSpec":
        if self.source_content_requested and (
            not self.source_warning or not self.license_warning
        ):
            raise ValueError(
                "fixtures requesting source content require source and "
                "license warnings"
            )
        if not self.source_content_requested and (
            self.source_warning is not None or self.license_warning is not None
        ):
            raise ValueError(
                "source warnings require source-content inclusion"
            )
        return self


class CapturedRegressionFixture(TraceModel):
    spec: RegressionFixtureSpec
    archive: TraceArchiveManifest


class FixtureAssertionReport(TraceModel):
    passed: bool
    failures: list[str] = Field(default_factory=list, max_length=256)

    @field_validator("failures")
    @classmethod
    def failures_are_safe(cls, value: list[str]) -> list[str]:
        return sanitize_document(value).value


class RegressionFixtureReplay(TraceModel):
    spec: RegressionFixtureSpec
    imported: ImportedTraceArchive
    replay: ReplayResult
    assertions: FixtureAssertionReport


def derive_fixture_assertions(
    trace: Trace,
    object_store: ContentAddressedStore,
) -> FixtureAssertions:
    verifier = _latest_component_result(
        trace,
        object_store,
        TraceSpanType.VERIFIER,
    )
    reviewer = _latest_component_result(
        trace,
        object_store,
        TraceSpanType.REVIEWER,
    )
    attributes = _run_attributes(trace)
    score = attributes.get("score")
    safe_score = (
        float(score)
        if isinstance(score, int | float)
        and not isinstance(score, bool)
        and math.isfinite(float(score))
        else None
    )
    file_scope_violations = attributes.get("file_scope_violations", [])
    if not isinstance(file_scope_violations, list):
        file_scope_violations = []
    return FixtureAssertions(
        final_status=trace.status,
        score=safe_score,
        verifier_passed=(
            bool(verifier.get("passed")) if verifier is not None else None
        ),
        reviewer_approved=(
            bool(reviewer.get("approved")) if reviewer is not None else None
        ),
        file_scope_violations=[
            str(item) for item in file_scope_violations[:256]
        ],
        policy_decisions=_span_outcomes(trace, TraceSpanType.TOOL_POLICY),
        tool_outcomes=_span_outcomes(trace, TraceSpanType.TOOL_INVOCATION),
        expected_patch_hashes=sorted(
            {
                artifact.sha256
                for span in trace.spans
                for artifact in span.artifact_references
                if artifact.sha256 is not None
                and any(
                    marker in artifact.artifact_type.lower()
                    for marker in ("diff", "patch")
                )
            }
        ),
        safety_failures=sorted(
            {
                span.failure.category
                for span in trace.spans
                if span.failure is not None
            }
        ),
    )


def capture_regression_fixture(
    trace: Trace,
    provenance: ProvenanceManifest,
    object_store: ContentAddressedStore,
    destination: str | Path,
    *,
    assertions: FixtureAssertions | None = None,
    include_source_content: bool = False,
) -> CapturedRegressionFixture:
    if trace.status != TraceStatus.SUCCEEDED or trace.completed_at is None:
        raise RegressionFixtureError(
            "Only successful terminal runs can become regression fixtures."
        )
    expected = assertions or derive_fixture_assertions(trace, object_store)
    failures = _source_assertion_failures(trace, object_store, expected)
    if failures:
        raise RegressionFixtureAssertionError(
            "Fixture assertions contradict captured evidence: "
            + "; ".join(failures)
        )
    replay_command = (
        "agentbus replay <archive> --mode offline"
        + (" --allow-source-content" if include_source_content else "")
    )
    spec = RegressionFixtureSpec(
        trace_id=trace.trace_id,
        run_id=trace.run_id,
        provenance_root=provenance.integrity_root,
        created_at=trace.completed_at,
        assertions=expected,
        source_content_requested=include_source_content,
        source_warning=(
            FIXTURE_SOURCE_WARNING if include_source_content else None
        ),
        license_warning=(
            FIXTURE_LICENSE_WARNING if include_source_content else None
        ),
        replay_command=replay_command,
    )
    archive = TraceArchiveExporter(object_store).export(
        trace,
        provenance,
        destination,
        assertions=spec.model_dump(mode="json"),
        include_source_content=include_source_content,
    )
    if archive.source_content_included and not include_source_content:
        raise RegressionFixtureError(
            "Fixture export included source content without explicit consent."
        )
    return CapturedRegressionFixture(spec=spec, archive=archive)


def replay_regression_fixture(
    source: str | Path,
    object_store: ContentAddressedStore,
    *,
    mode: ReplayMode = ReplayMode.OFFLINE,
    allow_source_content: bool = False,
    engine: ReplayEngine | None = None,
    from_span_id: str | None = None,
    from_checkpoint_id: str | None = None,
) -> RegressionFixtureReplay:
    imported = TraceArchiveImporter(object_store).import_archive(
        source,
        allow_source_content=allow_source_content,
    )
    try:
        spec = RegressionFixtureSpec.model_validate(imported.assertions)
    except Exception as exc:
        raise RegressionFixtureError(
            "Trace archive does not contain a valid regression fixture."
        ) from exc
    if (
        spec.trace_id != imported.trace.trace_id
        or spec.run_id != imported.trace.run_id
        or spec.provenance_root != imported.provenance.integrity_root
    ):
        raise RegressionFixtureError(
            "Regression fixture identities do not match the imported trace."
        )
    request = ReplayRequest(
        source_trace_id=imported.trace.trace_id,
        source_run_id=imported.trace.run_id,
        mode=mode,
        from_span_id=from_span_id,
        from_checkpoint_id=from_checkpoint_id,
    )
    replay = (engine or ReplayEngine(object_store)).replay(
        imported.trace,
        request,
    )
    report = evaluate_fixture_assertions(
        spec.assertions,
        imported.trace,
        object_store,
        replay,
    )
    return RegressionFixtureReplay(
        spec=spec,
        imported=imported,
        replay=replay,
        assertions=report,
    )


def evaluate_fixture_assertions(
    expected: FixtureAssertions,
    trace: Trace,
    object_store: ContentAddressedStore,
    replay: ReplayResult,
) -> FixtureAssertionReport:
    failures = _source_assertion_failures(trace, object_store, expected)
    if replay.session.status != expected.replay_session_status:
        failures.append(
            "replay session status was "
            f"{replay.session.status.value}, expected "
            f"{expected.replay_session_status.value}"
        )
    if replay.replayed_status != expected.final_status:
        failures.append(
            f"replayed status was {replay.replayed_status.value}, expected "
            f"{expected.final_status.value}"
        )
    if expected.verifier_passed is not None:
        actual = (
            bool(replay.verifier_result.get("passed"))
            if replay.verifier_result is not None
            else None
        )
        if actual != expected.verifier_passed:
            failures.append(
                f"replayed verifier result was {actual}, expected "
                f"{expected.verifier_passed}"
            )
    if expected.reviewer_approved is not None:
        actual = (
            bool(replay.reviewer_result.get("approved"))
            if replay.reviewer_result is not None
            else None
        )
        if actual != expected.reviewer_approved:
            failures.append(
                f"replayed reviewer result was {actual}, expected "
                f"{expected.reviewer_approved}"
            )
    return FixtureAssertionReport(
        passed=not failures,
        failures=failures,
    )


def _source_assertion_failures(
    trace: Trace,
    object_store: ContentAddressedStore,
    expected: FixtureAssertions,
) -> list[str]:
    actual = derive_fixture_assertions(trace, object_store)
    failures: list[str] = []
    for field in (
        "final_status",
        "score",
        "verifier_passed",
        "reviewer_approved",
        "file_scope_violations",
        "policy_decisions",
        "tool_outcomes",
        "expected_patch_hashes",
        "safety_failures",
    ):
        if getattr(actual, field) != getattr(expected, field):
            failures.append(f"{field} does not match captured evidence")
    return failures


def _latest_component_result(
    trace: Trace,
    object_store: ContentAddressedStore,
    span_type: TraceSpanType,
) -> dict[str, Any] | None:
    spans = sorted(
        (span for span in trace.spans if span.span_type == span_type),
        key=lambda item: item.sequence,
        reverse=True,
    )
    for span in spans:
        for reference in reversed(span.output_references):
            if (
                reference.media_type == "application/json"
                or reference.media_type.endswith("+json")
            ):
                value = object_store.get_json(reference.sha256)
                if isinstance(value, dict):
                    return sanitize_document(value).value
    return None


def _span_outcomes(
    trace: Trace,
    span_type: TraceSpanType,
) -> dict[str, str]:
    outcomes: dict[str, str] = {}
    for span in sorted(trace.spans, key=lambda item: item.sequence):
        if span.span_type != span_type:
            continue
        identifier = span.invocation_id or span.span_id
        outcomes[identifier] = span.status.value
    return outcomes


def _run_attributes(trace: Trace) -> dict[str, Any]:
    root = next(
        span for span in trace.spans if span.span_id == trace.root_span_id
    )
    return {**trace.attributes, **root.attributes}


__all__ = [
    "FIXTURE_LICENSE_WARNING",
    "FIXTURE_SOURCE_WARNING",
    "REGRESSION_FIXTURE_FORMAT",
    "REGRESSION_FIXTURE_SCHEMA_VERSION",
    "CapturedRegressionFixture",
    "FixtureAssertionReport",
    "FixtureAssertions",
    "RegressionFixtureReplay",
    "RegressionFixtureSpec",
    "capture_regression_fixture",
    "derive_fixture_assertions",
    "evaluate_fixture_assertions",
    "replay_regression_fixture",
]
