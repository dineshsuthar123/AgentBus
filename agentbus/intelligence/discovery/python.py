from __future__ import annotations

import ast
import configparser
import re
import tomllib
from pathlib import PurePosixPath
from typing import Any

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
    _relative_path,
)


_PYTHON_MANIFEST_NAMES = {
    "pyproject.toml",
    "setup.cfg",
    "setup.py",
}
_REQUIREMENTS_NAME = re.compile(r"^requirements(?:[-_.][a-z0-9_.-]+)?\.txt$")


class PythonProjectDetector:
    name = "python"

    def detect(
        self,
        repository: RepositoryIdentity,
        inventory: RepositoryInventory,
    ) -> ProjectDetection:
        roots: dict[str, set[str]] = {}
        for item in inventory.files:
            path = PurePosixPath(item.relative_path)
            root = path.parent.as_posix()
            if root == ".":
                root = ""
            if path.name in _PYTHON_MANIFEST_NAMES:
                roots.setdefault(root, set()).add(item.relative_path)
            elif _is_requirements_file(path):
                requirement_root = _requirements_root(path)
                roots.setdefault(requirement_root, set()).add(item.relative_path)

        projects: list[Project] = []
        diagnostics: list[IndexDiagnostic] = []
        for root, manifests in sorted(roots.items()):
            metadata, metadata_diagnostics = self._metadata(
                root,
                tuple(sorted(manifests)),
                inventory,
            )
            diagnostics.extend(metadata_diagnostics)
            name = _project_name(root, metadata)
            source_roots = _source_roots(root, inventory, metadata)
            test_roots = _test_roots(root, inventory, metadata)
            generated_roots = _under_root(inventory.generated_roots, root)
            projects.append(
                Project(
                    project_id=project_id(
                        repository.repository_id,
                        root,
                        ProjectKind.PYTHON,
                        name=name,
                    ),
                    repository_id=repository.repository_id,
                    name=name,
                    kind=ProjectKind.PYTHON,
                    root=root,
                    source_roots=source_roots,
                    test_roots=test_roots,
                    generated_roots=generated_roots,
                    manifest_paths=tuple(sorted(manifests)),
                )
            )
        return ProjectDetection(
            projects=tuple(projects),
            diagnostics=tuple(diagnostics),
        )

    def _metadata(
        self,
        root: str,
        manifests: tuple[str, ...],
        inventory: RepositoryInventory,
    ) -> tuple[dict[str, Any], tuple[IndexDiagnostic, ...]]:
        metadata: dict[str, Any] = {}
        diagnostics: list[IndexDiagnostic] = []
        for manifest in manifests:
            name = PurePosixPath(manifest).name
            try:
                text = inventory.read_text(manifest)
                if name == "pyproject.toml":
                    parsed = tomllib.loads(text)
                    metadata["pyproject"] = parsed
                elif name == "setup.cfg":
                    metadata["setup_cfg_name"] = _setup_cfg_name(text)
                elif name == "setup.py":
                    metadata["setup_py_name"] = _setup_py_name(text)
            except (
                OSError,
                UnicodeError,
                ValueError,
                RecursionError,
                SyntaxError,
                configparser.Error,
                RepositoryIntelligenceError,
            ) as exc:
                diagnostics.append(
                    IndexDiagnostic(
                        code="discovery.python_metadata_invalid",
                        severity=DiagnosticSeverity.WARNING,
                        message="Python project metadata could not be parsed safely.",
                        relative_path=manifest,
                        recoverable=True,
                        details={"error_type": type(exc).__name__},
                    )
                )
        return metadata, tuple(diagnostics)


def _requirements_root(path: PurePosixPath) -> str:
    parent = path.parent
    if parent.name.casefold() in {"requirement", "requirements"}:
        parent = parent.parent
    return "" if parent.as_posix() == "." else parent.as_posix()


def _is_requirements_file(path: PurePosixPath) -> bool:
    return bool(
        _REQUIREMENTS_NAME.fullmatch(path.name.casefold())
        or (
            path.parent.name.casefold() in {"requirement", "requirements"}
            and path.suffix.casefold() in {".in", ".txt"}
        )
    )


def _project_name(root: str, metadata: dict[str, Any]) -> str:
    pyproject = metadata.get("pyproject")
    if isinstance(pyproject, dict):
        project = pyproject.get("project")
        if isinstance(project, dict):
            value = project.get("name")
            if isinstance(value, str) and value.strip():
                return value.strip()[:256]
        tool = pyproject.get("tool")
        if isinstance(tool, dict):
            poetry = tool.get("poetry")
            if isinstance(poetry, dict):
                value = poetry.get("name")
                if isinstance(value, str) and value.strip():
                    return value.strip()[:256]
    for key in ("setup_cfg_name", "setup_py_name"):
        value = metadata.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()[:256]
    return PurePosixPath(root).name[:256] if root else "python-project"


def _source_roots(
    root: str,
    inventory: RepositoryInventory,
    metadata: dict[str, Any],
) -> tuple[str, ...]:
    candidates: set[str] = set()
    configured = _configured_source_roots(root, metadata)
    candidates.update(
        candidate
        for candidate in configured
        if _contains_file_under(inventory, candidate)
    )
    src = _join(root, "src")
    if _contains_python_under(inventory, src):
        candidates.add(src)
    direct_packages: set[str] = set()
    for item in inventory.files:
        path = item.relative_path
        if not path.endswith("/__init__.py"):
            continue
        parent = PurePosixPath(path).parent.as_posix()
        if _parent(parent) == root:
            direct_packages.add(parent)
    candidates.update(direct_packages)
    if any(
        _parent(item.relative_path) == root
        and item.relative_path.endswith(".py")
        for item in inventory.files
    ):
        candidates.add(root)
    if not candidates:
        candidates.add(root)
    return tuple(sorted(candidates))


def _test_roots(
    root: str,
    inventory: RepositoryInventory,
    metadata: dict[str, Any],
) -> tuple[str, ...]:
    candidates: set[str] = set()
    for name in ("test", "tests"):
        candidate = _join(root, name)
        if _contains_file_under(inventory, candidate):
            candidates.add(candidate)
    pyproject = metadata.get("pyproject")
    if isinstance(pyproject, dict):
        tool = pyproject.get("tool")
        pytest_config = (
            tool.get("pytest", {}).get("ini_options", {})
            if isinstance(tool, dict)
            and isinstance(tool.get("pytest"), dict)
            else {}
        )
        configured = pytest_config.get("testpaths", ())
        if isinstance(configured, str):
            configured = (configured,)
        if isinstance(configured, (list, tuple)):
            for value in configured[:128]:
                if not isinstance(value, str):
                    continue
                try:
                    candidate = _join(root, _relative_path(value))
                except ValueError:
                    continue
                if _contains_file_under(inventory, candidate):
                    candidates.add(candidate)
    return tuple(sorted(candidates))


def _configured_source_roots(
    root: str,
    metadata: dict[str, Any],
) -> tuple[str, ...]:
    pyproject = metadata.get("pyproject")
    if not isinstance(pyproject, dict):
        return ()
    candidates: set[str] = set()
    tool = pyproject.get("tool")
    if not isinstance(tool, dict):
        return ()
    setuptools = tool.get("setuptools")
    if isinstance(setuptools, dict):
        package_dir = setuptools.get("package-dir")
        if isinstance(package_dir, dict):
            for value in tuple(package_dir.values())[:128]:
                _add_configured_root(candidates, root, value)
    poetry = tool.get("poetry")
    if isinstance(poetry, dict):
        packages = poetry.get("packages")
        if isinstance(packages, list):
            for package in packages[:128]:
                if not isinstance(package, dict):
                    continue
                base = package.get("from")
                include = package.get("include")
                _add_configured_root(candidates, root, base or include)
    return tuple(sorted(candidates))


def _add_configured_root(
    candidates: set[str],
    root: str,
    value: object,
) -> None:
    if not isinstance(value, str) or not value.strip():
        return
    try:
        candidates.add(_join(root, _relative_path(value, allow_root=True)))
    except ValueError:
        return


def _setup_cfg_name(content: str) -> str | None:
    parser = configparser.ConfigParser(
        interpolation=None,
        strict=False,
    )
    parser.read_string(content)
    return parser.get("metadata", "name", fallback=None)


def _setup_py_name(content: str) -> str | None:
    tree = ast.parse(content, mode="exec")
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not _is_setup_call(node.func):
            continue
        for keyword in node.keywords:
            if (
                keyword.arg == "name"
                and isinstance(keyword.value, ast.Constant)
                and isinstance(keyword.value.value, str)
            ):
                return keyword.value.value
    return None


def _is_setup_call(node: ast.expr) -> bool:
    return bool(
        isinstance(node, ast.Name)
        and node.id == "setup"
        or isinstance(node, ast.Attribute)
        and node.attr == "setup"
    )


def _contains_python_under(
    inventory: RepositoryInventory,
    root: str,
) -> bool:
    return any(
        item.relative_path.endswith(".py")
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
