from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from agentbus.configuration import resolve_configuration
from agentbus.intelligence.errors import RepositoryIntelligenceError
from agentbus.intelligence.models import (
    ContextRole,
    SourceLanguage,
    SymbolKind,
)
from agentbus.intelligence.service import (
    ContextPlanSummary,
    GraphQueryReport,
    IndexClearReport,
    IndexGarbageCollectionReport,
    IndexMutationReport,
    IndexVerificationReport,
    RepositoryIntelligenceService,
    RepositorySearchReport,
    SymbolQueryReport,
)
from agentbus.security.redaction import redact_text


INTELLIGENCE_COMMANDS = (
    "index",
    "search",
    "symbols",
    "dependencies",
    "dependents",
    "impact",
    "tests-for",
    "context-plan",
)
_DEFAULT_INDEX_DATABASE = "repository-index.sqlite3"
_MAX_HUMAN_ITEMS = 50
_EVIDENCE_FIELDS = {
    "dependency_path",
    "diagnostics",
    "evidence",
    "exclusion_reason",
    "explanation",
    "indexed_paths",
    "invalidated_paths",
    "matched_terms",
    "reasons",
    "reused_paths",
    "score_components",
    "signature",
    "skipped_paths",
    "deleted_paths",
}
_ALWAYS_OMITTED_FIELDS = {"attributes", "content", "documentation"}


def intelligence_command(command: str, arguments: list[str]) -> int:
    if command not in INTELLIGENCE_COMMANDS:
        raise ValueError(f"Unsupported repository intelligence command: {command}")
    parser = _parser(command)
    args = parser.parse_args(arguments)
    service: RepositoryIntelligenceService | None = None
    try:
        service = _service(args)
        if command == "index":
            return _index(service, args)
        if command == "search":
            result = service.search(
                args.query,
                projects=args.project,
                languages=args.language,
                symbol_kinds=args.kind,
                path_prefixes=args.path_prefix,
                test_only=args.test_only,
                limit=args.limit,
                offset=args.offset,
            )
            return _render_search(result, args)
        if command == "symbols":
            result = service.symbols(
                args.subject,
                projects=args.project,
                languages=args.language,
                limit=args.limit,
            )
            return _render_symbols(result, args)
        if command in {"dependencies", "dependents"}:
            result = service.dependencies(
                args.subject,
                direction=command,
                max_depth=args.depth,
                projects=args.project,
                languages=args.language,
                include_unresolved=args.include_unresolved,
            )
            return _render_graph(result, args)
        if command == "impact":
            result = service.impact(
                args.subjects,
                max_depth=args.depth,
                max_nodes=args.max_nodes,
                projects=args.project,
                languages=args.language,
            )
            payload = {
                **result.model_dump(mode="json"),
                "provider_calls": 0,
                "network_calls": 0,
            }
            return _render_impact(payload, args, heading="Impact")
        if command == "tests-for":
            result = service.tests_for(
                args.subjects,
                max_depth=args.depth,
                max_nodes=args.max_nodes,
                projects=args.project,
                languages=args.language,
            )
            payload = {
                **result.model_dump(mode="json"),
                "provider_calls": 0,
                "network_calls": 0,
            }
            return _render_tests(payload, args)
        result = service.context_plan(
            " ".join(args.task),
            role=args.role,
            byte_budget=args.byte_budget,
            token_budget=args.token_budget,
            projects=args.project,
            changed_paths=args.changed_path,
        )
        return _render_context(result, args)
    except (OSError, RepositoryIntelligenceError, ValueError) as exc:
        return _render_error(
            command,
            exc,
            args,
            workspace=service.workspace if service is not None else None,
        )


def _parser(command: str) -> argparse.ArgumentParser:
    if command == "index":
        return _index_parser()
    parser = argparse.ArgumentParser(prog=f"agentbus {command}")
    if command == "search":
        parser.add_argument("query")
        _query_filters(parser, symbol_kinds=True, paths=True)
        parser.add_argument("--test-only", action="store_true")
        parser.add_argument("--limit", type=_limit, default=25)
        parser.add_argument("--offset", type=_offset, default=0)
    elif command == "symbols":
        parser.add_argument("subject")
        _query_filters(parser)
        parser.add_argument("--limit", type=_limit, default=50)
    elif command in {"dependencies", "dependents"}:
        parser.add_argument("subject")
        _query_filters(parser)
        parser.add_argument("--depth", type=_depth, default=1)
        parser.add_argument("--include-unresolved", action="store_true")
    elif command in {"impact", "tests-for"}:
        parser.add_argument("subjects", nargs="+")
        _query_filters(parser)
        parser.add_argument("--depth", type=_depth, default=4)
        parser.add_argument("--max-nodes", type=_max_nodes, default=500)
    else:
        parser.add_argument("task", nargs="+")
        parser.add_argument(
            "--role",
            choices=tuple(item.value for item in ContextRole),
            default=ContextRole.PLANNER.value,
        )
        parser.add_argument("--project", action="append", default=[])
        parser.add_argument("--changed-path", action="append", default=[])
        parser.add_argument("--byte-budget", type=_byte_budget, default=100_000)
        parser.add_argument("--token-budget", type=_token_budget, default=16_000)
    _common(parser, evidence=True)
    return parser


def _index_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="agentbus index")
    commands = parser.add_subparsers(dest="index_command", required=True)
    for name in ("build", "status", "update", "verify", "repair"):
        command = commands.add_parser(name)
        _common(command, evidence=True)
    clear = commands.add_parser("clear")
    clear.add_argument(
        "--yes",
        action="store_true",
        help="Confirm deletion of persisted index records for this repository.",
    )
    _common(clear, evidence=True)
    collect = commands.add_parser("gc")
    collect.add_argument("--retain", type=_retain, default=3)
    _common(collect, evidence=True)
    return parser


def _query_filters(
    parser: argparse.ArgumentParser,
    *,
    symbol_kinds: bool = False,
    paths: bool = False,
) -> None:
    parser.add_argument("--project", action="append", default=[])
    parser.add_argument(
        "--language",
        action="append",
        choices=tuple(item.value for item in SourceLanguage),
        default=[],
    )
    if symbol_kinds:
        parser.add_argument(
            "--kind",
            action="append",
            choices=tuple(item.value for item in SymbolKind),
            default=[],
        )
    if paths:
        parser.add_argument("--path-prefix", action="append", default=[])


def _common(parser: argparse.ArgumentParser, *, evidence: bool) -> None:
    parser.add_argument("--config")
    parser.add_argument("--workspace")
    parser.add_argument("--index-db")
    parser.add_argument("--repository-key")
    parser.add_argument("--json", action="store_true")
    if evidence:
        parser.add_argument("--evidence", action="store_true")


def _service(args: argparse.Namespace) -> RepositoryIntelligenceService:
    overrides = {"workspace_dir": args.workspace} if args.workspace else None
    config = resolve_configuration(
        config_file=args.config,
        cli_overrides=overrides,
    ).config
    database_path = (
        Path(args.index_db).expanduser()
        if args.index_db
        else config.state_database_path.parent / _DEFAULT_INDEX_DATABASE
    )
    return RepositoryIntelligenceService(
        config.workspace_path,
        database_path,
        repository_key=args.repository_key,
    )


def _index(
    service: RepositoryIntelligenceService,
    args: argparse.Namespace,
) -> int:
    operation = args.index_command
    if operation == "build":
        return _render_index_mutation(service.build(), args)
    if operation == "status":
        status = service.status()
        payload = {
            "command": "index status",
            **status.model_dump(mode="json"),
            "provider_calls": 0,
            "network_calls": 0,
        }
        lines = [
            f"Index status: {status.state.value}",
            status.message or "No status message.",
            f"Snapshot: {status.snapshot_id or '[none]'}",
            f"Files: {status.indexed_files}/{status.total_files}",
        ]
        if args.evidence:
            lines.extend(f"Stale: {item}" for item in status.stale_paths[:50])
        return _render(payload, args, lines)
    if operation == "update":
        return _render_index_mutation(service.update(), args)
    if operation == "repair":
        return _render_index_mutation(service.repair(), args)
    if operation == "verify":
        return _render_index_verification(service.verify(), args)
    if operation == "gc":
        return _render_index_gc(
            service.garbage_collect(retain=args.retain),
            args,
        )
    if not args.yes:
        message = "Index clear requires --yes; no records were deleted."
        payload = {
            "ok": False,
            "command": "index clear",
            "error": message,
            "deleted": False,
            "provider_calls": 0,
            "network_calls": 0,
        }
        return _render(payload, args, [message], exit_code=2)
    return _render_index_clear(service.clear(), args)


def _render_index_mutation(
    result: IndexMutationReport,
    args: argparse.Namespace,
) -> int:
    payload = {
        "command": f"index {result.operation.value}",
        **result.model_dump(mode="json"),
    }
    lines = [
        f"Index {result.operation.value}: {result.status.state.value}",
        f"Snapshot: {result.snapshot.snapshot_id}",
        (
            f"Files={result.snapshot.file_count} "
            f"symbols={result.snapshot.symbol_count} "
            f"edges={result.snapshot.edge_count}"
        ),
        (
            f"Indexed={result.indexed_count} reused={result.reused_count} "
            f"deleted={result.deleted_count} skipped={result.skipped_count}"
        ),
        "Provider calls: 0; network calls: 0",
    ]
    if args.evidence:
        lines.extend(f"Indexed: {item}" for item in result.indexed_paths[:50])
        lines.extend(f"Reused: {item}" for item in result.reused_paths[:50])
    return _render(payload, args, lines)


def _render_index_verification(
    result: IndexVerificationReport,
    args: argparse.Namespace,
) -> int:
    payload = {
        "command": "index verify",
        **result.model_dump(mode="json"),
    }
    lines = [
        f"Index verification: {'PASS' if result.valid else 'FAIL'}",
        f"Fresh: {str(result.fresh).lower()}",
        f"State: {result.status.state.value}",
        f"Schema version: {result.schema_version}",
        "Provider calls: 0; network calls: 0",
    ]
    return _render(payload, args, lines, exit_code=0 if result.valid else 1)


def _render_index_gc(
    result: IndexGarbageCollectionReport,
    args: argparse.Namespace,
) -> int:
    payload = {
        "command": "index gc",
        **result.model_dump(mode="json"),
    }
    lines = [
        f"Retained snapshots: {result.retained_snapshots}",
        f"Deleted snapshots: {result.deleted_snapshot_count}",
        f"Expired cache entries: {result.expired_cache_entries}",
        "Provider calls: 0; network calls: 0",
    ]
    if args.evidence:
        lines.extend(
            f"Deleted: {item}" for item in result.deleted_snapshot_ids[:50]
        )
    return _render(payload, args, lines)


def _render_index_clear(
    result: IndexClearReport,
    args: argparse.Namespace,
) -> int:
    payload = {
        "command": "index clear",
        **result.model_dump(mode="json"),
    }
    lines = [
        f"Deleted snapshots: {result.deleted_snapshot_count}",
        f"Index status: {result.status.state.value}",
        "Workspace files were not changed.",
        "Provider calls: 0; network calls: 0",
    ]
    return _render(payload, args, lines)


def _render_search(
    result: RepositorySearchReport,
    args: argparse.Namespace,
) -> int:
    payload = result.model_dump(mode="json")
    lines = [
        f"Search results: {len(result.results)} ({result.index_state.value})",
        f"Snapshot: {result.snapshot_id}",
    ]
    for item in result.results[:_MAX_HUMAN_ITEMS]:
        label = item.symbol.qualified_name if item.symbol else item.relative_path
        lines.append(
            f"{item.rank:>3}. {item.score:.3f} {label} [{item.relative_path}]"
        )
        if args.evidence:
            lines.append(f"     {item.explanation}")
    return _render(payload, args, lines)


def _render_symbols(
    result: SymbolQueryReport,
    args: argparse.Namespace,
) -> int:
    payload = result.model_dump(mode="json")
    lines = [
        f"Symbols: {len(result.symbols)} ({result.index_state.value})",
        f"Snapshot: {result.snapshot_id}",
    ]
    for item in result.symbols[:_MAX_HUMAN_ITEMS]:
        lines.append(
            f"{item.kind.value:<14} {item.qualified_name} "
            f"[{item.relative_path}:{item.start_line}] {item.symbol_id}"
        )
        if args.evidence and item.signature:
            lines.append(f"  Signature: {item.signature}")
    return _render(payload, args, lines)


def _render_graph(
    result: GraphQueryReport,
    args: argparse.Namespace,
) -> int:
    payload = result.model_dump(mode="json")
    labels = {item.node_id: item.label for item in result.nodes}
    lines = [
        f"{result.direction.title()} for {result.subject.qualified_name}",
        (
            f"Depth={result.maximum_depth_reached}/{result.max_depth} "
            f"nodes={len(result.nodes)} edges={len(result.edges)} "
            f"truncated={str(result.truncated).lower()}"
        ),
    ]
    for item in result.edges[:_MAX_HUMAN_ITEMS]:
        source = labels.get(item.source_id, item.source_id)
        target = labels.get(item.target_id, item.target_id)
        lines.append(f"{item.kind.value}: {source} -> {target}")
        if args.evidence:
            lines.append(
                f"  confidence={item.confidence:.3f}; {item.explanation}"
            )
    return _render(payload, args, lines)


def _render_impact(
    payload: dict[str, Any],
    args: argparse.Namespace,
    *,
    heading: str,
) -> int:
    lines = [
        f"{heading}: risk={payload['risk']} confidence={payload['confidence']:.3f}",
        f"Changed paths: {len(payload['changed_paths'])}",
        f"Direct dependents: {len(payload['direct_dependents'])}",
        f"Transitive dependents: {len(payload['transitive_dependents'])}",
        f"Affected projects: {len(payload['affected_projects'])}",
        f"Selected tests: {len(payload['tests']['selected_tests'])}",
        f"Truncated: {str(payload['truncated']).lower()}",
    ]
    lines.extend(
        f"Path: {item}" for item in payload["changed_paths"][:_MAX_HUMAN_ITEMS]
    )
    if args.evidence:
        lines.extend(
            f"Evidence: {item}" for item in payload["evidence"][:_MAX_HUMAN_ITEMS]
        )
    return _render(payload, args, lines)


def _render_tests(payload: dict[str, Any], args: argparse.Namespace) -> int:
    lines = [
        f"Selected tests: {len(payload['selected_tests'])}",
        f"Mandatory tests: {len(payload['mandatory_tests'])}",
        f"Optional tests: {len(payload['optional_tests'])}",
        (
            "Full suite recommended: "
            f"{str(payload['full_suite_recommended']).lower()}"
        ),
        f"Confidence: {payload['confidence']:.3f}",
    ]
    lines.extend(
        f"Test: {item}" for item in payload["selected_tests"][:_MAX_HUMAN_ITEMS]
    )
    if args.evidence:
        lines.extend(
            f"Evidence: {item}" for item in payload["evidence"][:_MAX_HUMAN_ITEMS]
        )
    return _render(payload, args, lines)


def _render_context(
    result: ContextPlanSummary,
    args: argparse.Namespace,
) -> int:
    payload = result.model_dump(mode="json")
    selected = tuple(item for item in result.candidates if item.selected)
    lines = [
        f"Context plan: {result.plan_id}",
        f"Role: {result.role.value}",
        (
            f"Selected: {len(selected)} candidates, "
            f"{result.selected_bytes}/{result.byte_budget} bytes, "
            f"{result.selected_tokens}/{result.token_budget} tokens"
        ),
        f"Snapshot: {result.snapshot_id or '[none]'}",
    ]
    if result.stale_warning:
        lines.append(f"Warning: {result.stale_warning}")
    for item in selected[:_MAX_HUMAN_ITEMS]:
        lines.append(f"{item.score:.3f} {item.relative_path}")
        if args.evidence and item.reasons:
            lines.append(f"  Evidence: {', '.join(item.reasons)}")
    return _render(payload, args, lines)


def _render(
    payload: dict[str, Any],
    args: argparse.Namespace,
    lines: list[str],
    *,
    exit_code: int = 0,
) -> int:
    if args.json:
        safe = _safe_payload(payload, include_evidence=args.evidence)
        print(json.dumps(safe, indent=2, sort_keys=True))
    else:
        print("\n".join(lines[: 2 * _MAX_HUMAN_ITEMS + 12]))
    return exit_code


def _safe_payload(value: Any, *, include_evidence: bool) -> Any:
    if isinstance(value, BaseModel):
        value = value.model_dump(mode="json")
    if isinstance(value, dict):
        return {
            key: _safe_payload(item, include_evidence=include_evidence)
            for key, item in value.items()
            if key not in _ALWAYS_OMITTED_FIELDS
            and (include_evidence or key not in _EVIDENCE_FIELDS)
        }
    if isinstance(value, (list, tuple)):
        return [
            _safe_payload(item, include_evidence=include_evidence)
            for item in value
        ]
    return value


def _render_error(
    command: str,
    error: Exception,
    args: argparse.Namespace,
    *,
    workspace: Path | None,
) -> int:
    message = str(error)
    private_paths = [workspace]
    for raw_path in (args.workspace, args.index_db, args.config):
        if raw_path:
            private_paths.append(Path(raw_path).expanduser().resolve())
    for path in private_paths:
        if path is not None:
            message = message.replace(str(path), "[LOCAL_PATH]")
    message = redact_text(message, max_chars=2_000) or "Repository query failed."
    payload = {
        "ok": False,
        "command": command,
        "error": message,
        "provider_calls": 0,
        "network_calls": 0,
    }
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        print(f"Repository intelligence error: {message}", file=sys.stderr)
    return 2


def _bounded_integer(
    raw: str,
    *,
    minimum: int,
    maximum: int,
    name: str,
) -> int:
    try:
        value = int(raw)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"{name} must be an integer") from exc
    if value < minimum or value > maximum:
        raise argparse.ArgumentTypeError(
            f"{name} must be between {minimum} and {maximum}"
        )
    return value


def _limit(raw: str) -> int:
    return _bounded_integer(raw, minimum=1, maximum=200, name="limit")


def _offset(raw: str) -> int:
    return _bounded_integer(raw, minimum=0, maximum=100_000, name="offset")


def _depth(raw: str) -> int:
    return _bounded_integer(raw, minimum=0, maximum=16, name="depth")


def _max_nodes(raw: str) -> int:
    return _bounded_integer(raw, minimum=1, maximum=10_000, name="max nodes")


def _retain(raw: str) -> int:
    return _bounded_integer(raw, minimum=1, maximum=1_000, name="retain")


def _byte_budget(raw: str) -> int:
    return _bounded_integer(
        raw,
        minimum=1,
        maximum=10_000_000,
        name="byte budget",
    )


def _token_budget(raw: str) -> int:
    return _bounded_integer(
        raw,
        minimum=1,
        maximum=2_000_000,
        name="token budget",
    )


__all__ = ["INTELLIGENCE_COMMANDS", "intelligence_command"]
