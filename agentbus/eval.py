from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from agentbus.evaluation.comparison import compare_runs
from agentbus.evaluation.config import EvaluationConfig
from agentbus.evaluation.errors import EvaluationError
from agentbus.evaluation.models import (
    ComparisonThresholds,
    EvaluationRun,
    EvaluationSeries,
    VariantComparisonReport,
)
from agentbus.evaluation.runner import EvaluationRunner
from agentbus.evaluation.statistics import compare_variants
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
    run.add_argument("--repeat", type=int, default=1)
    run.add_argument(
        "--allow-repository-download",
        action="store_true",
        help="Explicitly allow manifest-pinned real-repository cloning.",
    )
    run.add_argument("--manifest", help="Explicit real-repository manifest JSON.")
    run.add_argument("--json", action="store_true", help="Print machine-readable JSON.")

    show = commands.add_parser("show", help="Show a stored evaluation run.")
    show.add_argument("run_id")
    show.add_argument("--json", action="store_true")

    show_series = commands.add_parser("show-series", help="Show repeated-run statistics.")
    show_series.add_argument("series_id")
    show_series.add_argument("--json", action="store_true")

    compare = commands.add_parser("compare", help="Compare two stored evaluation runs.")
    compare.add_argument("run_a")
    compare.add_argument("run_b")
    _comparison_arguments(compare)
    compare.add_argument("--json", action="store_true")

    variants = commands.add_parser(
        "compare-variants",
        help="Compare run or series references without declaring a winner.",
    )
    variants.add_argument("left")
    variants.add_argument("right")
    variants.add_argument("--format", choices=["text", "json", "markdown"], default="text")
    variants.add_argument("--output")

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
    export.add_argument("--series", action="store_true")
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
        if args.command == "show-series":
            series = storage.load_series(args.series_id)
            print(
                series.model_dump_json(indent=2)
                if args.json
                else render_series(series)
            )
            return 0 if series.passed else 1
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
        if args.command == "compare-variants":
            comparison = compare_variants(
                args.left,
                storage.runs_for_reference(args.left),
                args.right,
                storage.runs_for_reference(args.right),
            )
            rendered = render_variant_comparison(comparison, args.format)
            if args.output:
                output = Path(args.output).expanduser().resolve()
                output.parent.mkdir(parents=True, exist_ok=True)
                output.write_text(rendered + ("" if rendered.endswith("\n") else "\n"), encoding="utf-8")
            print(rendered)
            return 0
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
            output = (
                storage.export_series(args.run_id, args.output)
                if args.series
                else storage.export_run(args.run_id, args.output)
            )
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
    if args.repeat < 1:
        raise ValueError("--repeat must be at least 1")
    repository_manager = None
    if "real-repository" in suite.tags:
        if not args.allow_repository_download:
            raise ValueError(
                "Real-repository suites require explicit --allow-repository-download."
            )
        if not args.live:
            raise ValueError(
                "Real-repository suites require explicit --live consent before download."
            )
        if not variant.live:
            raise ValueError(
                "Real-repository suites require an explicitly live evaluation variant."
            )
        from agentbus.evaluation.benchmarks import (
            RealRepositoryManager,
            load_manifest,
            suite_from_manifest,
        )

        manifest = load_manifest(args.manifest)
        selected_ids = set(args.cases or [])
        selected_tags = set(args.tags or [])
        selected_benchmarks = [
            item
            for item in manifest.repositories
            if (not selected_ids or item.benchmark_id in selected_ids)
            and (
                not selected_tags
                or selected_tags <= (item.tags | {"real-repository"})
            )
        ]
        if not selected_benchmarks:
            raise ValueError("No real-repository benchmarks matched the filters.")
        platform_name = (
            "windows"
            if sys.platform.startswith("win")
            else "macos"
            if sys.platform == "darwin"
            else "linux"
        )
        unsupported = [
            item.benchmark_id
            for item in selected_benchmarks
            if platform_name not in item.supported_platforms
        ]
        if unsupported:
            raise ValueError(
                f"Real-repository benchmark(s) do not support {platform_name}: "
                + ", ".join(unsupported)
            )
        manifest = manifest.model_copy(update={"repositories": selected_benchmarks})
        repository_manager = RealRepositoryManager()
        try:
            sources = {
                item.benchmark_id: repository_manager.clone(item)
                for item in manifest.repositories
            }
        except Exception:
            repository_manager.cleanup()
            raise
        suite = suite_from_manifest(manifest, sources)
        suites = {**suites, suite.suite_id: suite}
    try:
        return _execute_run(args, config, storage, suite, suites, variants, variant)
    finally:
        if repository_manager is not None:
            repository_manager.cleanup()


def _execute_run(args, config, storage, suite, suites, variants, variant) -> int:
    suite_id = suite.suite_id
    variant_id = variant.variant_id
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
        max_requests, max_tokens, timeout_seconds = _suite_budget_caps(
            suite,
            max_requests,
            max_tokens,
            timeout_seconds,
        )
        if not args.suite:
            raise ValueError("--live requires explicit --suite selection")
        print("WARNING: live provider evaluation explicitly enabled.")
        print(f"Provider: {variant.provider}")
        print(f"Role models/deployments: {variant.models_by_role or '[environment configuration]'}")
        print(
            "Estimated maximum calls: "
            f"{_estimated_calls(suite, set(args.cases or [])) * args.repeat}"
        )
        print(f"Hard request budget: {max_requests}")
        print(f"Hard token budget: {max_tokens}")
    runner = EvaluationRunner(
        results_dir=storage.root,
        owned_fixture_root=config.fixture_root,
        storage=storage,
        suites=suites,
        variants=variants,
    )
    run_arguments = {
        "variant_id": variant_id,
        "case_ids": set(args.cases or []) or None,
        "tags": set(args.tags or []) or None,
        "fail_fast": args.fail_fast,
        "preserve_fixtures": args.preserve_fixtures or config.preserve_fixtures,
        "live": args.live,
        "max_requests": max_requests,
        "max_tokens": max_tokens,
        "timeout_seconds": timeout_seconds,
    }
    if args.repeat > 1:
        series = runner.run_repeated(
            suite_id,
            repeat=args.repeat,
            **run_arguments,
        )
        print(series.model_dump_json(indent=2) if args.json else render_series(series))
        return 0 if series.passed else 1
    run = runner.run(suite_id, **run_arguments)
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
        intelligence = case.raw_metrics.get("repository_intelligence")
        if isinstance(intelligence, dict):
            measured = [
                f"files={intelligence.get('indexed_files', 0)}",
                f"symbols={intelligence.get('indexed_symbols', 0)}",
                f"build={float(intelligence.get('build_latency_seconds', 0)):.3f}s",
                f"storage={int(intelligence.get('storage_bytes', 0))}B",
                "provider/network=0/0",
            ]
            for label, key in (
                ("index", "indexing_correctness"),
                ("symbols", "symbol_precision"),
                ("references", "reference_precision"),
                ("retrieval", "retrieval_precision"),
                ("impact", "impact_recall"),
                ("tests", "test_impact_recall"),
                ("context", "context_budget_adherence"),
            ):
                value = intelligence.get(key)
                if isinstance(value, (int, float)):
                    measured.append(f"{label}={float(value):.0%}")
            lines.append(f"Intelligence {case.case_id}: " + ", ".join(measured))
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
    if run.suite_id == "repository-intelligence":
        lines.append("Note: " + _repository_intelligence_interpretation(run))
    return "\n".join(lines)


def _repository_intelligence_interpretation(run: EvaluationRun) -> str:
    for case in run.case_results:
        metrics = case.raw_metrics.get("repository_intelligence")
        if isinstance(metrics, dict) and isinstance(
            metrics.get("interpretation_note"),
            str,
        ):
            return metrics["interpretation_note"]
    return (
        "Deterministic synthetic-fixture measurements only; these small "
        "samples do not establish statistical significance."
    )


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


def render_series(series: EvaluationSeries) -> str:
    stats = series.aggregate
    lines = [
        f"Evaluation series: {series.series_id}",
        f"Suite: {series.suite_id}",
        f"Variant: {series.variant.variant_id}",
        f"Runs: {stats.samples}",
        f"Success rate: {stats.success_rate:.1%}",
        f"Score mean/median/min: {stats.score.mean:.2f}/{stats.score.median:.2f}/{stats.score.minimum:.2f}",
        f"Score sample stdev: {stats.score.sample_standard_deviation:.2f}",
        f"Duration mean/median: {stats.duration_seconds.mean:.3f}s/{stats.duration_seconds.median:.3f}s",
        f"Tokens mean/median: {stats.tokens.mean:.1f}/{stats.tokens.median:.1f}",
        f"Retry distribution: {stats.retry_distribution}",
        f"Fallback rate: {stats.fallback_rate:.1%}",
        f"Reviewer approval rate: {_format_rate(stats.reviewer_approval_rate)}",
        f"Verifier pass rate: {_format_rate(stats.verifier_pass_rate)}",
        f"File-scope violation rate: {stats.file_scope_violation_rate:.1%}",
        f"Conflict rate: {stats.conflict_rate:.1%}",
        f"Result: {'PASS' if series.passed else 'FAIL'}",
        f"Note: {stats.interpretation_note}",
    ]
    return "\n".join(lines)


def render_variant_comparison(
    report: VariantComparisonReport,
    output_format: str,
) -> str:
    if output_format == "json":
        return report.model_dump_json(indent=2)
    if output_format == "markdown":
        lines = [
            "# AgentBus variant comparison",
            "",
            f"| Metric | {report.left.variant_id} | {report.right.variant_id} | Difference (right-left) |",
            "| --- | ---: | ---: | ---: |",
        ]
        labels = {
            "success_rate": "Success rate",
            "score_mean": "Mean score",
            "duration_mean_seconds": "Mean duration (seconds)",
            "tokens_mean": "Mean tokens",
            "retries_mean": "Mean retries",
            "fallback_rate": "Fallback rate",
            "safety_failure_rate": "Safety failure rate",
            "scope_violation_rate": "Scope violation rate",
        }
        for name, label in labels.items():
            lines.append(
                f"| {label} | {getattr(report.left, name):.4f} | "
                f"{getattr(report.right, name):.4f} | {report.differences[name]:+.4f} |"
            )
        lines.extend(["", f"> {report.interpretation_note}"])
        return "\n".join(lines)
    lines = [
        f"Left: {report.left.reference} ({report.left.variant_id}, n={report.left.samples})",
        f"Right: {report.right.reference} ({report.right.variant_id}, n={report.right.samples})",
    ]
    lines.extend(f"{name}: {difference:+.4f}" for name, difference in report.differences.items())
    lines.append("Note: " + report.interpretation_note)
    return "\n".join(lines)


def _format_rate(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.1%}"


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


def _suite_budget_caps(suite, requests: int, tokens: int, timeout: float):
    limits = [
        case.metadata.get("limits", {})
        for case in suite.cases
        if isinstance(case.metadata.get("limits", {}), dict)
    ]
    request_caps = [int(item["max_requests"]) for item in limits if item.get("max_requests")]
    token_caps = [int(item["max_tokens"]) for item in limits if item.get("max_tokens")]
    timeout_caps = [
        float(item["max_elapsed_seconds"])
        for item in limits
        if item.get("max_elapsed_seconds")
    ]
    return (
        min([requests, *request_caps]),
        min([tokens, *token_caps]),
        min([timeout, *timeout_caps]),
    )


if __name__ == "__main__":
    raise SystemExit(main())
