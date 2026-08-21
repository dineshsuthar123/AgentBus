from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import UTC, datetime
from pathlib import Path

from agentbus.security.redaction import redact_text
from agentbus.validation.corpus import run_validation_corpus
from agentbus.validation.failures import (
    ManifestValidationError,
    RepositoryValidationError,
)
from agentbus.validation.models import (
    ReliabilityScorecard,
    RepositoryScale,
    RepositorySource,
    ValidationReport,
    ValidationRepository,
)
from agentbus.validation.reliability import run_reliability_validation
from agentbus.validation.reports import (
    render_reliability_scorecard,
    render_validation_report,
    write_validation_report,
)
from agentbus.validation.runner import ValidationRunner


_IDENTIFIER_CHARACTER = re.compile(r"[^a-z0-9._-]+")


def validation_command(arguments: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="agentbus validate")
    commands = parser.add_subparsers(dest="validation_command", required=True)

    repository = commands.add_parser(
        "repo",
        help="Validate one explicit local repository without providers.",
    )
    repository.add_argument("--path", required=True)
    repository.add_argument("--repository-id")
    repository.add_argument("--output")
    repository.add_argument("--json", action="store_true")

    corpus = commands.add_parser(
        "corpus",
        help="Run a bounded validation corpus; offline is the default.",
    )
    corpus.add_argument("--manifest")
    corpus.add_argument("--include-optional", action="store_true")
    corpus.add_argument("--cache-directory")
    mode = corpus.add_mutually_exclusive_group()
    mode.add_argument("--offline", action="store_true")
    mode.add_argument(
        "--download-public",
        action="store_true",
        help="Explicitly permit public Git downloads into --cache-directory.",
    )
    corpus.add_argument("--output")
    corpus.add_argument("--json", action="store_true")

    reliability = commands.add_parser(
        "reliability",
        help="Generate an explicit offline release reliability scorecard.",
    )
    reliability.add_argument(
        "--repository",
        "--repo",
        "--path",
        "--local-repository",
        dest="repository_paths",
        action="append",
        default=[],
        help="Also validate one explicit local repository; repeat as needed.",
    )
    reliability.add_argument("--duration", type=float, metavar="SECONDS")
    reliability.add_argument("--runs", type=int)
    reliability.add_argument("--parallelism", type=int)
    reliability.add_argument("--repository-files", type=int)
    reliability.add_argument("--seed", type=int, default=2026)
    reliability.add_argument("--output")
    reliability.add_argument("--json", action="store_true")
    args = parser.parse_args(arguments)

    try:
        if args.validation_command == "reliability":
            scorecard = run_reliability_validation(
                repository_paths=args.repository_paths,
                duration_seconds=args.duration,
                runs=args.runs,
                parallelism=args.parallelism,
                repository_files=args.repository_files,
                seed=args.seed,
            )
            report_path = (
                write_validation_report(scorecard, args.output)
                if args.output
                else None
            )
            _emit_reliability_scorecard(
                scorecard,
                report_path=report_path,
                json_output=args.json,
            )
            return 0 if scorecard.ok else 1
        if args.validation_command == "repo":
            report = _validate_repository(
                args.path,
                repository_id=args.repository_id,
            )
        else:
            report = run_validation_corpus(
                args.manifest,
                offline=not args.download_public,
                include_optional=args.include_optional,
                allow_download=args.download_public,
                cache_directory=args.cache_directory,
            )
        report_path = (
            write_validation_report(report, args.output) if args.output else None
        )
    except (ManifestValidationError, RepositoryValidationError) as exc:
        return _emit_error(exc, json_output=args.json)
    except (OSError, RuntimeError, ValueError) as exc:
        return _emit_error(
            RuntimeError(f"validation setup failed ({type(exc).__name__})"),
            json_output=args.json,
        )

    _emit_report(report, report_path=report_path, json_output=args.json)
    return 0 if report.ok else 1


def _validate_repository(
    path: str,
    *,
    repository_id: str | None,
) -> ValidationReport:
    identifier = repository_id or _repository_identifier(path)
    repository = ValidationRepository(
        repository_id=identifier,
        title=f"Local repository {identifier}",
        source=RepositorySource.LOCAL,
        path=path,
        scale=RepositoryScale.REAL_WORLD,
        known_characteristics=("explicit local checkout",),
    )
    run = ValidationRunner().run_repository(repository)
    return ValidationReport(
        status=run.status,
        generated_at=datetime.now(UTC),
        offline=True,
        network_used=False,
        runs=(run,),
    )


def _repository_identifier(path: str) -> str:
    candidate = Path(path).name.strip().lower()
    candidate = _IDENTIFIER_CHARACTER.sub("-", candidate).strip("._-")
    if not candidate or not candidate[0].isalnum():
        candidate = "local-repository"
    return candidate[:80].rstrip("._-") or "local-repository"


def _emit_report(
    report: ValidationReport,
    *,
    report_path: Path | None,
    json_output: bool,
) -> None:
    if json_output:
        payload = report.to_dict()
        payload["report_path"] = str(report_path) if report_path else None
        _print_safe(json.dumps(payload, indent=2, sort_keys=True))
        return
    rendered = render_validation_report(report)
    if report_path is not None:
        rendered += f"\nReport: {report_path}"
    _print_safe(rendered)


def _emit_reliability_scorecard(
    scorecard: ReliabilityScorecard,
    *,
    report_path: Path | None,
    json_output: bool,
) -> None:
    if json_output:
        payload = scorecard.to_dict()
        payload["report_path"] = str(report_path) if report_path else None
        _print_safe(json.dumps(payload, indent=2, sort_keys=True))
        return
    rendered = render_reliability_scorecard(scorecard)
    if report_path is not None:
        rendered += f"\nReport: {report_path}"
    _print_safe(rendered)


def _emit_error(exc: Exception, *, json_output: bool) -> int:
    message = redact_text(str(exc), max_chars=512) or "validation failed"
    if json_output:
        _print_safe(
            json.dumps(
                {
                    "error": message,
                    "error_type": type(exc).__name__,
                    "network_used": False,
                    "ok": False,
                },
                indent=2,
                sort_keys=True,
            )
        )
    else:
        _print_safe(f"Validation error: {message}")
    return 2


def _print_safe(value: str) -> None:
    encoding = getattr(sys.stdout, "encoding", None) or "utf-8"
    try:
        rendered = value.encode(encoding, errors="backslashreplace").decode(encoding)
    except (LookupError, UnicodeError):
        rendered = value.encode("ascii", errors="backslashreplace").decode("ascii")
    print(rendered)
