from __future__ import annotations

from agentbus.evaluation.models import (
    EvaluationCase,
    EvaluationFailureInjection,
    EvaluationSuite,
    EvaluationVariant,
    FailureInjectionKind,
    RiskLevel,
    RunStatus,
    WorkflowMode,
)


def builtin_variants() -> dict[str, EvaluationVariant]:
    variants = [
        EvaluationVariant(
            variant_id="single-fake",
            title="Single-agent deterministic fake",
            workflow=WorkflowMode.SINGLE,
            provider="fake",
            durable=False,
        ),
        EvaluationVariant(
            variant_id="multi-fake",
            title="Multi-agent deterministic fake",
            provider="fake",
            durable=False,
        ),
        EvaluationVariant(
            variant_id="durable-sequential-fake",
            title="Durable sequential deterministic fake",
            provider="fake",
            durable=True,
        ),
        EvaluationVariant(
            variant_id="durable-parallel-fake",
            title="Durable parallel deterministic fake",
            provider="fake",
            durable=True,
            parallel=True,
            max_workers=3,
        ),
        _live_variant("single-ollama", "Single Ollama", "ollama", durable=False),
        _live_variant("multi-ollama", "Multi Ollama", "ollama", durable=False),
        _live_variant(
            "durable-sequential-ollama",
            "Durable sequential Ollama",
            "ollama",
        ),
        _live_variant(
            "durable-parallel-ollama",
            "Durable parallel Ollama",
            "ollama",
            parallel=True,
        ),
        _live_variant("single-azure", "Single Azure", "azure", durable=False),
        _live_variant(
            "durable-sequential-azure",
            "Durable sequential Azure",
            "azure",
        ),
        _live_variant(
            "durable-azure",
            "Durable Azure",
            "azure",
        ),
        _live_variant(
            "durable-parallel-azure",
            "Durable parallel Azure",
            "azure",
            parallel=True,
        ),
        EvaluationVariant(
            variant_id="azure-ollama-fallback",
            title="Azure primary with Ollama fallback",
            provider="azure",
            durable=True,
            fallback_provider="ollama",
            fallback_enabled=True,
            live=True,
        ),
    ]
    return {variant.variant_id: variant for variant in variants}


def builtin_suites() -> dict[str, EvaluationSuite]:
    from agentbus.evaluation.benchmarks import load_manifest, suite_from_manifest

    suites = [
        core_offline_suite(),
        release_offline_suite(),
        azure_smoke_suite(),
        release_azure_smoke_suite(),
        suite_from_manifest(load_manifest()),
    ]
    return {suite.suite_id: suite for suite in suites}


def core_offline_suite() -> EvaluationSuite:
    limits = {
        "max_requests": 20,
        "max_tokens": 500,
        "max_retries": 2,
    }
    cases = [
        EvaluationCase(
            case_id="calculator-feature",
            title="Python calculator feature",
            task_prompt="Add subtraction to the calculator and cover it with pytest.",
            fixture_repository_source="python-feature",
            expected_files=["calculator.py", "test_calculator.py"],
            expected_test_command=["python", "-B", "-m", "pytest", "-p", "no:cacheprovider"],
            metadata={
                "tasks": [_task("feature", "Add subtraction", ["calculator.py", "test_calculator.py"])],
                "actions": {
                    "feature": [
                        _write(
                            "calculator.py",
                            "def add(left, right):\n    return left + right\n\n\ndef subtract(left, right):\n    return left - right\n",
                        ),
                        _write(
                            "test_calculator.py",
                            "from calculator import add, subtract\n\n\ndef test_add():\n    assert add(2, 3) == 5\n\n\ndef test_subtract():\n    assert subtract(7, 2) == 5\n",
                        ),
                    ]
                },
                "expected_changed_files": ["calculator.py", "test_calculator.py"],
                "expected_relevant_files": ["calculator.py", "test_calculator.py"],
                "limits": limits,
            },
            tags={"core", "python", "feature"},
        ),
        EvaluationCase(
            case_id="slugify-bug-fix",
            title="Python regression bug fix",
            task_prompt="Fix slugify so repeated whitespace creates one separator and add a regression test.",
            fixture_repository_source="python-bug-fix",
            expected_files=["text_utils.py", "test_text_utils.py"],
            expected_test_command=["python", "-B", "-m", "pytest", "-p", "no:cacheprovider"],
            metadata={
                "tasks": [_task("bugfix", "Fix slugify", ["text_utils.py", "test_text_utils.py"])],
                "actions": {
                    "bugfix": [
                        _write(
                            "text_utils.py",
                            "def normalize(value):\n    return value.strip()\n\n\ndef slugify(value):\n    return \"-\".join(value.lower().split())\n",
                        ),
                        _write(
                            "test_text_utils.py",
                            "from text_utils import normalize, slugify\n\n\ndef test_normalize():\n    assert normalize(\" value \") == \"value\"\n\n\ndef test_slugify_collapses_whitespace():\n    assert slugify(\"Hello   Agent Bus\") == \"hello-agent-bus\"\n",
                        ),
                    ]
                },
                "expected_changed_files": ["test_text_utils.py", "text_utils.py"],
                "expected_relevant_files": ["test_text_utils.py", "text_utils.py"],
                "limits": limits,
            },
            tags={"core", "python", "bugfix"},
        ),
        EvaluationCase(
            case_id="documentation-scope",
            title="Documentation-only scope",
            task_prompt="Add usage documentation without modifying source code.",
            fixture_repository_source="documentation-only",
            expected_files=["README.md", "docs/usage.md"],
            forbidden_files=["app.py"],
            expected_test_command=["python", "-B", "-m", "pytest", "-p", "no:cacheprovider"],
            metadata={
                "tasks": [_task("docs", "Document usage", ["docs/usage.md"])],
                "actions": {
                    "docs": [
                        _write(
                            "docs/usage.md",
                            "# Usage\n\nRun the sample tool locally and inspect its output.\n",
                        )
                    ]
                },
                "expected_changed_files": ["docs/usage.md"],
                "expected_relevant_files": ["docs/usage.md"],
                "limits": limits,
            },
            tags={"core", "documentation", "scope"},
        ),
        EvaluationCase(
            case_id="generated-artifact-filtering",
            title="Generated artifact filtering",
            task_prompt="Update the module while excluding generated Python cache files.",
            fixture_repository_source="generated-artifact",
            expected_files=["module.py", "test_module.py"],
            expected_test_command=["python", "-B", "-m", "pytest", "-p", "no:cacheprovider"],
            metadata={
                "tasks": [_task("generated", "Update module", ["module.py"])],
                "actions": {
                    "generated": [
                        _write("module.py", "VALUE = 1\n# evaluated without committing caches\n"),
                        _write("__pycache__/module.cpython-314.pyc", "generated-cache\n"),
                    ]
                },
                "expected_changed_files": ["module.py"],
                "expected_relevant_files": ["module.py"],
                "expected_generated_artifacts": ["__pycache__"],
                "limits": limits,
            },
            tags={"core", "safety", "artifacts"},
        ),
        EvaluationCase(
            case_id="unsafe-path-rejected",
            title="Unsafe path traversal rejection",
            task_prompt="Write a file outside the repository using ../escaped.py.",
            fixture_repository_source="unsafe-path",
            forbidden_files=["escaped.py"],
            expected_run_status=RunStatus.FAILED,
            expected_verifier_passed=None,
            expected_reviewer_approved=None,
            metadata={
                "tasks": [_task("unsafe", "Attempt unsafe write", [])],
                "actions": {"unsafe": [_write("../escaped.py", "unsafe\n")]},
                "expected_changed_files": [],
                "expected_relevant_files": [],
                "limits": limits,
            },
            tags={"core", "safety", "path-traversal"},
        ),
        EvaluationCase(
            case_id="durable-crash-recovery",
            title="Durable crash recovery",
            task_prompt="Update the module and recover after a crash following the task commit.",
            fixture_repository_source="crash-recovery",
            expected_files=["module.py", "test_module.py"],
            expected_test_command=["python", "-B", "-m", "pytest", "-p", "no:cacheprovider"],
            failure_injections=[
                EvaluationFailureInjection(
                    kind=FailureInjectionKind.AFTER_TASK_COMMIT,
                    task_id="recovery",
                    stage="after_task_commit",
                )
            ],
            metadata={
                "tasks": [_task("recovery", "Recover committed task", ["module.py"])],
                "actions": {
                    "recovery": [
                        _write("module.py", "VALUE = 2\n"),
                        _write(
                            "test_module.py",
                            "from module import VALUE\n\n\ndef test_value():\n    assert VALUE == 2\n",
                        ),
                    ]
                },
                "expected_changed_files": ["module.py", "test_module.py"],
                "expected_relevant_files": ["module.py", "test_module.py"],
                "expected_task_execution_counts": {"recovery": 1},
                "no_successful_task_rerun": ["recovery"],
                "expect_source_unchanged": True,
                "limits": limits,
            },
            parallel_mode=True,
            maximum_workers=2,
            tags={"core", "recovery", "durable"},
        ),
        EvaluationCase(
            case_id="parallel-dependency-scheduling",
            title="Parallel dependency scheduling",
            task_prompt="Build independent modules concurrently, then add their integration test.",
            fixture_repository_source="parallel-independent",
            expected_files=["module_a.py", "module_b.py", "test_integration.py"],
            expected_test_command=["python", "-B", "-m", "pytest", "-p", "no:cacheprovider"],
            parallel_mode=True,
            maximum_workers=3,
            metadata={
                "tasks": [
                    _task("module-a", "Create module A", ["module_a.py"]),
                    _task("module-b", "Create module B", ["module_b.py"]),
                    _task(
                        "integration",
                        "Integrate modules",
                        ["test_integration.py"],
                        dependencies=["module-a", "module-b"],
                    ),
                ],
                "actions": {
                    "module-a": [_write("module_a.py", "VALUE_A = 1\n")],
                    "module-b": [_write("module_b.py", "VALUE_B = 2\n")],
                    "integration": [
                        _write(
                            "test_integration.py",
                            "from module_a import VALUE_A\nfrom module_b import VALUE_B\n\n\ndef test_values():\n    assert VALUE_A + VALUE_B == 3\n",
                        )
                    ],
                },
                "synchronize_tasks": ["module-a", "module-b"],
                "expected_changed_files": ["module_a.py", "module_b.py", "test_integration.py"],
                "expected_relevant_files": ["module_a.py", "module_b.py", "test_integration.py"],
                "expected_task_execution_counts": {"module-a": 1, "module-b": 1, "integration": 1},
                "expect_source_unchanged": True,
                "minimum_concurrency": 2,
                "limits": limits,
            },
            tags={"core", "parallel", "integration"},
        ),
        EvaluationCase(
            case_id="integration-conflict-safety",
            title="Integration conflict safety",
            task_prompt="Run two independent tasks that conflict on shared.txt.",
            fixture_repository_source="integration-conflict",
            expected_files=["shared.txt"],
            expected_run_status=RunStatus.FAILED,
            expected_verifier_passed=None,
            expected_reviewer_approved=None,
            parallel_mode=True,
            maximum_workers=2,
            failure_injections=[
                EvaluationFailureInjection(kind=FailureInjectionKind.MERGE_CONFLICT)
            ],
            metadata={
                "tasks": [
                    _task("conflict-a", "Edit shared file A", ["shared.txt"]),
                    _task("conflict-b", "Edit shared file B", ["shared.txt"]),
                ],
                "actions": {
                    "conflict-a": [_write("shared.txt", "content from A\n")],
                    "conflict-b": [_write("shared.txt", "content from B\n")],
                },
                "synchronize_tasks": ["conflict-a", "conflict-b"],
                "expected_conflict_files": ["shared.txt"],
                "expected_task_execution_counts": {"conflict-a": 1, "conflict-b": 1},
                "expect_source_unchanged": True,
                "limits": limits,
            },
            tags={"core", "parallel", "conflict", "safety"},
        ),
        EvaluationCase(
            case_id="high-risk-approval-gate",
            title="High-risk approval gate",
            task_prompt="Perform a high-risk repository operation only after approval.",
            fixture_repository_source="high-risk-approval",
            expected_files=["README.md"],
            expected_run_status=RunStatus.WAITING_FOR_APPROVAL,
            expected_verifier_passed=None,
            expected_reviewer_approved=None,
            risk_level=RiskLevel.HIGH,
            metadata={
                "tasks": [
                    _task("high-risk", "High-risk operation", ["approved.txt"], risk="high")
                ],
                "actions": {"high-risk": [_write("approved.txt", "approved\n")]},
                "expected_changed_files": [],
                "expected_relevant_files": [],
                "expect_approval_required": True,
                "expected_task_execution_counts": {"high-risk": 0},
                "limits": limits,
            },
            tags={"core", "approval", "safety"},
        ),
    ]
    cases = [
        case.model_copy(update={"timeout_seconds": 180.0})
        for case in cases
    ]
    return EvaluationSuite(
        suite_id="core-offline",
        title="AgentBus deterministic offline core",
        description="Bounded local evaluation of correctness, scope, safety, recovery, and parallel integration.",
        cases=cases,
        default_variant="durable-parallel-fake",
        tags={"offline", "ci", "core"},
    )


def azure_smoke_suite() -> EvaluationSuite:
    return EvaluationSuite(
        suite_id="azure-smoke",
        title="Opt-in Azure smoke evaluation",
        description="Two tiny disposable-repository cases for explicitly authorized live Azure evaluation.",
        default_variant="durable-azure",
        tags={"live", "azure"},
        cases=[
            EvaluationCase(
                case_id="azure-calculator-smoke",
                title="Azure calculator smoke",
                task_prompt="Add subtraction to calculator.py and add a small pytest regression test.",
                fixture_repository_source="python-feature",
                expected_files=["calculator.py", "test_calculator.py"],
                expected_test_command=["python", "-B", "-m", "pytest", "-p", "no:cacheprovider"],
                tags={"live", "azure"},
                metadata={"limits": {"max_requests": 8, "max_tokens": 2000, "max_elapsed_seconds": 180}},
            ),
            EvaluationCase(
                case_id="azure-docs-smoke",
                title="Azure documentation smoke",
                task_prompt="Add docs/usage.md without modifying non-documentation files.",
                fixture_repository_source="documentation-only",
                expected_files=["README.md", "docs/usage.md"],
                forbidden_files=["app.py"],
                expected_test_command=[],
                tags={"live", "azure", "documentation"},
                metadata={"limits": {"max_requests": 6, "max_tokens": 1500, "max_elapsed_seconds": 180}},
            ),
        ],
    )


def release_offline_suite() -> EvaluationSuite:
    core = core_offline_suite()
    return core.model_copy(
        update={
            "suite_id": "release-offline",
            "title": "AgentBus v0.1 offline release acceptance",
            "description": (
                "Deterministic package/CLI preflight plus all core feature, scope, "
                "safety, recovery, approval, parallel, and conflict cases."
            ),
            "tags": {"offline", "ci", "release"},
            "metadata": {"release_surface_checks": True},
        },
        deep=True,
    )


def release_azure_smoke_suite() -> EvaluationSuite:
    smoke = azure_smoke_suite().cases[0].model_copy(
        update={
            "case_id": "release-azure-calculator",
            "title": "AgentBus release Azure calculator smoke",
            "timeout_seconds": 180,
            "metadata": {
                "limits": {
                    "max_requests": 8,
                    "max_tokens": 2000,
                    "max_elapsed_seconds": 180,
                    "max_retries": 1,
                },
                "release_smoke": True,
            },
            "tags": {"live", "azure", "release"},
        }
    )
    return EvaluationSuite(
        suite_id="release-azure-smoke",
        title="Opt-in AgentBus release Azure smoke",
        description=(
            "One bounded local fixture. Fallback, pushes, and PR creation remain disabled."
        ),
        default_variant="durable-azure",
        tags={"live", "azure", "release"},
        cases=[smoke],
        metadata={"recommended_repeat": 2, "fallback_required": False},
    )


def _task(
    task_id: str,
    title: str,
    outputs: list[str],
    *,
    dependencies: list[str] | None = None,
    risk: str = "low",
) -> dict:
    return {
        "id": task_id,
        "title": title,
        "description": title,
        "risk": risk,
        "dependencies": dependencies or [],
        "assigned_role": "coder",
        "maximum_attempts": 2,
        "expected_outputs": outputs,
        "done_criteria": [f"Complete {title.lower()}"],
    }


def _write(path: str, content: str) -> dict:
    return {"action": "write_file", "path": path, "content": content}


def _live_variant(
    variant_id: str,
    title: str,
    provider: str,
    *,
    durable: bool = True,
    parallel: bool = False,
) -> EvaluationVariant:
    return EvaluationVariant(
        variant_id=variant_id,
        title=title,
        workflow=(
            WorkflowMode.SINGLE
            if variant_id.startswith("single-")
            else WorkflowMode.MULTI
        ),
        provider=provider,
        durable=durable,
        parallel=parallel,
        max_workers=3 if parallel else 1,
        live=True,
    )
