from __future__ import annotations

import re
from pathlib import PurePosixPath

from agentbus.intelligence.discovery.base import ProjectDetection
from agentbus.intelligence.discovery.scanner import RepositoryInventory
from agentbus.intelligence.errors import RepositoryIntelligenceError
from agentbus.intelligence.identities import project_id
from agentbus.intelligence.models import (
    DiagnosticSeverity,
    IndexDiagnostic,
    Project,
    ProjectKind,
    RepositoryIdentity,
)


_GO_MANIFEST_NAMES = {"go.mod", "go.work"}


class GoProjectDetector:
    name = "go"

    def detect(
        self,
        repository: RepositoryIdentity,
        inventory: RepositoryInventory,
    ) -> ProjectDetection:
        manifests_by_root = _manifest_roots(inventory)
        metadata_by_root: dict[str, _GoMetadata] = {}
        diagnostics: list[IndexDiagnostic] = []
        for root, manifests in sorted(manifests_by_root.items()):
            metadata, metadata_diagnostics = _read_metadata(
                tuple(sorted(manifests)),
                inventory,
            )
            metadata_by_root[root] = metadata
            diagnostics.extend(metadata_diagnostics)

        declared_roots: set[str] = set()
        for root, metadata in metadata_by_root.items():
            for path, source in metadata.related_paths.items():
                related_root = _resolve_repository_path(root, path)
                if related_root is None:
                    diagnostics.append(_invalid_path_diagnostic(source))
                elif _contains_file_under(inventory, related_root):
                    declared_roots.add(related_root)
        for root in declared_roots:
            manifests_by_root.setdefault(root, set())
            metadata_by_root.setdefault(root, _GoMetadata())

        project_roots = frozenset(manifests_by_root)
        projects = tuple(
            _make_project(
                repository,
                root,
                tuple(sorted(manifests_by_root[root])),
                metadata_by_root[root],
                project_roots,
                inventory,
            )
            for root in sorted(manifests_by_root)
        )
        return ProjectDetection(
            projects=_link_projects(projects, metadata_by_root),
            diagnostics=tuple(diagnostics),
        )


class _GoMetadata:
    def __init__(
        self,
        *,
        module_name: str | None = None,
        workspace_paths: tuple[str, ...] = (),
        replacement_paths: tuple[str, ...] = (),
        related_paths: dict[str, str] | None = None,
    ) -> None:
        self.module_name = module_name
        self.workspace_paths = workspace_paths
        self.replacement_paths = replacement_paths
        self.related_paths = related_paths or {}


def _manifest_roots(
    inventory: RepositoryInventory,
) -> dict[str, set[str]]:
    manifests_by_root: dict[str, set[str]] = {}
    for item in inventory.files:
        if PurePosixPath(item.relative_path).name not in _GO_MANIFEST_NAMES:
            continue
        manifests_by_root.setdefault(
            _parent(item.relative_path),
            set(),
        ).add(item.relative_path)
    return manifests_by_root


def _read_metadata(
    manifests: tuple[str, ...],
    inventory: RepositoryInventory,
) -> tuple[_GoMetadata, tuple[IndexDiagnostic, ...]]:
    module_name: str | None = None
    workspace_paths: list[str] = []
    replacement_paths: list[str] = []
    related_paths: dict[str, str] = {}
    diagnostics: list[IndexDiagnostic] = []
    for manifest in manifests:
        try:
            content = inventory.read_text(manifest)
            name = PurePosixPath(manifest).name
            if name == "go.mod":
                parsed_name, replacements = _parse_go_mod(content)
                module_name = module_name or parsed_name
                workspace = ()
            else:
                workspace, replacements = _parse_go_work(content)
            for value in workspace:
                if value not in related_paths:
                    workspace_paths.append(value)
                    related_paths[value] = manifest
            for value in replacements:
                if value not in related_paths:
                    replacement_paths.append(value)
                    related_paths[value] = manifest
        except (
            RecursionError,
            RepositoryIntelligenceError,
            UnicodeError,
            ValueError,
        ) as exc:
            diagnostics.append(
                IndexDiagnostic(
                    code="discovery.go_metadata_invalid",
                    severity=DiagnosticSeverity.WARNING,
                    message="Go project metadata could not be parsed safely.",
                    relative_path=manifest,
                    recoverable=True,
                    details={"error_type": type(exc).__name__},
                )
            )
    return (
        _GoMetadata(
            module_name=module_name,
            workspace_paths=tuple(workspace_paths),
            replacement_paths=tuple(replacement_paths),
            related_paths=related_paths,
        ),
        tuple(diagnostics),
    )


def _parse_go_mod(content: str) -> tuple[str | None, tuple[str, ...]]:
    sanitized = _strip_go_comments(content)
    module_entries = _directive_entries(sanitized, "module")
    module_name = (
        module_entries[0][0][:256]
        if module_entries and module_entries[0]
        else None
    )
    replacements = _local_replacements(
        _directive_entries(sanitized, "replace")
    )
    return module_name, replacements


def _parse_go_work(content: str) -> tuple[tuple[str, ...], tuple[str, ...]]:
    sanitized = _strip_go_comments(content)
    workspace_paths = tuple(
        entry[0]
        for entry in _directive_entries(sanitized, "use")[:256]
        if entry
    )
    replacements = _local_replacements(
        _directive_entries(sanitized, "replace")
    )
    return workspace_paths, replacements


def _directive_entries(
    content: str,
    directive: str,
) -> tuple[tuple[str, ...], ...]:
    entries: list[tuple[str, ...]] = []
    in_block = False
    for line in content.splitlines()[:100_000]:
        tokens = list(_tokenize_go_line(line))
        if not tokens:
            continue
        if in_block:
            closed = ")" in tokens
            values = tokens[: tokens.index(")")] if closed else tokens
            if values and len(entries) < 256:
                entries.append(tuple(values))
            if closed:
                in_block = False
            continue
        if tokens[0] != directive:
            continue
        values = tokens[1:]
        if values and values[0] == "(":
            values = values[1:]
            closed = ")" in values
            block_values = values[: values.index(")")] if closed else values
            if block_values and len(entries) < 256:
                entries.append(tuple(block_values))
            in_block = not closed
        elif values and len(entries) < 256:
            entries.append(tuple(values))
    if in_block:
        raise ValueError(f"unterminated {directive} block")
    return tuple(entries[:256])


def _tokenize_go_line(line: str) -> tuple[str, ...]:
    tokens: list[str] = []
    current: list[str] = []
    quote: str | None = None
    escaped = False
    index = 0
    while index < len(line):
        character = line[index]
        if quote is not None:
            if escaped:
                current.append(character)
                escaped = False
            elif quote == '"' and character == "\\":
                escaped = True
            elif character == quote:
                quote = None
            else:
                current.append(character)
            index += 1
            continue
        if character in {'"', "`"}:
            quote = character
            index += 1
            continue
        if character.isspace():
            _finish_token(tokens, current)
            index += 1
            continue
        if character in "()":
            _finish_token(tokens, current)
            tokens.append(character)
            index += 1
            continue
        if character == "=" and index + 1 < len(line) and line[index + 1] == ">":
            _finish_token(tokens, current)
            tokens.append("=>")
            index += 2
            continue
        current.append(character)
        index += 1
    if quote is not None or escaped:
        raise ValueError("unterminated Go metadata string")
    _finish_token(tokens, current)
    return tuple(tokens)


def _finish_token(tokens: list[str], current: list[str]) -> None:
    if current:
        tokens.append("".join(current))
        current.clear()


def _strip_go_comments(content: str) -> str:
    output: list[str] = []
    index = 0
    quote: str | None = None
    escaped = False
    while index < len(content):
        character = content[index]
        if quote is not None:
            output.append(character)
            if escaped:
                escaped = False
            elif quote == '"' and character == "\\":
                escaped = True
            elif character == quote:
                quote = None
            index += 1
            continue
        if character in {'"', "`"}:
            quote = character
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
                    raise ValueError("unterminated Go metadata block comment")
                continue
        output.append(character)
        index += 1
    if quote is not None or escaped:
        raise ValueError("unterminated Go metadata string")
    return "".join(output)


def _local_replacements(
    entries: tuple[tuple[str, ...], ...],
) -> tuple[str, ...]:
    paths: list[str] = []
    for entry in entries:
        if "=>" not in entry:
            continue
        marker = entry.index("=>")
        target = entry[marker + 1] if marker + 1 < len(entry) else ""
        if _looks_like_local_path(target) and target not in paths:
            paths.append(target)
    return tuple(paths)


def _looks_like_local_path(value: str) -> bool:
    return bool(
        value.startswith((".", "/", "\\"))
        or re.match(r"^[A-Za-z]:", value)
    )


def _make_project(
    repository: RepositoryIdentity,
    root: str,
    manifests: tuple[str, ...],
    metadata: _GoMetadata,
    project_roots: frozenset[str],
    inventory: RepositoryInventory,
) -> Project:
    fallback = (
        PurePosixPath(root).name[:256]
        if root
        else (
            "go-workspace"
            if any(
                PurePosixPath(path).name == "go.work"
                for path in manifests
            )
            else "go-project"
        )
    )
    name = metadata.module_name or fallback
    return Project(
        project_id=project_id(
            repository.repository_id,
            root,
            ProjectKind.GO,
            name=name,
        ),
        repository_id=repository.repository_id,
        name=name,
        kind=ProjectKind.GO,
        root=root,
        source_roots=(
            (root,)
            if _owned_go_files(
                root,
                project_roots,
                inventory,
                tests=False,
            )
            else ()
        ),
        test_roots=tuple(
            sorted(
                {
                    _parent(path)
                    for path in _owned_go_files(
                        root,
                        project_roots,
                        inventory,
                        tests=True,
                    )
                }
            )[:128]
        ),
        generated_roots=_under_root(inventory.generated_roots, root),
        manifest_paths=manifests,
    )


def _owned_go_files(
    root: str,
    project_roots: frozenset[str],
    inventory: RepositoryInventory,
    *,
    tests: bool,
) -> tuple[str, ...]:
    nested_roots = tuple(
        candidate
        for candidate in project_roots
        if candidate != root and _is_under(candidate, root)
    )
    return tuple(
        item.relative_path
        for item in inventory.files
        if item.relative_path.endswith("_test.go") == tests
        and item.relative_path.endswith(".go")
        and _is_under(item.relative_path, root)
        and not any(
            _is_under(item.relative_path, nested)
            for nested in nested_roots
        )
    )


def _link_projects(
    projects: tuple[Project, ...],
    metadata_by_root: dict[str, _GoMetadata],
) -> tuple[Project, ...]:
    by_root = {project.root: project for project in projects}
    links = {project.project_id: set() for project in projects}
    for root, metadata in metadata_by_root.items():
        parent = by_root.get(root)
        if parent is None:
            continue
        for path in (
            *metadata.workspace_paths,
            *metadata.replacement_paths,
        ):
            related_root = _resolve_repository_path(root, path)
            related = (
                by_root.get(related_root)
                if related_root is not None
                else None
            )
            if related is None or related.project_id == parent.project_id:
                continue
            links[parent.project_id].add(related.project_id)
            links[related.project_id].add(parent.project_id)
    return tuple(
        Project.model_validate(
            project.model_copy(
                update={
                    "workspace_project_ids": tuple(
                        sorted(links[project.project_id])
                    )
                }
            ).model_dump(mode="python")
        )
        for project in projects
    )


def _invalid_path_diagnostic(relative_path: str) -> IndexDiagnostic:
    return IndexDiagnostic(
        code="discovery.go_module_path_invalid",
        severity=DiagnosticSeverity.WARNING,
        message="A Go workspace path escaped the repository and was ignored.",
        relative_path=relative_path,
        recoverable=True,
    )


def _resolve_repository_path(root: str, value: str) -> str | None:
    normalized = value.replace("\\", "/").strip()
    if (
        not normalized
        or normalized.startswith("/")
        or re.match(r"^[A-Za-z]:", normalized)
        or "\x00" in normalized
        or any(character in normalized for character in "*?[]")
    ):
        return None
    parts = list(PurePosixPath(root).parts) if root else []
    for part in normalized.split("/"):
        if part in {"", "."}:
            continue
        if part == "..":
            if not parts:
                return None
            parts.pop()
            continue
        parts.append(part)
        if len(parts) > 256:
            return None
    result = PurePosixPath(*parts).as_posix() if parts else ""
    return result if len(result) <= 4_096 else None


def _contains_file_under(
    inventory: RepositoryInventory,
    root: str,
) -> bool:
    return any(_is_under(item.relative_path, root) for item in inventory.files)


def _under_root(paths: tuple[str, ...], root: str) -> tuple[str, ...]:
    return tuple(sorted(path for path in paths if _is_under(path, root)))


def _is_under(path: str, root: str) -> bool:
    return not root or path == root or path.startswith(f"{root}/")


def _parent(path: str) -> str:
    parent = PurePosixPath(path).parent.as_posix()
    return "" if parent == "." else parent
