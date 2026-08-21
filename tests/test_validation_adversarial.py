from __future__ import annotations

import json

import pytest

from agentbus.intelligence.discovery import RepositoryInventoryScanner
from agentbus.intelligence.models import IndexState
from agentbus.intelligence.service import RepositoryIntelligenceService
from agentbus.validation.adversarial import generate_adversarial_repository
from agentbus.validation.failures import RepositoryValidationError


def test_adversarial_fixture_is_owned_descriptive_and_never_overwrites(tmp_path):
    fixture = generate_adversarial_repository(tmp_path / "hostile")
    marker = json.loads(
        (fixture.root / ".agentbus-adversarial-fixture.json").read_text(
            encoding="utf-8"
        )
    )

    assert marker["owner"] == "agentbus-validation"
    assert marker["schema_version"] == 1
    assert set(marker["created_features"]) == set(fixture.created_features)
    assert set(marker["unavailable_features"]) == set(fixture.unavailable_features)
    assert {
        "malformed-source",
        "protected-content",
        "nested-git-metadata",
        "oversized-file",
    } <= set(fixture.created_features)

    occupied = tmp_path / "occupied"
    occupied.mkdir()
    (occupied / "user-data.txt").write_text("preserve", encoding="utf-8")
    with pytest.raises(RepositoryValidationError, match="user data is preserved"):
        generate_adversarial_repository(occupied)
    assert (occupied / "user-data.txt").read_text(encoding="utf-8") == "preserve"


def test_adversarial_inventory_remains_bounded_and_excludes_protected_content(
    tmp_path,
):
    fixture = generate_adversarial_repository(tmp_path / "hostile")

    inventory = RepositoryInventoryScanner(fixture.root).scan()
    paths = {item.relative_path for item in inventory.files}
    diagnostics = {item.code for item in inventory.diagnostics}
    serialized_diagnostics = json.dumps(
        [item.model_dump(mode="json") for item in inventory.diagnostics]
    )

    assert "src/valid.py" in paths
    assert "src/cafe_\u8ba1\u7b97.py" in paths
    assert "nested/repository/source.py" not in paths
    assert ".env" not in paths
    assert ".env.local" not in paths
    assert "secrets.json" not in paths
    assert not any(path.startswith(".ssh/") for path in paths)
    assert not any(path.startswith("nested/repository/.git/") for path in paths)
    assert not any(path.startswith("dist/") for path in paths)
    assert not any(path.startswith("vendor/") for path in paths)
    assert not any(path.startswith("node_modules/") for path in paths)
    assert "oversized.bin" not in paths
    assert "discovery.gitignore_unreadable" in diagnostics
    assert "discovery.file_too_large" in diagnostics
    assert "discovery.nested_repository_boundary" in diagnostics
    assert any(
        item.code == "discovery.nested_repository_boundary"
        and item.relative_path == "nested/repository"
        for item in inventory.diagnostics
    )
    if "symlink-loop" in fixture.created_features:
        assert "discovery.link_rejected" in diagnostics
        assert "loop" not in paths
    assert "fixture-secret-must-not-be-indexed" not in serialized_diagnostics
    assert "fixture-token-must-not-be-indexed" not in serialized_diagnostics


def test_repository_index_survives_adversarial_source_and_manifests(tmp_path):
    fixture = generate_adversarial_repository(tmp_path / "hostile")
    service = RepositoryIntelligenceService(
        fixture.root,
        tmp_path / "index.sqlite3",
    )

    mutation = service.build()
    verification = service.verify()
    search = service.search("stable_api", limit=10)

    assert mutation.indexed_count > 0
    assert mutation.skipped_count > 0
    assert verification.valid is True
    assert verification.fresh is False
    assert verification.status.state == IndexState.PARTIALLY_CURRENT
    assert verification.status.stale_paths == ()
    assert any(item.relative_path == "src/valid.py" for item in search.results)
