from __future__ import annotations

import tempfile
import time
import tracemalloc
import uuid
from datetime import UTC, datetime
from pathlib import Path

from agentbus.git.repository import (
    GitRepository,
    GitRepositoryError,
    WorkspaceRepositoryMismatch,
)
from agentbus.intelligence.discovery import RepositoryInventoryScanner
from agentbus.intelligence.service import RepositoryIntelligenceService
from agentbus.validation.failures import (
    RepositoryValidationError,
    ResourceLimitExceeded,
    ScenarioValidationError,
    classify_failure,
)
from agentbus.validation.metrics import validation_metric
from agentbus.validation.models import (
    FailureCategory,
    ScenarioKind,
    ValidationFailure,
    ValidationMetric,
    ValidationRepository,
    ValidationRun,
    ValidationScenario,
    ValidationScenarioResult,
    ValidationStatus,
)


class ValidationRunner:
    """Runs providerless, contained repository validation with explicit budgets."""

    def run_repository(
        self,
        repository: ValidationRepository,
        *,
        path: str | Path | None = None,
    ) -> ValidationRun:
        started_at = datetime.now(UTC)
        started = time.monotonic()
        failures: list[ValidationFailure] = []
        warnings: list[str] = []
        metrics: list[ValidationMetric] = []
        scenarios: list[ValidationScenarioResult] = []
        root_fingerprint = "0" * 64
        commit_sha: str | None = None
        file_count = 0
        project_count = 0
        symbol_count = 0
        languages: tuple[str, ...] = ()

        try:
            root = self._repository_root(repository, path)
            inventory = RepositoryInventoryScanner(root).scan()
            root_fingerprint = inventory.fingerprint
            file_count = len(inventory.files)
            repository_bytes = sum(item.size_bytes for item in inventory.files)
            commit_sha = self._commit_sha(root, warnings)
            metrics.extend(
                (
                    validation_metric(
                        "repository.files",
                        "count",
                        file_count,
                        lower_bound=repository.expected_file_count.minimum,
                        upper_bound=repository.expected_file_count.maximum,
                    ),
                    validation_metric(
                        "resource.files",
                        "count",
                        file_count,
                        upper_bound=repository.resource_limits.maximum_files,
                    ),
                    validation_metric(
                        "repository.bytes",
                        "bytes",
                        repository_bytes,
                        upper_bound=repository.resource_limits.maximum_repository_bytes,
                    ),
                )
            )
            self._require_metric_budgets(metrics, repository.repository_id)
            if inventory.truncated:
                message = "Repository discovery reached a configured traversal bound."
                if repository.expected_indexing.allow_partial:
                    warnings.append(message)
                else:
                    raise ResourceLimitExceeded(message)

            with tempfile.TemporaryDirectory(
                prefix="agentbus-validation-"
            ) as temporary:
                database = Path(temporary) / "repository-index.sqlite3"
                service = RepositoryIntelligenceService(root, database)
                index_started = time.monotonic()
                tracing_was_active = tracemalloc.is_tracing()
                if not tracing_was_active:
                    tracemalloc.start()
                try:
                    mutation = service.build()
                    _, peak_memory = tracemalloc.get_traced_memory()
                finally:
                    if not tracing_was_active:
                        tracemalloc.stop()
                index_seconds = time.monotonic() - index_started
                overview = service.overview()
                project_count = len(overview.projects)
                symbol_count = sum(item.symbol_count for item in overview.languages)
                languages = tuple(item.language.value for item in overview.languages)
                database_bytes = self._database_bytes(database)
                metrics.extend(
                    (
                        validation_metric(
                            "repository.projects",
                            "count",
                            project_count,
                            lower_bound=repository.expected_project_count.minimum,
                            upper_bound=repository.expected_project_count.maximum,
                        ),
                        validation_metric(
                            "resource.projects",
                            "count",
                            project_count,
                            upper_bound=repository.resource_limits.maximum_projects,
                        ),
                        validation_metric(
                            "repository.symbols",
                            "count",
                            symbol_count,
                            lower_bound=repository.expected_symbol_count.minimum,
                            upper_bound=repository.expected_symbol_count.maximum,
                        ),
                        validation_metric(
                            "resource.symbols",
                            "count",
                            symbol_count,
                            upper_bound=repository.resource_limits.maximum_symbols,
                        ),
                        validation_metric(
                            "resource.index_duration",
                            "seconds",
                            index_seconds,
                            upper_bound=repository.resource_limits.maximum_index_seconds,
                        ),
                        validation_metric(
                            "resource.index_database",
                            "bytes",
                            database_bytes,
                            upper_bound=(
                                repository.resource_limits.maximum_index_database_bytes
                            ),
                        ),
                        validation_metric(
                            "resource.index_peak_memory",
                            "bytes",
                            peak_memory,
                            upper_bound=(
                                repository.resource_limits.maximum_peak_memory_bytes
                            ),
                        ),
                        validation_metric(
                            "index.skipped_files",
                            "count",
                            mutation.skipped_count,
                        ),
                    )
                )
                self._require_metric_budgets(metrics, repository.repository_id)
                for scenario in repository.scenarios:
                    try:
                        result = self._run_scenario(
                            service,
                            scenario,
                            repository.resource_limits.maximum_query_seconds,
                        )
                    except Exception as exc:
                        failures.append(
                            classify_failure(
                                exc,
                                repository_id=repository.repository_id,
                                scenario_id=scenario.scenario_id,
                            )
                        )
                    else:
                        scenarios.append(result)
                        if result.status == ValidationStatus.FAIL:
                            failures.append(
                                ValidationFailure(
                                    category=self._scenario_failure_category(
                                        scenario.kind
                                    ),
                                    summary=(
                                        f"Validation scenario failed: "
                                        f"{scenario.scenario_id}."
                                    ),
                                    detail=result.detail,
                                    repository_id=repository.repository_id,
                                    scenario_id=scenario.scenario_id,
                                )
                            )
        except Exception as exc:
            failures.append(
                classify_failure(exc, repository_id=repository.repository_id)
            )

        finished_at = datetime.now(UTC)
        status = (
            ValidationStatus.FAIL
            if any(item.fatal for item in failures)
            else ValidationStatus.PASS_WITH_WARNINGS
            if warnings or failures
            else ValidationStatus.PASS
        )
        return ValidationRun(
            run_id=uuid.uuid4().hex,
            repository_id=repository.repository_id,
            status=status,
            root_fingerprint=root_fingerprint,
            commit_sha=commit_sha,
            started_at=started_at,
            finished_at=finished_at,
            duration_seconds=time.monotonic() - started,
            file_count=file_count,
            project_count=project_count,
            symbol_count=symbol_count,
            languages=languages,
            metrics=tuple(metrics),
            scenarios=tuple(scenarios),
            failures=tuple(failures),
            warnings=tuple(warnings),
        )

    @staticmethod
    def _repository_root(
        repository: ValidationRepository,
        path: str | Path | None,
    ) -> Path:
        selected = path if path is not None else repository.path
        if selected is None:
            raise ValueError(
                "Repository validation requires an explicit local checkout path."
            )
        root = Path(selected).expanduser().resolve(strict=True)
        if not root.is_dir():
            raise ValueError("Repository validation path must be a directory.")
        return root

    @staticmethod
    def _commit_sha(root: Path, warnings: list[str]) -> str | None:
        repository = GitRepository(str(root), timeout_seconds=10)
        try:
            if repository.is_git_repo():
                return repository.head_commit(short=False)
        except WorkspaceRepositoryMismatch:
            warnings.append(
                "Git resolves this directory to a parent repository; commit provenance "
                "was intentionally omitted."
            )
        except GitRepositoryError:
            pass
        return None

    @staticmethod
    def _database_bytes(database: Path) -> int:
        return sum(
            candidate.stat().st_size
            for candidate in (
                database,
                database.with_name(database.name + "-wal"),
                database.with_name(database.name + "-shm"),
            )
            if candidate.exists()
        )

    @staticmethod
    def _require_metric_budgets(
        metrics: list[ValidationMetric], repository_id: str
    ) -> None:
        failed = [item.name for item in metrics if item.passed is False]
        if failed:
            resource_failures = [
                name for name in failed if name.startswith("resource.")
            ]
            if resource_failures:
                raise ResourceLimitExceeded(
                    f"Repository '{repository_id}' exceeded validation bounds: "
                    + ", ".join(resource_failures)
                )
            raise RepositoryValidationError(
                f"Repository '{repository_id}' did not meet expected characteristics: "
                + ", ".join(failed)
            )

    def _run_scenario(
        self,
        service: RepositoryIntelligenceService,
        scenario: ValidationScenario,
        default_maximum_seconds: float,
    ) -> ValidationScenarioResult:
        started = time.monotonic()
        observed_paths: tuple[str, ...] = ()
        observed_count = 0
        truncated = False
        if scenario.kind == ScenarioKind.INDEX:
            verification = service.verify()
            observed_count = int(verification.valid and verification.fresh)
        elif scenario.kind == ScenarioKind.SEARCH:
            report = service.search(
                scenario.query or "",
                limit=scenario.maximum_results,
            )
            observed_paths = tuple(item.relative_path for item in report.results)
            observed_count = len(report.results)
        elif scenario.kind in {ScenarioKind.CONTEXT, ScenarioKind.TASK}:
            report = service.context_plan(
                scenario.query or "",
                byte_budget=scenario.byte_budget,
                token_budget=scenario.token_budget,
            )
            observed_paths = tuple(
                item.relative_path for item in report.candidates if item.selected
            )
            observed_count = sum(item.selected for item in report.candidates)
        elif scenario.kind == ScenarioKind.IMPACT:
            report = service.impact(scenario.subjects)
            observed_paths = tuple(
                dict.fromkeys((*report.changed_paths, *report.tests.selected_tests))
            )
            observed_count = len(report.direct_dependents) + len(
                report.transitive_dependents
            )
            truncated = report.truncated
        else:
            raise ScenarioValidationError("Unsupported validation scenario kind.")
        duration = time.monotonic() - started
        maximum = scenario.maximum_duration_seconds or default_maximum_seconds
        expected = set(scenario.expected_paths)
        observed = set(observed_paths)
        missing = tuple(sorted(expected - observed))
        matched = tuple(sorted(expected & observed))
        passed = (
            observed_count >= scenario.expected_minimum_results
            and not missing
            and duration <= maximum
        )
        detail_parts: list[str] = []
        if observed_count < scenario.expected_minimum_results:
            detail_parts.append(
                f"observed {observed_count} result(s), expected at least "
                f"{scenario.expected_minimum_results}"
            )
        if missing:
            detail_parts.append("missing expected paths: " + ", ".join(missing[:20]))
        if duration > maximum:
            detail_parts.append(
                f"duration {duration:.3f}s exceeded {maximum:.3f}s"
            )
        return ValidationScenarioResult(
            scenario_id=scenario.scenario_id,
            kind=scenario.kind,
            status=ValidationStatus.PASS if passed else ValidationStatus.FAIL,
            duration_seconds=duration,
            observed_count=observed_count,
            matched_expected_paths=matched,
            missing_expected_paths=missing,
            truncated=truncated,
            detail="; ".join(detail_parts) or None,
        )

    @staticmethod
    def _scenario_failure_category(kind: ScenarioKind) -> FailureCategory:
        return {
            ScenarioKind.INDEX: FailureCategory.INDEXING,
            ScenarioKind.SEARCH: FailureCategory.SEARCH,
            ScenarioKind.CONTEXT: FailureCategory.SEARCH,
            ScenarioKind.IMPACT: FailureCategory.IMPACT,
            ScenarioKind.TASK: FailureCategory.TASK,
        }[kind]
