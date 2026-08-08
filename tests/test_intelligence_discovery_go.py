from __future__ import annotations

from pathlib import Path

from agentbus.intelligence import repository_identity
from agentbus.intelligence.discovery import ProjectDiscovery
from agentbus.intelligence.models import ProjectKind


def test_discovers_go_workspace_modules_and_owned_roots(
    tmp_path: Path,
) -> None:
    (tmp_path / "go.work").write_text(
        """
go 1.22

use (
    ./services/api
    "./services/worker"
)
""".strip(),
        encoding="utf-8",
    )
    for name in ("api", "worker"):
        root = tmp_path / "services" / name
        root.mkdir(parents=True)
        (root / "go.mod").write_text(
            f"module example.com/{name}\n\ngo 1.22\n",
            encoding="utf-8",
        )
        (root / "main.go").write_text(
            f"package {name}\n\nfunc Run() {{}}\n",
            encoding="utf-8",
        )
    api = tmp_path / "services" / "api"
    (api / "handlers").mkdir()
    (api / "handlers" / "handler_test.go").write_text(
        "package handlers\n\nfunc TestHandler(t *testing.T) {}\n",
        encoding="utf-8",
    )
    (api / "build").mkdir()
    (api / "build" / "generated.go").write_text(
        "package generated\n",
        encoding="utf-8",
    )

    result = ProjectDiscovery(
        tmp_path,
        repository_identity("example/go-workspace"),
    ).discover()
    projects = {
        project.root: project
        for project in result.projects
        if project.kind == ProjectKind.GO
    }

    assert tuple(projects) == ("", "services/api", "services/worker")
    assert projects[""].name == "go-workspace"
    assert projects[""].source_roots == ()
    assert projects["services/api"].name == "example.com/api"
    assert projects["services/api"].source_roots == ("services/api",)
    assert projects["services/api"].test_roots == ("services/api/handlers",)
    assert projects["services/api"].generated_roots == (
        "services/api/build",
    )
    children = {
        projects["services/api"].project_id,
        projects["services/worker"].project_id,
    }
    assert set(projects[""].workspace_project_ids) == children
    assert projects[""].project_id in projects[
        "services/worker"
    ].workspace_project_ids


def test_links_local_go_module_replacements(tmp_path: Path) -> None:
    api = tmp_path / "services" / "api"
    shared = tmp_path / "libraries" / "shared"
    api.mkdir(parents=True)
    shared.mkdir(parents=True)
    (api / "go.mod").write_text(
        """
module example.com/api

replace example.com/shared => ../../libraries/shared
""".strip(),
        encoding="utf-8",
    )
    (shared / "go.mod").write_text(
        "module example.com/shared\n",
        encoding="utf-8",
    )
    (api / "main.go").write_text("package api\n", encoding="utf-8")
    (shared / "shared.go").write_text("package shared\n", encoding="utf-8")

    result = ProjectDiscovery(
        tmp_path,
        repository_identity("example/go-replace"),
    ).discover()
    projects = {
        project.root: project
        for project in result.projects
        if project.kind == ProjectKind.GO
    }

    assert projects["libraries/shared"].project_id in projects[
        "services/api"
    ].workspace_project_ids
    assert projects["services/api"].project_id in projects[
        "libraries/shared"
    ].workspace_project_ids


def test_rejects_escaping_go_work_paths_but_keeps_safe_modules(
    tmp_path: Path,
) -> None:
    (tmp_path / "go.work").write_text(
        """
go 1.22
use (
    ../outside
    ./inside
)
replace example.com/outside => /outside
""".strip(),
        encoding="utf-8",
    )
    (tmp_path / "inside").mkdir()
    (tmp_path / "inside" / "main.go").write_text(
        "package inside\n",
        encoding="utf-8",
    )

    result = ProjectDiscovery(
        tmp_path,
        repository_identity("example/safe-go-work"),
    ).discover()
    projects = {
        project.root: project
        for project in result.projects
        if project.kind == ProjectKind.GO
    }

    assert tuple(projects) == ("", "inside")
    assert projects["inside"].source_roots == ("inside",)
    assert any(
        item.code == "discovery.go_module_path_invalid"
        for item in result.diagnostics
    )
    assert sum(
        item.code == "discovery.go_module_path_invalid"
        for item in result.diagnostics
    ) == 2


def test_invalid_go_metadata_is_recoverable_and_never_executed(
    tmp_path: Path,
) -> None:
    (tmp_path / "go.mod").write_text(
        'module "unterminated\n',
        encoding="utf-8",
    )
    (tmp_path / "main.go").write_text(
        'package main\n\nfunc init() { panic("must not run") }\n',
        encoding="utf-8",
    )

    result = ProjectDiscovery(
        tmp_path,
        repository_identity("example/broken-go"),
    ).discover()
    projects = [
        project
        for project in result.projects
        if project.kind == ProjectKind.GO
    ]

    assert len(projects) == 1
    assert projects[0].name == "go-project"
    assert projects[0].source_roots == ("",)
    assert any(
        item.code == "discovery.go_metadata_invalid"
        for item in result.diagnostics
    )


def test_large_go_workspace_is_bounded_without_false_syntax_error(
    tmp_path: Path,
) -> None:
    entries = "\n".join(f"    ./modules/module-{index}" for index in range(300))
    (tmp_path / "go.work").write_text(
        f"go 1.22\nuse (\n{entries}\n)\n",
        encoding="utf-8",
    )

    result = ProjectDiscovery(
        tmp_path,
        repository_identity("example/large-go-work"),
    ).discover()

    assert not any(
        item.code == "discovery.go_metadata_invalid"
        for item in result.diagnostics
    )
    assert [
        project.name
        for project in result.projects
        if project.kind == ProjectKind.GO
    ] == ["go-workspace"]
