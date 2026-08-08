from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from pydantic import Field, field_validator

from agentbus.security.redaction import redact_text
from agentbus.trace.models import (
    Sha256Digest,
    Trace,
    TraceIdentifier,
    TraceModel,
    TraceSpan,
    TraceSpanType,
)
from agentbus.trace.intelligence import REPOSITORY_INTELLIGENCE_COMPONENT
from agentbus.trace.provenance import ReplayabilityLevel


class SpanReplayability(TraceModel):
    span_id: TraceIdentifier
    span_type: TraceSpanType
    level: ReplayabilityLevel
    reasons: list[str] = Field(min_length=1, max_length=128)
    required_input_hashes: list[Sha256Digest] = Field(default_factory=list)
    missing_input_hashes: list[Sha256Digest] = Field(default_factory=list)
    substitution_kinds: list[str] = Field(default_factory=list, max_length=64)
    requires_isolated_workspace: bool = False
    live_provider_consent_required: bool = False

    @field_validator("reasons", "substitution_kinds")
    @classmethod
    def text_is_safe(cls, value: list[str]) -> list[str]:
        return [
            redact_text(item, max_chars=1_000) or "unspecified"
            for item in value
        ]


class RunReplayability(TraceModel):
    trace_id: TraceIdentifier
    run_id: TraceIdentifier
    level: ReplayabilityLevel
    replayable_offline: bool
    spans: list[SpanReplayability] = Field(default_factory=list)
    reasons: list[str] = Field(default_factory=list, max_length=1_024)
    missing_input_hashes: list[Sha256Digest] = Field(default_factory=list)
    live_provider_consent_required: bool = False


class ReplayabilityClassifier:
    """Conservatively explain which recorded behavior can be replayed."""

    def classify_trace(
        self,
        trace: Trace,
        *,
        available_object_hashes: Iterable[str] | None = None,
    ) -> RunReplayability:
        available = (
            set(available_object_hashes)
            if available_object_hashes is not None
            else _all_reference_hashes(trace)
        )
        spans = [
            self.classify_span(span, available_object_hashes=available)
            for span in sorted(trace.spans, key=lambda item: item.sequence)
        ]
        level = _aggregate_level(spans)
        missing = sorted(
            {
                digest
                for span in spans
                for digest in span.missing_input_hashes
            }
        )
        reasons = _aggregate_reasons(spans, level, missing)
        live_consent = any(
            span.live_provider_consent_required for span in spans
        )
        return RunReplayability(
            trace_id=trace.trace_id,
            run_id=trace.run_id,
            level=level,
            replayable_offline=level != ReplayabilityLevel.NON_REPLAYABLE,
            spans=spans,
            reasons=reasons,
            missing_input_hashes=missing,
            live_provider_consent_required=live_consent,
        )

    def classify_span(
        self,
        span: TraceSpan,
        *,
        available_object_hashes: Iterable[str],
    ) -> SpanReplayability:
        available = set(available_object_hashes)
        required = sorted(
            {
                reference.sha256
                for reference in span.input_references
                if reference.required_for_replay
            }
        )
        missing = sorted(set(required) - available)
        if missing:
            return _result(
                span,
                ReplayabilityLevel.NON_REPLAYABLE,
                [
                    "Required sanitized replay inputs are unavailable.",
                    f"{len(missing)} required content object(s) are missing.",
                ],
                required=required,
                missing=missing,
            )
        if span.attributes.get("replay_forbidden") is True:
            return _result(
                span,
                ReplayabilityLevel.NON_REPLAYABLE,
                ["The recorded span explicitly forbids replay."],
                required=required,
            )
        if _has_unresolved_nondeterminism(span):
            return _result(
                span,
                ReplayabilityLevel.PARTIALLY_REPLAYABLE,
                ["The span contains unresolved nondeterministic inputs."],
                required=required,
            )

        if span.span_type == TraceSpanType.MODEL_PARSE:
            captured_values = [
                *span.input_references,
                *(
                    reference
                    for reference in span.output_references
                    if reference.replayable
                ),
            ]
            if not captured_values:
                return _result(
                    span,
                    ReplayabilityLevel.NON_REPLAYABLE,
                    ["No captured structured model value is available."],
                    required=required,
                )
            missing_values = sorted(
                {
                    reference.sha256
                    for reference in captured_values
                    if reference.sha256 not in available
                }
            )
            if missing_values:
                return _result(
                    span,
                    ReplayabilityLevel.NON_REPLAYABLE,
                    ["Captured structured model values are unavailable."],
                    required=required,
                    missing=missing_values,
                )
            return _result(
                span,
                ReplayabilityLevel.EXACTLY_REPLAYABLE,
                ["Captured structured model input can be parsed deterministically."],
                required=required,
            )
        if span.span_type in {
            TraceSpanType.TOOL_POLICY,
            TraceSpanType.VERIFIER,
            TraceSpanType.REVIEWER,
        }:
            return _result(
                span,
                ReplayabilityLevel.EXACTLY_REPLAYABLE,
                ["Captured structured inputs can run through deterministic logic."],
                required=required,
            )
        if span.span_type in {
            TraceSpanType.PROVIDER_REQUEST,
            TraceSpanType.PROVIDER_RESPONSE,
        }:
            if span.attributes.get("provider") == "deterministic":
                return _result(
                    span,
                    ReplayabilityLevel.EXACTLY_REPLAYABLE,
                    ["The deterministic provider route is locally reproducible."],
                    required=required,
                )
            if span.output_references:
                return _result(
                    span,
                    ReplayabilityLevel.DETERMINISTICALLY_SUBSTITUTABLE,
                    ["A captured structured provider envelope replaces the live call."],
                    required=required,
                    substitutions=["provider_response"],
                )
            return _result(
                span,
                ReplayabilityLevel.NON_REPLAYABLE,
                ["No captured provider response is available for offline replay."],
                required=required,
                live_consent=True,
            )
        if span.span_type == TraceSpanType.APPROVAL_WAIT:
            if span.approval_references:
                return _result(
                    span,
                    ReplayabilityLevel.DETERMINISTICALLY_SUBSTITUTABLE,
                    ["The bounded approval decision can be replayed in the same policy scope."],
                    required=required,
                    substitutions=["approval_decision"],
                )
            return _result(
                span,
                ReplayabilityLevel.NON_REPLAYABLE,
                ["No compatible approval decision was captured."],
                required=required,
            )
        if span.span_type == TraceSpanType.TOOL_INVOCATION:
            return _classify_tool(span, required)
        if span.span_type in {
            TraceSpanType.GIT_MUTATION,
            TraceSpanType.INTEGRATION,
        }:
            return _result(
                span,
                ReplayabilityLevel.PARTIALLY_REPLAYABLE,
                [
                    "Repository mutation can only be reconstructed in an isolated replay worktree."
                ],
                required=required,
                isolated=True,
            )
        if span.span_type in {
            TraceSpanType.CANCELLATION,
            TraceSpanType.CLEANUP,
        }:
            return _result(
                span,
                ReplayabilityLevel.OBSERVATIONAL_ONLY,
                ["Lifecycle ordering is retained, but host timing is observational."],
                required=required,
            )
        if span.span_type in {
            TraceSpanType.RUN,
            TraceSpanType.PLANNING,
            TraceSpanType.TASK,
        }:
            if span.output_references or span.span_type == TraceSpanType.RUN:
                return _result(
                    span,
                    ReplayabilityLevel.DETERMINISTICALLY_SUBSTITUTABLE,
                    ["Captured outputs allow orchestration state to be reconstructed."],
                    required=required,
                    substitutions=["captured_outputs"],
                )
            return _result(
                span,
                ReplayabilityLevel.PARTIALLY_REPLAYABLE,
                ["The orchestration span lacks complete captured outputs."],
                required=required,
            )
        if (
            span.span_type == TraceSpanType.CUSTOM
            and span.attributes.get("component")
            == REPOSITORY_INTELLIGENCE_COMPONENT
        ):
            replayable_outputs = [
                reference
                for reference in span.output_references
                if reference.replayable and reference.sha256 in available
            ]
            if not replayable_outputs:
                return _result(
                    span,
                    ReplayabilityLevel.NON_REPLAYABLE,
                    ["Captured repository intelligence evidence is unavailable."],
                    required=required,
                )
            return _result(
                span,
                ReplayabilityLevel.DETERMINISTICALLY_SUBSTITUTABLE,
                [
                    "Captured repository intelligence can be reused and compared "
                    "without providers."
                ],
                required=required,
                substitutions=["repository_intelligence_snapshot"],
            )
        return _result(
            span,
            ReplayabilityLevel.PARTIALLY_REPLAYABLE,
            ["No exact replay contract is registered for this span type."],
            required=required,
        )


def _classify_tool(
    span: TraceSpan,
    required: list[str],
) -> SpanReplayability:
    effect = str(span.attributes.get("tool_effect", "unknown")).lower()
    strategy = str(span.attributes.get("replay_strategy", "")).lower()
    if effect in {"network", "external_mutation"}:
        if span.output_references and strategy == "reuse_captured":
            return _result(
                span,
                ReplayabilityLevel.DETERMINISTICALLY_SUBSTITUTABLE,
                ["A captured bounded tool envelope replaces the external call."],
                required=required,
                substitutions=["tool_result"],
            )
        return _result(
            span,
            ReplayabilityLevel.NON_REPLAYABLE,
            ["An external side effect has no safe offline substitution."],
            required=required,
            live_consent=True,
        )
    if effect in {"read", "pure_read"} and span.output_references:
        return _result(
            span,
            ReplayabilityLevel.EXACTLY_REPLAYABLE,
            ["Captured pure-read content can be reused or checked deterministically."],
            required=required,
            substitutions=["tool_result"],
        )
    if effect in {"filesystem_mutation", "git_mutation", "process"}:
        return _result(
            span,
            ReplayabilityLevel.PARTIALLY_REPLAYABLE,
            ["The tool may rerun only inside an isolated replay workspace."],
            required=required,
            isolated=True,
        )
    if strategy == "reject":
        return _result(
            span,
            ReplayabilityLevel.NON_REPLAYABLE,
            ["The recorded tool replay strategy rejects replay."],
            required=required,
        )
    if span.output_references:
        return _result(
            span,
            ReplayabilityLevel.DETERMINISTICALLY_SUBSTITUTABLE,
            ["The captured tool result can substitute for unknown host behavior."],
            required=required,
            substitutions=["tool_result"],
        )
    return _result(
        span,
        ReplayabilityLevel.NON_REPLAYABLE,
        ["Tool inputs or bounded outputs are insufficient for replay."],
        required=required,
    )


def _result(
    span: TraceSpan,
    level: ReplayabilityLevel,
    reasons: list[str],
    *,
    required: list[str],
    missing: list[str] | None = None,
    substitutions: list[str] | None = None,
    isolated: bool = False,
    live_consent: bool = False,
) -> SpanReplayability:
    return SpanReplayability(
        span_id=span.span_id,
        span_type=span.span_type,
        level=level,
        reasons=reasons,
        required_input_hashes=required,
        missing_input_hashes=missing or [],
        substitution_kinds=substitutions or [],
        requires_isolated_workspace=isolated,
        live_provider_consent_required=live_consent,
    )


def _has_unresolved_nondeterminism(span: TraceSpan) -> bool:
    sources = span.attributes.get("nondeterminism")
    if not isinstance(sources, list):
        return False
    return any(
        isinstance(source, dict) and source.get("disposition") == "unresolved"
        for source in sources
    )


def _all_reference_hashes(trace: Trace) -> set[str]:
    return {
        reference.sha256
        for span in trace.spans
        for reference in [*span.input_references, *span.output_references]
    }


def _aggregate_level(spans: list[SpanReplayability]) -> ReplayabilityLevel:
    levels = {span.level for span in spans}
    if ReplayabilityLevel.NON_REPLAYABLE in levels:
        return ReplayabilityLevel.NON_REPLAYABLE
    if levels == {ReplayabilityLevel.OBSERVATIONAL_ONLY}:
        return ReplayabilityLevel.OBSERVATIONAL_ONLY
    if (
        ReplayabilityLevel.PARTIALLY_REPLAYABLE in levels
        or ReplayabilityLevel.OBSERVATIONAL_ONLY in levels
    ):
        return ReplayabilityLevel.PARTIALLY_REPLAYABLE
    if ReplayabilityLevel.DETERMINISTICALLY_SUBSTITUTABLE in levels:
        return ReplayabilityLevel.DETERMINISTICALLY_SUBSTITUTABLE
    return ReplayabilityLevel.EXACTLY_REPLAYABLE


def _aggregate_reasons(
    spans: list[SpanReplayability],
    level: ReplayabilityLevel,
    missing: list[str],
) -> list[str]:
    counts: dict[ReplayabilityLevel, int] = {}
    for span in spans:
        counts[span.level] = counts.get(span.level, 0) + 1
    reasons = [
        f"Overall replayability is {level.value}.",
        *[
            f"{count} span(s) are {item_level.value}."
            for item_level, count in sorted(
                counts.items(),
                key=lambda item: item[0].value,
            )
        ],
    ]
    if missing:
        reasons.append(f"{len(missing)} required content object(s) are missing.")
    return reasons


__all__ = [
    "ReplayabilityClassifier",
    "RunReplayability",
    "SpanReplayability",
]
