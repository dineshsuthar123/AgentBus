from text_utils import normalize


def test_normalize():
    assert normalize(" value ") == "value"
