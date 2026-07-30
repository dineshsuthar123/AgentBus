from __future__ import annotations

from pathlib import Path

from agentbus.intelligence import repository_identity
from agentbus.intelligence.discovery import ProjectDiscovery
from agentbus.intelligence.models import ProjectKind


def test_discovers_maven_modules_and_standard_roots(tmp_path: Path) -> None:
    (tmp_path / "pom.xml").write_text(
        """
<project xmlns="http://maven.apache.org/POM/4.0.0">
  <artifactId>sample-parent</artifactId>
  <modules>
    <module>services/api</module>
    <module>services/worker</module>
  </modules>
</project>
""".strip(),
        encoding="utf-8",
    )
    for name in ("api", "worker"):
        root = tmp_path / "services" / name
        (root / "src" / "main" / "java" / "example").mkdir(parents=True)
        (root / "pom.xml").write_text(
            f"<project><artifactId>{name}</artifactId></project>",
            encoding="utf-8",
        )
        (root / "src" / "main" / "java" / "example" / "App.java").write_text(
            "package example; class App {}\n",
            encoding="utf-8",
        )
    api = tmp_path / "services" / "api"
    (api / "src" / "test" / "java" / "example").mkdir(parents=True)
    (api / "src" / "test" / "java" / "example" / "AppTest.java").write_text(
        "package example; class AppTest {}\n",
        encoding="utf-8",
    )
    (api / "target").mkdir()
    (api / "target" / "Generated.java").write_text(
        "class Generated {}\n",
        encoding="utf-8",
    )

    result = ProjectDiscovery(
        tmp_path,
        repository_identity("example/maven"),
    ).discover()
    projects = {
        project.root: project
        for project in result.projects
        if project.kind == ProjectKind.JAVA
    }

    assert tuple(projects) == ("", "services/api", "services/worker")
    assert projects[""].name == "sample-parent"
    assert projects[""].source_roots == ()
    assert projects["services/api"].source_roots == (
        "services/api/src/main/java",
    )
    assert projects["services/api"].test_roots == (
        "services/api/src/test/java",
    )
    assert projects["services/api"].generated_roots == (
        "services/api/target",
    )
    children = {
        projects["services/api"].project_id,
        projects["services/worker"].project_id,
    }
    assert set(projects[""].workspace_project_ids) == children
    assert projects[""].project_id in projects[
        "services/api"
    ].workspace_project_ids


def test_discovers_gradle_includes_and_custom_project_directory(
    tmp_path: Path,
) -> None:
    (tmp_path / "settings.gradle.kts").write_text(
        """
rootProject.name = "sample-gradle"
include(":apps:api", ":libraries:shared")
project(":libraries:shared").projectDir = file("components/shared")
""".strip(),
        encoding="utf-8",
    )
    (tmp_path / "build.gradle.kts").write_text(
        "tasks.register(\"mustNotRun\")\n",
        encoding="utf-8",
    )
    for relative in ("apps/api", "components/shared"):
        root = tmp_path.joinpath(*relative.split("/"))
        (root / "src" / "main" / "java" / "example").mkdir(parents=True)
        (root / "src" / "main" / "java" / "example" / "Unit.java").write_text(
            "package example; class Unit {}\n",
            encoding="utf-8",
        )

    result = ProjectDiscovery(
        tmp_path,
        repository_identity("example/gradle"),
    ).discover()
    projects = {
        project.root: project
        for project in result.projects
        if project.kind == ProjectKind.JAVA
    }

    assert tuple(projects) == ("", "apps/api", "components/shared")
    assert projects[""].name == "sample-gradle"
    assert projects["apps/api"].manifest_paths == ()
    assert projects["components/shared"].source_roots == (
        "components/shared/src/main/java",
    )
    assert len(projects[""].workspace_project_ids) == 2
    assert not (tmp_path / "mustNotRun").exists()


def test_rejects_xml_entities_and_repository_escape_modules(
    tmp_path: Path,
) -> None:
    (tmp_path / "pom.xml").write_text(
        """
<!DOCTYPE project [<!ENTITY unsafe SYSTEM "file:///etc/passwd">]>
<project>
  <artifactId>&unsafe;</artifactId>
  <modules><module>../outside</module></modules>
</project>
""".strip(),
        encoding="utf-8",
    )
    (tmp_path / "src" / "main" / "java").mkdir(parents=True)
    (tmp_path / "src" / "main" / "java" / "App.java").write_text(
        "class App {}\n",
        encoding="utf-8",
    )

    result = ProjectDiscovery(
        tmp_path,
        repository_identity("example/unsafe-maven"),
    ).discover()
    projects = [
        project
        for project in result.projects
        if project.kind == ProjectKind.JAVA
    ]

    assert len(projects) == 1
    assert projects[0].name == "java-project"
    assert any(
        item.code == "discovery.java_metadata_invalid"
        for item in result.diagnostics
    )


def test_gradle_module_escape_is_diagnosed_and_ignored(
    tmp_path: Path,
) -> None:
    (tmp_path / "settings.gradle").write_text(
        """
rootProject.name = 'safe'
include(':inside', ':outside')
project(':outside').projectDir = file('../outside')
""".strip(),
        encoding="utf-8",
    )
    (tmp_path / "inside").mkdir()
    (tmp_path / "inside" / "Example.java").write_text(
        "class Example {}\n",
        encoding="utf-8",
    )

    result = ProjectDiscovery(
        tmp_path,
        repository_identity("example/safe-gradle"),
    ).discover()
    projects = {
        project.root: project
        for project in result.projects
        if project.kind == ProjectKind.JAVA
    }

    assert tuple(projects) == ("", "inside")
    assert projects["inside"].source_roots == ("inside",)
    assert any(
        item.code == "discovery.java_module_path_invalid"
        for item in result.diagnostics
    )
