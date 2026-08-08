from __future__ import annotations

from pathlib import Path

from agentbus.intelligence import repository_identity
from agentbus.intelligence.discovery import ProjectDiscovery
from agentbus.intelligence.models import ProjectKind


def test_discovers_pyproject_src_and_test_layout(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text(
        """
[project]
name = "sample-service"

[tool.pytest.ini_options]
testpaths = ["checks"]

[tool.setuptools.package-dir]
"" = "src"
""".strip(),
        encoding="utf-8",
    )
    (tmp_path / "src" / "sample").mkdir(parents=True)
    (tmp_path / "src" / "sample" / "__init__.py").write_text(
        "",
        encoding="utf-8",
    )
    (tmp_path / "checks").mkdir()
    (tmp_path / "checks" / "test_sample.py").write_text(
        "def test_sample(): pass\n",
        encoding="utf-8",
    )
    (tmp_path / "build").mkdir()
    (tmp_path / "build" / "generated.py").write_text(
        "generated = True\n",
        encoding="utf-8",
    )

    result = ProjectDiscovery(
        tmp_path,
        repository_identity("example/sample"),
    ).discover()

    assert len(result.projects) == 1
    project = result.projects[0]
    assert project.name == "sample-service"
    assert project.kind == ProjectKind.PYTHON
    assert project.root == ""
    assert project.source_roots == ("src",)
    assert project.test_roots == ("checks",)
    assert project.generated_roots == ("build",)
    assert project.manifest_paths == ("pyproject.toml",)


def test_discovers_nested_setup_cfg_and_requirements_projects(
    tmp_path: Path,
) -> None:
    service = tmp_path / "services" / "billing"
    service.mkdir(parents=True)
    (service / "setup.cfg").write_text(
        "[metadata]\nname = billing-service\n",
        encoding="utf-8",
    )
    (service / "billing").mkdir()
    (service / "billing" / "__init__.py").write_text("", encoding="utf-8")
    worker = tmp_path / "workers" / "events"
    (worker / "requirements").mkdir(parents=True)
    (worker / "requirements" / "dev.txt").write_text(
        "pytest\n",
        encoding="utf-8",
    )
    (worker / "worker.py").write_text("value = 1\n", encoding="utf-8")

    result = ProjectDiscovery(
        tmp_path,
        repository_identity("example/monorepo"),
    ).discover()

    assert [(item.root, item.name) for item in result.projects] == [
        ("services/billing", "billing-service"),
        ("workers/events", "events"),
    ]


def test_setup_py_is_inspected_statically_without_execution(tmp_path: Path) -> None:
    marker = tmp_path / "executed.txt"
    (tmp_path / "setup.py").write_text(
        """
from pathlib import Path
from setuptools import setup
Path("executed.txt").write_text("unsafe")
setup(name="static-name")
""".strip(),
        encoding="utf-8",
    )
    (tmp_path / "module.py").write_text("value = 1\n", encoding="utf-8")

    result = ProjectDiscovery(
        tmp_path,
        repository_identity("example/static"),
    ).discover()

    assert result.projects[0].name == "static-name"
    assert marker.exists() is False


def test_invalid_python_metadata_is_recoverable(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text(
        "[project\nname = broken",
        encoding="utf-8",
    )
    (tmp_path / "app.py").write_text("value = 1\n", encoding="utf-8")

    result = ProjectDiscovery(
        tmp_path,
        repository_identity("example/broken"),
    ).discover()

    assert result.projects[0].name == "python-project"
    assert result.projects[0].source_roots == ("",)
    assert any(
        item.code == "discovery.python_metadata_invalid"
        for item in result.diagnostics
    )
