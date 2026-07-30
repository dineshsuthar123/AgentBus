from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from agentbus.intelligence.models import (
    ContextPlan,
    ContextRole,
    DependencyKind,
    ImpactRequest,
    IndexSnapshot,
    IndexState,
    Project,
    ProjectKind,
    RepositoryIdentity,
    SourceLanguage,
    SymbolKind,
    SymbolLocation,
    WorkspaceIdentity,
)


def identity(prefix: str, fill: str = "a") -> str:
    return f"{prefix}_{fill * 32}"


def digest(fill: str = "b") -> str:
    return fill * 64


def test_repository_models_are_frozen_and_path_portable():
    repository = RepositoryIdentity(
        repository_id=identity("repo"),
        key_hash=digest(),
        display_name="sample",
    )
    workspace = WorkspaceIdentity(
        workspace_id=identity("workspace"),
        repository_id=repository.repository_id,
        roots=("", "services/api"),
    )
    project = Project(
        project_id=identity("project"),
        repository_id=repository.repository_id,
        name="api",
        kind=ProjectKind.PYTHON,
        root="services/api",
        source_roots=("services/api/src",),
        test_roots=("services/api/tests",),
    )

    assert workspace.roots == ("", "services/api")
    assert project.root == "services/api"
    with pytest.raises(ValidationError):
        project.name = "mutated"


@pytest.mark.parametrize(
    "unsafe",
    [
        "../outside.py",
        "/absolute/path.py",
        "C:/personal/path.py",
        r"C:\personal\path.py",
        "src/../../outside.py",
        "src/*.py",
    ],
)
def test_repository_models_reject_unsafe_or_personal_paths(unsafe):
    with pytest.raises(ValidationError):
        SymbolLocation(
            relative_path=unsafe,
            start_line=1,
            start_column=0,
            end_line=1,
            end_column=1,
        )


def test_repository_models_accept_literal_bracketed_route_paths():
    location = SymbolLocation(
        relative_path="src/app/users/[id]/route.ts",
        start_line=1,
        start_column=0,
        end_line=1,
        end_column=1,
    )

    assert location.relative_path == "src/app/users/[id]/route.ts"


def test_symbol_location_rejects_reversed_ranges():
    with pytest.raises(ValidationError, match="must not precede"):
        SymbolLocation(
            relative_path="src/module.py",
            start_line=4,
            start_column=2,
            end_line=3,
            end_column=9,
        )


def test_index_snapshot_requires_deterministic_hashes_and_versions():
    snapshot = IndexSnapshot(
        snapshot_id=identity("snapshot"),
        repository_id=identity("repo"),
        workspace_id=identity("workspace"),
        state=IndexState.CURRENT,
        created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        file_count=2,
        symbol_count=3,
        project_map_hash=digest("1"),
        graph_hash=digest("2"),
        parser_versions={"python": "1.0"},
        source_fingerprint=digest("3"),
    )

    payload = snapshot.model_dump(mode="json")
    assert payload["state"] == "current"
    assert payload["parser_versions"] == {"python": "1.0"}


def test_context_plan_enforces_both_budgets():
    with pytest.raises(ValidationError, match="byte budget"):
        ContextPlan(
            plan_id=identity("plan"),
            role=ContextRole.PLANNER,
            task_hash=digest("4"),
            byte_budget=10,
            token_budget=10,
            selected_bytes=11,
            selected_tokens=2,
            plan_hash=digest("5"),
        )


def test_impact_request_is_bounded_and_requires_a_subject():
    request = ImpactRequest(paths=("src/module.py",), max_depth=3, max_nodes=20)

    assert request.max_depth == 3
    with pytest.raises(ValidationError, match="at least one path or symbol"):
        ImpactRequest()
    with pytest.raises(ValidationError):
        ImpactRequest(paths=("src/module.py",), max_depth=17)


def test_required_symbol_and_dependency_kinds_are_stable():
    assert {kind.value for kind in SymbolKind} >= {
        "module",
        "class",
        "interface",
        "record",
        "constructor",
        "endpoint",
        "test",
        "configuration_unit",
    }
    assert {kind.value for kind in DependencyKind} >= {
        "imports",
        "calls",
        "inherits",
        "implements",
        "tests",
        "owns",
        "generated_from",
    }
    assert SourceLanguage.PYTHON.value == "python"
