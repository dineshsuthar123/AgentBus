from __future__ import annotations

from agentbus.intelligence.models import (
    DiagnosticSeverity,
    IndexDiagnostic,
    Project,
)


def normalize_project_relationships(
    projects: tuple[Project, ...],
    *,
    repository_id: str,
    maximum_relationships: int = 256,
) -> tuple[tuple[Project, ...], tuple[IndexDiagnostic, ...]]:
    if maximum_relationships < 1 or maximum_relationships > 256:
        raise ValueError("maximum_relationships must be between 1 and 256")
    by_id = {project.project_id: project for project in projects}
    if len(by_id) != len(projects):
        raise ValueError("project identities must be unique")
    roots: set[tuple[str, object]] = set()
    for project in projects:
        if project.repository_id != repository_id:
            raise ValueError("project belongs to a different repository")
        root_key = (project.root, project.kind)
        if root_key in roots:
            raise ValueError("project roots must be unique per project kind")
        roots.add(root_key)

    links = {project.project_id: set() for project in projects}
    diagnostics: list[IndexDiagnostic] = []
    for project in projects:
        for related_id in project.workspace_project_ids:
            if related_id == project.project_id:
                diagnostics.append(
                    _relationship_diagnostic(
                        "discovery.project_self_link",
                        "A project workspace self-link was ignored.",
                        project,
                    )
                )
                continue
            if related_id not in by_id:
                diagnostics.append(
                    _relationship_diagnostic(
                        "discovery.project_link_missing",
                        "A project workspace link referenced an unknown project.",
                        project,
                    )
                )
                continue
            links[project.project_id].add(related_id)
            links[related_id].add(project.project_id)

    bounded_links = {project.project_id: set() for project in projects}
    limited_projects: set[str] = set()
    edges = sorted(
        {
            tuple(sorted((source, target)))
            for source, targets in links.items()
            for target in targets
        }
    )
    for source, target in edges:
        if (
            len(bounded_links[source]) >= maximum_relationships
            or len(bounded_links[target]) >= maximum_relationships
        ):
            limited_projects.update((source, target))
            continue
        bounded_links[source].add(target)
        bounded_links[target].add(source)

    normalized: list[Project] = []
    for project in projects:
        if project.project_id in limited_projects:
            diagnostics.append(
                _relationship_diagnostic(
                    "discovery.project_link_limit",
                    "Project workspace relationships exceeded the configured limit.",
                    project,
                )
            )
        related = tuple(sorted(bounded_links[project.project_id]))
        normalized.append(
            Project.model_validate(
                project.model_copy(
                    update={"workspace_project_ids": related}
                ).model_dump(mode="python")
            )
        )
    return tuple(normalized), tuple(diagnostics)


def _relationship_diagnostic(
    code: str,
    message: str,
    project: Project,
) -> IndexDiagnostic:
    return IndexDiagnostic(
        code=code,
        severity=DiagnosticSeverity.WARNING,
        message=message,
        relative_path=project.root or None,
        recoverable=True,
    )
