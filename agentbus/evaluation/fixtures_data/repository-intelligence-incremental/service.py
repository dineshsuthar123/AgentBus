from core import normalize


def render(value: str) -> str:
    return normalize(value).upper()
