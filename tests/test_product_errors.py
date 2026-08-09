import json

import pytest

from agentbus.product.errors import (
    ProductError,
    ProductErrorCategory,
    as_product_error,
    render_product_error,
)


@pytest.mark.parametrize("category", list(ProductErrorCategory))
def test_every_public_error_category_has_a_stable_code(category):
    error = ProductError(
        category=category,
        message="Operation failed.",
        likely_cause="A safe cause.",
        recommended_action="Take a safe action.",
    )

    assert error.code.startswith("AGENTBUS-E")
    assert len(error.code) == 14
    assert error.to_dict()["category"] == category.value


def test_public_error_output_redacts_secrets_and_bounds_details():
    error = ProductError(
        category=ProductErrorCategory.PROVIDER_CONFIGURATION_ERROR,
        message="API_KEY=super-secret is invalid.",
        likely_cause="Bearer secret-token was supplied.",
        recommended_action="Set a credential in the process environment.",
        safe_detail="https://example.invalid/path?token=private " + ("x" * 10_000),
    )

    rendered = render_product_error(error)
    payload = json.dumps(error.to_dict())

    assert "super-secret" not in rendered + payload
    assert "secret-token" not in rendered + payload
    assert "token=private" not in rendered + payload
    assert "[REDACTED]" in rendered + payload
    assert len(payload) < 4_000


def test_normal_conversion_does_not_expose_traceback_or_exception_message():
    try:
        raise ValueError("API_KEY=do-not-print")
    except ValueError as exc:
        product_error = as_product_error(exc)

    rendered = render_product_error(product_error)

    assert "Traceback" not in rendered
    assert "do-not-print" not in rendered
    assert product_error.safe_detail == "ValueError"


def test_invalid_public_error_code_is_rejected():
    with pytest.raises(ValueError, match="AGENTBUS-E"):
        ProductError(
            category=ProductErrorCategory.INTERNAL_ERROR,
            code="bad-code",
            message="Operation failed.",
            likely_cause="A cause.",
            recommended_action="An action.",
        )
