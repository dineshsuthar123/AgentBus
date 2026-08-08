from packages.shared_python.rules import normalize_total


def add_values(left: int, right: int) -> int:
    return normalize_total(left + right)
