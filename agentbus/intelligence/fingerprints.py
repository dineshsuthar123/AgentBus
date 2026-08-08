from __future__ import annotations

from hashlib import sha256
from typing import Iterable, Mapping

from agentbus.intelligence.identities import stable_hash
from agentbus.intelligence.models import (
    ContextCandidate,
    DependencyEdge,
    Project,
    SourceFile,
    _relative_path,
)


def content_hash(content: bytes | str) -> str:
    payload = content.encode("utf-8") if isinstance(content, str) else content
    return sha256(payload).hexdigest()


def file_set_fingerprint(files: Mapping[str, str] | Iterable[SourceFile]) -> str:
    if isinstance(files, Mapping):
        records = [
            {
                "path": _relative_path(path),
                "content_hash": digest,
            }
            for path, digest in files.items()
        ]
    else:
        records = [
            {
                "path": source.relative_path,
                "content_hash": source.content_hash,
                "parser_name": source.parser_name,
                "parser_version": source.parser_version,
                "generated": source.generated,
                "protected": source.protected,
            }
            for source in files
        ]
    return stable_hash(sorted(records, key=lambda item: item["path"]))


def parser_versions_fingerprint(versions: Mapping[str, str]) -> str:
    bounded = {
        str(name): str(version)
        for name, version in versions.items()
    }
    if len(bounded) > 64:
        raise ValueError("parser version map exceeds the maximum entry count")
    return stable_hash(bounded)


def project_map_fingerprint(projects: Iterable[Project]) -> str:
    records = [
        project.model_dump(mode="json")
        for project in sorted(projects, key=lambda item: item.project_id)
    ]
    return stable_hash(records)


def graph_fingerprint(edges: Iterable[DependencyEdge]) -> str:
    records = [
        edge.model_dump(mode="json")
        for edge in sorted(edges, key=lambda item: item.edge_id)
    ]
    return stable_hash(records)


def context_candidates_fingerprint(
    candidates: Iterable[ContextCandidate],
) -> str:
    records = []
    for candidate in sorted(candidates, key=lambda item: item.candidate_id):
        record = candidate.model_dump(mode="json", exclude={"content"})
        records.append(record)
    return stable_hash(records)
