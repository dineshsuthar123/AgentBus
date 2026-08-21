from __future__ import annotations

import hashlib
import json
import os
import platform
import tempfile
import time
import tracemalloc
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from agentbus import __version__
from agentbus.execution.cancellation import CancellationToken
from agentbus.intelligence import (
    FileChangeKind,
    IndexOperationState,
    IndexState,
    IndexStore,
    IndexingResult,
    RepositoryChangeBuffer,
    RepositoryIndexer,
    RepositoryWatchUpdater,
    WatchLimits,
    repository_identity,
    workspace_identity,
)
from agentbus.intelligence.models import RepositoryIdentity, WorkspaceIdentity
from agentbus.intelligence.parsers import (
    ParseRequest,
    ParseResult,
    ParserLimits,
    ParserRegistry,
    PythonAstParser,
)
from agentbus.intelligence.parsers.base import CancellationSignal
from agentbus.product.synthetic import SYNTHETIC_SIZES, generate_synthetic_repository


INDEX_SCALE_GROUP = "index-scale"
_MINIMUM_SCALE_FILES = 4
_MAXIMUM_STORM_FILES = 100
_INTERPRETATION_NOTE = (
    "Generated local measurements are regression evidence for this machine, not "
    "universal repository-capacity or latency guarantees."
)


@dataclass(frozen=True)
class IndexScaleScenarioMetrics:
    name: str
    duration_ms: float
    changed_files: int
    expected_reindexed_files: int
    indexed_files: int
    necessary_reindexed_files: int
    unnecessarily_reindexed_files: int
    reused_files: int
    invalidated_files: int
    deleted_files: int
    renamed_files: int
    file_count_after: int
    snapshot_state: str
    invalidation_efficiency: float
    checks: tuple[tuple[str, bool], ...]

    @property
    def passed(self) -> bool:
        return all(value for _, value in self.checks)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "duration_ms": round(self.duration_ms, 3),
            "changed_files": self.changed_files,
            "expected_reindexed_files": self.expected_reindexed_files,
            "indexed_files": self.indexed_files,
            "necessary_reindexed_files": self.necessary_reindexed_files,
            "unnecessarily_reindexed_files": self.unnecessarily_reindexed_files,
            "reused_files": self.reused_files,
            "invalidated_files": self.invalidated_files,
            "deleted_files": self.deleted_files,
            "renamed_files": self.renamed_files,
            "file_count_after": self.file_count_after,
            "snapshot_state": self.snapshot_state,
            "invalidation_efficiency": round(self.invalidation_efficiency, 6),
            "checks": dict(self.checks),
            "passed": self.passed,
        }


@dataclass(frozen=True)
class IndexScaleBenchmarkReport:
    profile: str
    repository_files: int
    repository_bytes: int
    repository_fingerprint: str
    seed: int
    generation_ms: float
    database_bytes: int
    peak_memory_bytes: int | None
    final_file_count: int
    final_snapshot_state: str
    operations: tuple[IndexScaleScenarioMetrics, ...]
    environment: dict[str, Any]
    environment_fingerprint: str
    generated_at: str

    @property
    def passed(self) -> bool:
        return (
            self.final_snapshot_state == IndexState.CURRENT.value
            and self.database_bytes > 0
            and all(operation.passed for operation in self.operations)
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.passed,
            "selected_group": INDEX_SCALE_GROUP,
            "iterations": 1,
            "repository": {
                "profile": self.profile,
                "file_count": self.repository_files,
                "byte_count": self.repository_bytes,
                "fingerprint": self.repository_fingerprint,
                "generated": True,
                "retained": False,
            },
            "seed": self.seed,
            "generation_ms": round(self.generation_ms, 3),
            "database_bytes": self.database_bytes,
            "peak_memory_bytes": self.peak_memory_bytes,
            "peak_memory_measured": self.peak_memory_bytes is not None,
            "final_file_count": self.final_file_count,
            "final_snapshot_state": self.final_snapshot_state,
            "operations": [operation.to_dict() for operation in self.operations],
            "environment": self.environment,
            "environment_fingerprint": self.environment_fingerprint,
            "generated_at": self.generated_at,
            "provider_calls": 0,
            "network_calls": 0,
            "network_used": False,
            "interpretation_note": _INTERPRETATION_NOTE,
        }


class _VersionedPythonParser:
    def __init__(
        self,
        *,
        version: str,
        after_parse: Callable[[ParseRequest], None] | None = None,
    ) -> None:
        self.delegate = PythonAstParser()
        self.descriptor = self.delegate.descriptor.model_copy(
            update={"version": version}
        )
        self.after_parse = after_parse

    def parse(
        self,
        request: ParseRequest,
        *,
        limits: ParserLimits | None = None,
        cancellation: CancellationSignal | None = None,
    ) -> ParseResult:
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


def run_index_scale_benchmark(
    *,
    profile: str = "medium",
    file_count: int | None = None,
    seed: int = 2026,
    measure_peak_memory: bool = True,
    temporary_parent: str | Path | None = None,
) -> IndexScaleBenchmarkReport:
    selected_count = _selected_file_count(profile, file_count)
    parent = _temporary_parent(temporary_parent)
    with tempfile.TemporaryDirectory(
        prefix="agentbus-index-scale-",
        dir=parent,
    ) as temporary:
        return _run_owned_benchmark(
            Path(temporary),
            profile=profile,
            file_count=selected_count,
            seed=seed,
            measure_peak_memory=measure_peak_memory,
        )


def _run_owned_benchmark(
    owned_root: Path,
    *,
    profile: str,
    file_count: int,
    seed: int,
    measure_peak_memory: bool,
) -> IndexScaleBenchmarkReport:
    repository_root = owned_root / "repository"
    generation_started = time.perf_counter_ns()
    generated = generate_synthetic_repository(
        repository_root,
        profile=profile,
        file_count=file_count,
        seed=seed,
    )
    generation_ms = _elapsed_ms(generation_started)
    _write_configuration(repository_root, version="0.1.0")

    database_path = owned_root / "state" / "repository-index.sqlite3"
    store = IndexStore(database_path)
    repository = repository_identity(
        f"generated/index-scale/{generated.fingerprint}"
    )
    workspace = workspace_identity(repository.repository_id, ("",))
    parser_version = PythonAstParser.descriptor.version
    operations: list[IndexScaleScenarioMetrics] = []
    peak_memory_bytes: int | None = None
    owns_memory_trace = bool(
        measure_peak_memory and not tracemalloc.is_tracing()
    )
    if owns_memory_trace:
        tracemalloc.start()
    try:
        indexer = _indexer(
            repository_root,
            store,
            repository,
            workspace,
            parser_version=parser_version,
        )
        source_paths = _source_paths(repository_root)
        started = time.perf_counter_ns()
        built = indexer.build()
        operations.append(
            _scenario_metrics(
                "full_index",
                _elapsed_ms(started),
                built,
                changed_paths=source_paths,
                expected_paths=source_paths,
                extra_checks={
                    "all_generated_files_indexed": (
                        len(built.indexed_paths) == file_count
                    ),
                    "no_initial_reuse": not built.reused_paths,
                },
            )
        )

        single_path = source_paths[-1]
        _append_markers(repository_root, (single_path,), "single")
        started = time.perf_counter_ns()
        single = indexer.update()
        operations.append(
            _scenario_metrics(
                "one_file_incremental",
                _elapsed_ms(started),
                single,
                changed_paths=(single_path,),
                expected_paths=_expected_paths(single, (single_path,)),
                extra_checks={
                    "changed_file_indexed": single_path in single.indexed_paths,
                },
            )
        )

        batch_paths = source_paths[-min(100, len(source_paths)) :]
        _append_markers(repository_root, batch_paths, "batch")
        started = time.perf_counter_ns()
        batch = indexer.update()
        operations.append(
            _scenario_metrics(
                "hundred_file_update",
                _elapsed_ms(started),
                batch,
                changed_paths=batch_paths,
                expected_paths=_expected_paths(batch, batch_paths),
                extra_checks={
                    "changed_batch_indexed": set(batch_paths).issubset(
                        batch.indexed_paths
                    ),
                },
            )
        )

        storm_size = _storm_size(file_count)
        rename_sources = _source_paths(repository_root)[-storm_size:]
        renamed_paths = _rename_storm(repository_root, rename_sources)
        rename_targets = tuple(target for _, target in renamed_paths)
        started = time.perf_counter_ns()
        renamed = indexer.update()
        operations.append(
            _scenario_metrics(
                "rename_storm",
                _elapsed_ms(started),
                renamed,
                changed_paths=rename_targets,
                expected_paths=_expected_paths(renamed, rename_targets),
                extra_checks={
                    "all_renames_detected": (
                        set(renamed.renamed_paths) == set(renamed_paths)
                    ),
                    "renames_not_reported_as_deletes": (
                        not set(rename_sources).intersection(
                            renamed.deleted_paths
                        )
                    ),
                },
            )
        )

        delete_paths = _source_paths(repository_root)[-storm_size:]
        _delete_storm(repository_root, delete_paths)
        started = time.perf_counter_ns()
        deleted = indexer.update()
        operations.append(
            _scenario_metrics(
                "delete_storm",
                _elapsed_ms(started),
                deleted,
                changed_paths=delete_paths,
                expected_paths=_expected_paths(deleted, ()),
                extra_checks={
                    "all_deletes_detected": (
                        set(deleted.deleted_paths) == set(delete_paths)
                    ),
                },
            )
        )

        parser_version = _next_patch_version(parser_version)
        indexer = _indexer(
            repository_root,
            store,
            repository,
            workspace,
            parser_version=parser_version,
        )
        current_paths = _source_paths(repository_root)
        started = time.perf_counter_ns()
        parser_invalidated = indexer.update()
        operations.append(
            _scenario_metrics(
                "parser_version_invalidation",
                _elapsed_ms(started),
                parser_invalidated,
                changed_paths=(),
                expected_paths=current_paths,
                extra_checks={
                    "parser_owned_files_reindexed": (
                        set(parser_invalidated.indexed_paths)
                        == set(current_paths)
                    ),
                    "parser_version_recorded": (
                        parser_invalidated.snapshot.parser_versions.get(
                            PythonAstParser.descriptor.name
                        )
                        == parser_version
                    ),
                },
            )
        )

        _write_configuration(repository_root, version="0.2.0")
        current_paths = _source_paths(repository_root)
        started = time.perf_counter_ns()
        configuration_invalidated = indexer.update()
        operations.append(
            _scenario_metrics(
                "configuration_invalidation",
                _elapsed_ms(started),
                configuration_invalidated,
                changed_paths=(),
                expected_paths=current_paths,
                extra_checks={
                    "project_files_reindexed": (
                        set(configuration_invalidated.indexed_paths)
                        == set(current_paths)
                    ),
                    "configuration_disables_reuse": (
                        not configuration_invalidated.reused_paths
                    ),
                },
            )
        )

        overflow_path = current_paths[-1]
        _append_markers(repository_root, (overflow_path,), "overflow")
        changes = RepositoryChangeBuffer(
            repository_root,
            limits=WatchLimits(
                debounce_seconds=0,
                maximum_pending_paths=2,
            ),
        )
        changes.observe(overflow_path, FileChangeKind.MODIFIED)
        changes.mark_overflow()
        started = time.perf_counter_ns()
        watch_update = RepositoryWatchUpdater(
            changes,
            indexer,
        ).process_ready(force=True)
        if watch_update is None or watch_update.indexing_result is None:
            raise RuntimeError("Watcher overflow did not produce an index update.")
        overflow = watch_update.indexing_result
        operations.append(
            _scenario_metrics(
                "watcher_overflow_recovery",
                _elapsed_ms(started),
                overflow,
                changed_paths=(overflow_path,),
                expected_paths=_expected_paths(overflow, (overflow_path,)),
                extra_checks={
                    "overflow_reported": watch_update.batch.overflowed,
                    "full_rescan_requested": (
                        watch_update.batch.full_rescan_required
                    ),
                    "changed_file_recovered": (
                        overflow_path in overflow.indexed_paths
                    ),
                },
            )
        )

        cancellation_paths = _source_paths(repository_root)[-2:]
        _append_markers(repository_root, cancellation_paths, "cancel")
        cancellation = CancellationToken()
        parse_count = 0

        def cancel_after_first_parse(_request: ParseRequest) -> None:
            nonlocal parse_count
            parse_count += 1
            if parse_count == 1:
                cancellation.request("index scale cancellation injection")

        cancelling_indexer = _indexer(
            repository_root,
            store,
            repository,
            workspace,
            parser_version=parser_version,
            after_parse=cancel_after_first_parse,
        )
        started = time.perf_counter_ns()
        cancelled = cancelling_indexer.update(cancellation=cancellation)
        operations.append(
            _scenario_metrics(
                "cancellation",
                _elapsed_ms(started),
                cancelled,
                changed_paths=cancellation_paths,
                expected_paths=_expected_paths(cancelled, cancellation_paths),
                expected_state=IndexState.PAUSED,
                expected_operation_state=IndexOperationState.PAUSED,
                extra_checks={
                    "injection_fired_once": parse_count == 1,
                    "cancellation_requested": cancellation.is_requested,
                    "checkpoint_published": cancelled.snapshot.file_count
                    == len(_source_paths(repository_root)),
                },
            )
        )

        recovered_store = IndexStore(database_path)
        recovered_indexer = _indexer(
            repository_root,
            recovered_store,
            repository,
            workspace,
            parser_version=parser_version,
        )
        pending_paths = tuple(
            sorted(set(cancellation_paths).difference(cancelled.indexed_paths))
        )
        started = time.perf_counter_ns()
        recovered = recovered_indexer.update()
        recovered_store.verify()
        operations.append(
            _scenario_metrics(
                "restart_recovery",
                _elapsed_ms(started),
                recovered,
                changed_paths=pending_paths,
                expected_paths=_expected_paths(recovered, pending_paths),
                extra_checks={
                    "pending_changes_recovered": set(cancellation_paths).issubset(
                        set(cancelled.indexed_paths)
                        | set(recovered.indexed_paths)
                    ),
                    "new_store_reopened_database": (
                        recovered_store.journal_mode == "wal"
                    ),
                },
            )
        )
        final_snapshot = recovered.snapshot
        database_bytes = _database_size(database_path)
    finally:
        if owns_memory_trace:
            _, peak_memory_bytes = tracemalloc.get_traced_memory()
            tracemalloc.stop()

    environment = _environment()
    environment_payload = json.dumps(
        environment,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return IndexScaleBenchmarkReport(
        profile=generated.profile,
        repository_files=generated.file_count,
        repository_bytes=generated.byte_count,
        repository_fingerprint=generated.fingerprint,
        seed=generated.seed,
        generation_ms=generation_ms,
        database_bytes=database_bytes,
        peak_memory_bytes=peak_memory_bytes,
        final_file_count=final_snapshot.file_count,
        final_snapshot_state=final_snapshot.state.value,
        operations=tuple(operations),
        environment=environment,
        environment_fingerprint=hashlib.sha256(environment_payload).hexdigest(),
        generated_at=datetime.now(UTC).isoformat(),
    )


def _indexer(
    workspace_path: Path,
    store: IndexStore,
    repository: RepositoryIdentity,
    workspace: WorkspaceIdentity,
    *,
    parser_version: str,
    after_parse: Callable[[ParseRequest], None] | None = None,
) -> RepositoryIndexer:
    parser = _VersionedPythonParser(
        version=parser_version,
        after_parse=after_parse,
    )
    return RepositoryIndexer(
        workspace_path,
        repository,
        workspace,
        store,
        registry=ParserRegistry((parser,)),
    )


def _scenario_metrics(
    name: str,
    duration_ms: float,
    result: IndexingResult,
    *,
    changed_paths: Iterable[str],
    expected_paths: Iterable[str],
    expected_state: IndexState = IndexState.CURRENT,
    expected_operation_state: IndexOperationState = IndexOperationState.COMPLETED,
    extra_checks: dict[str, bool] | None = None,
) -> IndexScaleScenarioMetrics:
    changed = set(changed_paths)
    expected = set(expected_paths)
    indexed = set(result.indexed_paths)
    necessary = indexed.intersection(expected)
    unnecessary = indexed.difference(expected)
    checks = {
        "snapshot_state": result.snapshot.state == expected_state,
        "operation_state": (
            result.operation is not None
            and result.operation.state == expected_operation_state
        ),
        "no_unnecessary_reindex": not unnecessary,
    }
    checks.update(extra_checks or {})
    efficiency = len(necessary) / len(indexed) if indexed else 1.0
    return IndexScaleScenarioMetrics(
        name=name,
        duration_ms=duration_ms,
        changed_files=len(changed),
        expected_reindexed_files=len(expected),
        indexed_files=len(indexed),
        necessary_reindexed_files=len(necessary),
        unnecessarily_reindexed_files=len(unnecessary),
        reused_files=len(result.reused_paths),
        invalidated_files=len(result.invalidated_paths),
        deleted_files=len(result.deleted_paths),
        renamed_files=len(result.renamed_paths),
        file_count_after=result.snapshot.file_count,
        snapshot_state=result.snapshot.state.value,
        invalidation_efficiency=efficiency,
        checks=tuple(sorted(checks.items())),
    )


def _expected_paths(
    result: IndexingResult,
    direct_paths: Iterable[str],
) -> tuple[str, ...]:
    dependent_paths = (
        result.invalidation_plan.dependent_paths
        if result.invalidation_plan is not None
        else ()
    )
    return tuple(sorted({*direct_paths, *dependent_paths}))


def _source_paths(repository: Path) -> tuple[str, ...]:
    return tuple(
        sorted(
            path.relative_to(repository).as_posix()
            for path in repository.glob("package_*/*.py")
            if path.is_file()
        )
    )


def _append_markers(
    repository: Path,
    relative_paths: Iterable[str],
    scenario: str,
) -> None:
    for index, relative_path in enumerate(relative_paths):
        target = repository / relative_path
        with target.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(
                f"\nINDEX_SCALE_{scenario.upper()}_{index:05d} = {index}\n"
            )


def _rename_storm(
    repository: Path,
    relative_paths: Iterable[str],
) -> tuple[tuple[str, str], ...]:
    renamed: list[tuple[str, str]] = []
    for index, relative_path in enumerate(relative_paths):
        source = repository / relative_path
        target = source.with_name(f"renamed_{index:03d}_{source.name}")
        source.rename(target)
        renamed.append(
            (relative_path, target.relative_to(repository).as_posix())
        )
    return tuple(renamed)


def _delete_storm(repository: Path, relative_paths: Iterable[str]) -> None:
    for relative_path in relative_paths:
        (repository / relative_path).unlink()


def _write_configuration(repository: Path, *, version: str) -> None:
    (repository / "pyproject.toml").write_text(
        "[project]\n"
        'name = "agentbus-index-scale"\n'
        f'version = "{version}"\n',
        encoding="utf-8",
        newline="\n",
    )


def _selected_file_count(profile: str, file_count: int | None) -> int:
    if profile not in SYNTHETIC_SIZES:
        raise ValueError(
            "Index scale profile must be one of: " + ", ".join(SYNTHETIC_SIZES)
        )
    selected = SYNTHETIC_SIZES[profile] if file_count is None else file_count
    if (
        not isinstance(selected, int)
        or isinstance(selected, bool)
        or selected < _MINIMUM_SCALE_FILES
    ):
        raise ValueError(
            f"Index scale file count must be at least {_MINIMUM_SCALE_FILES}."
        )
    return selected


def _temporary_parent(value: str | Path | None) -> str | None:
    if value is None:
        return None
    parent = Path(value).expanduser().resolve()
    if not parent.is_dir():
        raise ValueError("Index scale temporary parent must be an existing directory.")
    return str(parent)


def _storm_size(file_count: int) -> int:
    return min(_MAXIMUM_STORM_FILES, max(1, file_count // 10))


def _next_patch_version(version: str) -> str:
    core = version.split("-", 1)[0].split("+", 1)[0]
    parts = core.split(".")
    if len(parts) != 3 or not all(part.isdigit() for part in parts):
        raise ValueError("Parser version must use semantic version syntax.")
    major, minor, patch = parts
    return f"{major}.{minor}.{int(patch) + 1}"


def _database_size(path: Path) -> int:
    return sum(
        candidate.stat().st_size
        for candidate in (path, Path(f"{path}-wal"), Path(f"{path}-shm"))
        if candidate.is_file()
    )


def _environment() -> dict[str, Any]:
    return {
        "agentbus_version": __version__,
        "python": platform.python_version(),
        "implementation": platform.python_implementation(),
        "system": platform.system(),
        "machine": platform.machine(),
        "processor_count": os.cpu_count(),
    }


def _elapsed_ms(started_ns: int) -> float:
    return (time.perf_counter_ns() - started_ns) / 1_000_000


__all__ = [
    "INDEX_SCALE_GROUP",
    "IndexScaleBenchmarkReport",
    "IndexScaleScenarioMetrics",
    "run_index_scale_benchmark",
]
