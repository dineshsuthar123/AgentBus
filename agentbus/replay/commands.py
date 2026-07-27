from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from agentbus.configuration import resolve_configuration
from agentbus.execution.state_store import (
    ProvenanceRecordNotFoundError,
    StateStoreError,
)
from agentbus.replay.errors import ReplayError
from agentbus.replay.forks import ForkRequest
from agentbus.replay.service import TraceReplayService
from agentbus.replay.session import (
    ReplayRequest,
    ReplaySessionStatus,
)
from agentbus.trace.errors import TraceError
from agentbus.trace.models import ReplayMode, Trace, TraceSpanType, TraceStatus
from agentbus.trace.retention import TraceRetentionPolicy


def trace_command(arguments: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="agentbus trace")
    commands = parser.add_subparsers(dest="trace_command", required=True)

    listing = commands.add_parser("list", help="List persisted traces.")
    _common(listing)
    listing.add_argument("--status", choices=[item.value for item in TraceStatus])
    listing.add_argument("--limit", type=int, default=100)

    inspect = commands.add_parser("inspect", help="Inspect safe trace metadata.")
    inspect.add_argument("identifier", help="Run ID or trace ID.")
    _common(inspect)

    verify = commands.add_parser("verify", help="Verify provenance and objects.")
    verify.add_argument("identifier", help="Run ID or trace ID.")
    _common(verify)

    export = commands.add_parser("export", help="Export a portable trace archive.")
    export.add_argument("identifier", help="Run ID or trace ID.")
    export.add_argument("--output", required=True)
    export.add_argument("--include-source-content", action="store_true")
    _common(export)

    capture = commands.add_parser(
        "capture",
        help="Capture a successful run as a regression fixture.",
    )
    capture.add_argument("identifier", help="Run ID or trace ID.")
    capture.add_argument("--output", required=True)
    capture.add_argument("--include-source-content", action="store_true")
    _common(capture)

    importing = commands.add_parser(
        "import",
        help="Validate and import archive objects without execution.",
    )
    importing.add_argument("archive")
    importing.add_argument("--allow-source-content", action="store_true")
    _common(importing)

    gc = commands.add_parser("gc", help="Plan or explicitly execute trace GC.")
    gc.add_argument("--execute", action="store_true")
    gc.add_argument("--resume", action="store_true")
    gc.add_argument("--keep-all", action="store_true")
    gc.add_argument("--no-keep-failures", action="store_true")
    gc.add_argument("--keep-recent", type=int, default=100)
    gc.add_argument("--no-keep-referenced", action="store_true")
    gc.add_argument("--max-age-seconds", type=int)
    gc.add_argument("--max-total-bytes", type=int)
    _common(gc)

    args = parser.parse_args(arguments)
    try:
        service = _service(args.config)
        if args.trace_command == "list":
            return _trace_list(service, args)
        if args.trace_command == "inspect":
            return _trace_inspect(service, args)
        if args.trace_command == "verify":
            report = service.verify(args.identifier)
            _render_model(report, as_json=args.json)
            return 0
        if args.trace_command == "export":
            manifest = service.export_trace(
                args.identifier,
                args.output,
                include_source_content=args.include_source_content,
            )
            _render_archive_write(
                manifest,
                args.output,
                as_json=args.json,
                kind="trace archive",
            )
            return 0
        if args.trace_command == "capture":
            captured = service.capture_fixture(
                args.identifier,
                args.output,
                include_source_content=args.include_source_content,
            )
            _render_archive_write(
                captured.archive,
                args.output,
                as_json=args.json,
                kind="regression fixture",
                extra={
                    "replay_command": captured.spec.replay_command,
                    "license_warning": captured.spec.license_warning,
                },
            )
            return 0
        if args.trace_command == "import":
            imported = service.import_archive(
                args.archive,
                allow_source_content=args.allow_source_content,
            )
            payload = {
                "trace_id": imported.trace.trace_id,
                "run_id": imported.trace.run_id,
                "objects_imported": imported.objects_imported,
                "available_object_hashes": imported.available_object_hashes,
                "missing_object_hashes": imported.missing_object_hashes,
                "source_content_included": (
                    imported.manifest.source_content_included
                ),
                "execution_started": False,
            }
            _render(payload, as_json=args.json)
            return 0
        return _trace_gc(service, args)
    except _SAFE_COMMAND_ERRORS as exc:
        print(f"Trace command failed: {exc}", file=sys.stderr)
        return 2


def replay_command(arguments: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="agentbus replay")
    parser.add_argument("target", help="Run ID, trace ID, or .agentbus-trace archive.")
    parser.add_argument(
        "--mode",
        choices=[item.value for item in ReplayMode],
        default=ReplayMode.OFFLINE.value,
    )
    parser.add_argument(
        "--from",
        dest="from_item",
        help="Span, task, checkpoint, beginning, pre-verifier, or pre-integration.",
    )
    parser.add_argument("--fork", action="store_true")
    parser.add_argument(
        "--change",
        action="append",
        default=[],
        metavar="NAME=JSON",
    )
    parser.add_argument("--live-provider-consent", action="store_true")
    parser.add_argument("--allow-source-content", action="store_true")
    _common(parser)
    args = parser.parse_args(arguments)
    try:
        service = _service(args.config)
        mode = ReplayMode(args.mode)
        target_path = Path(args.target).expanduser()
        if target_path.is_file() or target_path.suffix == ".agentbus-trace":
            if args.fork or args.from_item or args.change:
                raise ValueError(
                    "Archive replay does not support fork or partial replay."
                )
            result = service.replay_archive(
                target_path,
                mode=mode,
                allow_source_content=args.allow_source_content,
            )
            payload = {
                "trace_id": result.imported.trace.trace_id,
                "run_id": result.imported.trace.run_id,
                "replay": result.replay.model_dump(mode="json"),
                "fixture_assertions": (
                    result.fixture_assertions.model_dump(mode="json")
                    if result.fixture_assertions is not None
                    else None
                ),
            }
            _render(payload, as_json=args.json)
            succeeded = (
                result.replay.session.status
                == ReplaySessionStatus.SUCCEEDED
                and (
                    result.fixture_assertions is None
                    or result.fixture_assertions.passed
                )
            )
            return 0 if succeeded else 3

        trace = service.resolve_trace(args.target)
        if args.fork:
            changes = _changed_inputs(args.change)
            result = service.fork(
                args.target,
                ForkRequest(
                    source_trace_id=trace.trace_id,
                    source_run_id=trace.run_id,
                    mode=mode,
                    changed_inputs=changes,
                    live_provider_consent=args.live_provider_consent,
                ),
            )
            payload = result.model_dump(mode="json")
            _render(payload, as_json=args.json)
            return 0
        if args.change or args.live_provider_consent:
            raise ValueError("--change and live consent require --fork.")
        from_span_id, from_checkpoint_id = _replay_start(
            trace,
            args.from_item,
        )
        request = ReplayRequest(
            source_trace_id=trace.trace_id,
            source_run_id=trace.run_id,
            mode=mode,
            from_span_id=from_span_id,
            from_checkpoint_id=from_checkpoint_id,
        )
        result = service.replay(args.target, request)
        _render_model(result, as_json=args.json)
        return (
            0
            if result.session.status == ReplaySessionStatus.SUCCEEDED
            else 3
        )
    except _SAFE_COMMAND_ERRORS as exc:
        print(f"Replay failed: {exc}", file=sys.stderr)
        return 2


def compare_command(arguments: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="agentbus compare")
    parser.add_argument("left", help="Left run ID or trace ID.")
    parser.add_argument("right", help="Right run ID or trace ID.")
    _common(parser)
    args = parser.parse_args(arguments)
    try:
        comparison = _service(args.config).compare(args.left, args.right)
        _render_model(comparison, as_json=args.json)
        return 0
    except _SAFE_COMMAND_ERRORS as exc:
        print(f"Comparison failed: {exc}", file=sys.stderr)
        return 2


def _trace_list(service: TraceReplayService, args) -> int:
    status = TraceStatus(args.status) if args.status else None
    traces = service.list_traces(status=status, limit=args.limit)
    payload = {
        "traces": [
            {
                "trace_id": trace.trace_id,
                "run_id": trace.run_id,
                "status": trace.status.value,
                "created_at": trace.created_at.isoformat(),
                "completed_at": (
                    trace.completed_at.isoformat()
                    if trace.completed_at is not None
                    else None
                ),
                "span_count": len(trace.spans),
                "checkpoint_count": len(trace.checkpoints),
            }
            for trace in traces
        ],
        "count": len(traces),
    }
    _render(payload, as_json=args.json)
    return 0


def _trace_inspect(service: TraceReplayService, args) -> int:
    trace = service.resolve_trace(args.identifier)
    replayability = service.replayability(trace.trace_id)
    try:
        provenance = service.provenance(trace.trace_id)
    except ProvenanceRecordNotFoundError:
        provenance = None
    payload = {
        "trace": {
            "trace_id": trace.trace_id,
            "run_id": trace.run_id,
            "status": trace.status.value,
            "created_at": trace.created_at.isoformat(),
            "completed_at": (
                trace.completed_at.isoformat()
                if trace.completed_at is not None
                else None
            ),
            "root_span_id": trace.root_span_id,
            "span_count": len(trace.spans),
            "event_count": len(trace.events),
            "checkpoint_count": len(trace.checkpoints),
        },
        "replayability": replayability.model_dump(mode="json"),
        "provenance": (
            {
                "integrity_root": provenance.integrity_root,
                "replayability": provenance.replayability.value,
                "protocol_hashes": provenance.protocol_hashes,
            }
            if provenance is not None
            else None
        ),
    }
    _render(payload, as_json=args.json)
    return 0


def _trace_gc(service: TraceReplayService, args) -> int:
    if args.resume:
        if args.execute:
            raise ValueError("--resume and --execute are mutually exclusive.")
        report = service.resume_gc()
        _render_model(report, as_json=args.json)
        return 0
    policy = TraceRetentionPolicy(
        keep_all=args.keep_all,
        keep_failures=not args.no_keep_failures,
        keep_recent=args.keep_recent,
        keep_referenced=not args.no_keep_referenced,
        max_age_seconds=args.max_age_seconds,
        max_total_bytes=args.max_total_bytes,
    )
    plan = service.plan_gc(policy)
    if args.execute:
        report = service.execute_gc(plan)
        _render_model(report, as_json=args.json)
    else:
        payload = {
            **plan.model_dump(mode="json"),
            "dry_run": True,
            "execution_required": "--execute",
        }
        _render(payload, as_json=args.json)
    return 0


def _replay_start(
    trace: Trace,
    value: str | None,
) -> tuple[str | None, str | None]:
    if value is None or value == "beginning":
        return None, None
    checkpoint = next(
        (
            item
            for item in trace.checkpoints
            if item.checkpoint_id == value or item.label == value
        ),
        None,
    )
    if checkpoint is not None:
        return None, checkpoint.checkpoint_id
    span = next(
        (
            item
            for item in trace.spans
            if item.span_id == value or item.task_id == value
        ),
        None,
    )
    if span is not None:
        return span.span_id, None
    aliases = {
        "pre-verifier": TraceSpanType.VERIFIER,
        "pre-integration": TraceSpanType.INTEGRATION,
    }
    span_type = aliases.get(value)
    if span_type is not None:
        selected = next(
            (item for item in trace.spans if item.span_type == span_type),
            None,
        )
        if selected is not None:
            return selected.span_id, None
    raise ValueError(
        "Replay start does not identify a captured span or checkpoint."
    )


def _changed_inputs(values: list[str]) -> dict[str, Any]:
    if not values:
        raise ValueError("--fork requires at least one --change NAME=JSON.")
    changed: dict[str, Any] = {}
    for item in values:
        name, separator, payload = item.partition("=")
        if not separator or not name:
            raise ValueError("Fork changes must use NAME=JSON.")
        if name in changed:
            raise ValueError(f"Fork input '{name}' was provided more than once.")
        try:
            changed[name] = json.loads(payload)
        except json.JSONDecodeError as exc:
            raise ValueError(
                f"Fork input '{name}' must contain valid JSON."
            ) from exc
    return changed


def _service(config_file: str | None) -> TraceReplayService:
    config = resolve_configuration(config_file=config_file).config
    return TraceReplayService(config)


def _common(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--config")
    parser.add_argument("--json", action="store_true")


def _render_archive_write(
    manifest,
    destination: str,
    *,
    as_json: bool,
    kind: str,
    extra: dict[str, Any] | None = None,
) -> None:
    payload = {
        "kind": kind,
        "destination": str(Path(destination).expanduser().resolve()),
        "trace_id": manifest.trace_id,
        "run_id": manifest.run_id,
        "archive_root": manifest.archive_root,
        "source_content_included": manifest.source_content_included,
        "source_content_warning": manifest.source_content_warning,
        **(extra or {}),
    }
    _render(payload, as_json=as_json)


def _render_model(value, *, as_json: bool) -> None:
    _render(value.model_dump(mode="json"), as_json=as_json)


def _render(value: dict[str, Any], *, as_json: bool) -> None:
    if as_json:
        print(json.dumps(value, indent=2, sort_keys=True))
    else:
        print(json.dumps(value, indent=2, sort_keys=True))


_SAFE_COMMAND_ERRORS = (
    OSError,
    ReplayError,
    StateStoreError,
    TraceError,
    ValueError,
)


__all__ = ["compare_command", "replay_command", "trace_command"]
