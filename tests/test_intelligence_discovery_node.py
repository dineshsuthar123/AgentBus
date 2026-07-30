from __future__ import annotations

import json
from pathlib import Path

from agentbus.intelligence import repository_identity
from agentbus.intelligence.discovery import ProjectDiscovery
from agentbus.intelligence.models import ProjectKind


def test_discovers_node_workspace_projects_and_relationships(
    tmp_path: Path,
) -> None:
    _write_json(
        tmp_path / "package.json",
        {
            "name": "sample-monorepo",
            "private": True,
            "workspaces": ["packages/*"],
            "scripts": {"postinstall": "must-not-run"},
        },
    )
    for name in ("api", "web"):
        root = tmp_path / "packages" / name
        root.mkdir(parents=True)
        _write_json(root / "package.json", {"name": f"@sample/{name}"})
        (root / "src").mkdir()
        (root / "src" / "index.ts").write_text(
            f"export const name = '{name}';\n",
            encoding="utf-8",
        )
    (tmp_path / "packages" / "web" / "tests").mkdir()
    (tmp_path / "packages" / "web" / "tests" / "page.test.ts").write_text(
        "test('page', () => {});\n",
        encoding="utf-8",
    )
    (tmp_path / "node_modules").mkdir()
    (tmp_path / "node_modules" / "unsafe.js").write_text(
        "throw new Error();\n",
        encoding="utf-8",
    )

    result = ProjectDiscovery(
        tmp_path,
        repository_identity("example/node-monorepo"),
    ).discover()
    projects = {project.root: project for project in result.projects}

    assert tuple(projects) == ("", "packages/api", "packages/web")
    assert all(project.kind == ProjectKind.NODE for project in projects.values())
    assert projects[""].name == "sample-monorepo"
    assert projects[""].source_roots == ()
    assert projects["packages/api"].source_roots == ("packages/api/src",)
    assert projects["packages/web"].test_roots == ("packages/web/tests",)
    child_ids = {
        projects["packages/api"].project_id,
        projects["packages/web"].project_id,
    }
    assert set(projects[""].workspace_project_ids) == child_ids
    assert projects[""].project_id in projects["packages/api"].workspace_project_ids
    assert result.generated_roots == ("node_modules",)
    assert not (tmp_path / "must-not-run").exists()


def test_discovers_jsonc_typescript_project_without_package_json(
    tmp_path: Path,
) -> None:
    (tmp_path / "apps" / "dashboard" / "client").mkdir(parents=True)
    (tmp_path / "apps" / "dashboard" / "client" / "main.tsx").write_text(
        "export const App = () => null;\n",
        encoding="utf-8",
    )
    (tmp_path / "apps" / "dashboard" / "tsconfig.json").write_text(
        """
{
  // JSONC comments and trailing commas are supported without TypeScript.
  "compilerOptions": {
    "rootDir": "client",
  },
  "include": ["client/**/*"],
}
""".strip(),
        encoding="utf-8",
    )

    result = ProjectDiscovery(
        tmp_path,
        repository_identity("example/typescript"),
    ).discover()

    assert len(result.projects) == 1
    project = result.projects[0]
    assert project.root == "apps/dashboard"
    assert project.name == "dashboard"
    assert project.source_roots == ("apps/dashboard/client",)
    assert project.manifest_paths == ("apps/dashboard/tsconfig.json",)


def test_invalid_package_json_is_recoverable_and_never_executed(
    tmp_path: Path,
) -> None:
    (tmp_path / "package.json").write_text(
        '{"name": "broken", "scripts": {"test": "touch executed"},}',
        encoding="utf-8",
    )
    (tmp_path / "index.js").write_text("module.exports = 1;\n", encoding="utf-8")

    result = ProjectDiscovery(
        tmp_path,
        repository_identity("example/broken-node"),
    ).discover()

    assert result.projects[0].name == "node-project"
    assert result.projects[0].source_roots == ("",)
    assert not (tmp_path / "executed").exists()
    assert any(
        item.code == "discovery.node_metadata_invalid"
        for item in result.diagnostics
    )


def test_workspace_patterns_cannot_traverse_repository_root(
    tmp_path: Path,
) -> None:
    _write_json(
        tmp_path / "package.json",
        {
            "name": "safe-root",
            "workspaces": ["../outside/*", "packages/*", "!packages/private"],
        },
    )
    for name in ("public", "private"):
        root = tmp_path / "packages" / name
        root.mkdir(parents=True)
        _write_json(root / "package.json", {"name": name})

    result = ProjectDiscovery(
        tmp_path,
        repository_identity("example/safe-workspaces"),
    ).discover()
    projects = {project.root: project for project in result.projects}

    assert projects[""].workspace_project_ids == (
        projects["packages/public"].project_id,
    )
    assert projects["packages/private"].workspace_project_ids == ()


def _write_json(path: Path, value: object) -> None:
    path.write_text(
        json.dumps(value, indent=2, sort_keys=True),
        encoding="utf-8",
    )
