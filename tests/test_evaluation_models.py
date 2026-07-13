import json

import pytest
from pydantic import ValidationError

from agentbus.evaluation.models import (
    ContentExpectation,
    EvaluationCase,
    EvaluationSuite,
    EvaluationVariant,
    ScoringWeights,
)


def case(**overrides):
    values = {
        "case_id": "sample-case",
        "title": "Sample",
        "task_prompt": "Make a safe change",
        "fixture_repository_source": "python-feature",
    }
    values.update(overrides)
    return EvaluationCase(**values)


def test_evaluation_case_serialization_round_trip_is_json_safe():
    original = case(
        expected_files=["src/app.py"],
        content_expectations=[ContentExpectation(path="src/app.py", pattern="safe")],
        metadata={"api_key": "must-not-survive", "nested": {"value": 1}},
    )

    restored = EvaluationCase.model_validate_json(original.model_dump_json())

    assert restored == original
    serialized = json.loads(original.model_dump_json())
    assert serialized["metadata"]["api_key"] == "[REDACTED]"
    assert "must-not-survive" not in original.model_dump_json()


@pytest.mark.parametrize(
    "path",
    ["../escape.py", "C:/escape.py", "/escape.py", r"\\server\share\escape.py"],
)
def test_evaluation_case_rejects_unsafe_expected_paths(path):
    with pytest.raises(ValidationError):
        case(expected_files=[path])


def test_evaluation_case_rejects_remote_fixture_source():
    with pytest.raises(ValidationError, match="must be local"):
        case(fixture_repository_source="https://example.invalid/repo")


def test_parallel_variant_requires_durable_mode_and_multiple_workers():
    with pytest.raises(ValidationError):
        EvaluationVariant(
            variant_id="bad-parallel",
            title="Bad",
            provider="fake",
            durable=False,
            parallel=True,
            max_workers=2,
        )
    with pytest.raises(ValidationError):
        EvaluationVariant(
            variant_id="bad-workers",
            title="Bad",
            provider="fake",
            durable=True,
            parallel=True,
            max_workers=1,
        )


def test_suite_rejects_duplicate_case_ids():
    with pytest.raises(ValidationError, match="unique"):
        EvaluationSuite(
            suite_id="duplicate-suite",
            title="Duplicates",
            description="invalid",
            cases=[case(), case()],
            default_variant="single-fake",
        )


def test_content_expectation_requires_exactly_one_matcher():
    with pytest.raises(ValidationError):
        ContentExpectation(path="README.md")
    with pytest.raises(ValidationError):
        ContentExpectation(path="README.md", exact="x", pattern="x")


def test_scoring_weights_must_total_one_hundred():
    with pytest.raises(ValidationError, match="total 100"):
        ScoringWeights(efficiency=6)
