from service import render


def test_render() -> None:
    assert render(" value ") == "VALUE"
