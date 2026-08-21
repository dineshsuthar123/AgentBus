from __future__ import annotations

import json
import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest

from agentbus.config import AgentBusConfig
from agentbus.execution.engine import DurableExecutionEngine
from agentbus.execution.models import FailureCategory, RunStatus
from agentbus.execution.state_store import StateStore
from agentbus.execution.task_graph import PlanContractValidationError
from agentbus.models.azure_openai import AzureOpenAIProvider
from agentbus.models.azure_schema import (
    AzureAgentActionWire,
    AzurePlannerOutputWire,
    AzureReviewerOutputWire,
    validate_azure_structured_output_schema,
)
from agentbus.models.errors import ModelServiceUnavailableError
from agentbus.models.router import (
    ModelProviderFactory,
    ModelRouter,
    model_request_context,
)
from agentbus.models.types import ModelResult, ModelUsage
from agentbus.replay.service import TraceReplayService
from agentbus.replay.session import ReplayRequest, ReplaySessionStatus
from agentbus.runtime.loop import AgentLoop
from agentbus.runtime.orchestrator import MultiAgentOrchestrator
from agentbus.trace import ReplayMode, TraceSpanType
from agentbus.tools.protocol import ToolInvocationStatus


PLAN = {
    "goal": "Complete one safe task",
    "steps": [
        {
            "id": "step-1",
            "title": "Finish",
            "description": "Finish without changing files",
            "risk": "low",
            "execution_kind": "analysis",
            "maximum_attempts": 2,
            "expected_outputs": [],
            "done_criteria": ["Agent finishes"],
            "required_capabilities": [],
        }
    ],
    "test_strategy": "Use fake verifier",
    "done_criteria": ["Approved"],
}


class ScriptedProvider:
    def __init__(self, route, outcomes):
        self.route = route
        self.outcomes = list(outcomes)
        self.calls = []

    @property
    def provider_name(self):
        return self.route.provider

    @property
    def model_name(self):
        return self.route.model

    def generate_json(self, prompt, **kwargs):
        self.calls.append({"prompt": prompt, "kwargs": kwargs})
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return ModelResult(
            value=outcome,
            provider=self.route.provider,
            model=self.route.model,
            role=self.route.role,
            request_id=f"request-{self.route.role.value}-{len(self.calls)}",
            usage=ModelUsage(input_tokens=5, output_tokens=2, total_tokens=7),
            finish_status="completed",
            latency_seconds=0.01,
        )

    def generate_text(self, prompt, **kwargs):
        return self.generate_json(prompt, **kwargs)


class FakeVerifier:
    def __init__(self):
        self.calls = 0

    def verify(self):
        self.calls += 1
        return {
            "command": ["python", "-m", "pytest"],
            "exit_code": 0,
            "passed": True,
            "output": "offline verification passed",
            "reason": "fake verifier",
        }


class StrictAzureResponses:
    """Offline Responses transport that enforces Azure's strict schema subset."""

    def __init__(self, scripts):
        self.scripts = {model: list(outcomes) for model, outcomes in scripts.items()}
        self.parse_calls = []
        self.create_calls = []

    def parse(self, **kwargs):
        text_format = kwargs["text_format"]
        validate_azure_structured_output_schema(text_format.model_json_schema())
        self.parse_calls.append(kwargs)
        return self._response(kwargs["model"], parsed=True)

    def create(self, **kwargs):
        self.create_calls.append(kwargs)
        return self._response(kwargs["model"], parsed=False)

    def _response(self, model, *, parsed):
        outcome = self.scripts[model].pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return SimpleNamespace(
            output_text=(
                json.dumps(outcome, ensure_ascii=True)
                if isinstance(outcome, dict)
                else str(outcome)
            ),
            output_parsed=outcome if parsed else None,
            _request_id=f"strict-{model}",
            status="completed",
            usage=SimpleNamespace(
                input_tokens=5,
                output_tokens=2,
                total_tokens=7,
                input_tokens_details=SimpleNamespace(cached_tokens=0),
            ),
        )


class StrictAzureClient:
    def __init__(self, scripts):
        self.responses = StrictAzureResponses(scripts)


def approved_review(summary):
    return {
        "approved": True,
        "issues": [],
        "summary": summary,
        "required_fixes": [],
        "unplanned_affected_components": [],
        "missing_tests": [],
        "boundary_violations": [],
        "index_uncertainty": [],
    }


def config(tmp_path, *, fallback=False):
    return AgentBusConfig(
        provider_name="azure",
        fallback_provider_name="ollama",
        enable_provider_fallback=fallback,
        model_name="local-fallback-model",
        workspace_dir=str(tmp_path / "workspace"),
        runs_dir=str(tmp_path / "runs"),
        state_dir=str(tmp_path / "state"),
        max_steps=2,
        model_max_retries=1,
        model_retry_base_seconds=0,
        model_retry_max_seconds=0,
        azure_openai_endpoint="https://sample.openai.azure.com",
        azure_openai_api_key="integration-super-secret",
        azure_openai_default_deployment="default-deployment",
        azure_openai_planner_deployment="planner-deployment",
        azure_openai_coder_deployment="coder-deployment",
        azure_openai_reviewer_deployment="reviewer-deployment",
    )


def build_runner(tmp_path, scripts, *, fallback=False):
    settings = config(tmp_path, fallback=fallback)
    workspace = settings.workspace_path
    workspace.mkdir(parents=True)
    subprocess.run(
        ["git", "init", "-q", str(workspace)],
        check=True,
        capture_output=True,
        text=True,
        shell=False,
    )
    providers = {}

    def builder(route):
        key = (route.provider, route.role.value)
        provider = ScriptedProvider(route, scripts[key])
        providers[key] = provider
        return provider

    factory = ModelProviderFactory(
        settings,
        builders={"azure": builder, "ollama": builder},
    )
    router = ModelRouter(
        settings,
        provider_factory=factory,
        sleeper=lambda delay: None,
        jitter=lambda: 0,
    )
    verifier = FakeVerifier()
    store = StateStore(settings.state_database_path)
    runner = MultiAgentOrchestrator(
        config=settings,
        verifier=verifier,
        state_store=store,
        model_router=router,
    )
    return runner, store, router, providers, verifier


def test_strict_azure_schema_exercises_real_coder_loop_and_managed_write(tmp_path):
    settings = config(tmp_path).with_overrides(max_steps=2)
    client = StrictAzureClient(
        {
            "coder-deployment": [
                {
                    "action": "tool_call",
                    "tool_call": {
                        "tool_name": "filesystem.write",
                        "arguments_json": json.dumps(
                            {"path": "result.py", "content": "VALUE = 3\n"}
                        ),
                        "expected_capabilities": [
                            "filesystem.write",
                            "filesystem.create",
                        ],
                        "timeout_seconds": None,
                        "invocation_revision": 1,
                        "idempotency_key": "strict-azure-create-result",
                    },
                    "summary": None,
                },
                {
                    "action": "finish",
                    "tool_call": None,
                    "summary": "strict Azure managed write complete",
                },
            ]
        }
    )

    def builder(route):
        return AzureOpenAIProvider(
            endpoint=settings.azure_openai_endpoint,
            api_key="offline",
            deployment=route.model,
            timeout_seconds=route.timeout_seconds,
            role=route.role,
            client=client,
        )

    router = ModelRouter(
        settings,
        provider_factory=ModelProviderFactory(
            settings,
            builders={"azure": builder},
        ),
        sleeper=lambda delay: None,
        jitter=lambda: 0,
    )
    loop = AgentLoop(config=settings, model_router=router)

    summary = loop.run("Create result.py through the managed runtime")

    assert summary == "strict Azure managed write complete"
    assert (settings.workspace_path / "result.py").read_text(encoding="utf-8") == (
        "VALUE = 3\n"
    )
    assert client.responses.create_calls == []
    assert len(client.responses.parse_calls) == 2
    assert {
        call["model"] for call in client.responses.parse_calls
    } == {"coder-deployment"}
    assert all(
        call["text_format"] is AzureAgentActionWire
        for call in client.responses.parse_calls
    )
    invocations = StateStore(settings.state_database_path).list_tool_invocations(
        loop.run_id
    )
    assert len(invocations) == 1
    assert invocations[0].status == ToolInvocationStatus.SUCCEEDED


def test_read_only_prerequisite_is_rejected_before_durable_persistence(tmp_path):
    workspace = tmp_path / "read-only-prerequisite"
    workspace.mkdir()
    calculator = workspace / "calculator.py"
    calculator.write_text(
        "def divide(a, b):\n"
        "    return a / b\n",
        encoding="utf-8",
    )
    (workspace / "test_calculator.py").write_text(
        "from calculator import divide\n\n"
        "def test_divide():\n"
        "    assert divide(10, 2) == 5\n\n"
        "def test_divide_by_zero():\n"
        "    try:\n"
        "        divide(10, 0)\n"
        "        assert False\n"
        "    except ValueError:\n"
        "        pass\n",
        encoding="utf-8",
    )
    for command in (
        ["git", "init", "-q"],
        ["git", "config", "user.name", "AgentBus Offline Test"],
        ["git", "config", "user.email", "agentbus-offline@example.invalid"],
        ["git", "add", "--", "calculator.py", "test_calculator.py"],
        ["git", "commit", "-q", "-m", "test: failing calculator baseline"],
    ):
        subprocess.run(
            command,
            cwd=workspace,
            check=True,
            capture_output=True,
            text=True,
            shell=False,
        )

    plan = {
        "goal": "Fix division by zero",
        "steps": [
            {
                "id": "step-1",
                "title": "Inspect calculator implementation",
                "description": "Inspect divide and its tests.",
                "risk": "low",
                "execution_kind": "implementation",
                "dependencies": [],
                "assigned_role": "coder",
                "maximum_attempts": 1,
                "expected_outputs": [],
                "done_criteria": ["The implementation problem is understood."],
                "required_capabilities": ["filesystem.read"],
                "targeted_files": ["calculator.py", "test_calculator.py"],
                "targeted_symbols": None,
                "expected_impacted_components": None,
                "proposed_tests": ["test_calculator.py"],
                "architecture_constraints": None,
            },
            {
                "id": "step-2",
                "title": "Fix division behavior",
                "description": "Raise ValueError when the divisor is zero.",
                "risk": "low",
                "execution_kind": "implementation",
                "dependencies": ["step-1"],
                "assigned_role": "coder",
                "maximum_attempts": 1,
                "expected_outputs": ["calculator.py"],
                "done_criteria": ["divide raises ValueError for zero."],
                "required_capabilities": [
                    "filesystem.read",
                    "filesystem.write",
                ],
                "targeted_files": ["calculator.py"],
                "targeted_symbols": None,
                "expected_impacted_components": None,
                "proposed_tests": ["test_calculator.py"],
                "architecture_constraints": None,
            },
            {
                "id": "step-3",
                "title": "Verify behavior",
                "description": "Run the existing calculator tests.",
                "risk": "low",
                "execution_kind": "implementation",
                "dependencies": ["step-2"],
                "assigned_role": "coder",
                "maximum_attempts": 1,
                "expected_outputs": [],
                "done_criteria": ["The existing calculator tests pass."],
                "required_capabilities": [
                    "test.execute",
                    "process.execute",
                ],
                "targeted_files": ["test_calculator.py"],
                "targeted_symbols": None,
                "expected_impacted_components": None,
                "proposed_tests": ["test_calculator.py"],
                "architecture_constraints": None,
            },
        ],
        "test_strategy": "Run pytest.",
        "done_criteria": ["Both calculator tests pass."],
        "targeted_files": ["calculator.py", "test_calculator.py"],
        "targeted_symbols": None,
        "expected_impacted_components": None,
        "proposed_tests": ["test_calculator.py"],
        "architecture_constraints": None,
        "intelligence_snapshot_id": None,
        "intelligence_context_hash": None,
        "intelligence_warnings": None,
        "intelligence_scope_validated": None,
    }
    client = StrictAzureClient(
        {
            "agentbus-planner": [plan, plan],
        }
    )
    settings = AgentBusConfig(
        provider_name="azure",
        workspace_dir=str(workspace),
        runs_dir=str(tmp_path / "runs"),
        state_dir=str(tmp_path / "state"),
        max_steps=2,
        model_max_retries=0,
        azure_openai_endpoint="https://sample.openai.azure.com",
        azure_openai_api_key="offline-fake-key",
        azure_openai_default_deployment="agentbus-reviewer",
        azure_openai_planner_deployment="agentbus-planner",
        azure_openai_coder_deployment="agentbus-coder",
        azure_openai_reviewer_deployment="agentbus-reviewer",
        azure_openai_summarizer_deployment="agentbus-reviewer",
    )

    def builder(route):
        return AzureOpenAIProvider(
            endpoint=settings.azure_openai_endpoint,
            api_key="offline",
            deployment=route.model,
            timeout_seconds=route.timeout_seconds,
            role=route.role,
            client=client,
        )

    store = StateStore(settings.state_database_path)
    router = ModelRouter(
        settings,
        provider_factory=ModelProviderFactory(
            settings,
            builders={"azure": builder},
        ),
        sleeper=lambda delay: None,
        jitter=lambda: 0,
    )
    runner = MultiAgentOrchestrator(
        config=settings,
        state_store=store,
        model_router=router,
    )
    task = (
        "Fix divide() so division by zero raises ValueError with a clear message. "
        "Preserve normal division behavior. Make the existing tests pass. "
        "Do not modify unrelated files."
    )

    with pytest.raises(PlanContractValidationError) as captured:
        runner.create_durable_run(task)

    assert {
        (issue.task_id, issue.code) for issue in captured.value.issues
    } == {
        ("step-1", "implementation_without_mutation"),
        ("step-3", "implementation_without_mutation"),
    }
    assert calculator.read_text(encoding="utf-8") == (
        "def divide(a, b):\n"
        "    return a / b\n"
    )
    assert store.list_runs() == []
    requests = client.responses.parse_calls
    assert [call["model"] for call in requests] == [
        "agentbus-planner",
        "agentbus-planner",
    ]
    assert "previous durable plan was rejected" in requests[1]["input"]
    assert "implementation_without_mutation" in requests[1]["input"]


def test_strict_fake_azure_completes_durable_calculator_workflow(tmp_path):
    workspace = tmp_path / "calculator-repository"
    workspace.mkdir()
    calculator = workspace / "calculator.py"
    tests = workspace / "test_calculator.py"
    calculator.write_text(
        "def divide(a, b):\n"
        "    return a / b\n",
        encoding="utf-8",
    )
    initial_tests = (
        "from calculator import divide\n\n"
        "def test_divide():\n"
        "    assert divide(10, 2) == 5\n\n"
        "def test_divide_by_zero():\n"
        "    try:\n"
        "        divide(10, 0)\n"
        "        assert False\n"
        "    except ValueError:\n"
        "        pass\n"
    )
    tests.write_text(initial_tests, encoding="utf-8")
    for command in (
        ["git", "init", "-q"],
        ["git", "config", "user.name", "AgentBus Offline Test"],
        ["git", "config", "user.email", "agentbus-offline@example.invalid"],
        ["git", "add", "--", "calculator.py", "test_calculator.py"],
        ["git", "commit", "-q", "-m", "test: failing calculator baseline"],
    ):
        subprocess.run(
            command,
            cwd=workspace,
            check=True,
            capture_output=True,
            text=True,
            shell=False,
        )
    baseline = subprocess.run(
        [
            sys.executable,
            "-B",
            "-m",
            "pytest",
            "-p",
            "no:cacheprovider",
            "-q",
        ],
        cwd=workspace,
        check=False,
        capture_output=True,
        text=True,
        shell=False,
    )
    assert baseline.returncode == 1
    assert "1 failed" in baseline.stdout
    assert "1 passed" in baseline.stdout

    fixed_source = (
        "def divide(a, b):\n"
        "    if b == 0:\n"
        "        raise ValueError(\"division by zero is not allowed\")\n"
        "    return a / b\n"
    )
    plan = {
        "goal": "Make divide reject division by zero without changing normal division",
        "steps": [
            {
                "id": "step-1",
                "title": "Fix divide",
                "description": "Raise a clear ValueError when the divisor is zero.",
                "risk": "low",
                "execution_kind": "implementation",
                "dependencies": None,
                "assigned_role": "coder",
                "maximum_attempts": 1,
                "expected_outputs": ["calculator.py"],
                "done_criteria": ["Both calculator tests pass"],
                "required_capabilities": [
                    "filesystem.write",
                    "filesystem.create",
                ],
                "targeted_files": ["calculator.py"],
                "targeted_symbols": None,
                "expected_impacted_components": None,
                "proposed_tests": ["test_calculator.py"],
                "architecture_constraints": None,
            }
        ],
        "test_strategy": "Run pytest and preserve the existing tests.",
        "done_criteria": ["pytest reports two passing tests"],
        "targeted_files": ["calculator.py"],
        "targeted_symbols": None,
        "expected_impacted_components": None,
        "proposed_tests": ["test_calculator.py"],
        "architecture_constraints": None,
        "intelligence_snapshot_id": None,
        "intelligence_context_hash": None,
        "intelligence_warnings": None,
        "intelligence_scope_validated": None,
    }
    client = StrictAzureClient(
        {
            "agentbus-planner": [plan],
            "agentbus-coder": [
                {
                    "action": "tool_call",
                    "tool_call": {
                        "tool_name": "filesystem.write",
                        "arguments_json": json.dumps(
                            {"path": "calculator.py", "content": fixed_source}
                        ),
                        "expected_capabilities": [
                            "filesystem.write",
                            "filesystem.create",
                        ],
                        "timeout_seconds": None,
                        "invocation_revision": 1,
                        "idempotency_key": "fix-calculator-divide",
                    },
                    "summary": None,
                },
                {
                    "action": "finish",
                    "tool_call": None,
                    "summary": "divide now raises a clear ValueError for zero",
                },
            ],
            "agentbus-reviewer": [
                approved_review("Current calculator task is complete."),
                approved_review("The whole calculator run is approved."),
            ],
        }
    )
    settings = AgentBusConfig(
        provider_name="azure",
        workspace_dir=str(workspace),
        runs_dir=str(tmp_path / "runs"),
        state_dir=str(tmp_path / "state"),
        max_steps=2,
        model_max_retries=0,
        azure_openai_endpoint="https://sample.openai.azure.com",
        azure_openai_api_key="offline-fake-key",
        azure_openai_default_deployment="agentbus-reviewer",
        azure_openai_planner_deployment="agentbus-planner",
        azure_openai_coder_deployment="agentbus-coder",
        azure_openai_reviewer_deployment="agentbus-reviewer",
        azure_openai_summarizer_deployment="agentbus-reviewer",
    )

    def builder(route):
        return AzureOpenAIProvider(
            endpoint=settings.azure_openai_endpoint,
            api_key="offline",
            deployment=route.model,
            timeout_seconds=route.timeout_seconds,
            role=route.role,
            client=client,
        )

    store = StateStore(settings.state_database_path)
    router = ModelRouter(
        settings,
        provider_factory=ModelProviderFactory(
            settings,
            builders={"azure": builder},
        ),
        sleeper=lambda delay: None,
        jitter=lambda: 0,
    )
    runner = MultiAgentOrchestrator(
        config=settings,
        state_store=store,
        model_router=router,
    )
    task = (
        "Fix divide() so division by zero raises ValueError with a clear message. "
        "Preserve normal division behavior. Make the existing tests pass. "
        "Do not modify unrelated files."
    )

    run_id = runner.create_durable_run(task)
    report = runner.run_durable(run_id)

    assert report.status == RunStatus.SUCCEEDED
    assert report.successful_tasks == ["step-1"]
    assert report.failed_tasks == []
    assert report.blocked_tasks == []
    assert report.verifier_status == "passed"
    assert report.reviewer_status == "approved"
    assert report.changed_files == ["calculator.py"]
    assert report.relevant_changed_files == ["calculator.py"]
    assert report.commit_eligible_files == ["calculator.py"]
    assert calculator.read_text(encoding="utf-8") == fixed_source
    assert tests.read_text(encoding="utf-8") == initial_tests

    final_tests = subprocess.run(
        [
            sys.executable,
            "-B",
            "-m",
            "pytest",
            "-p",
            "no:cacheprovider",
            "-q",
        ],
        cwd=workspace,
        check=False,
        capture_output=True,
        text=True,
        shell=False,
    )
    assert final_tests.returncode == 0
    assert "2 passed" in final_tests.stdout
    changed = subprocess.run(
        ["git", "diff", "--name-only", "--", "."],
        cwd=workspace,
        check=True,
        capture_output=True,
        text=True,
        shell=False,
    )
    assert changed.stdout.strip() == "calculator.py"

    requests = client.responses.parse_calls
    assert [call["model"] for call in requests] == [
        "agentbus-planner",
        "agentbus-coder",
        "agentbus-coder",
        "agentbus-reviewer",
        "agentbus-reviewer",
    ]
    assert [call["text_format"] for call in requests] == [
        AzurePlannerOutputWire,
        AzureAgentActionWire,
        AzureAgentActionWire,
        AzureReviewerOutputWire,
        AzureReviewerOutputWire,
    ]
    assert "Review only the current task" in requests[3]["input"]
    assert "Planner output" in requests[4]["input"]
    assert all(not outcomes for outcomes in client.responses.scripts.values())

    attempt = store.list_attempts(run_id, "step-1")[0]
    assert attempt.metadata["task_review"]["approved"] is True
    persisted = store.get_run(run_id)
    assert persisted.metadata["final_review"]["status"] == "approved"
    write_invocations = [
        invocation
        for invocation in store.list_tool_invocations(run_id)
        if invocation.tool_name == "filesystem.write"
    ]
    assert len(write_invocations) == 1
    assert write_invocations[0].status == ToolInvocationStatus.SUCCEEDED

    trace = store.get_run_trace(run_id)
    final_verifier = next(
        span for span in trace.spans if span.name == "final verifier"
    )
    final_reviewer = next(
        span for span in trace.spans if span.name == "final reviewer"
    )
    assert final_verifier.sequence < final_reviewer.sequence
    assert final_reviewer.span_type == TraceSpanType.REVIEWER
    replay_service = TraceReplayService(settings, state_store=store)
    assert replay_service.verify(run_id).valid is True
    replayability = replay_service.replayability(run_id)
    assert replayability.replayable_offline is True, [
        (span.span_type.value, span.level.value, span.reasons)
        for span in replayability.spans
        if span.level.value == "non_replayable"
    ]
    replay = replay_service.replay(
        run_id,
        ReplayRequest(
            source_trace_id=trace.trace_id,
            source_run_id=run_id,
            mode=ReplayMode.OFFLINE,
        ),
    )
    assert replay.session.status == ReplaySessionStatus.SUCCEEDED, (
        replay.session.failure_category,
        replay.session.failure_message,
        replay.session.missing_inputs,
        replay.session.policy_drift,
        replay.session.substitutions,
        [
            (span.action.value, span.succeeded, span.summary)
            for span in replay.session.span_results
        ],
    )
    assert replay.session.provider_calls == 0
    assert replay.session.network_calls == 0


@pytest.mark.parametrize("provider_name", ["azure", "ollama"])
def test_parallel_worker_providers_are_isolated_and_usage_is_task_attributed(
    tmp_path, provider_name
):
    workspace = tmp_path / "repo"
    workspace.mkdir()
    worker_a = tmp_path / "worktrees" / "task-A"
    worker_b = tmp_path / "worktrees" / "task-B"
    worker_a.mkdir(parents=True)
    worker_b.mkdir(parents=True)
    settings = AgentBusConfig(
        provider_name=provider_name,
        model_name="ollama-fake",
        workspace_dir=str(workspace),
        runs_dir=str(tmp_path / "runs"),
        state_dir=str(tmp_path / "state"),
        parallel_execution=True,
        max_workers=2,
        worktree_root=str(tmp_path / "worktrees"),
        model_max_retries=0,
        azure_openai_endpoint="https://sample.openai.azure.com",
        azure_openai_api_key="offline-fake-key",
        azure_openai_default_deployment="default-deployment",
        azure_openai_coder_deployment="coder-deployment",
        azure_openai_reviewer_deployment="reviewer-deployment",
    )
    barrier = threading.Barrier(2)
    lock = threading.Lock()
    provider_instances = []

    class ConcurrentProvider:
        def __init__(self, route):
            self.route = route

        @property
        def provider_name(self):
            return self.route.provider

        @property
        def model_name(self):
            return self.route.model

        def generate_json(self, prompt, **kwargs):
            barrier.wait(timeout=10)
            return ModelResult(
                value={"ok": True},
                provider=self.route.provider,
                model=self.route.model,
                role=self.route.role,
                request_id=f"offline-{self.route.provider}-{id(self)}",
                usage=ModelUsage(input_tokens=3, output_tokens=2, total_tokens=5),
                finish_status="completed",
                latency_seconds=0.01,
            )

        def generate_text(self, prompt, **kwargs):
            return self.generate_json(prompt, **kwargs)

    def builder(route):
        instance = ConcurrentProvider(route)
        with lock:
            provider_instances.append(instance)
        return instance

    factory = ModelProviderFactory(
        settings,
        builders={"azure": builder, "ollama": builder},
    )
    router = ModelRouter(
        settings,
        provider_factory=factory,
        sleeper=lambda delay: None,
        jitter=lambda: 0,
    )
    runner = MultiAgentOrchestrator(config=settings, model_router=router)
    executors = [
        runner._parallel_task_executor(worker_a),
        runner._parallel_task_executor(worker_b),
    ]

    def invoke(index):
        task_id = f"task-{'AB'[index]}"
        with model_request_context(run_id="parallel-provider-run", task_id=task_id):
            return executors[index].coder.model.generate_json("offline request")

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(invoke, range(2)))

    assert results == [{"ok": True}, {"ok": True}]
    assert len(provider_instances) == 2
    assert provider_instances[0] is not provider_instances[1]
    assert router.usage_ledger.total(
        run_id="parallel-provider-run", task_id="task-A"
    ).total_tokens == 5
    assert router.usage_ledger.total(
        run_id="parallel-provider-run", task_id="task-B"
    ).total_tokens == 5


def test_offline_azure_durable_smoke_routes_roles_retries_and_persists_usage(
    tmp_path,
):
    transient = ModelServiceUnavailableError(
        "temporary service failure",
        provider="azure",
        model="coder-deployment",
    )
    scripts = {
        ("azure", "planner"): [PLAN],
        ("azure", "coder"): [
            transient,
            {"action": "finish", "summary": "coder completed"},
        ],
        ("azure", "reviewer"): [
            {
                "approved": True,
                "issues": [],
                "summary": "review approved",
                "required_fixes": [],
            },
            {
                "approved": True,
                "issues": [],
                "summary": "final review approved",
                "required_fixes": [],
            },
        ],
    }
    runner, store, router, providers, verifier = build_runner(tmp_path, scripts)

    run_id = runner.create_durable_run("Run offline Azure integration smoke")
    report = runner.run_durable(run_id)
    attempt = store.list_attempts(run_id, "step-1")[0]

    assert report.status == RunStatus.SUCCEEDED
    assert verifier.calls == 2
    assert len(providers[("azure", "coder")].calls) == 2
    assert attempt.metadata["model_requests"][0]["provider"] == "azure"
    assert attempt.metadata["model_requests"][0]["model"] == "coder-deployment"
    assert attempt.metadata["model_requests"][0]["retry_count"] == 1
    assert attempt.metadata["model_requests"][1]["model"] == "reviewer-deployment"
    assert attempt.metadata["model_requests"][1]["usage"]["total_tokens"] == 7
    assert router.usage_ledger.total(run_id=run_id).total_tokens == 28

    calls_before_resume = sum(len(item.calls) for item in providers.values())
    resumed = runner.resume_durable(run_id)
    assert resumed.status == RunStatus.SUCCEEDED
    assert sum(len(item.calls) for item in providers.values()) == calls_before_resume
    combined_state = str(store.load_snapshot(run_id).model_dump(mode="json"))
    combined_logs = "".join(
        path.read_text(encoding="utf-8")
        for path in (tmp_path / "runs").glob("*.jsonl")
    )
    assert "integration-super-secret" not in combined_state + combined_logs


def test_offline_azure_capability_mismatch_stops_before_dispatch_and_verifier(
    tmp_path,
):
    plan = {
        "goal": "Create a result module",
        "steps": [
            {
                "id": "step-1",
                "title": "Create result module",
                "description": "Create result.py and verify the implementation.",
                "risk": "low",
                "execution_kind": "implementation",
                "dependencies": [],
                "assigned_role": "coder",
                "maximum_attempts": 2,
                "expected_outputs": ["result.py"],
                "done_criteria": ["The result module is complete"],
                "required_capabilities": ["filesystem.write"],
            }
        ],
        "test_strategy": "Use the fake verifier",
        "done_criteria": ["The result module is complete"],
    }
    raw_secret = "raw-tool-argument-must-not-be-logged"
    scripts = {
        ("azure", "planner"): [plan],
        ("azure", "coder"): [
            {
                "action": "tool_call",
                "tool_call": {
                    "tool_name": "filesystem.write",
                    "arguments": {
                        "path": "result.py",
                        "content": f"VALUE = '{raw_secret}'\n",
                    },
                    "expected_capabilities": [
                        "filesystem.write",
                        "filesystem.create",
                    ],
                    "idempotency_key": "planner-contract-mismatch",
                },
            }
        ],
        ("azure", "reviewer"): [],
    }
    runner, store, _, providers, verifier = build_runner(tmp_path, scripts)

    run_id = runner.create_durable_run("Create a result module")
    report = runner.run_durable(run_id)
    attempt = store.list_attempts(run_id, "step-1")[0]

    assert report.status == RunStatus.FAILED
    assert report.failed_tasks == ["step-1"]
    assert report.verifier_status == "not_run"
    assert report.reviewer_status == "not_run"
    assert report.changed_files == []
    assert attempt.error_category == FailureCategory.PLAN_CAPABILITY_MISMATCH
    assert attempt.metadata["plan_capability_mismatch"] == {
        "task_id": "step-1",
        "tool_name": "filesystem.write",
        "requested_capabilities": [
            "filesystem.create",
            "filesystem.write",
        ],
        "declared_capabilities": ["filesystem.write"],
        "undeclared_capabilities": ["filesystem.create"],
    }
    assert attempt.metadata["_agentbus"]["retryable_override"] is False
    assert verifier.calls == 0
    assert len(providers[("azure", "coder")].calls) == 1
    assert ("azure", "reviewer") not in providers
    assert store.list_tool_invocations(run_id) == []
    assert not (runner.config.workspace_path / "result.py").exists()

    logs = "".join(
        path.read_text(encoding="utf-8")
        for path in (tmp_path / "runs").glob("*.jsonl")
    )
    persisted = str(store.load_snapshot(run_id).model_dump(mode="json"))
    assert "plan_capability_mismatch" in logs
    assert "filesystem.create" in logs
    assert raw_secret not in logs + persisted

    calls_before_resume = len(providers[("azure", "coder")].calls)
    resumed = runner.resume_durable(run_id)
    assert resumed.status == RunStatus.FAILED
    assert len(providers[("azure", "coder")].calls) == calls_before_resume
    assert len(store.list_attempts(run_id, "step-1")) == 1


def test_offline_fallback_smoke_exhausts_azure_then_uses_ollama_and_gates(
    tmp_path,
):
    transient_errors = [
        ModelServiceUnavailableError(
            "temporary service failure",
            provider="azure",
            model="coder-deployment",
        )
        for _ in range(2)
    ]
    scripts = {
        ("azure", "planner"): [PLAN],
        ("azure", "coder"): transient_errors,
        ("ollama", "coder"): [
            {"action": "finish", "summary": "local fallback completed"}
        ],
        ("azure", "reviewer"): [
            {
                "approved": True,
                "issues": [],
                "summary": "review approved fallback",
                "required_fixes": [],
            },
            {
                "approved": True,
                "issues": [],
                "summary": "final review approved fallback",
                "required_fixes": [],
            },
        ],
    }
    runner, store, _, providers, verifier = build_runner(
        tmp_path,
        scripts,
        fallback=True,
    )

    run_id = runner.create_durable_run("Run offline fallback smoke")
    report = runner.run_durable(run_id)
    attempt = store.list_attempts(run_id, "step-1")[0]
    coder_result = attempt.metadata["model_requests"][0]

    assert report.status == RunStatus.SUCCEEDED
    assert len(providers[("azure", "coder")].calls) == 2
    assert len(providers[("ollama", "coder")].calls) == 1
    assert verifier.calls == 2
    assert len(providers[("azure", "reviewer")].calls) == 2
    assert coder_result["provider"] == "ollama"
    assert coder_result["fallback_used"] is True
    assert coder_result["original_provider"] == "azure"
    assert coder_result["original_error_category"] == "service_unavailable"


def test_fallback_cannot_bypass_high_risk_approval(tmp_path):
    high_risk_plan = {
        **PLAN,
        "steps": [{**PLAN["steps"][0], "risk": "high"}],
    }
    scripts = {
        ("azure", "planner"): [high_risk_plan],
        ("azure", "coder"): [
            ModelServiceUnavailableError(
                "temporary",
                provider="azure",
                model="coder-deployment",
            ),
            ModelServiceUnavailableError(
                "temporary",
                provider="azure",
                model="coder-deployment",
            ),
        ],
        ("ollama", "coder"): [
            {"action": "finish", "summary": "approved fallback"}
        ],
        ("azure", "reviewer"): [
            {
                "approved": True,
                "issues": [],
                "summary": "approved",
                "required_fixes": [],
            },
            {
                "approved": True,
                "issues": [],
                "summary": "final approved",
                "required_fixes": [],
            },
        ],
    }
    runner, store, _, providers, _ = build_runner(
        tmp_path,
        scripts,
        fallback=True,
    )
    run_id = runner.create_durable_run("High-risk fallback smoke")

    waiting = runner.run_durable(run_id)

    assert waiting.status == RunStatus.WAITING_FOR_APPROVAL
    assert ("azure", "coder") not in providers
    DurableExecutionEngine(store).approve_task(run_id, "step-1", "Reviewed")
    completed = runner.resume_durable(run_id)
    assert completed.status == RunStatus.SUCCEEDED
    assert len(providers[("ollama", "coder")].calls) == 1
