from datetime import datetime, timezone

from agentbus.replay import ReplayabilityClassifier, ReplayabilityLevel
from agentbus.trace import (
    Trace,
    TraceInput,
    TraceOutput,
    TraceSpan,
    TraceSpanType,
    TraceStatus,
)


NOW = datetime(2026, 1, 2, tzinfo=timezone.utc)
INPUT = "1" * 64
OUTPUT = "2" * 64


def _span(
    span_id: str,
    span_type: TraceSpanType,
    sequence: int,
    *,
    parent: str | None = "root",
    inputs=(),
    outputs=(),
    attributes=None,
) -> TraceSpan:
    return TraceSpan(
        trace_id="trace-1",
        span_id=span_id,
        parent_span_id=parent,
        run_id="run-1",
        span_type=span_type,
        name=span_id,
        sequence=sequence,
        started_at=NOW,
        ended_at=NOW,
        status=TraceStatus.SUCCEEDED,
        input_references=list(inputs),
        output_references=list(outputs),
        attributes=attributes or {},
    )


def _root() -> TraceSpan:
    return _span(
        "root",
        TraceSpanType.RUN,
        1,
        parent=None,
        outputs=[
            TraceOutput(
                reference_id="run-output",
                name="run state",
                sha256=OUTPUT,
                byte_length=1,
            )
        ],
    )


def test_captured_provider_response_is_substitutable_offline() -> None:
    span = _span(
        "provider",
        TraceSpanType.PROVIDER_RESPONSE,
        2,
        outputs=[
            TraceOutput(
                reference_id="response",
                name="structured response",
                sha256=OUTPUT,
                byte_length=10,
            )
        ],
    )

    result = ReplayabilityClassifier().classify_span(
        span,
        available_object_hashes={OUTPUT},
    )

    assert result.level == ReplayabilityLevel.DETERMINISTICALLY_SUBSTITUTABLE
    assert result.substitution_kinds == ["provider_response"]
    assert result.live_provider_consent_required is False


def test_missing_required_input_is_non_replayable_and_explained() -> None:
    span = _span(
        "parse",
        TraceSpanType.MODEL_PARSE,
        2,
        inputs=[
            TraceInput(
                reference_id="input",
                name="model envelope",
                sha256=INPUT,
                byte_length=10,
            )
        ],
    )

    result = ReplayabilityClassifier().classify_span(
        span,
        available_object_hashes=set(),
    )

    assert result.level == ReplayabilityLevel.NON_REPLAYABLE
    assert result.missing_input_hashes == [INPUT]
    assert "unavailable" in result.reasons[0]


def test_tool_and_repository_mutations_require_isolation() -> None:
    tool = _span(
        "tool",
        TraceSpanType.TOOL_INVOCATION,
        2,
        attributes={"tool_effect": "filesystem_mutation"},
    )
    git = _span("git", TraceSpanType.GIT_MUTATION, 3)
    classifier = ReplayabilityClassifier()

    tool_result = classifier.classify_span(
        tool,
        available_object_hashes=set(),
    )
    git_result = classifier.classify_span(
        git,
        available_object_hashes=set(),
    )

    assert tool_result.level == ReplayabilityLevel.PARTIALLY_REPLAYABLE
    assert tool_result.requires_isolated_workspace is True
    assert git_result.requires_isolated_workspace is True


def test_external_tool_without_capture_requires_live_consent() -> None:
    span = _span(
        "mcp",
        TraceSpanType.TOOL_INVOCATION,
        2,
        attributes={"tool_effect": "network"},
    )

    result = ReplayabilityClassifier().classify_span(
        span,
        available_object_hashes=set(),
    )

    assert result.level == ReplayabilityLevel.NON_REPLAYABLE
    assert result.live_provider_consent_required is True


def test_run_classification_conservatively_aggregates_span_results() -> None:
    parse = _span("parse", TraceSpanType.MODEL_PARSE, 2)
    cleanup = _span("cleanup", TraceSpanType.CLEANUP, 3)
    trace = Trace(
        trace_id="trace-1",
        run_id="run-1",
        root_span_id="root",
        status=TraceStatus.SUCCEEDED,
        created_at=NOW,
        completed_at=NOW,
        spans=[_root(), parse, cleanup],
    )

    result = ReplayabilityClassifier().classify_trace(
        trace,
        available_object_hashes={OUTPUT},
    )

    assert result.level == ReplayabilityLevel.NON_REPLAYABLE
    assert result.replayable_offline is False
    assert len(result.spans) == 3


def test_unresolved_nondeterminism_prevents_exact_classification() -> None:
    span = _span(
        "policy",
        TraceSpanType.TOOL_POLICY,
        2,
        attributes={
            "nondeterminism": [
                {"source": "environment", "disposition": "unresolved"}
            ]
        },
    )

    result = ReplayabilityClassifier().classify_span(
        span,
        available_object_hashes=set(),
    )

    assert result.level == ReplayabilityLevel.PARTIALLY_REPLAYABLE
    assert "nondeterministic" in result.reasons[0]
