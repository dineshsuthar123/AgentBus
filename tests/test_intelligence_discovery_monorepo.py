from __future__ import annotations

import json
from pathlib import Path

from agentbus.intelligence import project_id, repository_identity
from agentbus.intelligence.discovery import (
    ProjectDetection,
    ProjectDiscovery,
    normalize_project_relationships,
)
from agentbus.intelligence.models import Project, ProjectKind


def test_discovers_mixed_monorepo_without_inventing_cross_language_links(
    tmp_path: Path,
) -> None:
    (tmp_path / "package.json").write_text(
        json.dumps(
            {
                "name": "mixed-root",
                "workspaces": ["apps/*"],
            }
        ),
        encoding="utf-8",
    )
    web = tmp_path / "apps" / "web"
    web.mkdir(parents=True)
    (web / "package.json").write_text(
        json.dumps({"name": "web"}),
        encoding="utf-8",
    )
    (web / "index.ts").write_text("export const web = true;\n", encoding="utf-8")

    service = tmp_path / "services" / "api"
    service.mkdir(parents=True)
    (service / "go.mod").write_text(
        "module example.com/api\n",
        encoding="utf-8",
    )
    (service / "main.go").write_text("package api\n", encoding="utf-8")

    library = tmp_path / "libraries" / "core"
    (library / "src" / "main" / "java").mkdir(parents=True)
    (library / "pom.xml").write_text(
        "<project><artifactId>core</artifactId></project>",
        encoding="utf-8",
    )
    (library / "src" / "main" / "java" / "Core.java").write_text(
        "class Core {}\n",
        encoding="utf-8",
    )

    result = ProjectDiscovery(
        tmp_path,
        repository_identity("example/mixed"),
    ).discover()
    projects = {(project.kind, project.root): project for project in result.projects}

    root_node = projects[(ProjectKind.NODE, "")]
    web_node = projects[(ProjectKind.NODE, "apps/web")]
    go_project = projects[(ProjectKind.GO, "services/api")]
    java_project = projects[(ProjectKind.JAVA, "libraries/core")]
    assert root_node.workspace_project_ids == (web_node.project_id,)
    assert web_node.workspace_project_ids == (root_node.project_id,)
    assert go_project.workspace_project_ids == ()
    assert java_project.workspace_project_ids == ()


def test_normalizes_explicit_links_and_drops_dangling_relationships(
    tmp_path: Path,
) -> None:
    repository = repository_identity("example/custom-detector")
    first_id = project_id(
        repository.repository_id,
        "one",
        ProjectKind.GENERIC,
        name="one",
    )
    second_id = project_id(
        repository.repository_id,
        "two",
        ProjectKind.GENERIC,
        name="two",
    )

    class CustomDetector:
        name = "custom"

        def detect(self, repository_identity_value, inventory):
            del repository_identity_value, inventory
            return ProjectDetection(
                projects=(
                    Project(
                        project_id=first_id,
                        repository_id=repository.repository_id,
                        name="one",
                        kind=ProjectKind.GENERIC,
                        root="one",
                        workspace_project_ids=(
                            first_id,
                            second_id,
                            "project_" + "f" * 64,
                        ),
                    ),
                    Project(
                        project_id=second_id,
                        repository_id=repository.repository_id,
                        name="two",
                        kind=ProjectKind.GENERIC,
                        root="two",
                    ),
                )
            )

    (tmp_path / "one").mkdir()
    (tmp_path / "two").mkdir()
    result = ProjectDiscovery(
        tmp_path,
        repository,
        detectors=(CustomDetector(),),
    ).discover()
    projects = {project.root: project for project in result.projects}

    assert projects["one"].workspace_project_ids == (second_id,)
    assert projects["two"].workspace_project_ids == (first_id,)
    assert {
        diagnostic.code
        for diagnostic in result.diagnostics
    } == {
        "discovery.project_link_missing",
        "discovery.project_self_link",
    }


def test_relationship_limit_preserves_bidirectional_links() -> None:
    repository = repository_identity("example/bounded-links")
    projects = tuple(
        Project(
            project_id=project_id(
                repository.repository_id,
                name,
                ProjectKind.GENERIC,
                name=name,
            ),
            repository_id=repository.repository_id,
            name=name,
            kind=ProjectKind.GENERIC,
            root=name,
        )
        for name in ("one", "two", "three")
    )
    connected = (
        projects[0].model_copy(
            update={
                "workspace_project_ids": (
                    projects[1].project_id,
                    projects[2].project_id,
                )
            }
        ),
        projects[1],
        projects[2],
    )

    normalized, diagnostics = normalize_project_relationships(
        connected,
        repository_id=repository.repository_id,
        maximum_relationships=1,
    )
    links = {
        project.project_id: set(project.workspace_project_ids)
        for project in normalized
    }

    assert all(
        source in links[target]
        for source, targets in links.items()
        for target in targets
    )
    assert all(len(targets) <= 1 for targets in links.values())
    assert any(
        item.code == "discovery.project_link_limit"
        for item in diagnostics
    )
