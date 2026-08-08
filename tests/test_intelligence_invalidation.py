from __future__ import annotations

from pathlib import Path

import pytest

from agentbus.intelligence import (
    DependencyInvalidator,
    DependencyKind,
    InvalidationCause,
    InvalidationLimits,
    SourceFile,
    SourceLanguage,
    Symbol,
    SymbolKind,
    SymbolLocation,
    SymbolReference,
    file_id,
    reference_id,
    repository_identity,
    symbol_id,
)


def _records() -> tuple[
    tuple[SourceFile, ...],
    tuple[Symbol, ...],
    tuple[SymbolReference, ...],
]:
    repository = repository_identity("fixtures/invalidation")
    paths = ("target.py", "caller.py", "api.py", "unrelated.py")
    files = tuple(
        SourceFile(
            file_id=file_id(repository.repository_id, path),
            repository_id=repository.repository_id,
            relative_path=path,
            language=SourceLanguage.PYTHON,
            content_hash=f"{index:064x}",
            size_bytes=100,
            parser_name="python-ast",
            parser_version="1.2.0",
        )
        for index, path in enumerate(paths, start=1)
    )
    symbols = tuple(
        Symbol(
            symbol_id=symbol_id(
                source.file_id,
                f"{Path(source.relative_path).stem}.run",
                SymbolKind.FUNCTION,
                signature="run()",
            ),
            file_id=source.file_id,
            name="run",
            qualified_name=f"{Path(source.relative_path).stem}.run",
            kind=SymbolKind.FUNCTION,
            language=SourceLanguage.PYTHON,
            location=SymbolLocation(
                relative_path=source.relative_path,
                start_line=1,
                start_column=0,
                end_line=2,
                end_column=1,
            ),
        )
        for source in files
    )
    by_path = {
        source.relative_path: (source, symbol)
        for source, symbol in zip(files, symbols, strict=True)
    }
    references = (
        _reference(
            by_path["caller.py"],
            by_path["target.py"][1],
            line=3,
        ),
        _reference(
            by_path["api.py"],
            by_path["caller.py"][1],
            line=4,
        ),
    )
    return files, symbols, references


def _reference(
    source: tuple[SourceFile, Symbol],
    target: Symbol,
    *,
    line: int,
) -> SymbolReference:
    source_file, source_symbol = source
    return SymbolReference(
        reference_id=reference_id(
            source_file.file_id,
            source_file.relative_path,
            line,
            4,
            target.qualified_name,
            DependencyKind.CALLS.value,
        ),
        source_symbol_id=source_symbol.symbol_id,
        source_file_id=source_file.file_id,
        target_symbol_id=target.symbol_id,
        kind=DependencyKind.CALLS,
        location=SymbolLocation(
            relative_path=source_file.relative_path,
            start_line=line,
            start_column=4,
            end_line=line,
            end_column=12,
        ),
        explanation="Static call target.",
    )


def test_invalidates_reverse_dependencies_transitively() -> None:
    files, symbols, references = _records()

    plan = DependencyInvalidator().plan(
        files,
        symbols,
        references,
        changed_paths=("target.py",),
    )

    assert plan.direct_paths == ("target.py",)
    assert plan.dependent_paths == ("api.py", "caller.py")
    assert plan.all_paths == ("api.py", "caller.py", "target.py")
    assert plan.requires_full_reindex is False
    assert plan.reason_for("caller.py") == next(
        reason
        for reason in plan.reasons
        if reason.cause == InvalidationCause.DEPENDENCY_CHANGED
        and reason.depth == 1
    )
    assert plan.reason_for("api.py").depth == 2
    assert "unrelated.py" not in plan.all_paths


def test_depth_bound_fails_closed_to_full_reindex() -> None:
    files, symbols, references = _records()

    plan = DependencyInvalidator(
        limits=InvalidationLimits(maximum_depth=1)
    ).plan(
        files,
        symbols,
        references,
        changed_paths=("target.py",),
    )

    assert plan.dependent_paths == ("caller.py",)
    assert plan.truncated is True
    assert plan.requires_full_reindex is True


def test_reference_bound_fails_closed_to_full_reindex() -> None:
    files, symbols, references = _records()

    plan = DependencyInvalidator(
        limits=InvalidationLimits(maximum_references=1)
    ).plan(
        files,
        symbols,
        reversed(references),
        changed_paths=("target.py",),
    )

    assert plan.truncated is True
    assert plan.requires_full_reindex is True


def test_parser_and_configuration_invalidation_is_scoped() -> None:
    files, symbols, references = _records()
    project_identity = "project_" + ("a" * 64)
    configured = files[0].model_copy(
        update={"project_id": project_identity}
    )

    plan = DependencyInvalidator().plan(
        (configured, *files[1:]),
        symbols,
        references,
        parser_names=("python-ast",),
        project_ids=(project_identity,),
    )

    assert plan.direct_paths == (
        "api.py",
        "caller.py",
        "target.py",
        "unrelated.py",
    )
    assert (
        plan.reason_for("target.py").cause
        == InvalidationCause.CONFIGURATION_CHANGED
    )
    assert (
        plan.reason_for("unrelated.py").cause
        == InvalidationCause.PARSER_VERSION_CHANGED
    )


def test_deleted_and_renamed_paths_remain_explainable() -> None:
    files, symbols, references = _records()

    plan = DependencyInvalidator().plan(
        files,
        symbols,
        references,
        deleted_paths=("target.py",),
        renamed_paths=(("caller.py", "renamed_caller.py"),),
    )

    assert plan.reason_for("target.py").cause == InvalidationCause.DELETED
    assert plan.reason_for("caller.py").cause == InvalidationCause.RENAMED
    assert (
        plan.reason_for("renamed_caller.py").source_path == "caller.py"
    )
    assert "api.py" in plan.dependent_paths


def test_unresolved_name_can_trigger_dependency_invalidation() -> None:
    files, symbols, _ = _records()
    caller_file = next(
        source for source in files if source.relative_path == "caller.py"
    )
    caller_symbol = next(
        symbol for symbol in symbols if symbol.file_id == caller_file.file_id
    )
    unresolved = SymbolReference(
        reference_id=reference_id(
            caller_file.file_id,
            caller_file.relative_path,
            3,
            4,
            "target.new_api",
            DependencyKind.CALLS.value,
        ),
        source_symbol_id=caller_symbol.symbol_id,
        source_file_id=caller_file.file_id,
        unresolved_target="target.new_api",
        kind=DependencyKind.CALLS,
        location=SymbolLocation(
            relative_path=caller_file.relative_path,
            start_line=3,
            start_column=4,
            end_line=3,
            end_column=18,
        ),
        explanation="Unresolved static call target.",
    )

    plan = DependencyInvalidator().plan(
        files,
        symbols,
        (unresolved,),
        changed_paths=("target.py",),
        changed_qualified_names=("target.new_api",),
    )

    assert plan.dependent_paths == ("caller.py",)


@pytest.mark.parametrize(
    "unsafe_path",
    ("../outside.py", "/absolute.py", "C:/outside.py"),
)
def test_rejects_unsafe_invalidation_paths(unsafe_path: str) -> None:
    files, symbols, references = _records()

    with pytest.raises(ValueError):
        DependencyInvalidator().plan(
            files,
            symbols,
            references,
            changed_paths=(unsafe_path,),
        )
