from __future__ import annotations

from pathlib import Path

from agentbus.intelligence import (
    ContextPlanner,
    ContextPlanningConfig,
    ContextPlanningRequest,
    ContextRole,
    DependencyGraph,
    IndexState,
    Project,
    ProjectKind,
    RepositoryLexicalIndex,
    SourceFile,
    SourceLanguage,
    content_hash,
    file_id,
    project_id,
    repository_identity,
)
from agentbus.intelligence.discovery import RepositoryInventoryScanner
from agentbus.intelligence.hybrid import HybridRetriever


def _large_planner(
    tmp_path: Path,
) -> tuple[ContextPlanner, tuple[str, ...], str]:
    workspace = tmp_path / "workspace"
    source_root = workspace / "src"
    source_root.mkdir(parents=True)
    repository = repository_identity("fixtures/context-scale")
    owner = project_id(
        repository.repository_id,
        "",
        ProjectKind.PYTHON,
        name="context-scale",
    )
    project = Project(
        project_id=owner,
        repository_id=repository.repository_id,
        name="context-scale",
        kind=ProjectKind.PYTHON,
        root="",
        source_roots=("src",),
    )
    files: list[SourceFile] = []
    paths: list[str] = []
    contents: dict[str, str] = {}
    for index in range(999):
        relative_path = f"src/module_{index:04d}.py"
        group = index // 2
        content = (
            "def shared_context_payload():\n"
            f"    return 'payload-{group:04d}-"
            f"{'x' * 128}'\n"
        )
        target = workspace.joinpath(*relative_path.split("/"))
        target.write_text(content, encoding="utf-8", newline="\n")
        paths.append(relative_path)
        contents[relative_path] = content
        files.append(
            SourceFile(
                file_id=file_id(repository.repository_id, relative_path),
                repository_id=repository.repository_id,
                project_id=owner,
                relative_path=relative_path,
                language=SourceLanguage.PYTHON,
                content_hash=content_hash(content),
                size_bytes=len(content.encode("utf-8")),
                parser_name="context-scale-fixture",
                parser_version="1.0.0",
            )
        )

    protected_content = "REAL_KEY=synthetic-protected-context-marker\n"
    (workspace / ".env").write_text(
        protected_content,
        encoding="utf-8",
        newline="\n",
    )
    files.append(
        SourceFile(
            file_id=file_id(repository.repository_id, ".env"),
            repository_id=repository.repository_id,
            project_id=owner,
            relative_path=".env",
            language=SourceLanguage.TEXT,
            content_hash=content_hash(protected_content),
            size_bytes=len(protected_content.encode("utf-8")),
            parser_name="context-scale-fixture",
            parser_version="1.0.0",
            protected=True,
        )
    )
    file_records = tuple(files)
    lexical = RepositoryLexicalIndex((project,), file_records, (), ())
    graph = DependencyGraph((), files=file_records)
    retriever = HybridRetriever(
        lexical,
        graph,
        file_records,
        (),
    )
    inventory = RepositoryInventoryScanner(workspace).scan()
    planner = ContextPlanner(
        inventory,
        retriever,
        file_records,
        (),
        config=ContextPlanningConfig(
            maximum_candidates=500,
            maximum_queries=4,
            results_per_query=20,
        ),
    )

    stale_path = paths[0]
    stale_target = workspace.joinpath(*stale_path.split("/"))
    original = contents[stale_path]
    changed = original.replace("payload-0000-", "STALE-MARKER-")
    assert len(changed.encode("utf-8")) == len(original.encode("utf-8"))
    stale_target.write_text(changed, encoding="utf-8", newline="\n")
    return planner, (*paths, ".env"), changed


def _request(
    changed_paths: tuple[str, ...],
    *,
    byte_budget: int,
    token_budget: int,
) -> ContextPlanningRequest:
    return ContextPlanningRequest(
        task="Review module_0998 with bounded large repository context",
        role=ContextRole.CODER,
        byte_budget=byte_budget,
        token_budget=token_budget,
        index_state=IndexState.STALE,
        changed_paths=changed_paths,
    )


def _assert_bounded_plan(
    plan,
    *,
    stale_content: str,
) -> None:
    selected = tuple(item for item in plan.candidates if item.selected)
    excluded = tuple(item for item in plan.candidates if not item.selected)

    assert len(plan.candidates) == 500
    assert len({item.candidate_id for item in plan.candidates}) == 500
    assert plan.selected_bytes == sum(item.byte_count for item in selected)
    assert plan.selected_tokens == sum(
        item.estimated_tokens for item in selected
    )
    assert plan.selected_bytes <= plan.byte_budget
    assert plan.selected_tokens <= plan.token_budget
    assert len({content_hash(item.content) for item in selected}) == len(
        selected
    )
    assert any(
        item.relative_path == "src/module_0998.py" for item in selected
    )
    assert any(
        item.exclusion_reason == "duplicate_content" for item in excluded
    )
    assert any(
        item.exclusion_reason == "budget_exceeded" for item in excluded
    )
    mismatch = tuple(
        item
        for item in excluded
        if item.relative_path == "src/module_0000.py"
    )
    assert mismatch
    assert all(
        item.exclusion_reason == "source_hash_mismatch"
        for item in mismatch
    )
    assert all(item.exclusion_reason is None for item in selected)
    assert all(item.relative_path != ".env" for item in plan.candidates)
    assert plan.stale_warning is not None
    assert "mismatched content was excluded" in plan.stale_warning
    assert "not represented" in plan.stale_warning
    assert "state is stale" in plan.stale_warning
    assert stale_content not in repr(plan)
    assert "synthetic-protected-context-marker" not in repr(plan)


def test_large_repository_context_is_deduplicated_and_byte_bounded(
    tmp_path: Path,
) -> None:
    planner, changed_paths, stale_content = _large_planner(tmp_path)
    request = _request(
        changed_paths,
        byte_budget=1_024,
        token_budget=2_000_000,
    )

    first = planner.plan(request)
    repeated = planner.plan(request)

    assert first == repeated
    _assert_bounded_plan(first, stale_content=stale_content)


def test_large_repository_context_is_independently_token_bounded(
    tmp_path: Path,
) -> None:
    planner, changed_paths, stale_content = _large_planner(tmp_path)
    plan = planner.plan(
        _request(
            changed_paths,
            byte_budget=10_000_000,
            token_budget=200,
        )
    )

    _assert_bounded_plan(plan, stale_content=stale_content)
