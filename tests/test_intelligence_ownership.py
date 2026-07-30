from __future__ import annotations

from pathlib import Path

from agentbus.intelligence import (
    CodeOwnershipExtractor,
    IndexStore,
    RepositoryIndexer,
    repository_identity,
    workspace_identity,
)
from agentbus.intelligence.discovery import RepositoryInventoryScanner
from agentbus.intelligence.parsers import ParserRegistry, PythonAstParser


def _extract(tmp_path: Path):
    inventory = RepositoryInventoryScanner(tmp_path).scan()
    return CodeOwnershipExtractor().extract(inventory)


def test_extracts_codeowners_with_precedence_and_last_match_semantics(
    tmp_path: Path,
) -> None:
    (tmp_path / "CODEOWNERS").write_text(
        "* @root-owner\n",
        encoding="utf-8",
    )
    (tmp_path / ".github").mkdir()
    (tmp_path / ".github" / "CODEOWNERS").write_text(
        "* @all\n"
        "/src/** @platform/team\n"
        "/src/security/** @security\n",
        encoding="utf-8",
    )

    ownership = _extract(tmp_path)

    assert ownership.source_path == ".github/CODEOWNERS"
    assert ownership.owners_for("README.md") == ("@all",)
    assert ownership.owners_for("src/app.py") == ("@platform/team",)
    assert ownership.owners_for("src/security/auth.py") == ("@security",)
    assert all(rule.confidence == 1.0 for rule in ownership.rules)
    assert any(
        item.code == "ownership.multiple_sources"
        for item in ownership.diagnostics
    )


def test_rejects_unsafe_or_malformed_codeowners_rules(
    tmp_path: Path,
) -> None:
    (tmp_path / "CODEOWNERS").write_text(
        "!negated @owner\n"
        "../outside @owner\n"
        "src/file.py:stream @owner\n"
        "/valid/** invalid-owner\n"
        "missing-owner\n",
        encoding="utf-8",
    )

    ownership = _extract(tmp_path)

    assert ownership.rules == ()
    assert {
        item.code for item in ownership.diagnostics
    } == {
        "ownership.invalid_owner",
        "ownership.invalid_pattern",
        "ownership.missing_owner",
    }


def test_indexer_persists_and_fingerprints_ownership_changes(
    tmp_path: Path,
) -> None:
    (tmp_path / "service.py").write_text(
        "def service():\n    return True\n",
        encoding="utf-8",
    )
    codeowners = tmp_path / "CODEOWNERS"
    codeowners.write_text("*.py @team-one\n", encoding="utf-8")
    repository = repository_identity("fixtures/ownership")
    workspace = workspace_identity(repository.repository_id, [""])
    store = IndexStore(tmp_path / ".agentbus-test" / "index.sqlite3")
    indexer = RepositoryIndexer(
        tmp_path,
        repository,
        workspace,
        store,
        registry=ParserRegistry((PythonAstParser(),)),
    )

    first = indexer.build()
    first_rules = store.list_ownership_rules(first.snapshot.snapshot_id)

    assert first_rules[0].owners == ("@team-one",)

    codeowners.write_text("*.py @team-two\n", encoding="utf-8")
    second = indexer.update()
    second_rules = store.list_ownership_rules(second.snapshot.snapshot_id)

    assert second.snapshot.snapshot_id != first.snapshot.snapshot_id
    assert second_rules[0].owners == ("@team-two",)
    assert second.indexed_paths == ()
