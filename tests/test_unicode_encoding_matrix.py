from __future__ import annotations

import io
import json
import subprocess
import sys
from pathlib import Path

import pytest

from agentbus import cli
from agentbus.intelligence.discovery import RepositoryInventoryScanner
from agentbus.intelligence.errors import UnsafeRepositoryPathError
from agentbus.intelligence.models import IndexState
from agentbus.intelligence.service import RepositoryIntelligenceService
from agentbus.tools.filesystem_operations import (
    ContainedFileSystem,
    FileContentKind,
)
from agentbus.tools.filesystem_security import ContainedPathResolver


ACCENTED_FILENAME = "caf\u00e9.py"
EMOJI_FILENAME = "emoji_\U0001f9ea.py"
CJK_IDENTIFIER = "\u8ba1\u7b97"
ACCENTED_IDENTIFIER = "r\u00e9sum\u00e9"
RTL_TEXT = "\u0645\u0631\u062d\u0628\u0627"
UTF16_FILENAME = "utf16.py"
MALFORMED_FILENAME = "malformed.py"


def _git(repository: Path, *arguments: str) -> None:
    subprocess.run(
        ["git", *arguments],
        cwd=repository,
        check=True,
        capture_output=True,
        text=True,
        shell=False,
    )


def _unicode_repository(tmp_path: Path) -> Path:
    repository = tmp_path / "repository"
    repository.mkdir()
    _git(repository, "init", "-q")
    _git(repository, "config", "user.name", "AgentBus Encoding Test")
    _git(repository, "config", "user.email", "encoding@agentbus.invalid")
    _git(repository, "config", "core.autocrlf", "false")
    (repository / "pyproject.toml").write_text(
        '[project]\nname = "unicode-matrix"\nversion = "0.1.0"\n',
        encoding="utf-8",
    )
    (repository / "utf8.py").write_bytes(
        (
            f"def {ACCENTED_IDENTIFIER}() -> str:\n"
            f'    \"\"\"{RTL_TEXT}\"\"\"\n'
            f'    return "{RTL_TEXT}"\n'
        ).encode("utf-8")
    )
    (repository / "bom.py").write_bytes(
        "def bom_ready() -> bool:\n    return True\n".encode("utf-8-sig")
    )
    (repository / ACCENTED_FILENAME).write_bytes(
        b"def unicode_filename() -> bool:\n    return True\n"
    )
    (repository / EMOJI_FILENAME).write_bytes(
        (f"def {CJK_IDENTIFIER}() -> int:\n    return 3\n").encode("utf-8")
    )
    (repository / UTF16_FILENAME).write_bytes(
        "def utf16_payload() -> bool:\n    return True\n".encode("utf-16")
    )
    (repository / MALFORMED_FILENAME).write_bytes(
        b"def malformed_payload():\n    return '\xff'\n"
    )
    _git(repository, "add", "--all")
    _git(repository, "commit", "-q", "-m", "unicode fixture")
    return repository.resolve()


def test_repository_index_handles_unicode_and_encoding_matrix(
    tmp_path: Path,
) -> None:
    repository = _unicode_repository(tmp_path)
    inventory = RepositoryInventoryScanner(repository).scan()
    service = RepositoryIntelligenceService(
        repository,
        tmp_path / "repository-index.sqlite3",
    )

    mutation = service.build()

    expected_indexed = {
        "utf8.py",
        "bom.py",
        ACCENTED_FILENAME,
        EMOJI_FILENAME,
    }
    expected_skipped = {UTF16_FILENAME, MALFORMED_FILENAME}
    assert mutation.status.state == IndexState.PARTIALLY_CURRENT
    assert expected_indexed <= set(mutation.indexed_paths)
    assert expected_skipped <= set(mutation.skipped_paths)
    assert inventory.read_text("bom.py").startswith("def bom_ready")
    assert inventory.read_text("utf8.py").endswith(f'return "{RTL_TEXT}"\n')

    cjk_results = service.search(CJK_IDENTIFIER, limit=10)
    accented_results = service.search(ACCENTED_IDENTIFIER, limit=10)
    assert any(
        item.symbol is not None
        and item.symbol.name == CJK_IDENTIFIER
        and item.relative_path == EMOJI_FILENAME
        for item in cjk_results.results
    )
    assert any(
        item.symbol is not None
        and item.symbol.name == ACCENTED_IDENTIFIER
        and item.relative_path == "utf8.py"
        for item in accented_results.results
    )

    failures = tuple(
        diagnostic
        for diagnostic in mutation.snapshot.diagnostics
        if diagnostic.code == "index.file_failed"
        and diagnostic.relative_path in expected_skipped
    )
    assert {item.relative_path for item in failures} == expected_skipped
    for failure in failures:
        serialized = json.dumps(failure.model_dump(mode="json"), sort_keys=True)
        assert len(serialized) < 1_000
        assert "payload" not in serialized
        assert str(repository) not in serialized


def test_raw_byte_inputs_and_unicode_paths_remain_safely_classified(
    tmp_path: Path,
) -> None:
    repository = _unicode_repository(tmp_path)
    inventory = RepositoryInventoryScanner(repository).scan()
    filesystem = ContainedFileSystem(repository)
    resolver = ContainedPathResolver(repository)

    for relative_path in (ACCENTED_FILENAME, EMOJI_FILENAME):
        resolved = resolver.resolve(relative_path)
        assert resolved.relative_path == relative_path
        assert resolved.classification.protected is False
        assert resolved.classification.generated is False

    for relative_path in (UTF16_FILENAME, MALFORMED_FILENAME):
        with pytest.raises(UnsafeRepositoryPathError) as captured:
            inventory.read_text(relative_path)
        assert len(str(captured.value)) < 256
        assert str(repository) not in str(captured.value)

        result = filesystem.read(relative_path)
        assert result.relative_path == relative_path
        assert result.content_kind == FileContentKind.BINARY
        assert result.content is None
        assert result.sha256 is not None
        assert result.truncated is False


def test_human_cli_escapes_characters_unsupported_by_cp1252(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = _unicode_repository(tmp_path)
    database = tmp_path / "repository-index.sqlite3"
    RepositoryIntelligenceService(repository, database).build()
    raw_output = io.BytesIO()
    console = io.TextIOWrapper(
        raw_output,
        encoding="cp1252",
        errors="strict",
    )
    monkeypatch.setattr(sys, "stdout", console)
    common = [
        "--workspace",
        str(repository),
        "--index-db",
        str(database),
    ]

    assert cli.main(["search", CJK_IDENTIFIER, *common]) == 0
    assert cli.main(["search", ACCENTED_IDENTIFIER, *common]) == 0
    console.flush()
    rendered = raw_output.getvalue().decode("cp1252")

    assert "\\u8ba1\\u7b97" in rendered
    assert "\\U0001f9ea" in rendered
    assert ACCENTED_IDENTIFIER in rendered
