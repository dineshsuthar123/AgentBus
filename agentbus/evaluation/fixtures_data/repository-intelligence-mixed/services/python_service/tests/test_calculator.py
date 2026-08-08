from services.python_service.calculator import calculate_total


def test_calculate_total() -> None:
    assert calculate_total(2, 3) == 5
