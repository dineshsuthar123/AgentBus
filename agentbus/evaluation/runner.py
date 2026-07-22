from __future__ import annotations

import json
import os
import subprocess
import tempfile
import threading
import time
import uuid
from collections import defaultdict
from contextlib import contextmanager
from dataclasses import dataclass, replace
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Protocol

from agentbus.config import AgentBusConfig
from agentbus.evaluation.assertions import AssertionEvaluator, RuntimeObservation
from agentbus.evaluation.budget import EvaluationBudget
from agentbus.evaluation.errors import (
    EvaluationBudgetExceeded,
    EvaluationConfigurationError,
)
from agentbus.evaluation.fingerprint import agentbus_commit_sha, configuration_fingerprint
from agentbus.evaluation.fixtures import FixtureRepositoryManager, FixtureWorkspace
from agentbus.evaluation.models import (
    EvaluationArtifact,
    EvaluationCase,
    EvaluationCaseResult,
    EvaluationMetrics,
    EvaluationRun,
    EvaluationSuite,
    EvaluationVariant,
    ExecutionMetrics,
    GitMetrics,
    ProviderMetrics,
    QualityMetrics,
    RunStatus,
    utc_now,
)
from agentbus.evaluation.providers import (
    BudgetedProviderFactory,
    DeterministicFakeProvider,
    ScriptedOutcome,
    ScriptedResponseStore,
)
from agentbus.evaluation.scoring import calculate_score
from agentbus.evaluation.statistics import build_series
from agentbus.evaluation.storage import EvaluationStorage
from agentbus.evaluation.suites import builtin_suites, builtin_variants
from agentbus.execution.engine import DurableExecutionEngine
from agentbus.execution.models import FailureCategory, TaskExecutionResult, utc_now
from agentbus.execution.state_store import StateStore
from agentbus.git.repository import GitRepository
from agentbus.models.errors import ModelOutputError, ModelProviderError
from agentbus.models.router import ModelProviderFactory, ModelRouter
from agentbus.models.types import ModelResult, ModelRole
from agentbus.runtime.loop import AgentLoop
from agentbus.runtime.orchestrator import MultiAgentOrchestrator
from agentbus.runtime.verifier import Verifier
from agentbus.security.redaction import sanitize_json
from agentbus.tools.filesystem import FileSystemTools
from agentbus.worktrees.models import WorktreePurpose


@dataclass
class BackendResult:
    observation: RuntimeObservation
    metrics: EvaluationMetrics
    runtime_run_id: str | None
    failure_category: str | None = None
    failure_message: str | None = None
    raw_metrics: dict[str, Any] | None = None


class EvaluationBackend(Protocol):
    def execute(
        self,
        case: EvaluationCase,
        variant: EvaluationVariant,
        fixture: FixtureWorkspace,
        budget: EvaluationBudget,
    ) -> BackendResult:
        ...


class EvaluationRunner:
    def __init__(
        self,
        *,
        results_dir: str | Path = ".agentbus/evaluations",
        fixture_root: str | Path | None = None,
        owned_fixture_root: str | Path | None = None,
        storage: EvaluationStorage | None = None,
        suites: dict[str, EvaluationSuite] | None = None,
        variants: dict[str, EvaluationVariant] | None = None,
        offline_backend: EvaluationBackend | None = None,
        live_backend: EvaluationBackend | None = None,
    ):
        self.storage = storage or EvaluationStorage(results_dir)
        self.fixture_manager = FixtureRepositoryManager(
            fixture_root or Path(__file__).with_name("fixtures_data"),
            owned_fixture_root
            or os.getenv("AGENTBUS_EVAL_FIXTURE_ROOT")
            or Path(tempfile.gettempdir()) / "agentbus-eval-fixtures",
        )
        self.suites = suites or builtin_suites()
        self.variants = variants or builtin_variants()
        self.offline_backend = offline_backend or OfflineRuntimeBackend()
        self.live_backend = live_backend or LiveRuntimeBackend()
        self.assertions = AssertionEvaluator()

    def run(
        self,
        suite_id: str,
        *,
        variant_id: str | None = None,
        case_ids: set[str] | None = None,
        tags: set[str] | None = None,
        fail_fast: bool = False,
        preserve_fixtures: bool = False,
        live: bool = False,
        max_requests: int = 100,
        max_tokens: int = 10_000,
        timeout_seconds: float = 300,
    ) -> EvaluationRun:
        suite = self._suite(suite_id)
        variant = self._variant(variant_id or suite.default_variant)
        self._validate_live_mode(suite, variant, live)
        selected = self._select_cases(suite, case_ids, tags)
        if not selected:
            raise EvaluationConfigurationError("No evaluation cases matched the filters.")
        run = EvaluationRun(
            evaluation_run_id=uuid.uuid4().hex,
            suite_id=suite.suite_id,
            variant=variant,
            agentbus_commit_sha=agentbus_commit_sha(),
            configuration_fingerprint=configuration_fingerprint(variant),
            metadata={
                "case_filter": sorted(case_ids or []),
                "tag_filter": sorted(tags or []),
                "live": live,
                "prompt_content_persisted": False,
            },
        )
        if suite.metadata.get("release_surface_checks"):
            run.metadata["release_acceptance"] = _release_surface_checks()
        self.storage.save_run(run)
        backend = self.live_backend if live else self.offline_backend
        for case in selected:
            fixture = self.fixture_manager.create(case, run.evaluation_run_id)
            budget = EvaluationBudget(
                max_requests=max_requests,
                max_tokens=max_tokens,
                timeout_seconds=min(timeout_seconds, case.timeout_seconds),
            )
            result = self._run_case(
                case,
                variant,
                fixture,
                backend,
                budget,
                preserve_fixtures,
            )
            run.case_results.append(result)
            run.aggregate_metrics = _aggregate_metrics(run.case_results)
            run.aggregate_score = _average_score(run.case_results)
            run.passed = all(item.passed for item in run.case_results)
            run.partial = len(run.case_results) < len(selected)
            self.storage.save_run(run)
            if fail_fast and not result.passed:
                break
        run.status = "completed" if len(run.case_results) == len(selected) else "failed"
        run.completed_at = utc_now()
        run.partial = len(run.case_results) < len(selected)
        run.passed = bool(run.case_results) and all(
            result.passed for result in run.case_results
        )
        run.aggregate_metrics = _aggregate_metrics(run.case_results)
        run.aggregate_score = _average_score(run.case_results)
        if suite.metadata.get("release_surface_checks"):
            release_checks = run.metadata["release_acceptance"]
            try:
                loaded = self.storage.load_run(run.evaluation_run_id)
                export_path = self.storage.exports_dir / (
                    f".{run.evaluation_run_id}-release-roundtrip.json"
                )
                self.storage.export_run(run.evaluation_run_id, export_path)
                release_checks["storage_roundtrip"] = (
                    loaded.evaluation_run_id == run.evaluation_run_id
                    and export_path.is_file()
                )
                export_path.unlink(missing_ok=True)
            except Exception:
                release_checks["storage_roundtrip"] = False
            run.passed = run.passed and all(release_checks.values())
        self.storage.save_run(run)
        return run

    def run_repeated(
        self,
        suite_id: str,
        *,
        repeat: int,
        **kwargs: Any,
    ):
        if repeat < 1:
            raise EvaluationConfigurationError("--repeat must be at least 1.")
        runs = [self.run(suite_id, **kwargs) for _ in range(repeat)]
        series = build_series(runs)
        for index, run in enumerate(runs, start=1):
            run.metadata.update(
                {
                    "series_id": series.series_id,
                    "repeat_index": index,
                    "repeat_count": repeat,
                }
            )
            self.storage.save_run(run)
        self.storage.save_series(series)
        return series

    def _run_case(
        self,
        case: EvaluationCase,
        variant: EvaluationVariant,
        fixture: FixtureWorkspace,
        backend: EvaluationBackend,
        budget: EvaluationBudget,
        preserve_fixtures: bool,
    ) -> EvaluationCaseResult:
        try:
            backend_result = backend.execute(case, variant, fixture, budget)
        except Exception as exc:
            backend_result = _exception_result(case, fixture, budget, exc)
        assertion_case = (
            case.model_copy(update={"expected_reviewer_approved": None})
            if variant.workflow.value == "single"
            else case
        )
        assertions = self.assertions.evaluate(assertion_case, backend_result.observation)
        score = calculate_score(assertions)
        passed = bool(assertions) and all(item.passed is True for item in assertions)
        backend_result.metrics.quality.success = passed
        backend_result.metrics.quality.assertion_pass_rate = (
            sum(item.passed is True for item in assertions) / len(assertions)
            if assertions
            else 0
        )
        backend_result.metrics.quality.safety_violation_count = sum(
            item.hard_failure and item.passed is False for item in assertions
        )
        retain = preserve_fixtures
        retained_path = str(fixture.owned_root) if retain else None
        artifacts = [
            EvaluationArtifact(
                artifact_type="fixture_repository",
                identifier=str(fixture.repository if retain else fixture.source),
                retained=retain,
                metadata={"owned": retain},
            )
        ]
        result = EvaluationCaseResult(
            case_id=case.case_id,
            title=case.title,
            passed=passed,
            run_status=backend_result.observation.run_status,
            verifier_passed=backend_result.observation.verifier_passed,
            reviewer_approved=backend_result.observation.reviewer_approved,
            assertions=assertions,
            score=score,
            metrics=backend_result.metrics,
            artifacts=artifacts,
            relevant_changed_files=backend_result.observation.relevant_changed_files,
            failure_category=backend_result.failure_category,
            failure_message=backend_result.failure_message,
            retained_fixture_path=retained_path,
            runtime_run_id=backend_result.runtime_run_id,
            raw_metrics=backend_result.raw_metrics or {},
        )
        if not retain:
            self.fixture_manager.cleanup(fixture)
        return result

    def _suite(self, suite_id: str) -> EvaluationSuite:
        try:
            return self.suites[suite_id]
        except KeyError as exc:
            raise EvaluationConfigurationError(f"Unknown evaluation suite: {suite_id}") from exc

    def _variant(self, variant_id: str) -> EvaluationVariant:
        try:
            return self.variants[variant_id]
        except KeyError as exc:
            raise EvaluationConfigurationError(
                f"Unknown evaluation variant: {variant_id}"
            ) from exc

    @staticmethod
    def _select_cases(
        suite: EvaluationSuite,
        case_ids: set[str] | None,
        tags: set[str] | None,
    ) -> list[EvaluationCase]:
        selected = list(suite.cases)
        if case_ids:
            selected = [case for case in selected if case.case_id in case_ids]
        if tags:
            selected = [case for case in selected if tags <= case.tags]
        return selected

    @staticmethod
    def _validate_live_mode(
        suite: EvaluationSuite,
        variant: EvaluationVariant,
        live: bool,
    ) -> None:
        if live and not variant.live:
            raise EvaluationConfigurationError(
                "--live requires an explicitly live evaluation variant."
            )
        if variant.live and not live:
            raise EvaluationConfigurationError(
                f"Variant '{variant.variant_id}' requires explicit --live consent."
            )
        if live and "live" not in suite.tags:
            raise EvaluationConfigurationError(
                "Live provider access requires an explicitly selected live suite."
            )


class OfflineRuntimeBackend:
    def execute(
        self,
        case: EvaluationCase,
        variant: EvaluationVariant,
        fixture: FixtureWorkspace,
        budget: EvaluationBudget,
    ) -> BackendResult:
        tracker = PhaseTracker()
        scripts = ScriptedResponseStore()
        tasks = _case_tasks(case)
        _register_scripts(case, tasks, scripts)
        probe = ConcurrencyProbe(set(case.metadata.get("synchronize_tasks", [])))
        counters: dict[str, int] = defaultdict(int)
        buffers = {
            role: ResultBuffer() for role in ("planner", "coder", "reviewer")
        }
        providers = {
            role: DeterministicFakeProvider(
                role=role,
                scripts=scripts,
                budget=budget,
            )
            for role in (ModelRole.PLANNER, ModelRole.CODER, ModelRole.REVIEWER)
        }
        planner = OfflinePlanner(case, providers[ModelRole.PLANNER], buffers["planner"], tracker)
        coder = OfflineCoder(
            case,
            fixture.repository,
            providers[ModelRole.CODER],
            buffers["coder"],
            tracker,
            probe,
            counters,
        )
        reviewer = OfflineReviewer(
            case,
            providers[ModelRole.REVIEWER],
            buffers["reviewer"],
            tracker,
        )
        state_store = StateStore(fixture.owned_root / "runtime-state.db")
        lease_clock = (
            ControlledLeaseClock() if _has_injection(case, "lease_expiry") else None
        )
        config = AgentBusConfig(
            model_name="deterministic-evaluation-v1",
            workspace_dir=str(fixture.repository),
            runs_dir=str(fixture.owned_root / "runtime-logs"),
            state_dir=str(fixture.owned_root),
            state_db="runtime-state.db",
            command_timeout_seconds=max(1, int(case.timeout_seconds)),
            parallel_execution=bool(variant.parallel or case.parallel_mode),
            max_workers=max(2, variant.max_workers, case.maximum_workers)
            if (variant.parallel or case.parallel_mode)
            else 1,
            worktree_root=str(fixture.owned_root / "worktrees"),
            keep_worktrees=True,
            worker_lease_seconds=30,
            worker_heartbeat_seconds=(
                0.01 if _has_injection(case, "lease_expiry") else 5
            ),
        )
        verifier = CapturingVerifier(config=config, tracker=tracker, case=case)
        dynamic_verifier = CapturingVerifier(
            config=config,
            tracker=tracker,
            state_store=state_store,
            case=case,
        )
        crash_hook = OneShotCrashHook(case, lease_clock=lease_clock)
        integration_crash_hook = OneShotIntegrationCrashHook(case)
        pr_client = RecordingPRClient()
        orchestrator = MultiAgentOrchestrator(
            config=config,
            planner=planner,
            coder=coder,
            reviewer=reviewer,
            verifier=verifier,
            state_store=state_store,
            git_repository=GitRepository(str(fixture.repository)),
            pr_client=pr_client,
            create_branch=False,
            commit_changes=False,
            open_pr=False,
            parallel_executor_factory=lambda workspace: OfflineTaskExecutor(
                case,
                workspace,
                coder.for_workspace(workspace),
                reviewer,
            ),
            parallel_final_verifier=dynamic_verifier,
            parallel_final_reviewer=reviewer,
            worker_crash_hook=crash_hook if crash_hook.enabled else None,
            integration_crash_hook=(
                integration_crash_hook if integration_crash_hook.enabled else None
            ),
            lease_clock=lease_clock,
        )
        started = time.perf_counter()
        runtime_run_id = None
        report = None
        failure_category = None
        failure_message = None
        try:
            if variant.durable or case.parallel_mode:
                runtime_run_id = orchestrator.create_durable_run(case.task_prompt)
                report = orchestrator.run_durable(runtime_run_id)
                if (
                    crash_hook.kind == "lease_expiry"
                    and crash_hook.fired
                    and report.status == RunStatus.RUNNING
                ):
                    persisted = state_store.get_run(runtime_run_id)
                    parallel = persisted.metadata.get("parallel_execution", {})
                    state_store.update_run_details(
                        runtime_run_id,
                        metadata_updates={
                            "parallel_execution": {
                                **parallel,
                                "lease_seconds": 30,
                                "heartbeat_seconds": 5,
                            }
                        },
                        event_type="evaluation_lease_expiry_injection_completed",
                    )
                    report = orchestrator.run_durable(runtime_run_id, resume=True)
                if (
                    report.status == RunStatus.WAITING_FOR_APPROVAL
                    and _has_injection(case, "approval_rejection")
                    and report.pending_approvals
                ):
                    report = DurableExecutionEngine(state_store).reject_task(
                        runtime_run_id,
                        report.pending_approvals[0],
                        "Deterministic evaluation approval rejection.",
                    )
            elif variant.workflow.value == "multi":
                result = orchestrator.run(case.task_prompt)
                report = _non_durable_report(
                    case, result, verifier.last_result, fixture.repository
                )
            else:
                report = _run_single_offline(case, config, coder, verifier)
        except Exception as exc:
            injected_interruption = bool(
                integration_crash_hook.fired
                or (crash_hook.kind == "lease_expiry" and crash_hook.fired)
            )
            if runtime_run_id is not None and injected_interruption:
                try:
                    report = orchestrator.run_durable(runtime_run_id, resume=True)
                except Exception as recovery_exc:
                    failure_category = type(recovery_exc).__name__
                    failure_message = str(recovery_exc)
            else:
                failure_category = type(exc).__name__
                failure_message = str(exc)
                if runtime_run_id is not None:
                    try:
                        report = orchestrator.get_durable_report(runtime_run_id)
                    except Exception:
                        report = None
        elapsed = time.perf_counter() - started
        budget.check_time()
        observation, metrics, raw = _collect_runtime_result(
            case=case,
            fixture=fixture,
            report=report,
            state_store=state_store,
            runtime_run_id=runtime_run_id,
            scripts=scripts,
            tracker=tracker,
            probe=probe,
            counters=counters,
            verifier=dynamic_verifier if config.parallel_execution else verifier,
            pr_client=pr_client,
            elapsed=elapsed,
            budget=budget,
        )
        if report is not None and getattr(report, "task_failures", None):
            first = report.task_failures[0]
            failure_category = failure_category or first.get("category")
            failure_message = failure_message or first.get("message")
        failure_message = failure_message or getattr(report, "failure_reason", None)
        return BackendResult(
            observation=observation,
            metrics=metrics,
            runtime_run_id=runtime_run_id,
            failure_category=failure_category,
            failure_message=failure_message,
            raw_metrics=raw,
        )


class LiveRuntimeBackend:
    def execute(
        self,
        case: EvaluationCase,
        variant: EvaluationVariant,
        fixture: FixtureWorkspace,
        budget: EvaluationBudget,
    ) -> BackendResult:
        if not variant.live:
            raise EvaluationConfigurationError("Live backend requires a live variant.")
        tracker = PhaseTracker()
        base = AgentBusConfig.from_env()
        config = replace(
            base,
            workspace_dir=str(fixture.repository),
            runs_dir=str(fixture.owned_root / "runtime-logs"),
            state_dir=str(fixture.owned_root),
            state_db="runtime-state.db",
            provider_name=variant.provider,
            fallback_provider_name=variant.fallback_provider or base.fallback_provider_name,
            enable_provider_fallback=variant.fallback_enabled,
            parallel_execution=bool(variant.parallel or case.parallel_mode),
            max_workers=max(2, variant.max_workers, case.maximum_workers)
            if (variant.parallel or case.parallel_mode)
            else 1,
            worktree_root=str(fixture.owned_root / "worktrees"),
            keep_worktrees=True,
            model_timeout_seconds=min(base.model_timeout_seconds, case.timeout_seconds),
            model_max_retries=variant.max_retries,
        )
        roles = (
            ("coder",)
            if variant.workflow.value == "single"
            else ("planner", "coder", "reviewer")
        )
        for role in roles:
            config.validate_provider_configuration(variant.provider, role=role)
        base_factory = ModelProviderFactory(config)
        router = ModelRouter(
            config,
            provider_factory=BudgetedProviderFactory(base_factory, budget),
        )
        store = StateStore(fixture.owned_root / "runtime-state.db")
        verifier = CapturingVerifier(config=config, tracker=tracker)
        pr_client = RecordingPRClient()
        orchestrator = MultiAgentOrchestrator(
            config=config,
            model_router=router,
            verifier=verifier,
            state_store=store,
            git_repository=GitRepository(str(fixture.repository)),
            pr_client=pr_client,
            create_branch=False,
            commit_changes=False,
            open_pr=False,
        )
        started = time.perf_counter()
        run_id = None
        if variant.durable:
            run_id = orchestrator.create_durable_run(case.task_prompt)
            report = orchestrator.run_durable(run_id)
        elif variant.workflow.value == "multi":
            result = orchestrator.run(case.task_prompt)
            report = _non_durable_report(
                case, result, verifier.last_result, fixture.repository
            )
        else:
            report = _run_single_live(case, config, router, verifier)
        elapsed = time.perf_counter() - started
        observation, metrics, raw = _collect_runtime_result(
            case=case,
            fixture=fixture,
            report=report,
            state_store=store,
            runtime_run_id=run_id,
            scripts=None,
            tracker=tracker,
            probe=ConcurrencyProbe(set()),
            counters={},
            verifier=verifier,
            pr_client=pr_client,
            elapsed=elapsed,
            budget=budget,
            model_results=[record.result for record in router.usage_ledger.records()],
        )
        return BackendResult(
            observation=observation,
            metrics=metrics,
            runtime_run_id=run_id,
            failure_message=report.failure_reason,
            raw_metrics=raw,
        )


class ResultBuffer:
    def __init__(self):
        self.last_result: ModelResult | None = None
        self._results: list[ModelResult] = []
        self._lock = threading.Lock()

    def add(self, result: ModelResult) -> None:
        with self._lock:
            self.last_result = result
            self._results.append(result)

    def drain_results(self) -> list[ModelResult]:
        with self._lock:
            results = list(self._results)
            self._results.clear()
            return results


class OfflinePlanner:
    def __init__(self, case, provider, model, tracker):
        self.case = case
        self.provider = provider
        self.model = model
        self.tracker = tracker

    def plan(self, user_task, file_list=None, context_pack=None):
        with self.tracker.measure("planning"):
            result = self.provider.generate_json(
                "deterministic evaluation planner",
                metadata={
                    "case_id": self.case.case_id,
                    "task_id": "__planner__",
                    "attempt": 1,
                },
            )
            self.model.add(result)
            return result.json_value()

    @staticmethod
    def summarize(plan):
        return f"{plan.get('goal', 'evaluation plan')} ({len(plan.get('steps', []))} steps)"


class OfflineCoder:
    def __init__(
        self,
        case,
        workspace,
        provider,
        model,
        tracker,
        probe,
        counters,
    ):
        self.case = case
        self.workspace = Path(workspace)
        self.provider = provider
        self.model = model
        self.tracker = tracker
        self.probe = probe
        self.counters = counters
        self._lock = threading.Lock()

    def for_workspace(self, workspace):
        return OfflineCoder(
            self.case,
            workspace,
            self.provider,
            self.model,
            self.tracker,
            self.probe,
            self.counters,
        )

    def execute(self, user_task, plan, reviewer_feedback=None):
        task = plan["steps"][0]
        task_id = task["id"]
        with self._lock:
            self.counters[task_id] += 1
            attempt = self.counters[task_id]
        with self.tracker.measure("coding"):
            result = self.provider.generate_json(
                f"deterministic evaluation coder for {task_id}",
                metadata={
                    "case_id": self.case.case_id,
                    "task_id": task_id,
                    "attempt": attempt,
                },
            )
            self.model.add(result)
            with self.probe.active(task_id):
                tools = FileSystemTools(str(self.workspace))
                actions = self.case.metadata.get("actions", {}).get(task_id, [])
                for action_index, action in enumerate(actions, start=1):
                    if action.get("action") == "write_file":
                        invocation_id = uuid.uuid5(
                            uuid.NAMESPACE_URL,
                            (
                                f"agentbus-eval:{self.case.case_id}:{task_id}:"
                                f"{attempt}:{action_index}"
                            ),
                        ).hex
                        tools.write_file(
                            str(action["path"]),
                            str(action.get("content", "")),
                            task_id=str(task_id),
                            invocation_id=invocation_id,
                        )
                    else:
                        raise ValueError(f"Unsupported offline evaluation action: {action}")
            value = result.json_value()
            return str(value.get("summary", f"completed {task_id}"))


class OfflineReviewer:
    def __init__(self, case, provider, model, tracker):
        self.case = case
        self.provider = provider
        self.model = model
        self.tracker = tracker
        self._counts: dict[str, int] = defaultdict(int)
        self._lock = threading.Lock()

    def review_task(self, **kwargs):
        task_spec = kwargs.get("task_spec", {})
        return self._review(str(task_spec.get("id", "__task__")))

    def review(self, **kwargs):
        return self._review("__final__")

    def _review(self, task_id: str) -> dict[str, Any]:
        with self._lock:
            self._counts[task_id] += 1
            attempt = self._counts[task_id]
        with self.tracker.measure("review"):
            result = self.provider.generate_json(
                f"deterministic evaluation review for {task_id}",
                metadata={
                    "case_id": self.case.case_id,
                    "task_id": task_id,
                    "attempt": attempt,
                },
            )
            self.model.add(result)
            return result.json_value()


class OfflineTaskExecutor:
    def __init__(self, case, workspace, coder, reviewer):
        self.case = case
        self.workspace = Path(workspace)
        self.coder = coder
        self.reviewer = reviewer

    def execute(self, context):
        plan = {
            "goal": context.run.original_task,
            "steps": [
                {
                    "id": context.task.task_id,
                    "title": context.task.title,
                    "description": context.task.description,
                    "risk": context.task.risk.value,
                    "dependencies": context.task.dependency_ids,
                    "expected_outputs": context.task.expected_outputs,
                    "done_criteria": context.task.done_criteria,
                }
            ],
            "test_strategy": "deterministic offline verification",
            "done_criteria": context.task.done_criteria,
        }
        try:
            summary = self.coder.execute(context.run.original_task, plan)
            changed = GitRepository(str(self.workspace)).all_changed_files()
            review = self.reviewer.review_task(
                original_task=context.run.original_task,
                task_spec=plan["steps"][0],
                expected_outputs=context.task.expected_outputs,
                artifacts=changed,
                task_diff="deterministic offline diff",
                coder_summary=summary,
                verifier_result={"passed": True, "exit_code": 0},
            )
            if not review.get("approved"):
                return TaskExecutionResult(
                    succeeded=False,
                    summary=str(review.get("summary", "task review rejected")),
                    failure_category=FailureCategory.REVIEWER_REJECTION,
                    error_message="Deterministic task reviewer rejected the task.",
                    retryable=False,
                    changed_files=changed,
                    reviewer_status="rejected",
                )
            return TaskExecutionResult(
                succeeded=True,
                summary=summary,
                changed_files=changed,
                verifier_status="passed",
                reviewer_status="approved",
            )
        except ModelOutputError as exc:
            return _task_failure(FailureCategory.MODEL_OUTPUT_ERROR, exc, retryable=True)
        except ModelProviderError as exc:
            return _task_failure(
                FailureCategory.MODEL_TRANSPORT_ERROR,
                exc,
                retryable=exc.retryable,
            )
        except ValueError as exc:
            return _task_failure(FailureCategory.POLICY_VIOLATION, exc, retryable=False)


class CapturingVerifier:
    def __init__(self, *, config, tracker, state_store=None, case=None):
        self.config = config
        self.tracker = tracker
        self.state_store = state_store
        self.case = case
        self.last_result: dict[str, Any] | None = None

    def verify(self, require_command=False):
        if self.case is not None and _has_injection(self.case, "verifier_failure"):
            self.last_result = {
                "passed": False,
                "command": ["injected-verifier-failure"],
                "exit_code": 1,
                "reason": "Deterministic evaluation verifier failure.",
                "artifact_suppression_active": True,
                "pytest_cache_disabled": True,
            }
            return self.last_result
        config = self.config
        if self.state_store is not None:
            integrations = [
                item
                for item in self.state_store.list_worktrees()
                if item.purpose == WorktreePurpose.INTEGRATION
            ]
            if integrations:
                config = config.with_overrides(
                    workspace_dir=integrations[-1].path,
                    parallel_execution=False,
                )
        with self.tracker.measure("verification"):
            self.last_result = Verifier(config=config).verify(
                require_command=require_command
            )
        return self.last_result


class ConcurrencyProbe:
    def __init__(self, synchronized_tasks: set[str]):
        self.synchronized_tasks = synchronized_tasks
        self.barrier = (
            threading.Barrier(len(synchronized_tasks))
            if len(synchronized_tasks) > 1
            else None
        )
        self.active_count = 0
        self.maximum = 0
        self._lock = threading.Lock()

    @contextmanager
    def active(self, task_id: str):
        with self._lock:
            self.active_count += 1
            self.maximum = max(self.maximum, self.active_count)
        try:
            if self.barrier is not None and task_id in self.synchronized_tasks:
                self.barrier.wait(timeout=10)
            yield
        finally:
            with self._lock:
                self.active_count -= 1


class PhaseTracker:
    def __init__(self):
        self.values: dict[str, float] = defaultdict(float)
        self._lock = threading.Lock()

    @contextmanager
    def measure(self, phase: str):
        started = time.perf_counter()
        try:
            yield
        finally:
            with self._lock:
                self.values[phase] += time.perf_counter() - started


class ControlledLeaseClock:
    def __init__(self):
        self._value = utc_now()
        self._lock = threading.Lock()

    def __call__(self):
        with self._lock:
            return self._value

    def advance(self, seconds: float):
        with self._lock:
            self._value += timedelta(seconds=seconds)


class OneShotCrashHook:
    def __init__(
        self,
        case: EvaluationCase,
        *,
        lease_clock: ControlledLeaseClock | None = None,
    ):
        injection = next(
            (
                item
                for item in case.failure_injections
                if item.kind.value
                in {"worker_crash", "after_task_commit", "lease_expiry"}
            ),
            None,
        )
        self.enabled = injection is not None
        self.kind = injection.kind.value if injection else None
        self.task_id = injection.task_id if injection else None
        self.stage = (
            injection.stage
            or (
                "after_worktree_created"
                if self.kind == "lease_expiry"
                else "after_task_commit"
            )
            if injection
            else None
        )
        self.fired = False
        self.lease_clock = lease_clock
        self._lock = threading.Lock()

    def __call__(self, stage, run_id, task_id):
        with self._lock:
            if (
                not self.fired
                and stage == self.stage
                and (self.task_id is None or task_id == self.task_id)
            ):
                self.fired = True
                if self.kind == "lease_expiry":
                    if self.lease_clock is None:
                        raise RuntimeError(
                            "Lease expiry injection requires a controlled clock."
                        )
                    self.lease_clock.advance(31)
                    return
                raise RuntimeError(f"Deterministic worker crash at {stage}")


class OneShotIntegrationCrashHook:
    def __init__(self, case: EvaluationCase):
        injection = next(
            (
                item
                for item in case.failure_injections
                if item.kind.value == "during_integration"
            ),
            None,
        )
        self.enabled = injection is not None
        self.task_id = injection.task_id if injection else None
        self.stage = (injection.stage or "after_cherry_pick") if injection else None
        self.fired = False
        self._lock = threading.Lock()

    def __call__(self, stage, run_id, task_id):
        with self._lock:
            if (
                not self.fired
                and stage == self.stage
                and (self.task_id is None or task_id == self.task_id)
            ):
                self.fired = True
                raise RuntimeError(f"Deterministic integration interruption at {stage}")


class RecordingPRClient:
    def __init__(self):
        self.calls: list[dict[str, Any]] = []

    def create_pr(self, **kwargs):
        self.calls.append(sanitize_json(kwargs))
        raise AssertionError("Evaluation runs must never create pull requests.")


def _register_scripts(case, tasks, scripts):
    plan = {
        "goal": case.task_prompt,
        "steps": tasks,
        "test_strategy": "Run the detected local fixture tests.",
        "done_criteria": ["All deterministic assertions pass."],
    }
    planner_kind = (
        "malformed"
        if any(item.kind.value == "malformed_planner" for item in case.failure_injections)
        else "success"
    )
    scripts.register(
        case_id=case.case_id,
        task_id="__planner__",
        role=ModelRole.PLANNER,
        attempt=1,
        outcome=ScriptedOutcome(kind=planner_kind, value=plan),
    )
    transport_task = next(
        (
            item.task_id
            for item in case.failure_injections
            if item.kind.value == "coder_transport_failure"
        ),
        None,
    )
    if transport_task is None and _has_injection(case, "coder_transport_failure"):
        transport_task = tasks[0]["id"]
    reviewer_rejected = any(
        item.kind.value == "reviewer_rejection" for item in case.failure_injections
    )
    for task in tasks:
        task_id = task["id"]
        maximum = int(task.get("maximum_attempts", case.maximum_attempts))
        for attempt in range(1, maximum + 1):
            kind = (
                "transient_failure"
                if transport_task == task_id and attempt == 1
                else "success"
            )
            scripts.register(
                case_id=case.case_id,
                task_id=task_id,
                role=ModelRole.CODER,
                attempt=attempt,
                outcome=ScriptedOutcome(
                    kind=kind,
                    value={"summary": f"completed {task_id}"},
                    fallback_used=_has_injection(case, "provider_fallback"),
                    original_provider=(
                        "fake-primary"
                        if _has_injection(case, "provider_fallback")
                        else None
                    ),
                    original_error_category=(
                        "service_unavailable"
                        if _has_injection(case, "provider_fallback")
                        else None
                    ),
                ),
            )
            scripts.register(
                case_id=case.case_id,
                task_id=task_id,
                role=ModelRole.REVIEWER,
                attempt=attempt,
                outcome=ScriptedOutcome(value=_review_value(True, task_id)),
            )
    for attempt in range(1, 3):
        scripts.register(
            case_id=case.case_id,
            task_id="__final__",
            role=ModelRole.REVIEWER,
            attempt=attempt,
            outcome=ScriptedOutcome(
                value=_review_value(not reviewer_rejected, "__final__")
            ),
        )


def _review_value(approved: bool, task_id: str) -> dict[str, Any]:
    return {
        "approved": approved,
        "issues": [] if approved else [{"severity": "high", "message": "Injected rejection"}],
        "summary": f"deterministic review for {task_id}",
        "required_fixes": [] if approved else ["Correct the injected regression."],
    }


def _case_tasks(case: EvaluationCase) -> list[dict[str, Any]]:
    tasks = case.metadata.get("tasks")
    if isinstance(tasks, list) and tasks:
        return tasks
    return [
        {
            "id": "step-1",
            "title": case.title,
            "description": case.task_prompt,
            "risk": case.risk_level.value,
            "dependencies": [],
            "assigned_role": "coder",
            "maximum_attempts": case.maximum_attempts,
            "expected_outputs": case.expected_files,
            "done_criteria": ["Evaluation assertions pass."],
        }
    ]


def _has_injection(case: EvaluationCase, kind: str) -> bool:
    return any(item.kind.value == kind for item in case.failure_injections)


def _collect_runtime_result(
    *,
    case,
    fixture,
    report,
    state_store,
    runtime_run_id,
    scripts,
    tracker,
    probe,
    counters,
    verifier,
    pr_client,
    elapsed,
    budget,
    model_results=None,
):
    worktrees = state_store.list_worktrees(runtime_run_id) if runtime_run_id else []
    integrations = [item for item in worktrees if item.purpose == WorktreePurpose.INTEGRATION]
    result_repository = Path(integrations[-1].path) if integrations else fixture.repository
    if report is None:
        run_status = RunStatus.FAILED.value
        changed = GitRepository(str(result_repository)).all_changed_files()
        relevant = GitRepository(str(result_repository)).change_set(changed).review_files
        generated = GitRepository(str(result_repository)).change_set(changed).generated_files
        verifier_passed = None
        reviewer_approved = None
        conflicts = []
        approval_required = False
        commit_created = False
    else:
        run_status = report.status.value
        changed = list(report.changed_files)
        relevant = list(report.relevant_changed_files)
        generated = list(report.generated_artifacts)
        verifier_passed = _status_bool(report.verifier_status, "passed", "failed")
        reviewer_approved = _status_bool(report.reviewer_status, "approved", "rejected")
        conflicts = sorted(
            {
                path
                for item in report.integration_conflicts
                for path in item.get("conflict_files", [])
            }
        )
        approval_required = bool(report.pending_approvals)
        commit_created = bool(report.commit_identifier)
    attempts = []
    events = []
    task_commits = []
    if runtime_run_id:
        snapshot = state_store.load_snapshot(runtime_run_id)
        attempts = snapshot.attempts
        events = state_store.list_events(runtime_run_id)
        approval_required = approval_required or any(
            item["event_type"] == "approval_required" for item in events
        )
        task_commits = state_store.list_task_commits(runtime_run_id)
        for attempt in attempts:
            hygiene = attempt.metadata.get("artifact_hygiene", {})
            if isinstance(hygiene, dict):
                generated.extend(hygiene.get("generated_artifacts", []))
        for worktree in worktrees:
            if worktree.purpose != WorktreePurpose.TASK or not Path(worktree.path).is_dir():
                continue
            worktree_repository = GitRepository(worktree.path)
            observed = worktree_repository.all_changed_files()
            generated.extend(worktree_repository.change_set(observed).generated_files)
    generated = sorted(set(generated))
    results = model_results if model_results is not None else (scripts.results() if scripts else [])
    provider_metrics = _provider_metrics(results)
    retry_count = max(0, len(attempts) - len({item.task_id for item in attempts}))
    recoveries = sum("recover" in item["event_type"] for item in events)
    lines_added, lines_removed = _git_numstat(result_repository, fixture.baseline_commit)
    source_unchanged = (
        _git(fixture.repository, "rev-parse", "HEAD") == fixture.baseline_commit
        and _git(fixture.repository, "status", "--porcelain") == ""
    )
    expected = set(case.metadata.get("expected_relevant_files", case.expected_files))
    unrelated = sorted(set(relevant) - expected)
    conflicts_count = len(conflicts)
    verifier_result = verifier.last_result or {}
    diagnostic = json.dumps(
        sanitize_json(
            {
                "events": [
                    {
                        "event_type": item["event_type"],
                        "task_id": item["task_id"],
                        "payload": item["payload"],
                    }
                    for item in events
                ],
                "provider_calls": [
                    item.model_dump(mode="json") for item in (scripts.calls() if scripts else [])
                ],
            }
        ),
        sort_keys=True,
    )
    observation = RuntimeObservation(
        repository=result_repository,
        run_status=run_status,
        verifier_passed=verifier_passed,
        reviewer_approved=reviewer_approved,
        changed_files=sorted(changed),
        relevant_changed_files=sorted(relevant),
        generated_artifacts=generated,
        commit_created=commit_created,
        pr_attempted=bool(pr_client.calls),
        approval_required=approval_required,
        task_execution_counts=dict(counters),
        conflict_files=conflicts,
        source_unchanged=source_unchanged,
        test_command=list(verifier_result.get("command", [])),
        test_exit_code=verifier_result.get("exit_code"),
        total_tokens=provider_metrics.total_tokens,
        total_requests=provider_metrics.requests,
        elapsed_seconds=elapsed,
        retries=retry_count,
        safety_violations=[],
        sanitized_diagnostic_text=diagnostic,
    )
    quality = QualityMetrics(
        verifier_passed=verifier_passed,
        reviewer_approved=reviewer_approved,
        relevant_file_count=len(relevant),
        unrelated_file_count=len(unrelated),
        conflict_count=conflicts_count,
    )
    execution = ExecutionMetrics(
        total_duration_seconds=elapsed,
        planning_duration_seconds=tracker.values.get("planning", 0.0),
        coding_duration_seconds=tracker.values.get("coding", 0.0),
        verification_duration_seconds=tracker.values.get("verification", 0.0),
        review_duration_seconds=tracker.values.get("review", 0.0),
        integration_duration_seconds=_event_duration(events, "integration_started", "integration_completed"),
        tasks_attempted=len(attempts) or sum(counters.values()),
        retries=retry_count,
        recoveries=recoveries,
        workers_used=len(getattr(report, "workers_used", []) if report else []),
        maximum_observed_concurrency=probe.maximum,
    )
    git_metrics = GitMetrics(
        files_changed=sorted(changed),
        lines_added=lines_added,
        lines_removed=lines_removed,
        task_commits=len(task_commits),
        integration_commits=len(
            [item for item in (state_store.list_integrations(runtime_run_id) if runtime_run_id else []) if item.resulting_commit]
        ),
        conflict_count=conflicts_count,
    )
    metrics = EvaluationMetrics(
        quality=quality,
        execution=execution,
        provider=provider_metrics,
        git=git_metrics,
    )
    raw = {
        "attempts_per_task": (
            getattr(report, "attempts_per_task", {}) if report is not None else {}
        ),
        "workers_used": getattr(report, "workers_used", []) if report else [],
        "integration_order": getattr(report, "integration_order", []) if report else [],
        "test_command": observation.test_command,
        "test_exit_code": observation.test_exit_code,
        "unrelated_files": unrelated,
        "budget": budget.snapshot(),
    }
    return observation, metrics, raw


def _provider_metrics(results: list[ModelResult]) -> ProviderMetrics:
    by_role: dict[str, dict[str, Any]] = {}
    metrics = ProviderMetrics(requests=len(results))
    for result in results:
        usage = result.usage
        metrics.input_tokens += usage.input_tokens or 0
        metrics.output_tokens += usage.output_tokens or 0
        metrics.cached_tokens += usage.cached_tokens or 0
        metrics.total_tokens += usage.total_tokens or 0
        metrics.latency_seconds += result.latency_seconds or 0
        metrics.retries += result.retry_count
        metrics.fallbacks += int(result.fallback_used)
        role = by_role.setdefault(
            result.role.value,
            {
                "requests": 0,
                "tokens": 0,
                "latency_seconds": 0.0,
                "providers": [],
                "models": [],
            },
        )
        role["requests"] += 1
        role["tokens"] += usage.total_tokens or 0
        role["latency_seconds"] += result.latency_seconds or 0
        role["providers"] = sorted(set(role["providers"]) | {result.provider})
        role["models"] = sorted(set(role["models"]) | {result.model})
    metrics.by_role = by_role
    return metrics


def _aggregate_metrics(results: list[EvaluationCaseResult]) -> EvaluationMetrics:
    aggregate = EvaluationMetrics()
    for result in results:
        aggregate.quality.success = aggregate.quality.success or result.passed
        aggregate.quality.relevant_file_count += result.metrics.quality.relevant_file_count
        aggregate.quality.unrelated_file_count += result.metrics.quality.unrelated_file_count
        aggregate.quality.conflict_count += result.metrics.quality.conflict_count
        aggregate.quality.safety_violation_count += result.metrics.quality.safety_violation_count
        aggregate.execution.total_duration_seconds += result.metrics.execution.total_duration_seconds
        aggregate.execution.tasks_attempted += result.metrics.execution.tasks_attempted
        aggregate.execution.retries += result.metrics.execution.retries
        aggregate.execution.recoveries += result.metrics.execution.recoveries
        aggregate.execution.workers_used += result.metrics.execution.workers_used
        aggregate.execution.maximum_observed_concurrency = max(
            aggregate.execution.maximum_observed_concurrency,
            result.metrics.execution.maximum_observed_concurrency,
        )
        aggregate.provider.requests += result.metrics.provider.requests
        aggregate.provider.input_tokens += result.metrics.provider.input_tokens
        aggregate.provider.output_tokens += result.metrics.provider.output_tokens
        aggregate.provider.cached_tokens += result.metrics.provider.cached_tokens
        aggregate.provider.total_tokens += result.metrics.provider.total_tokens
        aggregate.provider.latency_seconds += result.metrics.provider.latency_seconds
        aggregate.provider.retries += result.metrics.provider.retries
        aggregate.provider.fallbacks += result.metrics.provider.fallbacks
        aggregate.git.lines_added += result.metrics.git.lines_added
        aggregate.git.lines_removed += result.metrics.git.lines_removed
        aggregate.git.task_commits += result.metrics.git.task_commits
        aggregate.git.integration_commits += result.metrics.git.integration_commits
        aggregate.git.conflict_count += result.metrics.git.conflict_count
        aggregate.git.files_changed = sorted(
            set(aggregate.git.files_changed) | set(result.metrics.git.files_changed)
        )
    if results:
        aggregate.quality.success = all(item.passed for item in results)
        aggregate.quality.assertion_pass_rate = sum(
            item.metrics.quality.assertion_pass_rate for item in results
        ) / len(results)
    return aggregate


def _exception_result(case, fixture, budget, exc):
    observation = RuntimeObservation(
        repository=fixture.repository,
        run_status=RunStatus.FAILED.value,
        verifier_passed=None,
        reviewer_approved=None,
        elapsed_seconds=float(budget.snapshot()["elapsed_seconds"]),
        safety_violations=(
            [str(exc)] if isinstance(exc, EvaluationBudgetExceeded) else []
        ),
        sanitized_diagnostic_text=json.dumps(
            sanitize_json({"error_type": type(exc).__name__, "error": str(exc)})
        ),
    )
    return BackendResult(
        observation=observation,
        metrics=EvaluationMetrics(),
        runtime_run_id=None,
        failure_category=type(exc).__name__,
        failure_message=str(exc),
        raw_metrics={"budget": budget.snapshot()},
    )


def _task_failure(category, error, *, retryable):
    return TaskExecutionResult(
        succeeded=False,
        summary=str(error),
        failure_category=category,
        error_message=str(error),
        retryable=retryable,
        changed_files=[],
    )


def _status_bool(value, true_value, false_value):
    if value == true_value:
        return True
    if value == false_value:
        return False
    return None


def _git(path: Path, *arguments: str) -> str:
    return subprocess.run(
        ["git", *arguments],
        cwd=path,
        check=True,
        capture_output=True,
        text=True,
        shell=False,
        env={**os.environ, "GIT_CONFIG_NOSYSTEM": "1"},
    ).stdout.strip()


def _git_numstat(repository: Path, baseline: str) -> tuple[int, int]:
    completed = subprocess.run(
        ["git", "diff", "--numstat", f"{baseline}..HEAD"],
        cwd=repository,
        check=False,
        capture_output=True,
        text=True,
        shell=False,
    )
    added = removed = 0
    for line in completed.stdout.splitlines():
        parts = line.split("\t", 2)
        if len(parts) < 2:
            continue
        if parts[0].isdigit():
            added += int(parts[0])
        if parts[1].isdigit():
            removed += int(parts[1])
    return added, removed


def _event_duration(events, start_type, end_type):
    start = next((item["created_at"] for item in events if item["event_type"] == start_type), None)
    end = next((item["created_at"] for item in reversed(events) if item["event_type"] == end_type), None)
    if start is None or end is None or end < start:
        return 0.0
    return (end - start).total_seconds()


def _average_score(results):
    return round(sum(item.score.total for item in results) / len(results), 4) if results else 0.0


def _release_surface_checks() -> dict[str, bool]:
    from agentbus import __version__
    from agentbus.cli import COMMANDS, main as cli_main
    from agentbus.eval import main as evaluation_main
    from agentbus.release_report import main as release_report_main

    return {
        "package_import": bool(__version__),
        "cli_entry_callable": callable(cli_main),
        "evaluation_entry_callable": callable(evaluation_main),
        "release_report_callable": callable(release_report_main),
        "documented_command_groups": {
            "run",
            "resume",
            "runs",
            "show-run",
            "providers",
            "config",
            "doctor",
            "evaluate",
        }.issubset(COMMANDS),
    }


def _non_durable_report(case, result, verifier_result, repository_path):
    repository = GitRepository(str(repository_path))
    changed_files = repository.all_changed_files()
    changes = repository.change_set(changed_files)
    return SimpleNamespace(
        status=(
            RunStatus.SUCCEEDED
            if result.approved and verifier_result.get("passed")
            else RunStatus.FAILED
        ),
        changed_files=changed_files,
        relevant_changed_files=changes.review_files,
        generated_artifacts=changes.generated_files,
        verifier_status="passed" if verifier_result.get("passed") else "failed",
        reviewer_status="approved" if result.approved else "rejected",
        integration_conflicts=[],
        pending_approvals=[],
        commit_identifier=result.commit_hash,
        task_failures=[],
        failure_reason=None,
        attempts_per_task={},
        workers_used=[],
        integration_order=[],
    )


def _run_single_offline(case, config, coder, verifier):
    tasks = _case_tasks(case)
    for task in tasks:
        coder.execute(case.task_prompt, {"steps": [task]})
    verify = verifier.verify()

    repository = GitRepository(config.workspace_dir)
    changed_files = repository.all_changed_files()
    changes = repository.change_set(changed_files)
    return SimpleNamespace(
        status=RunStatus.SUCCEEDED if verify.get("passed") else RunStatus.FAILED,
        changed_files=changed_files,
        relevant_changed_files=changes.review_files,
        generated_artifacts=changes.generated_files,
        verifier_status="passed" if verify.get("passed") else "failed",
        reviewer_status=None,
        integration_conflicts=[],
        pending_approvals=[],
        commit_identifier=None,
        task_failures=[],
        failure_reason=None,
        attempts_per_task={},
        workers_used=[],
        integration_order=[],
    )


def _run_single_live(case, config, router, verifier):
    AgentLoop(config=config, model_router=router).run(case.task_prompt)
    verify = verifier.verify()
    repository = GitRepository(config.workspace_dir)
    changed_files = repository.all_changed_files()
    changes = repository.change_set(changed_files)
    return SimpleNamespace(
        status=RunStatus.SUCCEEDED if verify.get("passed") else RunStatus.FAILED,
        changed_files=changed_files,
        relevant_changed_files=changes.review_files,
        generated_artifacts=changes.generated_files,
        verifier_status="passed" if verify.get("passed") else "failed",
        reviewer_status=None,
        integration_conflicts=[],
        pending_approvals=[],
        commit_identifier=None,
        task_failures=[],
        failure_reason=None,
        attempts_per_task={},
        workers_used=[],
        integration_order=[],
    )
