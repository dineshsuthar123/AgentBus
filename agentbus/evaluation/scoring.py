from __future__ import annotations

from collections import defaultdict

from agentbus.evaluation.models import (
    AssertionDimension,
    EvaluationAssertion,
    EvaluationScore,
    ScoringWeights,
)


def calculate_score(
    assertions: list[EvaluationAssertion],
    weights: ScoringWeights | None = None,
) -> EvaluationScore:
    selected_weights = weights or ScoringWeights()
    grouped: dict[str, list[EvaluationAssertion]] = defaultdict(list)
    for assertion in assertions:
        grouped[assertion.dimension.value].append(assertion)

    dimensions: dict[str, float] = {}
    hard_failure = any(
        assertion.hard_failure and assertion.passed is False for assertion in assertions
    )
    for dimension in AssertionDimension:
        weight = float(getattr(selected_weights, dimension.value))
        applicable = grouped.get(dimension.value, [])
        if not applicable:
            dimensions[dimension.value] = weight
            continue
        passed = sum(assertion.passed is True for assertion in applicable)
        dimensions[dimension.value] = round(weight * passed / len(applicable), 4)

    total = 0.0 if hard_failure else round(sum(dimensions.values()), 4)
    return EvaluationScore(
        total=total,
        dimensions=dimensions,
        weights=selected_weights,
        hard_failure=hard_failure,
    )
