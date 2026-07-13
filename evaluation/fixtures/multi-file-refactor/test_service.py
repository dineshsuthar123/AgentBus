from service import greeting


def test_greeting():
    assert greeting("Ada", "Lovelace") == "Hello, Ada Lovelace!"
