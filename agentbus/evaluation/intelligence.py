from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from pydantic import Field

from agentbus.evaluation.assertions import RuntimeObservation
from agentbus.evaluation.budget import EvaluationBudget
from agentbus.evaluation.fixtures import FixtureWorkspace
from agentbus.evaluation.models import (
    EvaluationCase,
    EvaluationMetrics,
    EvaluationModel,
    EvaluationSuite,
    EvaluationVariant,
    ExecutionMetrics,
    GitMetrics,
    ProviderMetrics,
    QualityMetrics,
    RunStatus,
)
from agentbus.intelligence import (
    IndexState,
    RepositoryIntelligenceService,
)


_INTERPRETATION_NOTE = (
    "Deterministic synthetic-fixture measurements only; these small samples "
    "do not establish statistical significance."
)


class RepositoryIntelligenceBenchmarkMetrics(EvaluationModel):
    indexing_correctness: float = Field(ge=0, le=1)
    incremental_invalidation_correctness: float | None = Field(
        default=None,
        ge=0,
        le=1,
    )
    incremental_reuse_ratio: float | None = Field(default=None, ge=0, le=1)
    symbol_precision: float | None = Field(default=None, ge=0, le=1)
    reference_precision: float | None = Field(default=None, ge=0, le=1)
    retrieval_precision: float | None = Field(default=None, ge=0, le=1)
    impact_recall: float | None = Field(default=None, ge=0, le=1)
    test_impact_recall: float | None = Field(default=None, ge=0, le=1)
    context_budget_adherence: float | None = Field(default=None, ge=0, le=1)
    protected_generated_exclusion: float | None = Field(
        default=None,
        ge=0,
        le=1,
    )
    stale_index_detected: bool | None = None
    build_latency_seconds: float = Field(ge=0)
    update_latency_seconds: float | None = Field(default=None, ge=0)
    storage_bytes: int = Field(ge=0)
    indexed_files: int = Field(ge=0)
    indexed_symbols: int = Field(ge=0)
    indexed_references: int = Field(ge=0)
    dependency_edges: int = Field(ge=0)
    provider_calls: Literal[0] = 0
    network_calls: Literal[0] = 0
    interpretation_note: str = _INTERPRETATION_NOTE


@dataclass
class RepositoryIntelligenceBackendResult:
    observation: RuntimeObservation
    metrics: EvaluationMetrics
    runtime_run_id: str | None
    failure_category: str | None = None
    failure_message: str | None = None
    raw_metrics: dict[str, Any] | None = None


class RepositoryIntelligenceEvaluationBackend:
    """Run bounded repository benchmarks through the real local index service."""

    def execute(
        self,
        case: EvaluationCase,
        variant: EvaluationVariant,
        fixture: FixtureWorkspace,
        budget: EvaluationBudget,
    ) -> RepositoryIntelligenceBackendResult:
        started = time.perf_counter()
        benchmark = _benchmark_metadata(case)
        checks: dict[str, bool] = {}
        details: dict[str, Any] = {}
        source_before = _tree_fingerprint(fixture.source)
        broken_source_fixture = benchmark.get("broken_source_fixture")
        if isinstance(broken_source_fixture, str):
            details["broken_source"] = _materialize_broken_source(
                fixture.repository,
                broken_source_fixture,
            )
        if benchmark.get("scenario") == "large":
            details["large_repository"] = _prepare_large_repository(
                fixture.repository,
                int(benchmark.get("module_count", 120)),
            )

        budget.check_time()
        database_path = fixture.owned_root / "state" / "repository-index.sqlite3"
        service = RepositoryIntelligenceService(
            fixture.repository,
            database_path,
            repository_key=f"evaluation/{case.case_id}",
        )
        build_started = time.perf_counter()
        built = service.build()
        build_seconds = time.perf_counter() - build_started
        reports: list[Any] = [built]
        overview = service.overview()
        reports.append(overview)
        initial_snapshot = built.snapshot
        files = service.store.list_files(initial_snapshot.snapshot_id)
        modules = service.store.list_modules(initial_snapshot.snapshot_id)
        symbols = service.store.list_symbols(initial_snapshot.snapshot_id)
        references = service.store.list_references(initial_snapshot.snapshot_id)

        checks["providerless_variant"] = variant.provider == "fake" and not variant.live
        expected_snapshot_state = str(
            benchmark.get("expected_snapshot_state", IndexState.CURRENT.value)
        )
        expected_status_state = str(
            benchmark.get("expected_status_state", expected_snapshot_state)
        )
        checks["initial_snapshot_state"] = (
            built.snapshot.state.value == expected_snapshot_state
        )
        checks["initial_index_status"] = (
            built.status.state.value == expected_status_state
        )
        indexing_score, exclusion_score = _evaluate_inventory(
            benchmark,
            fixture.repository,
            initial_snapshot,
            overview,
            files,
            checks,
            details,
        )
        symbol_precision = _evaluate_symbols(
            benchmark,
            symbols,
            checks,
            details,
        )
        reference_precision = _evaluate_references(
            benchmark,
            symbols,
            references,
            checks,
            details,
        )
        retrieval_precision = _evaluate_search(
            benchmark,
            service,
            checks,
            details,
            reports,
        )
        impact_recall, test_recall, context_adherence = _evaluate_planning(
            benchmark,
            service,
            files,
            modules,
            symbols,
            checks,
            details,
            reports,
        )

        incremental_score = None
        reuse_ratio = None
        stale_detected = None
        update_seconds = None
        if benchmark.get("scenario") == "incremental":
            (
                incremental_score,
                reuse_ratio,
                stale_detected,
                update_seconds,
                incremental_reports,
            ) = _evaluate_incremental(
                service,
                fixture.repository,
                checks,
                details,
            )
            reports.extend(incremental_reports)

        budget.check_time()
        verification = service.verify()
        reports.append(verification)
        checks["index_valid"] = verification.valid
        expected_fresh = bool(
            benchmark.get(
                "expect_fresh",
                expected_status_state == IndexState.CURRENT.value,
            )
        )
        checks["index_expected_freshness"] = verification.fresh == expected_fresh
        final_status = verification.status
        if final_status.snapshot_id is None:
            final_snapshot = initial_snapshot
        else:
            final_snapshot = service.store.get_snapshot(final_status.snapshot_id)

        provider_calls = sum(
            int(getattr(report, "provider_calls", 0)) for report in reports
        )
        network_calls = sum(
            int(getattr(report, "network_calls", 0)) for report in reports
        )
        checks["zero_provider_calls"] = provider_calls == 0
        checks["zero_network_calls"] = network_calls == 0
        source_unchanged = source_before == _tree_fingerprint(fixture.source)
        checks["source_fixture_unchanged"] = source_unchanged
        storage_bytes = _database_size(database_path)
        maximum_storage = int(benchmark.get("maximum_storage_bytes", 32 * 1024 * 1024))
        checks["storage_within_bound"] = storage_bytes <= maximum_storage

        elapsed = time.perf_counter() - started
        passed = all(checks.values())
        metrics_record = RepositoryIntelligenceBenchmarkMetrics(
            indexing_correctness=indexing_score,
            incremental_invalidation_correctness=incremental_score,
            incremental_reuse_ratio=reuse_ratio,
            symbol_precision=symbol_precision,
            reference_precision=reference_precision,
            retrieval_precision=retrieval_precision,
            impact_recall=impact_recall,
            test_impact_recall=test_recall,
            context_budget_adherence=context_adherence,
            protected_generated_exclusion=exclusion_score,
            stale_index_detected=stale_detected,
            build_latency_seconds=build_seconds,
            update_latency_seconds=update_seconds,
            storage_bytes=storage_bytes,
            indexed_files=final_snapshot.file_count,
            indexed_symbols=final_snapshot.symbol_count,
            indexed_references=final_snapshot.reference_count,
            dependency_edges=final_snapshot.edge_count,
        )
        failed_checks = [name for name, value in checks.items() if not value]
        diagnostic = json.dumps(
            {
                "benchmark_checks": checks,
                "provider_calls": 0,
                "network_calls": 0,
            },
            sort_keys=True,
        )
        observation = RuntimeObservation(
            repository=fixture.repository,
            run_status=(
                RunStatus.SUCCEEDED.value if passed else RunStatus.FAILED.value
            ),
            verifier_passed=passed,
            reviewer_approved=None,
            source_unchanged=source_unchanged,
            total_tokens=0,
            total_requests=0,
            elapsed_seconds=elapsed,
            retries=0,
            safety_violations=[],
            sanitized_diagnostic_text=diagnostic,
        )
        metrics = EvaluationMetrics(
            quality=QualityMetrics(
                verifier_passed=passed,
                reviewer_approved=None,
            ),
            execution=ExecutionMetrics(total_duration_seconds=elapsed),
            provider=ProviderMetrics(),
            git=GitMetrics(),
        )
        return RepositoryIntelligenceBackendResult(
            observation=observation,
            metrics=metrics,
            runtime_run_id=None,
            failure_category=(
                "RepositoryIntelligenceBenchmarkFailed" if failed_checks else None
            ),
            failure_message=(
                "Benchmark checks failed: " + ", ".join(failed_checks[:20])
                if failed_checks
                else None
            ),
            raw_metrics={
                "repository_intelligence": metrics_record.model_dump(mode="json"),
                "checks": checks,
                "details": details,
                "budget": budget.snapshot(),
            },
        )


def repository_intelligence_suite() -> EvaluationSuite:
    common = {
        "limits": {
            "max_requests": 0,
            "max_tokens": 0,
            "max_retries": 0,
            "max_elapsed_seconds": 180,
        },
        "expect_source_unchanged": True,
    }
    mixed_inventory = {
        "expected_languages": {
            "go": 2,
            "java": 3,
            "python": 8,
            "typescript": 3,
        },
        "expected_projects": [
            "@fixture/web",
            "example.invalid/intelligence",
            "intelligence-java-service",
            "intelligence-monorepo",
            "intelligence-python-service",
            "intelligence-shared-python",
        ],
        "minimum_counts": {
            "files": 16,
            "symbols": 40,
            "references": 50,
            "edges": 50,
        },
        "expected_diagnostics": [
            "parser.python_syntax_error",
            "architecture.project_crossings",
            "architecture.dependency_cycles",
        ],
        "excluded_paths": [".env", "dist/bundle.js"],
        "minimum_ownership_rules": 5,
        "minimum_architecture_boundaries": 6,
        "maximum_storage_bytes": 8 * 1024 * 1024,
        "expected_snapshot_state": IndexState.PARTIALLY_CURRENT.value,
        "expected_status_state": IndexState.PARTIALLY_CURRENT.value,
        "expect_fresh": False,
        "broken_source_fixture": "services/python_service/broken.py.fixture",
    }
    cases = [
        EvaluationCase(
            case_id="multilingual-index-correctness",
            title="Multilingual index correctness",
            task_prompt=(
                "Index a mixed Python, TypeScript, Java, and Go monorepo "
                "without providers or project execution."
            ),
            fixture_repository_source="repository-intelligence-mixed",
            expected_files=[
                "services/python_service/calculator.py",
                "packages/web/src/calculator.ts",
                "services/java/src/main/java/fixture/OrderService.java",
                "services/go/main.go",
            ],
            expected_reviewer_approved=None,
            maximum_attempts=1,
            timeout_seconds=180,
            durable_mode=False,
            tags={"repository-intelligence", "offline", "multilingual"},
            metadata={
                **common,
                "repository_intelligence": {
                    **mixed_inventory,
                    "scenario": "multilingual",
                    "symbol_precision_minimum": 0.85,
                    "reference_precision_minimum": 1.0,
                    "symbol_probes": [
                        _symbol_probe("calculate_total", "services/python_service/calculator.py", "function", "python"),
                        _symbol_probe("normalize_total", "packages/shared_python/rules.py", "function", "python"),
                        _symbol_probe("CalculatorPanel", "packages/web/src/calculator.ts", "class", "typescript"),
                        _symbol_probe("OrderService", "services/java/src/main/java/fixture/OrderService.java", "class", "java"),
                        _symbol_probe("healthHandler", "services/go/main.go", "function", "go"),
                        _symbol_probe("test_calculate_total", "services/python_service/tests/test_calculator.py", "test", "python"),
                        _symbol_probe("testTotal", "services/java/src/test/java/fixture/OrderServiceTest.java", "test", "java"),
                        _symbol_probe("TestRegisterRoutes", "services/go/main_test.go", "test", "go"),
                    ],
                    "reference_probes": [
                        _reference_probe("calculate_total", "services/python_service/calculator.py", "add_values", "services/python_service/pricing.py", "calls"),
                        _reference_probe("add_values", "services/python_service/pricing.py", "normalize_total", "packages/shared_python/rules.py", "calls"),
                        _reference_probe("test_calculate_total", "services/python_service/tests/test_calculator.py", "calculate_total", "services/python_service/calculator.py", "calls"),
                        _reference_probe("cycle_a", "services/python_service/cycle_a.py", "cycle_b", "services/python_service/cycle_b.py", "calls"),
                        _reference_probe("cycle_b", "services/python_service/cycle_b.py", "cycle_a", "services/python_service/cycle_a.py", "calls"),
                        _reference_probe("calculate", "packages/web/src/calculator.ts", "requestCalculation", "packages/web/src/api.ts", "exports"),
                        _reference_probe("testTotal", "services/java/src/test/java/fixture/OrderServiceTest.java", "OrderService", "services/java/src/main/java/fixture/OrderService.java", "instantiates"),
                        _reference_probe("main", "services/go/main.go", "registerRoutes", "services/go/main.go", "calls"),
                    ],
                },
            },
        ),
        EvaluationCase(
            case_id="graph-retrieval-impact",
            title="Graph retrieval and change impact",
            task_prompt=(
                "Evaluate bounded search, dependency impact, relevant tests, "
                "and role-specific context planning."
            ),
            fixture_repository_source="repository-intelligence-mixed",
            expected_files=["docs/adr/0001-boundaries.md", "CODEOWNERS"],
            expected_reviewer_approved=None,
            maximum_attempts=1,
            timeout_seconds=180,
            durable_mode=False,
            tags={"repository-intelligence", "offline", "retrieval", "impact"},
            metadata={
                **common,
                "repository_intelligence": {
                    **mixed_inventory,
                    "scenario": "planning",
                    "retrieval_precision_minimum": 1.0,
                    "impact_recall_minimum": 1.0,
                    "test_recall_minimum": 1.0,
                    "search_probes": [
                        _search_probe("calculate_total", "services/python_service/calculator.py", 1),
                        _search_probe("CalculatorPanel", "packages/web/src/calculator.ts", 1),
                        _search_probe("OrderService", "services/java/src/main/java/fixture/OrderService.java", 5),
                        _search_probe("healthHandler", "services/go/main.go", 1),
                    ],
                    "impact_probes": [
                        {
                            "subject": "services/python_service/calculator.py",
                            "expected_paths": [
                                "services/python_service/app.py",
                                "services/python_service/tests/test_calculator.py",
                            ],
                        },
                        {
                            "subject": "packages/web/src/calculator.ts",
                            "expected_paths": ["packages/web/test/calculator.test.ts"],
                        },
                        {
                            "subject": "services/java/src/main/java/fixture/OrderService.java",
                            "expected_paths": ["services/java/src/test/java/fixture/OrderServiceTest.java"],
                        },
                    ],
                    "test_probes": [
                        _test_probe("services/python_service/calculator.py", "services/python_service/tests/test_calculator.py"),
                        _test_probe("packages/web/src/calculator.ts", "packages/web/test/calculator.test.ts"),
                        _test_probe("services/java/src/main/java/fixture/OrderService.java", "services/java/src/test/java/fixture/OrderServiceTest.java"),
                        _test_probe("services/go/main.go", "services/go/main_test.go"),
                    ],
                    "context_probe": {
                        "task": "Change calculate_total and run relevant calculator tests",
                        "changed_paths": ["services/python_service/calculator.py"],
                        "expected_paths": [
                            "services/python_service/calculator.py",
                            "services/python_service/tests/test_calculator.py",
                        ],
                        "byte_budget": 12_000,
                        "token_budget": 2_000,
                    },
                },
            },
        ),
        EvaluationCase(
            case_id="incremental-staleness-and-rename",
            title="Incremental staleness, invalidation, and rename",
            task_prompt=(
                "Modify one dependency, detect staleness, update dependents, "
                "then rename a module while reusing unaffected records."
            ),
            fixture_repository_source="repository-intelligence-incremental",
            expected_files=["core.py", "renderer.py", "test_service.py"],
            forbidden_files=["service.py"],
            expected_reviewer_approved=None,
            maximum_attempts=1,
            timeout_seconds=180,
            durable_mode=False,
            tags={"repository-intelligence", "offline", "incremental"},
            metadata={
                **common,
                "repository_intelligence": {
                    "scenario": "incremental",
                    "expected_languages": {"python": 3},
                    "expected_projects": ["intelligence-incremental"],
                    "minimum_counts": {
                        "files": 3,
                        "symbols": 6,
                        "references": 4,
                        "edges": 4,
                    },
                    "maximum_storage_bytes": 8 * 1024 * 1024,
                },
            },
        ),
        EvaluationCase(
            case_id="large-repository-budget",
            title="Large repository indexing and context budget",
            task_prompt=(
                "Index a deterministic generated repository structure and "
                "keep retrieval and context selection bounded."
            ),
            fixture_repository_source="repository-intelligence-large",
            expected_files=["README.md", "src/module_0119.py"],
            expected_reviewer_approved=None,
            maximum_attempts=1,
            timeout_seconds=180,
            durable_mode=False,
            tags={"repository-intelligence", "offline", "large-repository"},
            metadata={
                **common,
                "repository_intelligence": {
                    "scenario": "large",
                    "module_count": 120,
                    "expected_languages": {"python": 132},
                    "expected_projects": ["intelligence-large"],
                    "minimum_counts": {
                        "files": 132,
                        "symbols": 250,
                        "references": 200,
                        "edges": 200,
                    },
                    "maximum_storage_bytes": 32 * 1024 * 1024,
                    "retrieval_precision_minimum": 1.0,
                    "search_probes": [
                        _search_probe("value_0119", "src/module_0119.py", 1),
                    ],
                    "context_probe": {
                        "task": "Change value_0119 without loading the whole repository",
                        "changed_paths": ["src/module_0119.py"],
                        "expected_paths": ["src/module_0119.py"],
                        "byte_budget": 8_000,
                        "token_budget": 1_000,
                    },
                },
            },
        ),
    ]
    return EvaluationSuite(
        suite_id="repository-intelligence",
        title="AgentBus repository intelligence offline evaluation",
        description=(
            "Providerless synthetic benchmarks for multilingual indexing, "
            "incremental reuse, retrieval, graph impact, and context budgets."
        ),
        cases=cases,
        default_variant="deterministic",
        tags={"offline", "ci", "repository-intelligence"},
        metadata={"repository_intelligence_backend": True},
    )


def _evaluate_inventory(
    benchmark: dict[str, Any],
    repository: Path,
    snapshot: Any,
    overview: Any,
    files: tuple[Any, ...],
    checks: dict[str, bool],
    details: dict[str, Any],
) -> tuple[float, float | None]:
    outcomes: list[bool] = []
    language_counts = {
        item.language.value: item.file_count for item in overview.languages
    }
    for language, expected in benchmark.get("expected_languages", {}).items():
        passed = language_counts.get(language) == int(expected)
        checks[f"language_{language}"] = passed
        outcomes.append(passed)
    project_names = {item.name for item in overview.projects}
    for name in benchmark.get("expected_projects", []):
        passed = name in project_names
        checks[f"project_{_check_name(name)}"] = passed
        outcomes.append(passed)
    counts = {
        "files": snapshot.file_count,
        "symbols": snapshot.symbol_count,
        "references": snapshot.reference_count,
        "edges": snapshot.edge_count,
    }
    for name, minimum in benchmark.get("minimum_counts", {}).items():
        passed = counts.get(name, 0) >= int(minimum)
        checks[f"minimum_{name}"] = passed
        outcomes.append(passed)
    diagnostic_codes = {item.code for item in snapshot.diagnostics}
    for code in benchmark.get("expected_diagnostics", []):
        passed = code in diagnostic_codes
        checks[f"diagnostic_{_check_name(code)}"] = passed
        outcomes.append(passed)
    ownership_minimum = int(benchmark.get("minimum_ownership_rules", 0))
    if ownership_minimum:
        passed = len(overview.ownership_rules) >= ownership_minimum
        checks["ownership_rules_detected"] = passed
        outcomes.append(passed)
    boundary_minimum = int(benchmark.get("minimum_architecture_boundaries", 0))
    if boundary_minimum:
        passed = len(overview.architecture_boundaries) >= boundary_minimum
        checks["architecture_boundaries_detected"] = passed
        outcomes.append(passed)

    indexed_paths = {item.relative_path for item in files}
    exclusion_outcomes: list[bool] = []
    for relative_path in benchmark.get("excluded_paths", []):
        present = (repository / relative_path).is_file()
        excluded = relative_path not in indexed_paths
        checks[f"fixture_present_{_check_name(relative_path)}"] = present
        checks[f"excluded_{_check_name(relative_path)}"] = excluded
        outcomes.extend((present, excluded))
        exclusion_outcomes.extend((present, excluded))
    details["inventory"] = {
        "counts": counts,
        "languages": language_counts,
        "projects": sorted(project_names),
        "diagnostics": sorted(diagnostic_codes),
        "excluded_paths": sorted(benchmark.get("excluded_paths", [])),
    }
    return _ratio(outcomes), (
        _ratio(exclusion_outcomes) if exclusion_outcomes else None
    )


def _evaluate_symbols(
    benchmark: dict[str, Any],
    symbols: tuple[Any, ...],
    checks: dict[str, bool],
    details: dict[str, Any],
) -> float | None:
    probes = benchmark.get("symbol_probes", [])
    if not probes:
        return None
    true_positives = 0
    predicted = 0
    outcomes: dict[str, bool] = {}
    for index, probe in enumerate(probes):
        candidates = [item for item in symbols if item.name == probe["name"]]
        predicted += len(candidates)
        matched = any(
            item.location.relative_path == probe["path"]
            and item.kind.value == probe["kind"]
            and item.language.value == probe["language"]
            for item in candidates
        )
        true_positives += int(matched)
        key = f"symbol_{index}_{_check_name(probe['name'])}"
        checks[key] = matched
        outcomes[probe["name"]] = matched
    precision = true_positives / max(len(probes), predicted, 1)
    checks["symbol_precision_threshold"] = precision >= float(
        benchmark.get("symbol_precision_minimum", 0)
    )
    details["symbol_probes"] = outcomes
    return precision


def _evaluate_references(
    benchmark: dict[str, Any],
    symbols: tuple[Any, ...],
    references: tuple[Any, ...],
    checks: dict[str, bool],
    details: dict[str, Any],
) -> float | None:
    probes = benchmark.get("reference_probes", [])
    if not probes:
        return None
    symbols_by_id = {item.symbol_id: item for item in symbols}
    outcomes: dict[str, bool] = {}
    hits = 0
    for index, probe in enumerate(probes):
        matched = False
        for reference in references:
            source = symbols_by_id.get(reference.source_symbol_id)
            target = symbols_by_id.get(reference.target_symbol_id)
            if source is None or target is None:
                continue
            if (
                source.name == probe["source_name"]
                and source.location.relative_path == probe["source_path"]
                and target.name == probe["target_name"]
                and target.location.relative_path == probe["target_path"]
                and reference.kind.value == probe["kind"]
            ):
                matched = True
                break
        hits += int(matched)
        label = f"{probe['source_name']}->{probe['target_name']}"
        checks[f"reference_{index}_{_check_name(label)}"] = matched
        outcomes[label] = matched
    cross_language = [
        reference.reference_id
        for reference in references
        if reference.source_symbol_id in symbols_by_id
        and reference.target_symbol_id in symbols_by_id
        and symbols_by_id[reference.source_symbol_id].language
        != symbols_by_id[reference.target_symbol_id].language
    ]
    checks["no_cross_language_heuristic_references"] = not cross_language
    precision = (hits + int(not cross_language)) / (len(probes) + 1)
    checks["reference_precision_threshold"] = precision >= float(
        benchmark.get("reference_precision_minimum", 0)
    )
    details["reference_probes"] = outcomes
    details["cross_language_reference_count"] = len(cross_language)
    return precision


def _evaluate_search(
    benchmark: dict[str, Any],
    service: RepositoryIntelligenceService,
    checks: dict[str, bool],
    details: dict[str, Any],
    reports: list[Any],
) -> float | None:
    probes = benchmark.get("search_probes", [])
    if not probes:
        return None
    outcomes: dict[str, bool] = {}
    hits = 0
    for index, probe in enumerate(probes):
        limit = int(probe["top_k"])
        report = service.search(probe["query"], limit=limit)
        reports.append(report)
        paths = [item.relative_path for item in report.results[:limit]]
        matched = probe["expected_path"] in paths
        hits += int(matched)
        checks[f"search_{index}_{_check_name(probe['query'])}"] = matched
        outcomes[probe["query"]] = matched
    precision = hits / len(probes)
    checks["retrieval_precision_threshold"] = precision >= float(
        benchmark.get("retrieval_precision_minimum", 0)
    )
    details["search_probes"] = outcomes
    return precision


def _evaluate_planning(
    benchmark: dict[str, Any],
    service: RepositoryIntelligenceService,
    files: tuple[Any, ...],
    modules: tuple[Any, ...],
    symbols: tuple[Any, ...],
    checks: dict[str, bool],
    details: dict[str, Any],
    reports: list[Any],
) -> tuple[float | None, float | None, float | None]:
    node_paths = {item.file_id: item.relative_path for item in files}
    node_paths.update({item.module_id: item.relative_path for item in modules})
    node_paths.update(
        {item.symbol_id: item.location.relative_path for item in symbols}
    )
    impact_hits = impact_total = 0
    impact_details: dict[str, list[str]] = {}
    for index, probe in enumerate(benchmark.get("impact_probes", [])):
        result = service.impact((probe["subject"],), max_depth=4)
        affected = {
            node_paths[identity]
            for identity in (*result.direct_dependents, *result.transitive_dependents)
            if identity in node_paths
        }
        expected = set(probe["expected_paths"])
        matched = expected.intersection(affected)
        impact_hits += len(matched)
        impact_total += len(expected)
        passed = matched == expected
        checks[f"impact_{index}_{_check_name(probe['subject'])}"] = passed
        impact_details[probe["subject"]] = sorted(affected)
    impact_recall = impact_hits / impact_total if impact_total else None
    if impact_recall is not None:
        checks["impact_recall_threshold"] = impact_recall >= float(
            benchmark.get("impact_recall_minimum", 0)
        )
        details["impact_paths"] = impact_details

    test_hits = test_total = 0
    test_details: dict[str, list[str]] = {}
    for index, probe in enumerate(benchmark.get("test_probes", [])):
        result = service.tests_for((probe["subject"],), max_depth=4)
        selected = set(result.selected_tests)
        expected = set(probe["expected_tests"])
        matched = expected.intersection(selected)
        test_hits += len(matched)
        test_total += len(expected)
        passed = matched == expected
        checks[f"tests_{index}_{_check_name(probe['subject'])}"] = passed
        test_details[probe["subject"]] = sorted(selected)
    test_recall = test_hits / test_total if test_total else None
    if test_recall is not None:
        checks["test_recall_threshold"] = test_recall >= float(
            benchmark.get("test_recall_minimum", 0)
        )
        details["selected_tests"] = test_details

    context_adherence = None
    context_probe = benchmark.get("context_probe")
    if isinstance(context_probe, dict):
        context = service.context_plan(
            context_probe["task"],
            role="coder",
            byte_budget=int(context_probe["byte_budget"]),
            token_budget=int(context_probe["token_budget"]),
            changed_paths=context_probe.get("changed_paths", []),
        )
        reports.append(context)
        selected = {
            item.relative_path for item in context.candidates if item.selected
        }
        outcomes = [
            context.selected_bytes <= context.byte_budget,
            context.selected_tokens <= context.token_budget,
            set(context_probe.get("expected_paths", [])).issubset(selected),
        ]
        checks["context_byte_budget"] = outcomes[0]
        checks["context_token_budget"] = outcomes[1]
        checks["context_expected_paths"] = outcomes[2]
        context_adherence = _ratio(outcomes)
        details["context_plan"] = {
            "selected_bytes": context.selected_bytes,
            "selected_tokens": context.selected_tokens,
            "byte_budget": context.byte_budget,
            "token_budget": context.token_budget,
            "selected_paths": sorted(selected),
        }
    return impact_recall, test_recall, context_adherence


def _evaluate_incremental(
    service: RepositoryIntelligenceService,
    repository: Path,
    checks: dict[str, bool],
    details: dict[str, Any],
) -> tuple[float, float, bool, float, list[Any]]:
    core = repository / "core.py"
    core.write_text(
        core.read_text(encoding="utf-8")
        + "\n\ndef normalize_key(value: str) -> str:\n"
        + "    return normalize(value).lower()\n",
        encoding="utf-8",
    )
    stale_after_edit = service.status()
    first_started = time.perf_counter()
    first_update = service.update()
    first_seconds = time.perf_counter() - first_started

    service_path = repository / "service.py"
    renderer_path = repository / "renderer.py"
    service_path.rename(renderer_path)
    test_path = repository / "test_service.py"
    test_path.write_text(
        test_path.read_text(encoding="utf-8").replace(
            "from service import render",
            "from renderer import render",
        ),
        encoding="utf-8",
    )
    stale_after_rename = service.status()
    second_started = time.perf_counter()
    second_update = service.update()
    second_seconds = time.perf_counter() - second_started

    outcomes = {
        "edit_stale": stale_after_edit.state == IndexState.STALE,
        "edit_path_detected": "core.py" in stale_after_edit.stale_paths,
        "dependent_service_invalidated": "service.py" in first_update.invalidated_paths,
        "dependent_test_invalidated": "test_service.py" in first_update.invalidated_paths,
        "rename_stale": stale_after_rename.state == IndexState.STALE,
        "rename_detected": second_update.renamed_count == 1,
        "renamed_file_indexed": "renderer.py" in second_update.indexed_paths,
        "unaffected_core_reused": "core.py" in second_update.reused_paths,
        "old_path_removed": not service_path.exists(),
    }
    for name, passed in outcomes.items():
        checks[f"incremental_{name}"] = passed
    final_total = max(second_update.snapshot.file_count, 1)
    reuse_ratio = second_update.reused_count / final_total
    checks["incremental_reuse_observed"] = reuse_ratio > 0
    details["incremental"] = {
        "first_update": {
            "indexed_count": first_update.indexed_count,
            "invalidated_count": first_update.invalidated_count,
            "indexed_paths": list(first_update.indexed_paths),
            "invalidated_paths": list(first_update.invalidated_paths),
        },
        "second_update": {
            "indexed_count": second_update.indexed_count,
            "reused_count": second_update.reused_count,
            "renamed_count": second_update.renamed_count,
            "indexed_paths": list(second_update.indexed_paths),
            "reused_paths": list(second_update.reused_paths),
        },
    }
    return (
        _ratio(list(outcomes.values())),
        reuse_ratio,
        outcomes["edit_stale"] and outcomes["rename_stale"],
        first_seconds + second_seconds,
        [first_update, second_update],
    )


def _prepare_large_repository(repository: Path, module_count: int) -> dict[str, int]:
    if module_count < 10 or module_count > 500:
        raise ValueError("large repository module count must be between 10 and 500")
    source = repository / "src"
    tests = repository / "tests"
    source.mkdir(parents=True, exist_ok=True)
    tests.mkdir(parents=True, exist_ok=True)
    (repository / "pyproject.toml").write_text(
        "[project]\nname = \"intelligence-large\"\nversion = \"0.1.0\"\n",
        encoding="utf-8",
    )
    test_count = 0
    for index in range(module_count):
        name = f"{index:04d}"
        if index == 0:
            body = f"def value_{name}() -> int:\n    return 0\n"
        else:
            previous = f"{index - 1:04d}"
            body = (
                f"from src.module_{previous} import value_{previous}\n\n\n"
                f"def value_{name}() -> int:\n"
                f"    return value_{previous}() + 1\n"
            )
        (source / f"module_{name}.py").write_text(body, encoding="utf-8")
        if (index + 1) % 10 == 0:
            (tests / f"test_module_{name}.py").write_text(
                f"from src.module_{name} import value_{name}\n\n\n"
                f"def test_value_{name}() -> None:\n"
                f"    assert value_{name}() == {index}\n",
                encoding="utf-8",
            )
            test_count += 1
    return {"module_count": module_count, "test_count": test_count}


def _materialize_broken_source(repository: Path, fixture_path: str) -> dict[str, str]:
    relative = Path(fixture_path)
    if (
        relative.is_absolute()
        or ".." in relative.parts
        or not fixture_path.endswith(".py.fixture")
    ):
        raise ValueError("broken source fixture must be a contained .py.fixture path")
    source = repository / relative
    target_relative = Path(fixture_path[: -len(".fixture")])
    target = repository / target_relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(source.read_bytes())
    return {
        "fixture": relative.as_posix(),
        "materialized": target_relative.as_posix(),
    }


def _benchmark_metadata(case: EvaluationCase) -> dict[str, Any]:
    value = case.metadata.get("repository_intelligence")
    if not isinstance(value, dict):
        raise ValueError("repository intelligence case metadata is missing")
    return value


def _tree_fingerprint(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(
        (item for item in root.rglob("*") if item.is_file()),
        key=lambda item: item.relative_to(root).as_posix(),
    ):
        relative = path.relative_to(root).as_posix()
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(hashlib.sha256(path.read_bytes()).digest())
    return digest.hexdigest()


def _database_size(path: Path) -> int:
    return sum(
        candidate.stat().st_size
        for candidate in (
            path,
            Path(f"{path}-wal"),
            Path(f"{path}-shm"),
        )
        if candidate.is_file()
    )


def _ratio(outcomes: list[bool]) -> float:
    return sum(outcomes) / len(outcomes) if outcomes else 1.0


def _check_name(value: str) -> str:
    return "".join(character if character.isalnum() else "_" for character in value)[
        :80
    ]


def _symbol_probe(name: str, path: str, kind: str, language: str) -> dict[str, str]:
    return {"name": name, "path": path, "kind": kind, "language": language}


def _reference_probe(
    source_name: str,
    source_path: str,
    target_name: str,
    target_path: str,
    kind: str,
) -> dict[str, str]:
    return {
        "source_name": source_name,
        "source_path": source_path,
        "target_name": target_name,
        "target_path": target_path,
        "kind": kind,
    }


def _search_probe(query: str, expected_path: str, top_k: int) -> dict[str, Any]:
    return {"query": query, "expected_path": expected_path, "top_k": top_k}


def _test_probe(subject: str, expected_test: str) -> dict[str, Any]:
    return {"subject": subject, "expected_tests": [expected_test]}


__all__ = [
    "RepositoryIntelligenceBenchmarkMetrics",
    "RepositoryIntelligenceEvaluationBackend",
    "repository_intelligence_suite",
]
