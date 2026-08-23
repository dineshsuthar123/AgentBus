import pytest

from agentbus.config import AgentBusConfig
from agentbus.execution.cancellation import CancellationToken
from agentbus.execution.cancellation_registry import CancellationRegistry
from agentbus.execution.engine import DurableExecutionEngine
from agentbus.execution.models import FailureCategory, RunStatus, TaskStatus
from agentbus.execution.state_store import StateStore, StateStoreError
from agentbus.execution.task_graph import PlanContractValidationError
from agentbus.memory.run_log import RunLogger
from agentbus.models.errors import ModelAuthenticationError
from agentbus.replay.checkpoints import CheckpointKind, CheckpointManager
from agentbus.runtime.intelligence import (
    PlannerIntelligenceContext,
    StaticPlannerIntelligenceSource,
)
from agentbus.runtime.loop import PlannedCapabilityMismatchError
from agentbus.runtime.orchestrator import MultiAgentOrchestrator
from agentbus.trace import (
    REPOSITORY_INTELLIGENCE_COMPONENT,
    ContentAddressedStore,
    RepositoryIntelligenceTraceEvidence,
    TraceSpanType,
    TraceStatus,
)
from agentbus.tools.protocol import ToolResourceBudget


PLAN = {
    "goal": "Create calculator",
    "steps": [
        {
            "id": "step-1",
            "title": "Implement",
            "description": "Create calculator",
            "risk": "low",
            "execution_kind": "implementation",
            "required_capabilities": ["filesystem.write"],
        },
        {
            "id": "step-2",
            "title": "Add calculator tests",
            "description": "Add independently verifiable calculator coverage",
            "risk": "low",
            "execution_kind": "implementation",
            "required_capabilities": ["filesystem.write"],
        },
    ],
    "test_strategy": "Run pytest",
    "done_criteria": ["Tests pass"],
}


class FakePlanner:
    def __init__(self, plan=None):
        self.output = plan or PLAN
        self.context_pack = None

    def plan(self, user_task, file_list=None, context_pack=None):
        self.context_pack = context_pack
        return self.output


class ReplanningPlanner:
    def __init__(self, outputs):
        self.outputs = list(outputs)
        self.feedback = []

    def plan(
        self,
        user_task,
        file_list=None,
        context_pack=None,
        contract_feedback=None,
    ):
        self.feedback.append(contract_feedback)
        return self.outputs.pop(0)


class FakeCoder:
    def __init__(self):
        self.calls = []

    def execute(
        self,
        user_task,
        plan,
        reviewer_feedback=None,
        repository_intelligence=None,
    ):
        self.calls.append(
            {
                "task_id": plan["steps"][0]["id"],
                "reviewer_feedback": reviewer_feedback,
                "plan": plan,
                "repository_intelligence": repository_intelligence,
            }
        )
        return f"implemented {plan['steps'][0]['id']}"


class FailingProviderCoder(FakeCoder):
    def execute(self, user_task, plan, reviewer_feedback=None):
        super().execute(user_task, plan, reviewer_feedback)
        raise ModelAuthenticationError(
            "Azure OpenAI authentication failed.",
            provider="azure",
            model="coder-deployment",
            request_id="safe-request-id",
            metadata={"api_key": "must-not-persist"},
        )


class CapabilityMismatchCoder(FakeCoder):
    def execute(self, user_task, plan, reviewer_feedback=None):
        super().execute(user_task, plan, reviewer_feedback)
        raise PlannedCapabilityMismatchError(
            task_id=plan["steps"][0]["id"],
            tool_name="filesystem.write",
            requested_capabilities=[
                "filesystem.write",
                "filesystem.create",
            ],
            declared_capabilities=["filesystem.write"],
        )


class FakeVerifier:
    def __init__(self, passed=True):
        self.passed = passed
        self.calls = 0

    def verify(self):
        self.calls += 1
        return {
            "command": ["python", "-m", "pytest"],
            "exit_code": 0 if self.passed else 1,
            "passed": self.passed,
            "output": "offline verifier output",
            "reason": "fake",
        }


class FakeReviewer:
    def __init__(self, approved=True):
        self.approved = approved
        self.calls = 0
        self.repository_intelligence = []
        self.findings = {}

    def review(
        self,
        user_task,
        plan,
        git_diff,
        test_output=None,
        repository_intelligence=None,
    ):
        self.calls += 1
        self.repository_intelligence.append(repository_intelligence)
        return {
            "approved": self.approved,
            "issues": [] if self.approved else [{"message": "Needs correction"}],
            "summary": "Approved" if self.approved else "Rejected",
            "required_fixes": [] if self.approved else ["Fix implementation"],
            **self.findings,
        }

    def review_task(self, **kwargs):
        self.repository_intelligence.append(
            kwargs.get("repository_intelligence")
        )
        return {
            "approved": True,
            "issues": [],
            "summary": "Current task approved",
            "required_fixes": [],
            **self.findings,
        }


class FakeGitRepository:
    def __init__(self):
        self.commits = []
        self.pushes = []
        self.created_branches = []
        self.head = "before1"
        self.dirty = True

    def is_git_repo(self):
        return True

    def current_branch(self):
        return "main"

    def head_commit(self, short=True):
        return self.head

    def has_uncommitted_changes(self):
        return False

    def create_branch(self, branch_name):
        self.created_branches.append(branch_name)
        return f"Created branch: {branch_name}"

    def changed_files(self):
        if not self.dirty:
            return []
        return ["calculator.py", "tests/test_calculator.py"]

    def worktree_snapshot(self):
        return {}

    def changed_since(self, snapshot):
        return self.changed_files()

    def full_diff(self, max_chars=30_000, paths=None):
        return "fake target repository diff"

    def commit(self, message):
        self.commits.append(message)
        self.head = "abc1234"
        self.dirty = False
        return self.head

    def push_branch(self, branch_name=None):
        self.pushes.append(branch_name)
        return f"Pushed branch: {branch_name}"


class FakePRClient:
    def __init__(self):
        self.calls = []

    def create_pr(self, **kwargs):
        self.calls.append(kwargs)
        return "https://github.com/acme/repo/pull/1"


class AmbiguousPRClient:
    def __init__(self):
        self.calls = 0

    def create_pr(self, **kwargs):
        self.calls += 1
        raise RuntimeError("connection ended after request")


def config(tmp_path):
    (tmp_path / "workspace").mkdir(exist_ok=True)
    return AgentBusConfig(
        workspace_dir=str(tmp_path / "workspace"),
        runs_dir=str(tmp_path / "runs"),
        state_dir=str(tmp_path / "state"),
    )


def orchestrator(tmp_path, **kwargs):
    settings = config(tmp_path)
    store = StateStore(settings.state_database_path)
    defaults = {
        "config": settings,
        "planner": FakePlanner(),
        "coder": FakeCoder(),
        "verifier": FakeVerifier(),
        "reviewer": FakeReviewer(),
        "git_repository": FakeGitRepository(),
        "pr_client": FakePRClient(),
        "state_store": store,
    }
    defaults.update(kwargs)
    return MultiAgentOrchestrator(**defaults), store


def test_durable_mode_persists_validated_planner_graph_before_execution(tmp_path):
    planner = FakePlanner()
    coder = FakeCoder()
    runner, store = orchestrator(tmp_path, planner=planner, coder=coder)

    run_id = runner.create_durable_run("Create calculator")
    snapshot = store.load_snapshot(run_id)

    assert snapshot.run.status == RunStatus.PENDING
    assert [task.task_id for task in snapshot.tasks] == ["step-1", "step-2"]
    assert snapshot.tasks[1].spec.dependency_ids == ["step-1"]
    assert all(task.status == TaskStatus.PENDING for task in snapshot.tasks)
    assert coder.calls == []
    assert "Repo Context Pack" in planner.context_pack
    assert snapshot.run.metadata["tool_runtime"]["resource_budget"] == (
        runner.config.tool_resource_budget.model_dump(mode="json")
    )

    report = runner.run_durable(run_id)

    assert report.status == RunStatus.SUCCEEDED
    assert [call["task_id"] for call in coder.calls] == ["step-1", "step-2"]


def test_durable_planner_replans_once_after_contract_rejection(tmp_path):
    invalid = {
        "goal": "Create calculator",
        "steps": [
            {
                "id": "step-1",
                "title": "Inspect first",
                "description": "Inspect before a later implementation phase.",
                "risk": "low",
                "execution_kind": "implementation",
                "required_capabilities": ["filesystem.read"],
                "done_criteria": ["Inspection is complete"],
            }
        ],
        "test_strategy": "Run pytest",
        "done_criteria": ["Tests pass"],
    }
    corrected = {
        "goal": "Create calculator",
        "steps": [
            {
                "id": "step-1",
                "title": "Implement and verify calculator",
                "description": "Create the calculator and its relevant tests.",
                "risk": "low",
                "execution_kind": "implementation",
                "required_capabilities": [
                    "filesystem.read",
                    "filesystem.write",
                    "filesystem.create",
                    "test.execute",
                    "process.execute",
                    "git.read",
                ],
                "expected_outputs": ["calculator.py", "test_calculator.py"],
                "done_criteria": ["Calculator tests pass"],
            }
        ],
        "test_strategy": "Run pytest",
        "done_criteria": ["Tests pass"],
    }
    planner = ReplanningPlanner([invalid, corrected])
    runner, store = orchestrator(tmp_path, planner=planner)

    run_id = runner.create_durable_run("Create calculator")

    persisted = store.get_run(run_id)
    persisted_step = persisted.planner_output["steps"][0]
    assert persisted_step["title"] == "Implement and verify calculator"
    assert persisted_step["execution_kind"] == "implementation"
    assert persisted_step["required_capabilities"] == corrected["steps"][0][
        "required_capabilities"
    ]
    assert planner.feedback[0] is None
    assert "implementation_without_mutation" in planner.feedback[1][0]


def test_missing_create_capability_correction_is_bounded_before_persistence(
    tmp_path,
):
    incoherent = {
        "goal": "Make duplicate payment delivery idempotent",
        "steps": [
            {
                "id": "step-1",
                "title": "Add an idempotency record",
                "description": "Create the new payment idempotency record.",
                "risk": "medium",
                "execution_kind": "implementation",
                "dependencies": [],
                "required_capabilities": [
                    "filesystem.read",
                    "filesystem.write",
                ],
                "expected_outputs": [
                    "src/main/java/com/example/payment/NewIdempotencyRecord.java"
                ],
                "targeted_files": [
                    "src/main/java/com/example/payment/NewIdempotencyRecord.java"
                ],
                "done_criteria": ["Duplicate deliveries share one record"],
            }
        ],
        "test_strategy": "Run the payment service tests",
        "done_criteria": ["Sequential and concurrent duplicates are safe"],
    }
    planner = ReplanningPlanner([incoherent, incoherent])
    runner, store = orchestrator(tmp_path, planner=planner)

    with pytest.raises(PlanContractValidationError) as captured:
        runner.create_durable_run("Make payment webhook delivery idempotent")

    assert {issue.code for issue in captured.value.issues} == {
        "missing_create_capability"
    }
    assert len(planner.feedback) == 2
    assert planner.feedback[0] is None
    assert "missing_create_capability" in planner.feedback[1][0]
    assert store.list_runs() == []


def test_capability_contract_failure_stops_before_verifier_and_reviewer(tmp_path):
    coder = CapabilityMismatchCoder()
    verifier = FakeVerifier()

    class NeverReviewer(FakeReviewer):
        def review(self, *args, **kwargs):
            raise AssertionError("reviewer must not run after a known coder failure")

        def review_task(self, *args, **kwargs):
            raise AssertionError("reviewer must not run after a known coder failure")

    repository = FakeGitRepository()
    repository.dirty = False
    runner, store = orchestrator(
        tmp_path,
        coder=coder,
        verifier=verifier,
        reviewer=NeverReviewer(),
        git_repository=repository,
    )
    run_id = runner.create_durable_run("Create calculator")

    report = runner.run_durable(run_id)

    assert report.status == RunStatus.FAILED
    assert report.failed_tasks == ["step-1"]
    assert report.blocked_tasks == ["step-2"]
    assert report.verifier_status == "not_run"
    assert report.reviewer_status == "not_run"
    assert report.changed_files == []
    assert verifier.calls == 0
    assert [call["task_id"] for call in coder.calls] == ["step-1"]
    attempts = store.list_attempts(run_id, "step-1")
    assert len(attempts) == 1
    assert attempts[0].error_category == FailureCategory.PLAN_CAPABILITY_MISMATCH
    assert attempts[0].metadata["plan_capability_mismatch"] == {
        "task_id": "step-1",
        "tool_name": "filesystem.write",
        "requested_capabilities": [
            "filesystem.create",
            "filesystem.write",
        ],
        "declared_capabilities": ["filesystem.write"],
        "undeclared_capabilities": ["filesystem.create"],
    }
    assert attempts[0].metadata["_agentbus"]["retryable_override"] is False
    assert repository.commits == []

    resumed = runner.resume_durable(run_id)
    assert resumed.status == RunStatus.FAILED
    assert len(store.list_attempts(run_id, "step-1")) == 1
    assert [call["task_id"] for call in coder.calls] == ["step-1"]


def test_analysis_task_persists_bounded_artifact_without_code_verification(tmp_path):
    analysis_plan = {
        "goal": "Inspect calculator safely",
        "steps": [
            {
                "id": "step-1",
                "title": "Inspect calculator",
                "description": "Report the calculator structure without edits.",
                "risk": "low",
                "execution_kind": "analysis",
                "required_capabilities": ["filesystem.read", "git.read"],
                "done_criteria": ["A bounded calculator analysis is available"],
            }
        ],
        "test_strategy": "No code verifier is applicable",
        "done_criteria": ["A bounded calculator analysis is available"],
    }

    class LongAnalysisCoder(FakeCoder):
        def execute(self, user_task, plan, reviewer_feedback=None):
            super().execute(user_task, plan, reviewer_feedback)
            return "A" * 17_000

    class AnalysisReviewer(FakeReviewer):
        def __init__(self):
            super().__init__()
            self.task_calls = []
            self.final_calls = []

        def review_task(self, **kwargs):
            self.task_calls.append(kwargs)
            return {
                "approved": True,
                "issues": [],
                "summary": "Analysis artifact approved",
                "required_fixes": [],
            }

        def review(self, **kwargs):
            self.final_calls.append(kwargs)
            return {
                "approved": True,
                "issues": [],
                "summary": "Analysis run approved",
                "required_fixes": [],
            }

    coder = LongAnalysisCoder()
    verifier = FakeVerifier()
    reviewer = AnalysisReviewer()
    repository = FakeGitRepository()
    repository.dirty = False
    runner, store = orchestrator(
        tmp_path,
        planner=FakePlanner(analysis_plan),
        coder=coder,
        verifier=verifier,
        reviewer=reviewer,
        git_repository=repository,
    )

    run_id = runner.create_durable_run("Inspect calculator safely")
    report = runner.run_durable(run_id)
    snapshot = store.load_snapshot(run_id)
    analysis_artifacts = [
        artifact
        for artifact in snapshot.artifacts
        if artifact.artifact_type == "analysis_summary"
    ]

    assert report.status == RunStatus.SUCCEEDED
    assert report.verifier_status == "not_applicable"
    assert report.reviewer_status == "approved"
    assert report.changed_files == []
    assert verifier.calls == 0
    assert len(analysis_artifacts) == 1
    artifact = analysis_artifacts[0]
    assert artifact.identifier.startswith("analysis:")
    assert artifact.metadata["summary"] == "A" * 16_000
    assert artifact.metadata["summary_chars"] == 16_000
    assert artifact.metadata["source_summary_chars"] == 17_000
    assert artifact.metadata["truncated"] is True
    assert artifact.metadata["commit_eligible"] is False
    assert reviewer.task_calls[0]["artifacts"] == [artifact.identifier]
    assert reviewer.task_calls[0]["coder_summary"] == "A" * 16_000
    assert reviewer.task_calls[0]["verifier_result"]["status"] == (
        "not_applicable"
    )
    assert reviewer.final_calls[0]["analysis_artifacts"] == [
        {
            "task_id": "step-1",
            "identifier": artifact.identifier,
            "summary": "A" * 16_000,
            "truncated": True,
        }
    ]
    trace = store.get_run_trace(run_id)
    assert not any(span.span_type == TraceSpanType.VERIFIER for span in trace.spans)
    assert "final_verification_not_applicable" in {
        event["event_type"] for event in store.list_events(run_id)
    }


def test_analysis_task_reports_filesystem_side_effect_without_rollback(tmp_path):
    analysis_plan = {
        "goal": "Inspect calculator safely",
        "steps": [
            {
                "id": "step-1",
                "title": "Inspect calculator",
                "description": "Report calculator structure without edits.",
                "risk": "low",
                "execution_kind": "analysis",
                "required_capabilities": ["filesystem.read"],
                "done_criteria": ["A calculator analysis is available"],
            }
        ],
        "test_strategy": "No code verifier is applicable",
        "done_criteria": ["A calculator analysis is available"],
    }
    workspace = tmp_path / "workspace"
    output = workspace / "unexpected.txt"

    class MutatingAnalysisCoder(FakeCoder):
        def execute(self, user_task, plan, reviewer_feedback=None):
            super().execute(user_task, plan, reviewer_feedback)
            output.write_text("preserve this side effect\n", encoding="utf-8")
            return "Analysis complete"

    class ObservingRepository(FakeGitRepository):
        def worktree_snapshot(self):
            return {"unexpected.txt": "present"} if output.exists() else {}

        def changed_since(self, snapshot):
            return ["unexpected.txt"] if output.exists() and not snapshot else []

    class NeverReviewer(FakeReviewer):
        def review(self, *args, **kwargs):
            raise AssertionError("final reviewer must not run after mutation")

        def review_task(self, *args, **kwargs):
            raise AssertionError("task reviewer must not run after mutation")

    verifier = FakeVerifier()
    runner, store = orchestrator(
        tmp_path,
        planner=FakePlanner(analysis_plan),
        coder=MutatingAnalysisCoder(),
        verifier=verifier,
        reviewer=NeverReviewer(),
        git_repository=ObservingRepository(),
    )

    run_id = runner.create_durable_run("Inspect calculator safely")
    report = runner.run_durable(run_id)
    attempt = store.list_attempts(run_id, "step-1")[0]

    assert report.status == RunStatus.FAILED
    assert report.changed_files == ["unexpected.txt"]
    assert report.verifier_status == "not_applicable"
    assert report.reviewer_status == "not_run"
    assert verifier.calls == 0
    assert attempt.error_category == FailureCategory.POLICY_VIOLATION
    assert attempt.metadata["analysis"]["repository_mutation_observed"] is True
    assert attempt.metadata["_agentbus"]["retryable_override"] is False
    artifact_ids = [
        artifact.identifier for artifact in store.load_snapshot(run_id).artifacts
    ]
    assert artifact_ids == ["unexpected.txt"]
    assert output.read_text(encoding="utf-8") == "preserve this side effect\n"


def test_parallel_analysis_plan_is_rejected_with_one_bounded_replan(tmp_path):
    analysis_plan = {
        "goal": "Inspect calculator safely",
        "steps": [
            {
                "id": "step-1",
                "title": "Inspect calculator",
                "description": "Report calculator structure without edits.",
                "risk": "low",
                "execution_kind": "analysis",
                "required_capabilities": ["filesystem.read"],
                "done_criteria": ["A calculator analysis is available"],
            }
        ],
        "test_strategy": "No code verifier is applicable",
        "done_criteria": ["A calculator analysis is available"],
    }
    settings = config(tmp_path).with_overrides(parallel_execution=True)
    store = StateStore(settings.state_database_path)
    planner = ReplanningPlanner([analysis_plan, analysis_plan])
    runner = MultiAgentOrchestrator(
        config=settings,
        planner=planner,
        coder=FakeCoder(),
        verifier=FakeVerifier(),
        reviewer=FakeReviewer(),
        git_repository=FakeGitRepository(),
        pr_client=FakePRClient(),
        state_store=store,
    )

    with pytest.raises(PlanContractValidationError) as captured:
        runner.create_durable_run("Inspect calculator safely")

    assert [issue.code for issue in captured.value.issues] == [
        "analysis_parallel_unsupported"
    ]
    assert planner.feedback[0] is None
    assert "analysis_parallel_unsupported" in planner.feedback[1][0]
    assert store.list_runs() == []


def test_durable_mode_persists_validated_repository_intelligence(tmp_path):
    intelligence_plan = {
        **PLAN,
        "targeted_files": ["calculator.py"],
        "steps": [
            {
                **PLAN["steps"][0],
                "targeted_files": ["calculator.py"],
            },
            {
                **PLAN["steps"][1],
                "targeted_files": ["tests/test_calculator.py"],
                "proposed_tests": ["tests/test_calculator.py"],
            },
        ],
    }
    planner = FakePlanner(intelligence_plan)
    coder = FakeCoder()
    reviewer = FakeReviewer()
    reviewer.findings = {
        "unplanned_affected_components": ["symbol_unplanned"],
        "missing_tests": ["tests/test_missing.py"],
        "boundary_violations": ["boundary_candidate"],
        "index_uncertainty": ["repository_index_state:stale"],
    }
    intelligence = PlannerIntelligenceContext(
        risk_areas=("calculator.py",),
    )
    runner, store = orchestrator(
        tmp_path,
        planner=planner,
        coder=coder,
        reviewer=reviewer,
        intelligence_source=StaticPlannerIntelligenceSource(intelligence),
    )

    run_id = runner.create_durable_run("Create calculator")
    snapshot = store.load_snapshot(run_id)

    assert "Repository Intelligence Context" in planner.context_pack
    assert snapshot.run.planner_output["intelligence_scope_validated"] is True
    assert snapshot.run.planner_output["intelligence_context_hash"] == (
        intelligence.context_hash
    )
    assert snapshot.run.metadata["repository_intelligence"] == (
        intelligence.safe_metadata()
    )
    assert all(
        task.spec.metadata["intelligence_scope_validated"] is True
        for task in snapshot.tasks
    )
    assert all(
        task.spec.metadata["intelligence_context_hash"]
        == intelligence.context_hash
        for task in snapshot.tasks
    )

    report = runner.run_durable(run_id)

    assert report.status == RunStatus.SUCCEEDED
    assert all(
        "Coder Repository Intelligence" in call["repository_intelligence"]
        for call in coder.calls
    )
    assert coder.calls[0]["plan"]["steps"][0]["targeted_files"] == [
        "calculator.py"
    ]
    assert any(
        value and "Reviewer Repository Intelligence" in value
        for value in reviewer.repository_intelligence
    )
    task_review = store.list_attempts(run_id, "step-1")[0].metadata[
        "task_review"
    ]
    final_review = store.get_run(run_id).metadata["final_review"]
    trace = store.get_run_trace(run_id)
    intelligence_span = next(
        span
        for span in trace.spans
        if span.attributes.get("component")
        == REPOSITORY_INTELLIGENCE_COMPONENT
    )
    object_store = ContentAddressedStore(
        runner.config.trace_store_path,
        private_roots=(runner.workspace,),
    )
    trace_evidence = RepositoryIntelligenceTraceEvidence.model_validate_json(
        object_store.get(intelligence_span.output_references[0].sha256).data
    )
    assert task_review["unplanned_affected_components"] == [
        "symbol_unplanned"
    ]
    assert task_review["missing_tests"] == ["tests/test_missing.py"]
    assert final_review["boundary_violations"] == ["boundary_candidate"]
    assert final_review["index_uncertainty"] == [
        "repository_index_state:stale"
    ]
    assert len(intelligence_span.input_references) == 1
    assert len(intelligence_span.output_references) == 1
    assert trace_evidence.context_hash == intelligence.context_hash
    assert trace_evidence.search_query == "Create calculator"


def test_durable_run_records_hierarchical_trace_and_final_review_order(
    tmp_path,
):
    runner, store = orchestrator(tmp_path)

    run_id = runner.create_durable_run("Create calculator")
    active = store.get_run_trace(run_id)

    assert active.status == TraceStatus.RUNNING
    assert active.spans[0].span_type == TraceSpanType.RUN
    assert any(
        span.span_type == TraceSpanType.PLANNING for span in active.spans
    )
    assert [item.label for item in active.checkpoints] == [
        "plan-created",
        "task-graph-persisted",
    ]
    assert store.get_run(run_id).metadata["execution_trace"]["trace_id"] == (
        active.trace_id
    )

    report = runner.run_durable(run_id)
    trace = store.get_run_trace(run_id)
    task_spans = [
        span for span in trace.spans if span.span_type == TraceSpanType.TASK
    ]
    final_verifier = next(
        span for span in trace.spans if span.name == "final verifier"
    )
    final_reviewer = next(
        span for span in trace.spans if span.name == "final reviewer"
    )

    assert report.status == RunStatus.SUCCEEDED
    assert trace.status == TraceStatus.SUCCEEDED
    assert [span.task_id for span in task_spans] == ["step-1", "step-2"]
    assert all(span.status == TraceStatus.SUCCEEDED for span in task_spans)
    assert max(span.sequence for span in task_spans) < final_verifier.sequence
    assert final_verifier.sequence < final_reviewer.sequence
    assert all(
        span.parent_span_id is not None
        for span in trace.spans
        if span.span_id != trace.root_span_id
    )
    manager = CheckpointManager(runner._trace_for_run(run_id).object_store)
    states = [
        manager.load_state(checkpoint) for checkpoint in trace.checkpoints
    ]
    assert [state.kind for state in states] == [
        CheckpointKind.PLAN_CREATED,
        CheckpointKind.GRAPH_PERSISTED,
        CheckpointKind.TASK_COMPLETED,
        CheckpointKind.TASK_COMPLETED,
        CheckpointKind.VERIFIER_COMPLETED,
    ]
    assert manager.validate_ancestry(
        trace,
        trace.checkpoints[-1].checkpoint_id,
    ) == states
    manifest = store.get_run_provenance_manifest(run_id)
    trace_metadata = store.get_run(run_id).metadata["execution_trace"]
    assert manifest.trace_id == trace.trace_id
    assert manifest.integrity_root == trace_metadata["provenance_root"]
    assert trace_metadata["status"] == "sealed"


def test_final_review_rejection_preserves_successful_task_trace_history(
    tmp_path,
):
    runner, store = orchestrator(
        tmp_path,
        reviewer=FakeReviewer(approved=False),
    )

    report = runner.run_durable(
        runner.create_durable_run("Create calculator")
    )
    trace = store.get_run_trace(report.run_id)
    task_spans = [
        span for span in trace.spans if span.span_type == TraceSpanType.TASK
    ]

    assert report.status == RunStatus.FAILED
    assert trace.status == TraceStatus.FAILED
    assert trace.spans[0].failure.category == "durable_run_failed"
    assert all(span.status == TraceStatus.SUCCEEDED for span in task_spans)
    assert any(
        span.name == "final reviewer"
        and span.status == TraceStatus.SUCCEEDED
        for span in trace.spans
    )
    assert store.get_run_provenance_manifest(
        report.run_id
    ).trace_id == trace.trace_id


def test_durable_run_persists_custom_tool_budget_for_resume(tmp_path):
    budget = ToolResourceBudget(
        invocations_per_task=2,
        invocations_per_run=3,
    )
    settings = config(tmp_path).with_overrides(tool_resource_budget=budget)
    runner, store = orchestrator(tmp_path, config=settings)

    run_id = runner.create_durable_run("Create calculator")

    persisted = store.get_run(run_id)
    assert persisted.metadata["tool_runtime"]["resource_budget"] == (
        budget.model_dump(mode="json")
    )


def test_durable_verifier_failure_prevents_commit(tmp_path):
    git_repository = FakeGitRepository()
    one_step = {**PLAN, "steps": [PLAN["steps"][0]]}
    runner, _ = orchestrator(
        tmp_path,
        planner=FakePlanner(one_step),
        verifier=FakeVerifier(passed=False),
        git_repository=git_repository,
        commit_changes=True,
    )

    run_id = runner.create_durable_run("Create calculator")
    runner.model_router.set_logger(RunLogger(log_dir=runner.config.runs_dir))
    assert runner.model_router.logger.run_id != run_id
    report = runner.run_durable(run_id)

    assert report.status == RunStatus.FAILED
    assert runner.model_router.logger.run_id == run_id
    assert report.verifier_status == "failed"
    assert git_repository.commits == []


def test_durable_reviewer_rejection_prevents_commit_and_pr(tmp_path):
    git_repository = FakeGitRepository()
    pr_client = FakePRClient()
    one_step = {**PLAN, "steps": [PLAN["steps"][0]]}
    runner, _ = orchestrator(
        tmp_path,
        planner=FakePlanner(one_step),
        reviewer=FakeReviewer(approved=False),
        git_repository=git_repository,
        pr_client=pr_client,
        commit_changes=True,
        open_pr=True,
    )

    run_id = runner.create_durable_run("Create calculator")
    report = runner.run_durable(run_id)

    assert report.status == RunStatus.FAILED
    assert report.reviewer_status == "rejected"
    assert report.successful_tasks == ["step-1"]
    assert report.failed_tasks == []
    attempt = runner.state_store.list_attempts(run_id, "step-1")[0]
    assert attempt.status.value == "succeeded"
    assert report.changed_files == ["calculator.py", "tests/test_calculator.py"]
    assert report.side_effects_persisted is True
    store_artifacts = runner.state_store.load_snapshot(run_id).artifacts
    assert store_artifacts
    assert {artifact.identifier for artifact in store_artifacts} == {
        "calculator.py",
        "tests/test_calculator.py",
    }
    assert git_repository.commits == []
    assert git_repository.pushes == []
    assert pr_client.calls == []


def test_task_reviews_are_scoped_and_final_review_runs_last(tmp_path):
    timeline = []

    class TimelineCoder(FakeCoder):
        def execute(self, user_task, plan, reviewer_feedback=None):
            timeline.append(f"coder:{plan['steps'][0]['id']}")
            return super().execute(user_task, plan, reviewer_feedback)

    class TimelineVerifier(FakeVerifier):
        def verify(self, **kwargs):
            timeline.append("verify")
            if kwargs.get("invocation_key") == "final-run":
                assert kwargs["run_id"]
                assert kwargs["task_id"] == "step-2"
                assert kwargs["tool_runtime"].workspace == (
                    tmp_path / "workspace"
                ).resolve()
            return super().verify()

    class TimelineReviewer(FakeReviewer):
        def review_task(self, **kwargs):
            task_id = kwargs["task_spec"]["id"]
            timeline.append(f"task-review:{task_id}")
            assert len(kwargs["task_spec"].get("dependencies", [])) <= 1
            return super().review_task(**kwargs)

        def review(self, user_task, plan, git_diff, test_output=None):
            timeline.append("final-review")
            return super().review(user_task, plan, git_diff, test_output)

    runner, _ = orchestrator(
        tmp_path,
        coder=TimelineCoder(),
        verifier=TimelineVerifier(),
        reviewer=TimelineReviewer(),
    )

    report = runner.run_durable(runner.create_durable_run("Create calculator"))

    assert report.status == RunStatus.SUCCEEDED
    assert timeline == [
        "coder:step-1",
        "verify",
        "task-review:step-1",
        "coder:step-2",
        "verify",
        "task-review:step-2",
        "verify",
        "final-review",
    ]


def test_resume_reconciles_persisted_final_review_without_rerunning_it(
    tmp_path,
    monkeypatch,
):
    reviewer = FakeReviewer()
    verifier = FakeVerifier()
    one_step = {**PLAN, "steps": [PLAN["steps"][0]]}
    runner, store = orchestrator(
        tmp_path,
        planner=FakePlanner(one_step),
        reviewer=reviewer,
        verifier=verifier,
    )
    run_id = runner.create_durable_run("Create calculator")
    original_update = store.update_run_status
    crash_once = {"pending": True}

    def crash_before_final_status(*args, **kwargs):
        if kwargs.get("event_type") == "durable_run_succeeded" and crash_once["pending"]:
            crash_once["pending"] = False
            raise StateStoreError("simulated final status interruption")
        return original_update(*args, **kwargs)

    monkeypatch.setattr(store, "update_run_status", crash_before_final_status)
    with pytest.raises(StateStoreError, match="simulated final status interruption"):
        runner.run_durable(run_id)

    review_calls = reviewer.calls
    verifier_calls = verifier.calls
    report = runner.resume_durable(run_id)

    assert report.status == RunStatus.SUCCEEDED
    assert reviewer.calls == review_calls
    assert verifier.calls == verifier_calls
    assert report.successful_tasks == ["step-1"]


def test_durable_provider_failure_is_classified_and_prevents_git_finalization(
    tmp_path,
):
    git_repository = FakeGitRepository()
    pr_client = FakePRClient()
    coder = FailingProviderCoder()
    one_step = {**PLAN, "steps": [PLAN["steps"][0]]}
    runner, store = orchestrator(
        tmp_path,
        planner=FakePlanner(one_step),
        coder=coder,
        git_repository=git_repository,
        pr_client=pr_client,
        commit_changes=True,
        open_pr=True,
    )

    run_id = runner.create_durable_run("Create calculator")
    report = runner.run_durable(run_id)
    attempt = store.list_attempts(run_id, "step-1")[0]

    assert report.status == RunStatus.FAILED
    assert attempt.error_category == FailureCategory.MODEL_PROVIDER_ERROR
    assert attempt.metadata["provider_failure"]["provider"] == "azure"
    assert attempt.metadata["provider_failure"]["model"] == "coder-deployment"
    assert "must-not-persist" not in str(attempt.metadata)
    assert len(coder.calls) == 1
    assert git_repository.commits == []
    assert git_repository.pushes == []
    assert pr_client.calls == []


def test_successful_durable_run_allows_opt_in_commit_and_pr(tmp_path):
    git_repository = FakeGitRepository()
    pr_client = FakePRClient()
    one_step = {**PLAN, "steps": [PLAN["steps"][0]]}
    runner, store = orchestrator(
        tmp_path,
        planner=FakePlanner(one_step),
        git_repository=git_repository,
        pr_client=pr_client,
        create_branch=True,
        branch_name="agentbus/calculator",
        commit_changes=True,
        open_pr=True,
    )

    run_id = runner.create_durable_run("Create calculator")
    report = runner.run_durable(run_id)

    assert report.status == RunStatus.SUCCEEDED
    assert report.commit_identifier == "abc1234"
    assert report.pr_url == "https://github.com/acme/repo/pull/1"
    assert git_repository.created_branches == ["agentbus/calculator"]
    assert git_repository.commits == ["feat: create calculator"]
    assert git_repository.pushes == ["agentbus/calculator"]
    persisted = store.get_run(run_id)
    assert persisted.commit_identifier == "abc1234"
    assert persisted.metadata["repository_revisions"] == {
        "base_commit": "before1",
        "result_commit": "abc1234",
    }


def test_ambiguous_pr_outcome_is_not_retried_automatically(tmp_path):
    git_repository = FakeGitRepository()
    pr_client = AmbiguousPRClient()
    one_step = {**PLAN, "steps": [PLAN["steps"][0]]}
    runner, _ = orchestrator(
        tmp_path,
        planner=FakePlanner(one_step),
        git_repository=git_repository,
        pr_client=pr_client,
        commit_changes=True,
        open_pr=True,
    )

    run_id = runner.create_durable_run("Create calculator")
    first = runner.run_durable(run_id)
    second = runner.run_durable(run_id, resume=True)

    assert first.status == RunStatus.SUCCEEDED
    assert second.status == RunStatus.SUCCEEDED
    assert pr_client.calls == 1
    assert "unknown outcome" in second.finalization_error


def test_commit_completed_before_state_crash_is_reconciled_without_duplicate(
    tmp_path, monkeypatch
):
    git_repository = FakeGitRepository()
    one_step = {**PLAN, "steps": [PLAN["steps"][0]]}
    runner, store = orchestrator(
        tmp_path,
        planner=FakePlanner(one_step),
        git_repository=git_repository,
        commit_changes=True,
    )
    run_id = runner.create_durable_run("Create calculator")
    original_update = store.update_run_details
    crash_once = {"pending": True}

    def crash_after_commit(*args, **kwargs):
        if kwargs.get("event_type") == "commit_created" and crash_once["pending"]:
            crash_once["pending"] = False
            raise StateStoreError("simulated state write interruption")
        return original_update(*args, **kwargs)

    monkeypatch.setattr(store, "update_run_details", crash_after_commit)
    with pytest.raises(StateStoreError, match="simulated state write interruption"):
        runner.run_durable(run_id)

    report = runner.run_durable(run_id, resume=True)

    assert report.status == RunStatus.SUCCEEDED
    assert report.commit_identifier == "abc1234"
    assert git_repository.commits == ["feat: create calculator"]


def test_cancellation_during_final_review_preserves_successful_task_history(
    tmp_path,
):
    settings = config(tmp_path)
    store = StateStore(settings.state_database_path)
    registry = CancellationRegistry(store)
    token = CancellationToken()
    registry.register("final-review-cancel", token)
    engine = DurableExecutionEngine(
        store,
        cancellation_registry=registry,
    )
    cancellation_reports = []
    git_repository = FakeGitRepository()

    class CancellingFinalReviewer(FakeReviewer):
        def review(self, user_task, plan, git_diff, test_output=None):
            with token.operation(
                "deterministic.generate_json",
                source="provider:deterministic",
                interruptible=True,
                provider="deterministic",
            ):
                assert token.snapshot().active_operations
                cancellation_reports.append(
                    engine.request_cancellation(
                        "final-review-cancel",
                        "stop final review",
                    )
                )
                token.checkpoint(
                    "provider:deterministic",
                    stage="final-review",
                    provider="deterministic",
                )
            raise AssertionError("Final reviewer must be interrupted")

    one_step = {**PLAN, "steps": [PLAN["steps"][0]]}
    runner = MultiAgentOrchestrator(
        config=settings,
        planner=FakePlanner(one_step),
        coder=FakeCoder(),
        verifier=FakeVerifier(),
        reviewer=CancellingFinalReviewer(),
        git_repository=git_repository,
        pr_client=FakePRClient(),
        state_store=store,
        commit_changes=True,
        cancellation=token,
        cancellation_registry=registry,
    )
    run_id = runner.create_durable_run(
        "Create calculator",
        run_id="final-review-cancel",
    )
    report = runner.run_durable(run_id)

    assert cancellation_reports[0].status == RunStatus.WAITING_FOR_REVIEW
    assert store.get_task(run_id, "step-1").status == TaskStatus.SUCCEEDED
    assert report.status == RunStatus.CANCELLED
    assert report.successful_tasks == ["step-1"]
    assert store.list_attempts(run_id, "step-1")[0].status.value == "succeeded"
    assert git_repository.commits == []
    assert store.get_run(run_id).commit_identifier is None
