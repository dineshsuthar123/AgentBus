from __future__ import annotations

import subprocess
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

from agentbus.config import AgentBusConfig
from agentbus.execution.engine import DurableExecutionEngine
from agentbus.execution.models import RunStatus
from agentbus.execution.state_store import StateStore
from agentbus.models.errors import ModelServiceUnavailableError
from agentbus.models.router import (
    ModelProviderFactory,
    ModelRouter,
    model_request_context,
)
from agentbus.models.types import ModelResult, ModelUsage
from agentbus.runtime.orchestrator import MultiAgentOrchestrator


PLAN = {
    "goal": "Complete one safe task",
    "steps": [
        {
            "id": "step-1",
            "title": "Finish",
            "description": "Finish without changing files",
            "risk": "low",
            "maximum_attempts": 2,
            "expected_outputs": [],
            "done_criteria": ["Agent finishes"],
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
