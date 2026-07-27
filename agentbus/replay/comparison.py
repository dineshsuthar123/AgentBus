from __future__ import annotations

import hashlib
from collections import Counter, defaultdict
from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import Field

from agentbus.trace.models import Trace, TraceIdentifier, TraceModel, TraceSpan, utc_now
from agentbus.trace.provenance import ProvenanceManifest
from agentbus.trace.redaction import canonical_json_bytes, sanitize_document


class DriftCategory(str, Enum):
    EXPECTED = "expected"
    CONFIGURATION = "configuration_drift"
    POLICY = "policy_drift"
    MODEL = "model_drift"
    TOOL = "tool_drift"
    ENVIRONMENT = "environment_drift"
    ORDERING = "ordering_drift"
    OUTPUT = "output_drift"
    REGRESSION = "regression"
    IMPROVEMENT = "improvement"
    UNKNOWN = "unknown"


class FieldDifference(TraceModel):
    field: str = Field(min_length=1, max_length=128)
    left_sha256: str | None = Field(
        default=None,
        pattern=r"^[0-9a-f]{64}$",
    )
    right_sha256: str | None = Field(
        default=None,
        pattern=r"^[0-9a-f]{64}$",
    )
    category: DriftCategory
    summary: str = Field(min_length=1, max_length=1_000)


class SpanComparison(TraceModel):
    semantic_key: str = Field(min_length=1, max_length=512)
    left_span_id: TraceIdentifier | None = None
    right_span_id: TraceIdentifier | None = None
    unchanged: bool
    categories: list[DriftCategory] = Field(default_factory=list)
    differences: list[FieldDifference] = Field(default_factory=list, max_length=256)


class ComparisonSummary(TraceModel):
    unchanged_spans: int = Field(ge=0)
    changed_spans: int = Field(ge=0)
    added_spans: int = Field(ge=0)
    removed_spans: int = Field(ge=0)
    category_counts: dict[DriftCategory, int] = Field(default_factory=dict)
    final_status_changed: bool = False
    provenance_root_changed: bool = False


class RunComparison(TraceModel):
    comparison_id: TraceIdentifier
    left_trace_id: TraceIdentifier
    right_trace_id: TraceIdentifier
    created_at: datetime
    spans: list[SpanComparison] = Field(default_factory=list)
    summary: ComparisonSummary
    categories: list[DriftCategory] = Field(default_factory=list)
    left_status: str
    right_status: str
    left_provenance_root: str | None = None
    right_provenance_root: str | None = None


def compare_traces(
    left: Trace,
    right: Trace,
    *,
    left_provenance: ProvenanceManifest | None = None,
    right_provenance: ProvenanceManifest | None = None,
    clock=utc_now,
) -> RunComparison:
    left_spans = _index_spans(left)
    right_spans = _index_spans(right)
    comparisons: list[SpanComparison] = []
    added = 0
    removed = 0
    for key in sorted(set(left_spans) | set(right_spans)):
        left_span = left_spans.get(key)
        right_span = right_spans.get(key)
        if left_span is None:
            added += 1
            comparisons.append(_one_sided(key, right_span, added=True, expected=_linked(left, right)))
        elif right_span is None:
            removed += 1
            comparisons.append(_one_sided(key, left_span, added=False, expected=_linked(left, right)))
        else:
            comparisons.append(_compare_span(key, left_span, right_span))

    final_status_changed = left.status != right.status
    if final_status_changed:
        category = _status_category(left.status.value, right.status.value)
        comparisons.append(
            SpanComparison(
                semantic_key="run:final_status",
                unchanged=False,
                categories=[category],
                differences=[
                    FieldDifference(
                        field="final_status",
                        left_sha256=_value_sha(left.status.value),
                        right_sha256=_value_sha(right.status.value),
                        category=category,
                        summary="Final run status changed.",
                    )
                ],
            )
        )
    left_root = left_provenance.integrity_root if left_provenance else None
    right_root = right_provenance.integrity_root if right_provenance else None
    provenance_changed = (
        left_root is not None
        and right_root is not None
        and left_root != right_root
    )
    category_counts = Counter(
        category
        for comparison in comparisons
        for category in comparison.categories
    )
    categories = sorted(category_counts, key=lambda item: item.value)
    comparison_id = _comparison_id(left.trace_id, right.trace_id)
    return RunComparison(
        comparison_id=comparison_id,
        left_trace_id=left.trace_id,
        right_trace_id=right.trace_id,
        created_at=clock(),
        spans=comparisons,
        summary=ComparisonSummary(
            unchanged_spans=sum(item.unchanged for item in comparisons),
            changed_spans=sum(not item.unchanged for item in comparisons),
            added_spans=added,
            removed_spans=removed,
            category_counts=dict(category_counts),
            final_status_changed=final_status_changed,
            provenance_root_changed=provenance_changed,
        ),
        categories=categories,
        left_status=left.status.value,
        right_status=right.status.value,
        left_provenance_root=left_root,
        right_provenance_root=right_root,
    )


def _index_spans(trace: Trace) -> dict[str, TraceSpan]:
    occurrences: dict[str, int] = defaultdict(int)
    indexed: dict[str, TraceSpan] = {}
    for span in sorted(trace.spans, key=lambda item: item.sequence):
        base = ":".join(
            [
                span.span_type.value,
                span.task_id or "-",
                span.invocation_id or "-",
                span.name,
            ]
        )
        occurrences[base] += 1
        indexed[f"{base}:{occurrences[base]}"] = span
    return indexed


def _compare_span(
    key: str,
    left: TraceSpan,
    right: TraceSpan,
) -> SpanComparison:
    values = {
        "status": (left.status.value, right.status.value),
        "inputs": (
            [item.sha256 for item in left.input_references],
            [item.sha256 for item in right.input_references],
        ),
        "outputs": (
            [item.sha256 for item in left.output_references],
            [item.sha256 for item in right.output_references],
        ),
        "policy_decisions": (
            left.policy_decision_references,
            right.policy_decision_references,
        ),
        "approvals": (left.approval_references, right.approval_references),
        "artifacts": (
            [
                (item.artifact_id, item.sha256)
                for item in left.artifact_references
            ],
            [
                (item.artifact_id, item.sha256)
                for item in right.artifact_references
            ],
        ),
        "cancellation": (left.cancellation_state, right.cancellation_state),
        "resource_usage": (
            left.resource_usage.model_dump(mode="json"),
            right.resource_usage.model_dump(mode="json"),
        ),
        "failure": (
            left.failure.model_dump(mode="json") if left.failure else None,
            right.failure.model_dump(mode="json") if right.failure else None,
        ),
        "attributes": (left.attributes, right.attributes),
        "sequence": (left.sequence, right.sequence),
        "parent": (left.parent_span_id, right.parent_span_id),
    }
    differences: list[FieldDifference] = []
    for field, (left_value, right_value) in values.items():
        safe_left = sanitize_document(left_value).value
        safe_right = sanitize_document(right_value).value
        if safe_left == safe_right:
            continue
        category = _field_category(left, right, field)
        differences.append(
            FieldDifference(
                field=field,
                left_sha256=_value_sha(safe_left),
                right_sha256=_value_sha(safe_right),
                category=category,
                summary=_difference_summary(field, category),
            )
        )
    categories = sorted(
        {difference.category for difference in differences},
        key=lambda item: item.value,
    )
    return SpanComparison(
        semantic_key=key,
        left_span_id=left.span_id,
        right_span_id=right.span_id,
        unchanged=not differences,
        categories=categories,
        differences=differences,
    )


def _one_sided(
    key: str,
    span: TraceSpan,
    *,
    added: bool,
    expected: bool,
) -> SpanComparison:
    category = DriftCategory.EXPECTED if expected else DriftCategory.UNKNOWN
    return SpanComparison(
        semantic_key=key,
        left_span_id=None if added else span.span_id,
        right_span_id=span.span_id if added else None,
        unchanged=False,
        categories=[category],
        differences=[
            FieldDifference(
                field="span_presence",
                left_sha256=None if added else _value_sha(span.span_id),
                right_sha256=_value_sha(span.span_id) if added else None,
                category=category,
                summary="Span was added." if added else "Span was removed.",
            )
        ],
    )


def _field_category(
    left: TraceSpan,
    right: TraceSpan,
    field: str,
) -> DriftCategory:
    if field == "status":
        return _status_category(left.status.value, right.status.value)
    if field in {"sequence", "parent"}:
        return DriftCategory.ORDERING
    if field == "resource_usage":
        return DriftCategory.ENVIRONMENT
    if left.span_type.value.startswith("provider") or right.span_type.value.startswith(
        "provider"
    ):
        return DriftCategory.MODEL
    if left.span_type.value == "tool_policy" or right.span_type.value == "tool_policy":
        return DriftCategory.POLICY
    if left.span_type.value == "tool_invocation" or right.span_type.value == "tool_invocation":
        return DriftCategory.TOOL
    if field in {"outputs", "artifacts", "failure"}:
        return DriftCategory.OUTPUT
    if field == "attributes" and (
        "configuration" in left.attributes or "configuration" in right.attributes
    ):
        return DriftCategory.CONFIGURATION
    return DriftCategory.UNKNOWN


def _status_category(left: str, right: str) -> DriftCategory:
    successful = {"succeeded"}
    if left in successful and right not in successful:
        return DriftCategory.REGRESSION
    if left not in successful and right in successful:
        return DriftCategory.IMPROVEMENT
    return DriftCategory.OUTPUT


def _difference_summary(field: str, category: DriftCategory) -> str:
    return f"Structured field '{field}' changed ({category.value})."


def _value_sha(value: Any) -> str:
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def _comparison_id(left_trace_id: str, right_trace_id: str) -> str:
    digest = hashlib.sha256(
        f"{left_trace_id}\0{right_trace_id}".encode("utf-8")
    ).hexdigest()
    return f"comparison-{digest[:24]}"


def _linked(left: Trace, right: Trace) -> bool:
    return any(
        link.trace_id == left.trace_id
        for link in right.links
    ) or any(link.trace_id == right.trace_id for link in left.links)


__all__ = [
    "ComparisonSummary",
    "DriftCategory",
    "FieldDifference",
    "RunComparison",
    "SpanComparison",
    "compare_traces",
]
