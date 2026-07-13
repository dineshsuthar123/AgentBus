from __future__ import annotations

import argparse
import json

from agentbus.evaluation.comparison import compare_runs
from agentbus.evaluation.config import EvaluationConfig
from agentbus.evaluation.errors import EvaluationError
from agentbus.evaluation.models import ComparisonThresholds, EvaluationRun
from agentbus.evaluation.runner import EvaluationRunner
from agentbus.evaluation.storage import EvaluationStorage
from agentbus.evaluation.suites import builtin_suites, builtin_variants


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m agentbus.eval",
        description="Run deterministic AgentBus evaluations and regression gates.",
    )
    parser.add_argument("--results-dir", help="Override evaluation result storage.")
    commands = parser.add_subparsers(dest="command", required=True)

    list_command = commands.add_parser("list", help="List built-in suites and variants.")
    list_command.add_argument("--json", action="store_true", help="Print JSON output.")

    run = commands.add_parser("run", help="Run an evaluation suite.")
    run.add_argument("--suite", help="Suite ID; defaults to core-offline.")
    run.add_argument("--variant", help="Variant ID; defaults to the suite variant.")
    run.add_argument("--case", action="append", dest="cases", help="Run one case ID.")
    run.add_argument("--tag", action="append", dest="tags", help="Require a case tag.")
    run.add_argument("--fail-fast", action="store_true")
    run.add_argument("--preserve-fixtures", action="store_true")
    run.add_argument("--live", action="store_true", help="Explicitly allow live provider calls.")
    run.add_argument("--max-requests", type=int)
    run.add_argument("--max-tokens", type=int)
    run.add_argument("--timeout-seconds", type=float)
    run.add_argument("--json", action="store_true", help="Print machine-readable JSON.")

    show = commands.add_parser("show", help="Show a stored evaluation run.")
    show.add_argument("run_id")
    show.add_argument("--json", action="store_true")

    compare = commands.add_parser("compare", help="Compare two stored evaluation runs.")
    compare.add_argument("run_a")
    compare.add_argument("run_b")
    _comparison_arguments(compare)
    compare.add_argument("--json", action="store_true")

    baseline = commands.add_parser("baseline", help="Manage named regression baselines.")
    baseline_commands = baseline.add_subparsers(dest="baseline_command", required=True)
    save = baseline_commands.add_parser("save", help="Save a run as a named baseline.")
    save.add_argument("run_id")
    save.add_argument("--name", required=True)
    save.add_argument("--replace", action="store_true")
    baseline_compare = baseline_commands.add_parser(
        "compare", help="Compare a run to a named baseline."
    )
    baseline_compare.add_argument("run_id")
    baseline_compare.add_argument("--name", required=True)
    _comparison_arguments(baseline_compare)
    baseline_compare.add_argument("--json", action="store_true")

    export = commands.add_parser("export", help="Export a sanitized self-contained report.")
    export.add_argument("run_id")
    export.add_argument("--output", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        config = EvaluationConfig.from_env()
        results_dir = args.results_dir or config.results_dir
        storage = EvaluationStorage(results_dir)
        if args.command == "list":
            return _list(args)
        if args.command == "run":
            return _run(args, config, storage)
        if args.command == "show":
            run = storage.load_run(args.run_id)
            print(run.model_dump_json(indent=2) if args.json else render_run(run))
            return 0 if run.passed else 1
        if args.command == "compare":
            comparison = compare_runs(
                storage.load_run(args.run_a),
                storage.load_run(args.run_b),
                _thresholds(args),
            )
            print(
                comparison.model_dump_json(indent=2)
                if args.json
                else render_comparison(comparison)
            )
            return 0 if comparison.passed else 1
        if args.command == "baseline" and args.baseline_command == "save":
            run = storage.load_run(args.run_id)
            path = storage.save_baseline(args.name, run, replace=args.replace)
            print(f"Saved baseline '{args.name}' from {args.run_id}: {path}")
            return 0
        if args.command == "baseline" and args.baseline_command == "compare":
            comparison = compare_runs(
                storage.load_baseline(args.name),
                storage.load_run(args.run_id),
                _thresholds(args),
            )
            print(
                comparison.model_dump_json(indent=2)
                if args.json
                else render_comparison(comparison)
            )
            return 0 if comparison.passed else 1
        if args.command == "export":
            output = storage.export_run(args.run_id, args.output)
            print(f"Exported sanitized evaluation report: {output}")
            return 0
    except (EvaluationError, ValueError) as exc:
        print(f"Evaluation error: {exc}")
        return 2
    parser.error("unsupported evaluation command")
    return 2


def _list(args) -> int:
    suites = builtin_suites()
    variants = builtin_variants()
    if args.json:
        print(
            json.dumps(
                {
                    "suites": [
                        {
                            "suite_id": suite.suite_id,
                            "title": suite.title,
                            "cases": [case.case_id for case in suite.cases],
                            "default_variant": suite.default_variant,
                            "tags": sorted(suite.tags),
                        }
                        for suite in suites.values()
                    ],
                    "variants": [
                        variant.model_dump(mode="json") for variant in variants.values()
                    ],
                },
                indent=2,
                sort_keys=True,
            )
        )
        return 0
    print("Evaluation suites:")
    for suite in suites.values():
        print(
            f"  {suite.suite_id}: {suite.title} "
            f"({len(suite.cases)} cases, default={suite.default_variant})"
        )
        for case in suite.cases:
            print(f"    {case.case_id}: {case.title}")
    print("Evaluation variants:")
    for variant in variants.values():
        mode = "live" if variant.live else "offline"
        parallel = "parallel" if variant.parallel else "sequential"
        print(f"  {variant.variant_id}: {variant.provider}, {parallel}, {mode}")
    return 0


def _run(args, config, storage) -> int:
    suite_id = args.suite or "core-offline"
    suites = builtin_suites()
    variants = builtin_variants()
    suite = suites.get(suite_id)
    if suite is None:
        raise ValueError(f"Unknown evaluation suite: {suite_id}")
    variant_id = args.variant or suite.default_variant
    variant = variants.get(variant_id)
    if variant is None:
        raise ValueError(f"Unknown evaluation variant: {variant_id}")
    max_requests = (
        args.max_requests if args.max_requests is not None else config.max_requests
    )
    max_tokens = args.max_tokens if args.max_tokens is not None else config.max_tokens
    timeout_seconds = (
        args.timeout_seconds
        if args.timeout_seconds is not None
        else config.timeout_seconds
    )
    if args.live:
        if not args.suite:
            raise ValueError("--live requires explicit --suite selection")
        print("WARNING: live provider evaluation explicitly enabled.")
        print(f"Provider: {variant.provider}")
        print(f"Role models/deployments: {variant.models_by_role or '[environment configuration]'}")
        print(f"Estimated maximum calls: {_estimated_calls(suite, set(args.cases or []))}")
        print(f"Hard request budget: {max_requests}")
        print(f"Hard token budget: {max_tokens}")
    runner = EvaluationRunner(
        results_dir=storage.root,
        owned_fixture_root=config.fixture_root,
        storage=storage,
        suites=suites,
        variants=variants,
    )
    run = runner.run(
        suite_id,
        variant_id=variant_id,
        case_ids=set(args.cases or []) or None,
        tags=set(args.tags or []) or None,
        fail_fast=args.fail_fast,
        preserve_fixtures=args.preserve_fixtures or config.preserve_fixtures,
        live=args.live,
        max_requests=max_requests,
        max_tokens=max_tokens,
        timeout_seconds=timeout_seconds,
    )
    print(run.model_dump_json(indent=2) if args.json else render_run(run))
    return 0 if run.passed else 1


def render_run(run: EvaluationRun) -> str:
    metrics = run.aggregate_metrics
    passed = sum(case.passed for case in run.case_results)
    lines = [
        f"Evaluation run: {run.evaluation_run_id}",
        f"Suite: {run.suite_id}",
        f"Variant: {run.variant.variant_id}",
        f"Cases: {passed}/{len(run.case_results)} passed",
        f"Score: {run.aggregate_score:.2f}/100",
        f"Duration: {metrics.execution.total_duration_seconds:.3f}s",
        f"Tokens: {metrics.provider.total_tokens}",
        f"Requests: {metrics.provider.requests}",
        f"Retries: {metrics.execution.retries}",
        f"Fallbacks: {metrics.provider.fallbacks}",
        f"Safety failures: {metrics.quality.safety_violation_count}",
        f"Result: {'PASS' if run.passed else 'FAIL'}",
    ]
    for case in run.case_results:
        if case.passed:
            continue
        lines.append(f"Failed case: {case.case_id} ({case.run_status})")
        lines.append(
            f"  Verifier: {case.verifier_passed}; reviewer: {case.reviewer_approved}"
        )
        if case.relevant_changed_files:
            lines.append("  Relevant files: " + ", ".join(case.relevant_changed_files))
        if case.failure_category or case.failure_message:
            lines.append(
                f"  Failure: {case.failure_category or 'unknown'}: "
                f"{case.failure_message or '[no message]'}"
            )
        for assertion in case.assertions:
            if assertion.passed is False:
                lines.append(f"  Assertion {assertion.assertion_id}: {assertion.message}")
        if case.retained_fixture_path:
            lines.append(f"  Retained fixture: {case.retained_fixture_path}")
        if case.runtime_run_id:
            lines.append(
                "  Runtime debug: python -m agentbus.main --show-run "
                f"{case.runtime_run_id} --workspace <retained-fixture-repo>"
            )
    return "\n".join(lines)


def render_comparison(comparison) -> str:
    lines = [
        f"Baseline: {comparison.baseline_run_id}",
        f"Current: {comparison.current_run_id}",
        f"Regression result: {'PASS' if comparison.passed else 'FAIL'}",
        comparison.summary,
    ]
    for regression in comparison.regressions:
        lines.append(
            f"  [{regression.severity.value}] {regression.case_id or 'suite'} "
            f"{regression.metric}: {regression.message}"
        )
    return "\n".join(lines)


def _comparison_arguments(parser) -> None:
    parser.add_argument("--score-drop", type=float, default=2.0)
    parser.add_argument("--token-increase-ratio", type=float, default=0.25)
    parser.add_argument("--latency-increase-ratio", type=float, default=0.30)
    parser.add_argument("--retry-increase", type=int, default=0)


def _thresholds(args) -> ComparisonThresholds:
    return ComparisonThresholds(
        score_drop=args.score_drop,
        token_increase_ratio=args.token_increase_ratio,
        latency_increase_ratio=args.latency_increase_ratio,
        retry_increase=args.retry_increase,
    )


def _estimated_calls(suite, selected: set[str]) -> int:
    total = 0
    for case in suite.cases:
        if selected and case.case_id not in selected:
            continue
        tasks = case.metadata.get("tasks")
        task_count = len(tasks) if isinstance(tasks, list) and tasks else 1
        total += 1 + task_count * case.maximum_attempts * 2 + 1
    return total


if __name__ == "__main__":
    raise SystemExit(main())
