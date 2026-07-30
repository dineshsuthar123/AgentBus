from __future__ import annotations

import json
from pathlib import PurePosixPath
from typing import Any

from agentbus.intelligence.discovery.base import ProjectDetection
from agentbus.intelligence.discovery.ignore import glob_match
from agentbus.intelligence.discovery.scanner import RepositoryInventory
from agentbus.intelligence.errors import RepositoryIntelligenceError
from agentbus.intelligence.identities import project_id
from agentbus.intelligence.models import (
    DiagnosticSeverity,
    IndexDiagnostic,
    Project,
    ProjectKind,
    RepositoryIdentity,
    _relative_path,
)


_NODE_CONFIG_NAMES = {
    "jsconfig.json",
    "package.json",
    "tsconfig.json",
}
_NODE_LOCK_NAMES = {
    "bun.lock",
    "bun.lockb",
    "npm-shrinkwrap.json",
    "package-lock.json",
    "pnpm-lock.yaml",
    "yarn.lock",
}
_SOURCE_DIRECTORY_NAMES = ("app", "lib", "src")
_TEST_DIRECTORY_NAMES = ("__tests__", "test", "tests")
_SOURCE_SUFFIXES = {
    ".cjs",
    ".cts",
    ".js",
    ".jsx",
    ".mjs",
    ".mts",
    ".ts",
    ".tsx",
}


class NodeProjectDetector:
    name = "node"

    def detect(
        self,
        repository: RepositoryIdentity,
        inventory: RepositoryInventory,
    ) -> ProjectDetection:
        manifests_by_root: dict[str, set[str]] = {}
        for item in inventory.files:
            path = PurePosixPath(item.relative_path)
            root = _parent(item.relative_path)
            if path.name in _NODE_CONFIG_NAMES:
                manifests_by_root.setdefault(root, set()).add(item.relative_path)
            elif path.name in _NODE_LOCK_NAMES:
                manifests_by_root.setdefault(root, set()).add(item.relative_path)

        metadata_by_root: dict[str, dict[str, Any]] = {}
        diagnostics: list[IndexDiagnostic] = []
        projects: list[Project] = []
        for root, manifests in sorted(manifests_by_root.items()):
            if not any(
                PurePosixPath(path).name in _NODE_CONFIG_NAMES
                for path in manifests
            ):
                continue
            metadata, metadata_diagnostics = _read_metadata(
                tuple(sorted(manifests)),
                inventory,
            )
            metadata_by_root[root] = metadata
            diagnostics.extend(metadata_diagnostics)
            name = _project_name(root, metadata)
            projects.append(
                Project(
                    project_id=project_id(
                        repository.repository_id,
                        root,
                        ProjectKind.NODE,
                        name=name,
                    ),
                    repository_id=repository.repository_id,
                    name=name,
                    kind=ProjectKind.NODE,
                    root=root,
                    source_roots=_source_roots(root, metadata, inventory),
                    test_roots=_test_roots(root, inventory),
                    generated_roots=_under_root(
                        inventory.generated_roots,
                        root,
                    ),
                    manifest_paths=tuple(sorted(manifests)),
                )
            )
        linked = _link_workspaces(tuple(projects), metadata_by_root)
        return ProjectDetection(
            projects=linked,
            diagnostics=tuple(diagnostics),
        )


def _read_metadata(
    manifests: tuple[str, ...],
    inventory: RepositoryInventory,
) -> tuple[dict[str, Any], tuple[IndexDiagnostic, ...]]:
    metadata: dict[str, Any] = {}
    diagnostics: list[IndexDiagnostic] = []
    for manifest in manifests:
        name = PurePosixPath(manifest).name
        if name not in _NODE_CONFIG_NAMES:
            continue
        try:
            text = inventory.read_text(manifest)
            parsed = (
                json.loads(text)
                if name == "package.json"
                else _load_jsonc(text)
            )
            if not isinstance(parsed, dict):
                raise ValueError("Node project metadata must be an object")
            metadata[name] = parsed
        except (
            json.JSONDecodeError,
            RecursionError,
            RepositoryIntelligenceError,
            UnicodeError,
            ValueError,
        ) as exc:
            diagnostics.append(
                IndexDiagnostic(
                    code="discovery.node_metadata_invalid",
                    severity=DiagnosticSeverity.WARNING,
                    message="Node project metadata could not be parsed safely.",
                    relative_path=manifest,
                    recoverable=True,
                    details={"error_type": type(exc).__name__},
                )
            )
    return metadata, tuple(diagnostics)


def _project_name(root: str, metadata: dict[str, Any]) -> str:
    package = metadata.get("package.json")
    if isinstance(package, dict):
        value = package.get("name")
        if isinstance(value, str) and value.strip():
            return value.strip()[:256]
    return PurePosixPath(root).name[:256] if root else "node-project"


def _source_roots(
    root: str,
    metadata: dict[str, Any],
    inventory: RepositoryInventory,
) -> tuple[str, ...]:
    candidates: set[str] = set()
    for name in _SOURCE_DIRECTORY_NAMES:
        candidate = _join(root, name)
        if _contains_source_under(inventory, candidate):
            candidates.add(candidate)
    for config_name in ("tsconfig.json", "jsconfig.json"):
        config = metadata.get(config_name)
        if not isinstance(config, dict):
            continue
        compiler_options = config.get("compilerOptions")
        if isinstance(compiler_options, dict):
            root_dir = compiler_options.get("rootDir")
            if isinstance(root_dir, str):
                _add_existing_root(candidates, root, root_dir, inventory)
            root_dirs = compiler_options.get("rootDirs")
            if isinstance(root_dirs, list):
                for value in root_dirs[:128]:
                    _add_existing_root(candidates, root, value, inventory)
        includes = config.get("include")
        if isinstance(includes, list):
            for value in includes[:128]:
                prefix = _glob_prefix(value) if isinstance(value, str) else None
                if prefix is not None:
                    _add_existing_root(candidates, root, prefix, inventory)
    if any(
        _parent(item.relative_path) == root
        and PurePosixPath(item.relative_path).suffix.casefold()
        in _SOURCE_SUFFIXES
        for item in inventory.files
    ):
        candidates.add(root)
    if not candidates:
        package = metadata.get("package.json")
        if not _workspace_patterns(package):
            candidates.add(root)
    return tuple(sorted(candidates))


def _test_roots(
    root: str,
    inventory: RepositoryInventory,
) -> tuple[str, ...]:
    return tuple(
        candidate
        for candidate in (
            _join(root, name)
            for name in _TEST_DIRECTORY_NAMES
        )
        if _contains_file_under(inventory, candidate)
    )


def _link_workspaces(
    projects: tuple[Project, ...],
    metadata_by_root: dict[str, dict[str, Any]],
) -> tuple[Project, ...]:
    by_root = {project.root: project for project in projects}
    links: dict[str, set[str]] = {
        project.project_id: set()
        for project in projects
    }
    for root, metadata in metadata_by_root.items():
        parent = by_root.get(root)
        if parent is None:
            continue
        package = metadata.get("package.json")
        patterns = _workspace_patterns(package)
        for child_root, child in by_root.items():
            if child_root == root or not _is_under(child_root, root):
                continue
            relative_child = (
                child_root[len(root) + 1 :]
                if root
                else child_root
            )
            if _matches_workspace(relative_child, patterns):
                links[parent.project_id].add(child.project_id)
                links[child.project_id].add(parent.project_id)
    return tuple(
        project.model_copy(
            update={
                "workspace_project_ids": tuple(
                    sorted(links[project.project_id])
                )
            }
        )
        for project in projects
    )


def _workspace_patterns(package: object) -> tuple[str, ...]:
    if not isinstance(package, dict):
        return ()
    value = package.get("workspaces")
    if isinstance(value, dict):
        value = value.get("packages")
    if not isinstance(value, list):
        return ()
    patterns: list[str] = []
    for item in value[:256]:
        if not isinstance(item, str):
            continue
        negated = item.startswith("!")
        raw = item[1:] if negated else item
        try:
            normalized = _relative_path(raw, allow_pattern=True)
        except ValueError:
            continue
        patterns.append(f"!{normalized}" if negated else normalized)
    return tuple(patterns)


def _matches_workspace(path: str, patterns: tuple[str, ...]) -> bool:
    matched = False
    for pattern in patterns:
        negated = pattern.startswith("!")
        candidate = pattern[1:] if negated else pattern
        if glob_match(path, candidate):
            matched = not negated
    return matched


def _load_jsonc(content: str) -> object:
    without_comments = _strip_json_comments(content)
    without_trailing_commas = _strip_trailing_commas(without_comments)
    return json.loads(without_trailing_commas)


def _strip_json_comments(content: str) -> str:
    output: list[str] = []
    index = 0
    in_string = False
    escaped = False
    while index < len(content):
        character = content[index]
        if in_string:
            output.append(character)
            if escaped:
                escaped = False
            elif character == "\\":
                escaped = True
            elif character == '"':
                in_string = False
            index += 1
            continue
        if character == '"':
            in_string = True
            output.append(character)
            index += 1
            continue
        if character == "/" and index + 1 < len(content):
            marker = content[index + 1]
            if marker == "/":
                output.extend("  ")
                index += 2
                while index < len(content) and content[index] not in "\r\n":
                    output.append(" ")
                    index += 1
                continue
            if marker == "*":
                output.extend("  ")
                index += 2
                closed = False
                while index < len(content):
                    if (
                        content[index] == "*"
                        and index + 1 < len(content)
                        and content[index + 1] == "/"
                    ):
                        output.extend("  ")
                        index += 2
                        closed = True
                        break
                    output.append(
                        content[index]
                        if content[index] in "\r\n"
                        else " "
                    )
                    index += 1
                if not closed:
                    raise ValueError("unterminated JSON block comment")
                continue
        output.append(character)
        index += 1
    if in_string:
        raise ValueError("unterminated JSON string")
    return "".join(output)


def _strip_trailing_commas(content: str) -> str:
    output: list[str] = []
    index = 0
    in_string = False
    escaped = False
    while index < len(content):
        character = content[index]
        if in_string:
            output.append(character)
            if escaped:
                escaped = False
            elif character == "\\":
                escaped = True
            elif character == '"':
                in_string = False
            index += 1
            continue
        if character == '"':
            in_string = True
            output.append(character)
            index += 1
            continue
        if character == ",":
            lookahead = index + 1
            while lookahead < len(content) and content[lookahead].isspace():
                lookahead += 1
            if lookahead < len(content) and content[lookahead] in "}]":
                index += 1
                continue
        output.append(character)
        index += 1
    return "".join(output)


def _glob_prefix(value: str) -> str | None:
    normalized = value.replace("\\", "/").strip()
    prefix = normalized
    for marker in ("*", "?", "["):
        prefix = prefix.split(marker, 1)[0]
    prefix = prefix.rstrip("/")
    try:
        return _relative_path(prefix, allow_root=True)
    except ValueError:
        return None


def _add_existing_root(
    candidates: set[str],
    root: str,
    value: object,
    inventory: RepositoryInventory,
) -> None:
    if not isinstance(value, str):
        return
    try:
        candidate = _join(root, _relative_path(value, allow_root=True))
    except ValueError:
        return
    if _contains_source_under(inventory, candidate):
        candidates.add(candidate)


def _contains_source_under(
    inventory: RepositoryInventory,
    root: str,
) -> bool:
    return any(
        PurePosixPath(item.relative_path).suffix.casefold()
        in _SOURCE_SUFFIXES
        and _is_under(item.relative_path, root)
        for item in inventory.files
    )


def _contains_file_under(
    inventory: RepositoryInventory,
    root: str,
) -> bool:
    return any(_is_under(item.relative_path, root) for item in inventory.files)


def _under_root(paths: tuple[str, ...], root: str) -> tuple[str, ...]:
    return tuple(sorted(path for path in paths if _is_under(path, root)))


def _is_under(path: str, root: str) -> bool:
    return not root or path == root or path.startswith(f"{root}/")


def _join(root: str, relative: str) -> str:
    if not root:
        return _relative_path(relative, allow_root=True)
    if not relative:
        return root
    return PurePosixPath(root, relative).as_posix()


def _parent(path: str) -> str:
    parent = PurePosixPath(path).parent.as_posix()
    return "" if parent == "." else parent
