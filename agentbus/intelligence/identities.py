from __future__ import annotations

import json
import re
import unicodedata
from hashlib import sha256
from typing import Any

from agentbus.intelligence.models import (
    ProjectKind,
    RepositoryIdentity,
    SymbolKind,
    WorkspaceIdentity,
    _relative_path,
)
from agentbus.intelligence.version import INTELLIGENCE_SCHEMA_VERSION


_PREFIXES = {
    "repo",
    "workspace",
    "project",
    "module",
    "file",
    "symbol",
    "reference",
    "edge",
    "snapshot",
    "plan",
    "impact",
    "testimpact",
}
_DRIVE_PATTERN = re.compile(r"^[A-Za-z]:[/\\]")


def repository_identity(
    repository_key: str,
    *,
    display_name: str | None = None,
) -> RepositoryIdentity:
    key = _portable_repository_key(repository_key)
    key_hash = stable_hash(
        {
            "schema_version": INTELLIGENCE_SCHEMA_VERSION,
            "repository_key": key,
        }
    )
    return RepositoryIdentity(
        repository_id=f"repo_{key_hash}",
        key_hash=key_hash,
        display_name=_bounded_display_name(display_name),
    )


def workspace_identity(
    repository_id: str,
    roots: tuple[str, ...] | list[str],
) -> WorkspaceIdentity:
    normalized = tuple(
        sorted({_relative_path(root, allow_root=True) for root in roots})
    )
    digest = stable_hash(
        {
            "repository_id": repository_id,
            "roots": normalized,
        }
    )
    return WorkspaceIdentity(
        workspace_id=f"workspace_{digest}",
        repository_id=repository_id,
        roots=normalized,
    )


def project_id(
    repository_id: str,
    root: str,
    kind: ProjectKind | str,
    *,
    name: str | None = None,
) -> str:
    return stable_id(
        "project",
        repository_id,
        _relative_path(root, allow_root=True),
        ProjectKind(kind).value,
        _canonical_text(name or ""),
    )


def file_id(repository_id: str, relative_path: str) -> str:
    return stable_id(
        "file",
        repository_id,
        _relative_path(relative_path),
    )


def module_id(
    project_identity: str,
    relative_path: str,
    qualified_name: str,
) -> str:
    return stable_id(
        "module",
        project_identity,
        _relative_path(relative_path),
        _canonical_text(qualified_name),
    )


def symbol_id(
    file_identity: str,
    qualified_name: str,
    kind: SymbolKind | str,
    *,
    signature: str | None = None,
    ordinal: int = 0,
) -> str:
    if ordinal < 0:
        raise ValueError("symbol ordinal must not be negative")
    return stable_id(
        "symbol",
        file_identity,
        _canonical_text(qualified_name),
        SymbolKind(kind).value,
        _canonical_text(signature or ""),
        ordinal,
    )


def reference_id(
    source_file_id: str,
    relative_path: str,
    start_line: int,
    start_column: int,
    target: str,
    kind: str,
) -> str:
    if start_line < 1 or start_column < 0:
        raise ValueError("reference location is invalid")
    return stable_id(
        "reference",
        source_file_id,
        _relative_path(relative_path),
        start_line,
        start_column,
        _canonical_text(target),
        _canonical_text(kind),
    )


def edge_id(
    source_id: str,
    target_id: str,
    kind: str,
    *,
    location_key: str = "",
) -> str:
    return stable_id(
        "edge",
        source_id,
        target_id,
        _canonical_text(kind),
        _canonical_text(location_key),
    )


def snapshot_id(
    repository_id: str,
    source_fingerprint: str,
    parser_fingerprint: str,
    project_map_hash: str,
    graph_hash: str,
) -> str:
    return stable_id(
        "snapshot",
        repository_id,
        source_fingerprint,
        parser_fingerprint,
        project_map_hash,
        graph_hash,
    )


def context_plan_id(
    snapshot_identity: str | None,
    task_hash: str,
    role: str,
    byte_budget: int,
    token_budget: int,
) -> str:
    return stable_id(
        "plan",
        snapshot_identity or "no-snapshot",
        task_hash,
        _canonical_text(role),
        byte_budget,
        token_budget,
    )


def impact_result_id(
    snapshot_identity: str | None,
    paths: tuple[str, ...] | list[str],
    symbols: tuple[str, ...] | list[str],
) -> str:
    return stable_id(
        "impact",
        snapshot_identity or "no-snapshot",
        sorted(_relative_path(path) for path in paths),
        sorted(symbols),
    )


def test_impact_result_id(
    snapshot_identity: str | None,
    paths: tuple[str, ...] | list[str],
    symbols: tuple[str, ...] | list[str],
) -> str:
    return stable_id(
        "testimpact",
        snapshot_identity or "no-snapshot",
        sorted(_relative_path(path) for path in paths),
        sorted(symbols),
    )


def stable_id(prefix: str, *parts: Any) -> str:
    if prefix not in _PREFIXES:
        raise ValueError(f"unsupported identity prefix: {prefix}")
    return f"{prefix}_{stable_hash(parts)}"


def stable_hash(value: Any) -> str:
    payload = json.dumps(
        _canonical_value(value),
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return sha256(payload).hexdigest()


def _canonical_value(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            _canonical_text(str(key)): _canonical_value(item)
            for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
        }
    if isinstance(value, (list, tuple)):
        return [_canonical_value(item) for item in value]
    if isinstance(value, set):
        return sorted(
            (_canonical_value(item) for item in value),
            key=lambda item: json.dumps(item, sort_keys=True),
        )
    if isinstance(value, str):
        return _canonical_text(value)
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    if hasattr(value, "model_dump"):
        return _canonical_value(value.model_dump(mode="json"))
    raise TypeError(f"unsupported canonical identity value: {type(value).__name__}")


def _portable_repository_key(value: str) -> str:
    key = _canonical_text(value).strip("/")
    lowered = key.casefold()
    if (
        not key
        or value.startswith(("/", "\\"))
        or _DRIVE_PATTERN.match(value)
        or lowered.startswith(("file:", "unc:"))
        or "\x00" in value
        or len(key) > 2_048
    ):
        raise ValueError("repository key must be portable and path independent")
    return key.casefold()


def _canonical_text(value: str) -> str:
    return unicodedata.normalize("NFKC", value).replace("\\", "/").strip()


def _bounded_display_name(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = _canonical_text(value)
    if not normalized or len(normalized) > 256:
        raise ValueError("repository display name is invalid")
    return normalized
