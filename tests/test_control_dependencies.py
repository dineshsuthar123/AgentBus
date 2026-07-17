from __future__ import annotations

import tomllib
from pathlib import Path


def test_ide_dependencies_are_optional() -> None:
    project = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))[
        "project"
    ]

    assert all(
        not dependency.startswith(("fastapi", "uvicorn"))
        for dependency in project["dependencies"]
    )
    assert {"fastapi>=0.115,<1", "uvicorn>=0.34,<1"} <= set(
        project["optional-dependencies"]["ide"]
    )
