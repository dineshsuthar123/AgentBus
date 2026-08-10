from __future__ import annotations

import pytest

from agentbus.product.synthetic import (
    SyntheticGenerationCancelled,
    generate_synthetic_repository,
    verify_synthetic_repository,
)


def test_synthetic_repository_is_deterministic_across_roots(tmp_path):
    first = generate_synthetic_repository(
        tmp_path / "first",
        file_count=125,
        seed=42,
    )
    second = generate_synthetic_repository(
        tmp_path / "second",
        file_count=125,
        seed=42,
    )

    assert first.file_count == 125
    assert first.byte_count == second.byte_count
    assert first.fingerprint == second.fingerprint
    assert verify_synthetic_repository(first.root).fingerprint == first.fingerprint
    assert len(list(first.root.glob("package_*/module_*.py"))) == 125


def test_synthetic_repository_refuses_nonempty_user_destination(tmp_path):
    destination = tmp_path / "user-repository"
    destination.mkdir()
    source = destination / "source.py"
    source.write_text("preserve = True\n", encoding="utf-8")

    with pytest.raises(ValueError, match="never replaces user data"):
        generate_synthetic_repository(destination, file_count=10)

    assert source.read_text(encoding="utf-8") == "preserve = True\n"


def test_synthetic_generation_is_cooperatively_cancellable(tmp_path):
    checks = 0

    def cancelled() -> bool:
        nonlocal checks
        checks += 1
        return checks >= 2

    with pytest.raises(SyntheticGenerationCancelled, match="after 100 files"):
        generate_synthetic_repository(
            tmp_path / "cancelled",
            file_count=1_000,
            cancelled=cancelled,
        )

    assert len(list((tmp_path / "cancelled").glob("package_*/module_*.py"))) == 100


@pytest.mark.parametrize("file_count", [0, 50_001])
def test_synthetic_repository_enforces_bounded_size(tmp_path, file_count):
    with pytest.raises(ValueError, match="between 1 and 50000"):
        generate_synthetic_repository(tmp_path / "repo", file_count=file_count)
