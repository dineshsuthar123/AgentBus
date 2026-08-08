from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from agentbus.execution.cancellation import CancellationToken
from agentbus.intelligence import (
    IndexState,
    IndexStore,
    InvalidationLimits,
    RepositoryIndexer,
    repository_identity,
    workspace_identity,
)
from agentbus.intelligence.parsers import (
    GoStaticParser,
    ParseRequest,
    ParseResult,
    ParserDescriptor,
    ParserLimits,
    ParserRegistry,
    PythonAstParser,
    TypeScriptStaticParser,
)
from agentbus.intelligence.parsers.base import CancellationSignal


class _CountingParser:
    def __init__(
        self,
        delegate: object,
        *,
        version: str | None = None,
        after_parse: Callable[[ParseRequest], None] | None = None,
    ) -> None:
        descriptor = getattr(delegate, "descriptor")
        assert isinstance(descriptor, ParserDescriptor)
        self.descriptor = descriptor.model_copy(
            update={"version": version or descriptor.version}
        )
        self.delegate = delegate
        self.after_parse = after_parse
        self.paths: list[str] = []

    def parse(
        self,
        request: ParseRequest,
        *,
        limits: ParserLimits | None = None,
        cancellation: CancellationSignal | None = None,
    ) -> ParseResult:
        self.paths.append(request.relative_path)
        result = self.delegate.parse(
            request,
            limits=limits,
            cancellation=cancellation,
        )
        if self.after_parse is not None:
            self.after_parse(request)
        return result.model_copy(
            update={
                "parser_name": self.descriptor.name,
                "parser_version": self.descriptor.version,
            }
        )


def _indexer(
    tmp_path: Path,
    store: IndexStore,
    *parsers: _CountingParser,
    invalidation_limits: InvalidationLimits | None = None,
) -> RepositoryIndexer:
    repository = repository_identity("fixtures/incremental-indexer")
    workspace = workspace_identity(repository.repository_id, [""])
    return RepositoryIndexer(
        tmp_path,
        repository,
        workspace,
        store,
        registry=ParserRegistry(parsers),
        invalidation_limits=invalidation_limits,
    )


def _store(tmp_path: Path) -> IndexStore:
    return IndexStore(tmp_path / ".agentbus-test" / "index.sqlite3")


def _write_python_project(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "incremental-fixture"\nversion = "0.1.0"\n',
        encoding="utf-8",
    )


def test_update_only_reparses_the_changed_file(tmp_path: Path) -> None:
    _write_python_project(tmp_path)
    (tmp_path / "alpha.py").write_text(
        "def alpha():\n    return 1\n",
        encoding="utf-8",
    )
    beta = tmp_path / "beta.py"
    beta.write_text("def beta():\n    return 1\n", encoding="utf-8")
    store = _store(tmp_path)
    parser = _CountingParser(PythonAstParser())
    indexer = _indexer(tmp_path, store, parser)
    first = indexer.build()
    first_alpha = next(
        item
        for item in store.list_files(first.snapshot.snapshot_id)
        if item.relative_path == "alpha.py"
    )
    parser.paths.clear()

    beta.write_text("def beta():\n    return 2\n", encoding="utf-8")
    updated = indexer.update()

    assert parser.paths == ["beta.py"]
    assert updated.indexed_paths == ("beta.py",)
    assert updated.reused_paths == ("alpha.py",)
    assert updated.unchanged is False
    updated_alpha = next(
        item
        for item in store.list_files(updated.snapshot.snapshot_id)
        if item.relative_path == "alpha.py"
    )
    assert updated_alpha == first_alpha


def test_update_removes_deleted_file_records(tmp_path: Path) -> None:
    _write_python_project(tmp_path)
    retained = tmp_path / "retained.py"
    retained.write_text(
        "def retained():\n    return True\n",
        encoding="utf-8",
    )
    deleted = tmp_path / "deleted.py"
    deleted.write_text(
        "def removed_marker():\n    return True\n",
        encoding="utf-8",
    )
    store = _store(tmp_path)
    parser = _CountingParser(PythonAstParser())
    indexer = _indexer(tmp_path, store, parser)
    indexer.build()
    parser.paths.clear()

    deleted.unlink()
    updated = indexer.update()

    assert parser.paths == []
    assert updated.deleted_paths == ("deleted.py",)
    assert updated.reused_paths == ("retained.py",)
    assert {
        item.relative_path
        for item in store.list_files(updated.snapshot.snapshot_id)
    } == {"retained.py"}
    assert all(
        "removed_marker" not in item.qualified_name
        for item in store.list_symbols(updated.snapshot.snapshot_id)
    )


def test_update_reports_content_preserving_rename(tmp_path: Path) -> None:
    _write_python_project(tmp_path)
    original = tmp_path / "before.py"
    original.write_text(
        "def renamed_marker():\n    return True\n",
        encoding="utf-8",
    )
    store = _store(tmp_path)
    parser = _CountingParser(PythonAstParser())
    indexer = _indexer(tmp_path, store, parser)
    initial = indexer.build()
    original_id = store.list_files(initial.snapshot.snapshot_id)[0].file_id
    parser.paths.clear()

    original.rename(tmp_path / "after.py")
    updated = indexer.update()

    assert parser.paths == ["after.py"]
    assert updated.renamed_paths == (("before.py", "after.py"),)
    assert updated.deleted_paths == ()
    source = store.list_files(updated.snapshot.snapshot_id)[0]
    assert source.relative_path == "after.py"
    assert source.file_id != original_id
    assert {
        item.location.relative_path
        for item in store.list_symbols(updated.snapshot.snapshot_id)
    } == {"after.py"}


def test_parser_version_only_invalidates_owned_languages(
    tmp_path: Path,
) -> None:
    (tmp_path / "service.py").write_text(
        "def run():\n    return True\n",
        encoding="utf-8",
    )
    (tmp_path / "client.ts").write_text(
        "export function call(): boolean { return true; }\n",
        encoding="utf-8",
    )
    store = _store(tmp_path)
    first_python = _CountingParser(PythonAstParser())
    first_typescript = _CountingParser(TypeScriptStaticParser())
    _indexer(
        tmp_path,
        store,
        first_python,
        first_typescript,
    ).build()
    next_python = _CountingParser(PythonAstParser(), version="1.3.0")
    next_typescript = _CountingParser(TypeScriptStaticParser())

    updated = _indexer(
        tmp_path,
        store,
        next_python,
        next_typescript,
    ).update()

    assert next_python.paths == ["service.py"]
    assert next_typescript.paths == []
    assert updated.indexed_paths == ("service.py",)
    assert updated.reused_paths == ("client.ts",)


def test_project_configuration_change_invalidates_project_files(
    tmp_path: Path,
) -> None:
    _write_python_project(tmp_path)
    (tmp_path / "alpha.py").write_text(
        "def alpha():\n    return 1\n",
        encoding="utf-8",
    )
    (tmp_path / "beta.py").write_text(
        "def beta():\n    return 2\n",
        encoding="utf-8",
    )
    store = _store(tmp_path)
    parser = _CountingParser(PythonAstParser())
    indexer = _indexer(tmp_path, store, parser)
    indexer.build()
    parser.paths.clear()

    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "incremental-fixture"\nversion = "0.2.0"\n',
        encoding="utf-8",
    )
    updated = indexer.update()

    assert parser.paths == ["alpha.py", "beta.py"]
    assert updated.indexed_paths == ("alpha.py", "beta.py")
    assert updated.reused_paths == ()


def test_changed_target_reindexes_and_resolves_dependent_reference(
    tmp_path: Path,
) -> None:
    _write_python_project(tmp_path)
    (tmp_path / "caller.py").write_text(
        "from target import run\n\n"
        "def call():\n"
        "    return run()\n",
        encoding="utf-8",
    )
    target = tmp_path / "target.py"
    target.write_text(
        "def run():\n    return True\n",
        encoding="utf-8",
    )
    store = _store(tmp_path)
    parser = _CountingParser(PythonAstParser())
    indexer = _indexer(tmp_path, store, parser)
    initial = indexer.build()
    original_target = next(
        item
        for item in store.list_symbols(initial.snapshot.snapshot_id)
        if item.qualified_name == "target.run"
    )
    parser.paths.clear()

    target.write_text(
        "def run(value: bool = True):\n    return value\n",
        encoding="utf-8",
    )
    updated = indexer.update()
    symbols = store.list_symbols(updated.snapshot.snapshot_id)
    current_target = next(
        item for item in symbols if item.qualified_name == "target.run"
    )
    caller_references = tuple(
        item
        for item in store.list_references(updated.snapshot.snapshot_id)
        if item.location.relative_path == "caller.py"
        and item.target_symbol_id is not None
    )

    assert parser.paths == ["target.py", "caller.py"]
    assert updated.invalidated_paths == ("caller.py",)
    assert updated.invalidation_plan is not None
    assert updated.invalidation_plan.dependent_paths == ("caller.py",)
    assert original_target.symbol_id != current_target.symbol_id
    assert caller_references
    assert {
        item.target_symbol_id for item in caller_references
    } == {current_target.symbol_id}


def test_deleted_target_reindexes_dependent_but_not_unrelated_file(
    tmp_path: Path,
) -> None:
    _write_python_project(tmp_path)
    (tmp_path / "caller.py").write_text(
        "from target import run\n\n"
        "def call():\n"
        "    return run()\n",
        encoding="utf-8",
    )
    target = tmp_path / "target.py"
    target.write_text(
        "def run():\n    return True\n",
        encoding="utf-8",
    )
    (tmp_path / "unrelated.py").write_text(
        "def other():\n    return True\n",
        encoding="utf-8",
    )
    store = _store(tmp_path)
    parser = _CountingParser(PythonAstParser())
    indexer = _indexer(tmp_path, store, parser)
    indexer.build()
    parser.paths.clear()

    target.unlink()
    updated = indexer.update()

    assert parser.paths == ["caller.py"]
    assert updated.deleted_paths == ("target.py",)
    assert updated.invalidated_paths == ("caller.py",)
    assert updated.reused_paths == ("unrelated.py",)
    assert {
        item.relative_path
        for item in store.list_files(updated.snapshot.snapshot_id)
    } == {"caller.py", "unrelated.py"}


def test_invalidation_bound_reindexes_all_carried_files(
    tmp_path: Path,
) -> None:
    _write_python_project(tmp_path)
    target = tmp_path / "target.py"
    target.write_text(
        "def run():\n    return True\n",
        encoding="utf-8",
    )
    (tmp_path / "caller.py").write_text(
        "from target import run\n\n"
        "def call():\n"
        "    return run()\n",
        encoding="utf-8",
    )
    (tmp_path / "api.py").write_text(
        "from caller import call\n\n"
        "def handle():\n"
        "    return call()\n",
        encoding="utf-8",
    )
    (tmp_path / "unrelated.py").write_text(
        "def other():\n    return True\n",
        encoding="utf-8",
    )
    store = _store(tmp_path)
    _indexer(
        tmp_path,
        store,
        _CountingParser(PythonAstParser()),
    ).build()
    target.write_text(
        "def run(value: bool = True):\n    return value\n",
        encoding="utf-8",
    )
    parser = _CountingParser(PythonAstParser())

    updated = _indexer(
        tmp_path,
        store,
        parser,
        invalidation_limits=InvalidationLimits(
            maximum_references=1
        ),
    ).update()

    assert parser.paths == [
        "target.py",
        "api.py",
        "caller.py",
        "unrelated.py",
    ]
    assert updated.invalidation_plan is not None
    assert updated.invalidation_plan.requires_full_reindex is True
    assert updated.invalidated_paths == (
        "api.py",
        "caller.py",
        "unrelated.py",
    )
    assert updated.reused_paths == ()
    assert any(
        diagnostic.code == "index.invalidation_fallback"
        for diagnostic in updated.snapshot.diagnostics
    )


def test_paused_dependency_invalidation_resumes_without_reparsing_target(
    tmp_path: Path,
) -> None:
    _write_python_project(tmp_path)
    (tmp_path / "caller.py").write_text(
        "from target import run\n\n"
        "def call():\n"
        "    return run()\n",
        encoding="utf-8",
    )
    target = tmp_path / "target.py"
    target.write_text(
        "def run():\n    return True\n",
        encoding="utf-8",
    )
    store = _store(tmp_path)
    _indexer(
        tmp_path,
        store,
        _CountingParser(PythonAstParser()),
    ).build()
    target.write_text(
        "def run(value: bool = True):\n    return value\n",
        encoding="utf-8",
    )
    cancellation = CancellationToken()

    def pause_after_target(request: ParseRequest) -> None:
        if request.relative_path == "target.py":
            cancellation.request("test")

    pausing_parser = _CountingParser(
        PythonAstParser(),
        after_parse=pause_after_target,
    )
    paused = _indexer(
        tmp_path,
        store,
        pausing_parser,
    ).update(cancellation=cancellation)

    assert pausing_parser.paths == ["target.py"]
    assert paused.snapshot.state == IndexState.PAUSED
    assert paused.invalidated_paths == ()
    assert any(
        diagnostic.code == "index.invalidation_pending"
        for diagnostic in paused.snapshot.diagnostics
    )
    resume_parser = _CountingParser(PythonAstParser())

    resumed = _indexer(tmp_path, store, resume_parser).update()

    assert resume_parser.paths == ["caller.py"]
    assert resumed.snapshot.state == IndexState.CURRENT
    assert resumed.indexed_paths == ("caller.py",)
    assert resumed.invalidated_paths == ("caller.py",)
    assert resumed.reused_paths == ("target.py",)


def test_failed_dependent_invalidation_is_retried(
    tmp_path: Path,
) -> None:
    _write_python_project(tmp_path)
    (tmp_path / "caller.py").write_text(
        "from target import run\n\n"
        "def call():\n"
        "    return run()\n",
        encoding="utf-8",
    )
    target = tmp_path / "target.py"
    target.write_text(
        "def run():\n    return True\n",
        encoding="utf-8",
    )
    store = _store(tmp_path)
    _indexer(
        tmp_path,
        store,
        _CountingParser(PythonAstParser()),
    ).build()
    target.write_text(
        "def run(value: bool = True):\n    return value\n",
        encoding="utf-8",
    )

    def fail_caller(request: ParseRequest) -> None:
        if request.relative_path == "caller.py":
            raise ValueError("injected parser failure")

    failing_parser = _CountingParser(
        PythonAstParser(),
        after_parse=fail_caller,
    )
    partial = _indexer(
        tmp_path,
        store,
        failing_parser,
    ).update()

    assert failing_parser.paths == ["target.py", "caller.py"]
    assert partial.snapshot.state == IndexState.PARTIALLY_CURRENT
    assert any(
        diagnostic.code == "index.invalidation_pending"
        for diagnostic in partial.snapshot.diagnostics
    )
    retry_parser = _CountingParser(PythonAstParser())

    retried = _indexer(tmp_path, store, retry_parser).update()

    assert retry_parser.paths == ["caller.py"]
    assert retried.snapshot.state == IndexState.CURRENT
    assert retried.invalidated_paths == ("caller.py",)
    assert retried.reused_paths == ("target.py",)


def test_paused_update_preserves_unprocessed_files_and_resumes(
    tmp_path: Path,
) -> None:
    _write_python_project(tmp_path)
    alpha = tmp_path / "alpha.py"
    beta = tmp_path / "beta.py"
    alpha.write_text("def alpha():\n    return 1\n", encoding="utf-8")
    beta.write_text("def beta():\n    return 1\n", encoding="utf-8")
    store = _store(tmp_path)
    initial_parser = _CountingParser(PythonAstParser())
    _indexer(tmp_path, store, initial_parser).build()
    alpha.write_text("def alpha():\n    return 2\n", encoding="utf-8")
    beta.write_text("def beta():\n    return 2\n", encoding="utf-8")
    cancellation = CancellationToken()
    pausing_parser = _CountingParser(
        PythonAstParser(),
        after_parse=lambda _request: cancellation.request("test"),
    )

    paused = _indexer(
        tmp_path,
        store,
        pausing_parser,
    ).update(cancellation=cancellation)

    assert pausing_parser.paths == ["alpha.py"]
    assert paused.snapshot.state == IndexState.PAUSED
    assert paused.snapshot.file_count == 2
    assert paused.indexed_paths == ("alpha.py",)
    resume_parser = _CountingParser(PythonAstParser())

    resumed = _indexer(tmp_path, store, resume_parser).update()

    assert resume_parser.paths == ["beta.py"]
    assert resumed.snapshot.state == IndexState.CURRENT
    assert resumed.indexed_paths == ("beta.py",)
    assert resumed.reused_paths == ("alpha.py",)
    assert {
        item.relative_path
        for item in store.list_files(resumed.snapshot.snapshot_id)
    } == {"alpha.py", "beta.py"}


def test_unchanged_mixed_language_snapshot_does_no_parser_work(
    tmp_path: Path,
) -> None:
    (tmp_path / "service.py").write_text(
        "def run():\n    return True\n",
        encoding="utf-8",
    )
    (tmp_path / "service.go").write_text(
        "package service\nfunc Run() {}\n",
        encoding="utf-8",
    )
    store = _store(tmp_path)
    python_parser = _CountingParser(PythonAstParser())
    go_parser = _CountingParser(GoStaticParser())
    indexer = _indexer(
        tmp_path,
        store,
        python_parser,
        go_parser,
    )
    indexer.build()
    python_parser.paths.clear()
    go_parser.paths.clear()

    unchanged = indexer.update()

    assert python_parser.paths == []
    assert go_parser.paths == []
    assert unchanged.unchanged is True
    assert unchanged.reused_paths == ("service.go", "service.py")
