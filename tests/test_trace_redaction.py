from pathlib import Path

import pytest

from agentbus.trace import (
    HIDDEN_CONTENT,
    PRIVATE_PATH,
    REDACTED,
    configuration_fingerprint,
    contains_secret_material,
    sanitize_document,
    sanitize_text,
)


def test_document_redaction_removes_secrets_hidden_content_and_private_paths(
    tmp_path: Path,
) -> None:
    document = sanitize_document(
        {
            "authorization": "Bearer definitely-secret",
            "nested": {"api_key": "key-value"},
            "chain_of_thought": "private reasoning",
            "artifact": str(tmp_path / "private" / "result.txt"),
        },
        private_roots=[tmp_path],
    )

    assert document.value["authorization"] == REDACTED
    assert document.value["nested"]["api_key"] == REDACTED
    assert document.value["chain_of_thought"] == HIDDEN_CONTENT
    assert document.value["artifact"].startswith(PRIVATE_PATH)
    assert b"definitely-secret" not in document.canonical_bytes
    assert b"private reasoning" not in document.canonical_bytes
    assert str(tmp_path).encode() not in document.canonical_bytes
    assert document.redaction.secret_field_count == 2
    assert document.redaction.hidden_field_count == 1
    assert document.redaction.private_path_count == 1


def test_text_redaction_is_bounded_and_reports_truncation() -> None:
    document = sanitize_text("x" * 100, max_chars=10)

    assert document.value == "x" * 10 + "\n[truncated]"
    assert document.redaction.truncated_value_count == 1
    assert document.redaction.retained_bytes == len(document.canonical_bytes)


@pytest.mark.parametrize(
    "payload",
    [
        "Authorization: Bearer abcdefghijklmnop",
        "api_key=definitely-secret",
        "-----BEGIN PRIVATE KEY-----\nsecret",
        b"\x00\xff",
    ],
)
def test_secret_classifier_rejects_unredacted_or_binary_material(payload) -> None:
    assert contains_secret_material(payload) is True


def test_canonical_redaction_and_fingerprint_ignore_mapping_order() -> None:
    left = configuration_fingerprint({"b": 2, "a": 1, "token": "one"})
    right = configuration_fingerprint({"token": "two", "a": 1, "b": 2})

    assert left == right


def test_redaction_rejects_nonfinite_or_unbounded_payloads() -> None:
    with pytest.raises(ValueError, match="non-finite"):
        sanitize_document({"value": float("nan")})

    with pytest.raises(ValueError, match="maximum node count"):
        sanitize_document(list(range(10)), max_nodes=3)


def test_home_paths_are_redacted_without_explicit_roots() -> None:
    windows = sanitize_text(r"read C:\Users\Alice\repo\secret.txt")
    posix = sanitize_text("read /home/alice/repo/secret.txt")

    assert r"C:\Users\Alice" not in windows.value
    assert "/home/alice" not in posix.value
    assert PRIVATE_PATH in windows.value
    assert PRIVATE_PATH in posix.value


def test_home_paths_with_spaces_are_fully_redacted() -> None:
    windows = sanitize_text(
        r"C:\Users\Alice Example\private project\secret.txt"
    )
    posix = sanitize_text("/Users/Alice Example/private project/secret.txt")

    assert windows.value == PRIVATE_PATH
    assert posix.value == PRIVATE_PATH


def test_explicit_private_root_matches_alternate_path_separators(
    tmp_path: Path,
) -> None:
    root = str((tmp_path / "private-worktree").resolve())
    alternate = (
        root.replace("\\", "/")
        if "\\" in root
        else root.replace("/", "\\")
    )

    document = sanitize_text(
        alternate + "/artifact.txt",
        private_roots=[root],
    )

    assert document.value.startswith(PRIVATE_PATH)
    assert "private-worktree" not in document.value
