from __future__ import annotations

import re
import xml.etree.ElementTree as ElementTree
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


_JAVA_MANIFEST_NAMES = {
    "build.gradle",
    "build.gradle.kts",
    "pom.xml",
    "settings.gradle",
    "settings.gradle.kts",
}
_GRADLE_SETTINGS_NAMES = {
    "settings.gradle",
    "settings.gradle.kts",
}
_GRADLE_NAME_PATTERN = re.compile(
    r"""(?m)^\s*rootProject\s*\.\s*name\s*=\s*["']([^"'\r\n]{1,256})["']"""
)
_GRADLE_PROJECT_DIRECTORY_PATTERN = re.compile(
    r"""project\s*\(\s*["']([^"'\r\n]{1,512})["']\s*\)"""
    r"""\s*\.\s*projectDir\s*=\s*file\s*\(\s*["']([^"'\r\n]{1,2048})["']"""
    r"""\s*\)"""
)
_QUOTED_VALUE_PATTERN = re.compile(r"""["']([^"'\r\n]{1,2048})["']""")


class JavaProjectDetector:
    name = "java"

    def detect(
        self,
        repository: RepositoryIdentity,
        inventory: RepositoryInventory,
    ) -> ProjectDetection:
        manifests_by_root = _manifest_roots(inventory)
        metadata_by_root: dict[str, _JavaMetadata] = {}
        diagnostics: list[IndexDiagnostic] = []
        for root, manifests in sorted(manifests_by_root.items()):
            metadata, metadata_diagnostics = _read_metadata(
                root,
                tuple(sorted(manifests)),
                inventory,
            )
            metadata_by_root[root] = metadata
            diagnostics.extend(metadata_diagnostics)

        declared_roots: set[str] = set()
        for root, metadata in metadata_by_root.items():
            for relative_module in metadata.modules:
                module_root = _resolve_repository_path(root, relative_module)
                if module_root is None:
                    diagnostics.append(
                        _invalid_module_diagnostic(
                            metadata.module_sources.get(
                                relative_module,
                                _join(root, "pom.xml"),
                            )
                        )
                    )
                elif _contains_file_under(inventory, module_root):
                    declared_roots.add(module_root)
        for root in declared_roots:
            manifests_by_root.setdefault(root, set())
            metadata_by_root.setdefault(root, _JavaMetadata())

        projects = tuple(
            _make_project(
                repository,
                root,
                tuple(sorted(manifests_by_root[root])),
                metadata_by_root[root],
                inventory,
            )
            for root in sorted(manifests_by_root)
        )
        return ProjectDetection(
            projects=_link_modules(projects, metadata_by_root),
            diagnostics=tuple(diagnostics),
        )


class _JavaMetadata:
    def __init__(
        self,
        *,
        name: str | None = None,
        modules: tuple[str, ...] = (),
        module_sources: dict[str, str] | None = None,
    ) -> None:
        self.name = name
        self.modules = modules
        self.module_sources = module_sources or {}


def _manifest_roots(
    inventory: RepositoryInventory,
) -> dict[str, set[str]]:
    manifests_by_root: dict[str, set[str]] = {}
    for item in inventory.files:
        path = PurePosixPath(item.relative_path)
        if path.name in _JAVA_MANIFEST_NAMES:
            manifests_by_root.setdefault(
                _parent(item.relative_path),
                set(),
            ).add(item.relative_path)
    return manifests_by_root


def _read_metadata(
    root: str,
    manifests: tuple[str, ...],
    inventory: RepositoryInventory,
) -> tuple[_JavaMetadata, tuple[IndexDiagnostic, ...]]:
    name: str | None = None
    modules: list[str] = []
    module_sources: dict[str, str] = {}
    diagnostics: list[IndexDiagnostic] = []
    for manifest in manifests:
        manifest_name = PurePosixPath(manifest).name
        if manifest_name != "pom.xml" and manifest_name not in _GRADLE_SETTINGS_NAMES:
            continue
        try:
            content = inventory.read_text(manifest)
            if manifest_name == "pom.xml":
                parsed_name, parsed_modules = _parse_pom(content)
                name = name or parsed_name
                discovered = parsed_modules
            else:
                parsed_name, discovered = _parse_gradle_settings(content)
                name = parsed_name or name
            for module in discovered:
                if module not in module_sources:
                    modules.append(module)
                    module_sources[module] = manifest
        except (
            ElementTree.ParseError,
            RecursionError,
            RepositoryIntelligenceError,
            UnicodeError,
            ValueError,
        ) as exc:
            diagnostics.append(
                IndexDiagnostic(
                    code="discovery.java_metadata_invalid",
                    severity=DiagnosticSeverity.WARNING,
                    message="Java project metadata could not be parsed safely.",
                    relative_path=manifest,
                    recoverable=True,
                    details={"error_type": type(exc).__name__},
                )
            )
    return (
        _JavaMetadata(
            name=name,
            modules=tuple(modules),
            module_sources=module_sources,
        ),
        tuple(diagnostics),
    )


def _parse_pom(content: str) -> tuple[str | None, tuple[str, ...]]:
    lowered = content.casefold()
    if "<!doctype" in lowered or "<!entity" in lowered:
        raise ValueError("XML declarations with external expansion are not allowed")
    root = ElementTree.fromstring(content)
    name = _first_child_text(root, "artifactId")
    modules_element = _first_child(root, "modules")
    modules: list[str] = []
    if modules_element is not None:
        for child in tuple(modules_element)[:256]:
            if _local_name(child.tag) != "module":
                continue
            value = (child.text or "").strip()
            if value and len(value) <= 2_048:
                modules.append(value)
    return name, tuple(modules)


def _parse_gradle_settings(
    content: str,
) -> tuple[str | None, tuple[str, ...]]:
    sanitized = _strip_gradle_comments(content)
    name_match = _GRADLE_NAME_PATTERN.search(sanitized)
    name = name_match.group(1).strip() if name_match else None
    module_paths: list[str] = []
    for statement in _gradle_statements(sanitized):
        stripped = statement.strip()
        if not re.match(r"^include(?:\s|\()", stripped):
            continue
        for match in _QUOTED_VALUE_PATTERN.finditer(stripped):
            normalized = _gradle_project_path(match.group(1))
            if normalized and normalized not in module_paths:
                module_paths.append(normalized)
    directory_overrides = {
        _gradle_project_path(match.group(1)): match.group(2)
        for match in _GRADLE_PROJECT_DIRECTORY_PATTERN.finditer(sanitized)
    }
    return (
        name,
        tuple(directory_overrides.get(module, module) for module in module_paths),
    )


def _gradle_statements(content: str) -> tuple[str, ...]:
    statements: list[str] = []
    current: list[str] = []
    depth = 0
    quote: str | None = None
    escaped = False
    for character in content:
        current.append(character)
        if quote is not None:
            if escaped:
                escaped = False
            elif character == "\\":
                escaped = True
            elif character == quote:
                quote = None
            continue
        if character in {"'", '"'}:
            quote = character
        elif character == "(":
            depth += 1
        elif character == ")":
            depth = max(0, depth - 1)
        elif character in {"\n", ";"} and depth == 0:
            statements.append("".join(current))
            current = []
    if current:
        statements.append("".join(current))
    return tuple(statements[:512])


def _strip_gradle_comments(content: str) -> str:
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
            elif character == "\\":
                escaped = True
            elif character == quote:
                quote = None
            index += 1
            continue
        if character in {"'", '"'}:
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
                    raise ValueError("unterminated Gradle block comment")
                continue
        output.append(character)
        index += 1
    if quote is not None:
        raise ValueError("unterminated Gradle string")
    return "".join(output)


def _make_project(
    repository: RepositoryIdentity,
    root: str,
    manifests: tuple[str, ...],
    metadata: _JavaMetadata,
    inventory: RepositoryInventory,
) -> Project:
    name = metadata.name or (
        PurePosixPath(root).name[:256] if root else "java-project"
    )
    return Project(
        project_id=project_id(
            repository.repository_id,
            root,
            ProjectKind.JAVA,
            name=name,
        ),
        repository_id=repository.repository_id,
        name=name,
        kind=ProjectKind.JAVA,
        root=root,
        source_roots=_java_roots(root, inventory, test=False),
        test_roots=_java_roots(root, inventory, test=True),
        generated_roots=_under_root(inventory.generated_roots, root),
        manifest_paths=manifests,
    )


def _java_roots(
    root: str,
    inventory: RepositoryInventory,
    *,
    test: bool,
) -> tuple[str, ...]:
    standard = _join(root, "src/test/java" if test else "src/main/java")
    if _contains_java_under(inventory, standard):
        return (standard,)
    direct_directories = {
        _parent(item.relative_path)
        for item in inventory.files
        if item.relative_path.endswith(".java")
        and item.test is test
        and _parent(item.relative_path) == root
    }
    return tuple(sorted(direct_directories))


def _link_modules(
    projects: tuple[Project, ...],
    metadata_by_root: dict[str, _JavaMetadata],
) -> tuple[Project, ...]:
    by_root = {project.root: project for project in projects}
    links = {project.project_id: set() for project in projects}
    for root, metadata in metadata_by_root.items():
        parent = by_root.get(root)
        if parent is None:
            continue
        for module in metadata.modules:
            child_root = _resolve_repository_path(root, module)
            child = by_root.get(child_root) if child_root is not None else None
            if child is None or child.project_id == parent.project_id:
                continue
            links[parent.project_id].add(child.project_id)
            links[child.project_id].add(parent.project_id)
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


def _invalid_module_diagnostic(relative_path: str) -> IndexDiagnostic:
    return IndexDiagnostic(
        code="discovery.java_module_path_invalid",
        severity=DiagnosticSeverity.WARNING,
        message="A Java module path escaped the repository and was ignored.",
        relative_path=relative_path,
        recoverable=True,
    )


def _first_child(
    element: ElementTree.Element,
    name: str,
) -> ElementTree.Element | None:
    return next(
        (child for child in element if _local_name(child.tag) == name),
        None,
    )


def _first_child_text(
    element: ElementTree.Element,
    name: str,
) -> str | None:
    child = _first_child(element, name)
    value = (child.text or "").strip() if child is not None else ""
    return value[:256] if value else None


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _gradle_project_path(value: str) -> str:
    return value.strip().strip(":").replace(":", "/")


def _resolve_repository_path(root: str, value: str) -> str | None:
    normalized = value.replace("\\", "/").strip()
    if (
        not normalized
        or normalized.startswith("/")
        or re.match(r"^[A-Za-z]:", normalized)
        or "\x00" in normalized
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
        if any(character in part for character in "*?[]"):
            return None
        parts.append(part)
        if len(parts) > 256:
            return None
    result = PurePosixPath(*parts).as_posix() if parts else ""
    return result if len(result) <= 4_096 else None


def _contains_java_under(
    inventory: RepositoryInventory,
    root: str,
) -> bool:
    return any(
        item.relative_path.endswith(".java")
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
    return (
        PurePosixPath(root, relative).as_posix()
        if root
        else PurePosixPath(relative).as_posix()
    )


def _parent(path: str) -> str:
    parent = PurePosixPath(path).parent.as_posix()
    return "" if parent == "." else parent
