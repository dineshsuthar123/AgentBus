from __future__ import annotations

import argparse
import json
import sqlite3
import subprocess
from dataclasses import asdict, dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any
from zipfile import ZipFile

from agentbus import __version__
from agentbus.configuration import resolve_configuration
from agentbus.doctor import run_doctor
from agentbus.evaluation.storage import EvaluationStorage
from agentbus.execution.schema import SCHEMA_VERSION
from agentbus.security.redaction import sanitize_json


class ReleaseStatus(str, Enum):
    PASS = "PASS"
    WARN = "WARN"
    FAIL = "FAIL"
    NOT_RUN = "NOT_RUN"


@dataclass(frozen=True)
class ReleaseCheck:
    name: str
    status: ReleaseStatus
    summary: str
    evidence: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ReleaseReport:
    version: str
    commit: str | None
    branch: str | None
    checks: list[ReleaseCheck]
    supported_providers: list[str]
    known_limitations: list[str]

    @property
    def ready(self) -> bool:
        required = {"tests", "offline-evaluation", "package-build", "installation", "doctor"}
        return all(
            check.status == ReleaseStatus.PASS
            for check in self.checks
            if check.name in required
        ) and required <= {check.name for check in self.checks}

    def to_dict(self) -> dict[str, Any]:
        return sanitize_json(
            {
                "version": self.version,
                "commit": self.commit,
                "branch": self.branch,
                "ready": self.ready,
                "checks": [
                    {**asdict(check), "status": check.status.value}
                    for check in self.checks
                ],
                "supported_providers": self.supported_providers,
                "known_limitations": self.known_limitations,
            }
        )


def build_release_report(
    *,
    repository: str | Path = ".",
    workspace: str | Path | None = None,
    config_file: str | Path | None = None,
    results_dir: str | Path = ".agentbus/evaluations",
    offline_run_id: str | None = None,
    live_run_id: str | None = None,
    test_evidence: str | Path | None = None,
    install_evidence: str | Path | None = None,
    dist_dir: str | Path = "dist",
) -> ReleaseReport:
    root = Path(repository).expanduser().resolve()
    commit = _git(root, "rev-parse", "HEAD")
    branch = _git(root, "branch", "--show-current")
    status = _git(root, "status", "--short", "--untracked-files=all")
    checks: list[ReleaseCheck] = [
        ReleaseCheck(
            "version",
            ReleaseStatus.PASS,
            f"Authoritative version is {__version__}.",
        ),
        ReleaseCheck(
            "worktree",
            ReleaseStatus.WARN if status else ReleaseStatus.PASS,
            "Git worktree has uncommitted files." if status else "Git worktree is clean.",
            {"dirty": bool(status)},
        ),
        _sensitive_file_check(root, status),
        _evidence_check("tests", test_evidence),
        _build_check(Path(dist_dir).expanduser().resolve()),
        _evidence_check("installation", install_evidence),
        _evaluation_check(results_dir, offline_run_id, "offline-evaluation"),
        _evaluation_check(results_dir, live_run_id, "live-evaluation", optional=True),
    ]
    resolved = resolve_configuration(
        config_file=config_file,
        cli_overrides={"workspace_dir": str(workspace or root)},
    )
    doctor = run_doctor(resolved.config)
    checks.append(
        ReleaseCheck(
            "doctor",
            ReleaseStatus.FAIL if doctor.status.value == "FAIL" else ReleaseStatus.PASS,
            f"Offline doctor completed with {doctor.status.value}; network was not used.",
            {"status": doctor.status.value, "network_used": doctor.network_used},
        )
    )
    checks.append(_schema_check(resolved.config.state_database_path.expanduser().resolve()))
    checks.append(_ci_check(root))
    return ReleaseReport(
        version=__version__,
        commit=commit,
        branch=branch,
        checks=checks,
        supported_providers=["ollama", "azure"],
        known_limitations=[
            "Local foreground execution only; no distributed exactly-once guarantee.",
            "Filesystem and external side effects are not transactionally rolled back.",
            "Worktrees reduce repository interference but are not complete sandbox isolation.",
            "Live-provider quality, quota, latency, and cost depend on the selected deployment.",
        ],
    )


def render_markdown(report: ReleaseReport) -> str:
    lines = [
        f"# AgentBus {report.version} release report",
        "",
        f"- Commit: `{report.commit or '[unavailable]'}`",
        f"- Branch: `{report.branch or '[detached]'}`",
        f"- Required evidence complete: **{'yes' if report.ready else 'no'}**",
        "",
        "## Checks",
        "",
        "| Check | Status | Summary |",
        "| --- | --- | --- |",
    ]
    for check in report.checks:
        summary = check.summary.replace("|", "\\|").replace("\n", " ")
        lines.append(f"| {check.name} | {check.status.value} | {summary} |")
    lines.extend(["", "## Supported providers", ""])
    lines.extend(f"- {provider}" for provider in report.supported_providers)
    lines.extend(["", "## Known limitations", ""])
    lines.extend(f"- {item}" for item in report.known_limitations)
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="agentbus release-report")
    parser.add_argument("--repository", default=".")
    parser.add_argument("--workspace")
    parser.add_argument("--config")
    parser.add_argument("--results-dir", default=".agentbus/evaluations")
    parser.add_argument("--offline-run")
    parser.add_argument("--live-run")
    parser.add_argument("--test-evidence")
    parser.add_argument("--install-evidence")
    parser.add_argument("--dist-dir", default="dist")
    parser.add_argument("--markdown-output")
    parser.add_argument("--json-output")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args(argv)
    try:
        report = build_release_report(
            repository=args.repository,
            workspace=args.workspace,
            config_file=args.config,
            results_dir=args.results_dir,
            offline_run_id=args.offline_run,
            live_run_id=args.live_run,
            test_evidence=args.test_evidence,
            install_evidence=args.install_evidence,
            dist_dir=args.dist_dir,
        )
    except (OSError, ValueError, sqlite3.Error) as exc:
        print(f"Release report error: {exc}")
        return 2
    markdown = render_markdown(report)
    payload = json.dumps(report.to_dict(), indent=2, sort_keys=True) + "\n"
    if args.markdown_output:
        _write(Path(args.markdown_output), markdown)
    if args.json_output:
        _write(Path(args.json_output), payload)
    print(payload if args.json else markdown, end="")
    return 1 if args.check and not report.ready else 0


def _evidence_check(name: str, path: str | Path | None) -> ReleaseCheck:
    if path is None:
        return ReleaseCheck(name, ReleaseStatus.NOT_RUN, "No evidence file was supplied.")
    evidence_path = Path(path).expanduser().resolve()
    try:
        value = json.loads(evidence_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return ReleaseCheck(
            name,
            ReleaseStatus.FAIL,
            f"Evidence file is unreadable or invalid JSON: {type(exc).__name__}",
        )
    raw_status = str(value.get("status", "")).upper()
    status = ReleaseStatus.PASS if raw_status == "PASS" else ReleaseStatus.FAIL
    summary = str(value.get("summary", "Evidence did not include a summary."))
    return ReleaseCheck(name, status, summary, {"source": str(evidence_path)})


def _build_check(dist_dir: Path) -> ReleaseCheck:
    wheels = sorted(dist_dir.glob("agentbus-*.whl")) if dist_dir.is_dir() else []
    sdists = sorted(dist_dir.glob("agentbus-*.tar.gz")) if dist_dir.is_dir() else []
    if not wheels and not sdists:
        return ReleaseCheck("package-build", ReleaseStatus.NOT_RUN, "No wheel or sdist was found.")
    if not wheels or not sdists:
        return ReleaseCheck(
            "package-build",
            ReleaseStatus.FAIL,
            "Both a wheel and source distribution are required.",
        )
    try:
        with ZipFile(wheels[-1]) as archive:
            metadata_name = next(name for name in archive.namelist() if name.endswith(".dist-info/METADATA"))
            metadata = archive.read(metadata_name).decode("utf-8", errors="replace")
    except (OSError, KeyError, StopIteration) as exc:
        return ReleaseCheck(
            "package-build",
            ReleaseStatus.FAIL,
            f"Wheel metadata is invalid: {type(exc).__name__}",
        )
    expected = f"Version: {__version__}"
    status = ReleaseStatus.PASS if expected in metadata else ReleaseStatus.FAIL
    return ReleaseCheck(
        "package-build",
        status,
        "Wheel and sdist exist with matching version metadata."
        if status == ReleaseStatus.PASS
        else "Built wheel version does not match the runtime version.",
        {"wheel": wheels[-1].name, "sdist": sdists[-1].name},
    )


def _evaluation_check(
    results_dir: str | Path,
    run_id: str | None,
    name: str,
    *,
    optional: bool = False,
) -> ReleaseCheck:
    if not run_id:
        summary = "Optional live acceptance was not run." if optional else "No evaluation run ID was supplied."
        return ReleaseCheck(name, ReleaseStatus.NOT_RUN, summary)
    storage = EvaluationStorage(results_dir)
    try:
        run = storage.load_run(run_id)
    except Exception:
        try:
            series = storage.load_series(run_id)
        except Exception as exc:
            return ReleaseCheck(name, ReleaseStatus.FAIL, f"Evaluation evidence could not be loaded: {type(exc).__name__}")
        return ReleaseCheck(
            name,
            ReleaseStatus.PASS if series.passed else ReleaseStatus.FAIL,
            f"{series.suite_id}: {series.aggregate.success_rate:.1%} success across {series.repeat} run(s).",
            {
                "series_id": series.series_id,
                "run_ids": series.run_ids,
                "variant": series.variant.variant_id,
                "agentbus_version": series.agentbus_version,
            },
        )
    return ReleaseCheck(
        name,
        ReleaseStatus.PASS if run.passed else ReleaseStatus.FAIL,
        f"{run.suite_id}: {sum(case.passed for case in run.case_results)}/{len(run.case_results)} passed at {run.aggregate_score:.2f}/100.",
        {
            "run_id": run.evaluation_run_id,
            "suite": run.suite_id,
            "variant": run.variant.variant_id,
            "agentbus_version": run.agentbus_version,
        },
    )


def _schema_check(path: Path) -> ReleaseCheck:
    if not path.is_file():
        return ReleaseCheck("schema", ReleaseStatus.NOT_RUN, "State database does not exist.")
    try:
        with sqlite3.connect(path.as_uri() + "?mode=ro", uri=True) as connection:
            row = connection.execute("SELECT value FROM schema_metadata WHERE key='schema_version'").fetchone()
        actual = int(row[0]) if row else None
    except (sqlite3.Error, OSError, ValueError):
        return ReleaseCheck("schema", ReleaseStatus.FAIL, "State schema could not be read.")
    return ReleaseCheck(
        "schema",
        ReleaseStatus.PASS if actual == SCHEMA_VERSION else ReleaseStatus.FAIL,
        f"State schema is {actual}; runtime expects {SCHEMA_VERSION}.",
    )


def _ci_check(root: Path) -> ReleaseCheck:
    expected = [root / ".github" / "workflows" / "ci.yml", root / ".github" / "workflows" / "azure-smoke.yml"]
    missing = [path.name for path in expected if not path.is_file()]
    return ReleaseCheck(
        "ci-workflows",
        ReleaseStatus.FAIL if missing else ReleaseStatus.PASS,
        "Missing workflow(s): " + ", ".join(missing) if missing else "PR and opt-in Azure workflows are present.",
    )


def _sensitive_file_check(root: Path, status: str | None) -> ReleaseCheck:
    sensitive: set[str] = set()
    for line in (status or "").splitlines():
        path = line[3:].strip().replace("\\", "/") if len(line) > 3 else ""
        name = Path(path).name.lower()
        if name in {".env", ".env.local", "credentials.json", "secrets.json"} or path.endswith((".db", ".sqlite", ".sqlite3", ".jsonl")):
            sensitive.add(path)
    tracked_output = _git(root, "ls-files", "-z") or ""
    ignored_output = _git(
        root,
        "ls-files",
        "--others",
        "--ignored",
        "--exclude-standard",
        "-z",
    ) or ""
    tracked = {
        path.replace("\\", "/")
        for path in tracked_output.split("\0")
        if _sensitive_name(path)
    }
    sensitive.update(
        path.replace("\\", "/")
        for path in ignored_output.split("\0")
        if _sensitive_name(path)
    )
    status_value = (
        ReleaseStatus.FAIL
        if tracked
        else ReleaseStatus.WARN
        if sensitive
        else ReleaseStatus.PASS
    )
    return ReleaseCheck(
        "sensitive-files",
        status_value,
        f"Found {len(sensitive)} uncommitted sensitive/runtime-looking path(s); contents were not read."
        if sensitive
        else "No uncommitted sensitive/runtime-looking paths were detected.",
        {"paths": sorted(sensitive), "tracked_sensitive_paths": sorted(tracked)},
    )


def _sensitive_name(value: str) -> bool:
    path = value.strip().replace("\\", "/")
    name = Path(path).name.lower()
    return name in {".env", ".env.local", "credentials.json", "secrets.json"} or path.endswith(
        (".db", ".sqlite", ".sqlite3", ".jsonl")
    )


def _git(root: Path, *arguments: str) -> str | None:
    result = subprocess.run(
        ["git", *arguments],
        cwd=root,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        shell=False,
        check=False,
        timeout=15,
    )
    return result.stdout.strip() if result.returncode == 0 else None


def _write(path: Path, content: str) -> None:
    target = path.expanduser().resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8", newline="\n")


if __name__ == "__main__":
    raise SystemExit(main())
