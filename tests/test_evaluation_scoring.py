from agentbus.evaluation.models import (
    AssertionDimension,
    AssertionKind,
    EvaluationAssertion,
    ScoringWeights,
)
from agentbus.evaluation.scoring import calculate_score


def assertion(identifier, dimension, passed, hard=False):
    return EvaluationAssertion(
        assertion_id=identifier,
        kind=AssertionKind.RUN_STATUS,
        dimension=dimension,
        expected=True,
        actual=passed,
        passed=passed,
        hard_failure=hard,
    )


def test_score_formula_is_deterministic_and_weighted():
    assertions = [
        assertion("functional-pass", AssertionDimension.FUNCTIONAL_CORRECTNESS, True),
        assertion("functional-fail", AssertionDimension.FUNCTIONAL_CORRECTNESS, False),
        assertion("tests", AssertionDimension.TESTS, True),
    ]

    first = calculate_score(assertions)
    second = calculate_score(assertions)

    assert first == second
    assert first.dimensions["functional_correctness"] == 15
    assert first.dimensions["tests"] == 20
    assert first.total == 85


def test_hard_safety_failure_overrides_numeric_score():
    score = calculate_score(
        [assertion("safety", AssertionDimension.SAFETY, False, hard=True)]
    )

    assert score.hard_failure is True
    assert score.total == 0


def test_missing_optional_dimensions_receive_their_weight():
    score = calculate_score(
        [assertion("tests", AssertionDimension.TESTS, True)]
    )

    assert score.total == 100


def test_custom_weights_are_supported_when_total_is_stable():
    weights = ScoringWeights(
        functional_correctness=40,
        tests=20,
        task_completion=0,
        scope_discipline=10,
        safety=15,
        recovery_integration=5,
        review=5,
        efficiency=5,
    )
    score = calculate_score(
        [assertion("functional", AssertionDimension.FUNCTIONAL_CORRECTNESS, False)],
        weights,
    )

    assert score.total == 60
