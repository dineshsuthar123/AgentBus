from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

from agentbus import __version__
from agentbus.bootstrap import BootstrapError, initialize
from agentbus.config import SUPPORTED_PROVIDERS
from agentbus.configuration import configuration_paths, resolve_configuration
from agentbus.doctor import CheckStatus, render_doctor, run_doctor


COMMANDS = (
    "run",
    "resume",
    "runs",
    "show-run",
    "approve",
    "reject",
    "providers",
    "config",
    "init",
    "setup",
    "quickstart",
    "demo",
    "cleanup",
    "logs",
    "support-bundle",
    "benchmark",
    "soak",
    "release-check",
    "doctor",
    "migrate",
    "upgrade-check",
    "serve",
    "daemon",
    "control-schema",
    "worktrees",
    "trace",
    "replay",
    "compare",
    "index",
    "search",
    "symbols",
    "dependencies",
    "dependents",
    "impact",
    "tests-for",
    "context-plan",
    "evaluate",
    "release-report",
    "version",
)


def main(argv: list[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    if not arguments:
        _root_parser().print_help()
        return 0
    if arguments in (["--version"], ["-V"]):
        print(f"agentbus {__version__}")
        return 0
    if arguments[0] in {"--help", "-h"}:
        _root_parser().print_help()
        return 0

    command, rest = arguments[0], arguments[1:]
    if command == "version":
        return _version_command(rest)
    if command == "run":
        return _legacy(rest)
    if command == "resume":
        return _single_id_operation("resume", "--resume", rest)
    if command == "runs":
        return _no_id_operation("runs", "--list-runs", rest)
    if command == "show-run":
        return _single_id_operation("show-run", "--show-run", rest)
    if command in {"approve", "reject"}:
        return _decision_operation(command, rest)
    if command == "providers":
        return _providers_command(rest)
    if command == "worktrees":
        return _worktrees_command(rest)
    if command in {"trace", "replay", "compare"}:
        from agentbus.replay.commands import (
            compare_command,
            replay_command,
            trace_command,
        )

        operations = {
            "trace": trace_command,
            "replay": replay_command,
            "compare": compare_command,
        }
        return operations[command](rest)
    if command in {
        "index",
        "search",
        "symbols",
        "dependencies",
        "dependents",
        "impact",
        "tests-for",
        "context-plan",
    }:
        from agentbus.intelligence.commands import intelligence_command

        return intelligence_command(command, rest)
    if command == "evaluate":
        from agentbus.eval import main as evaluation_main

        return evaluation_main(rest)
    if command == "config":
        return _config_command(rest)
    if command == "init":
        return _init_command(rest)
    if command == "setup":
        return _setup_command(rest)
    if command == "quickstart":
        return _quickstart_command(rest)
    if command == "demo":
        return _demo_command(rest)
    if command == "cleanup":
        return _cleanup_command(rest)
    if command == "logs":
        return _logs_command(rest)
    if command == "support-bundle":
        return _support_bundle_command(rest)
    if command == "benchmark":
        return _benchmark_command(rest)
    if command == "soak":
        return _soak_command(rest)
    if command == "release-check":
        return _release_check_command(rest)
    if command == "doctor":
        return _doctor_command(rest)
    if command == "migrate":
        return _migration_command(rest)
    if command == "upgrade-check":
        return _upgrade_check_command(rest)
    if command == "serve":
        return _serve_command(rest)
    if command == "daemon":
        return _daemon_command(rest)
    if command == "control-schema":
        return _control_schema_command(rest)
    if command == "release-report":
        from agentbus.release_report import main as release_report_main

        return release_report_main(rest)

    # Compatibility: direct tasks and the original option-oriented CLI still work.
    return _legacy(arguments)


def _root_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="agentbus",
        description="Safety-oriented local agent execution and evaluation.",
        epilog=(
            "Existing option-oriented invocations remain supported. Use "
            "'agentbus <command> --help' for command details."
        ),
    )
    parser.add_argument("--version", action="version", version=f"agentbus {__version__}")
    commands = parser.add_subparsers(dest="command", metavar="COMMAND")
    help_text = {
        "run": "Run a task using the existing AgentBus runner.",
        "resume": "Resume a durable run.",
        "runs": "List durable runs.",
        "show-run": "Inspect a durable run.",
        "approve": "Approve a high-risk task.",
        "reject": "Reject a high-risk task.",
        "providers": "Inspect or explicitly check providers.",
        "config": "Show, validate, or locate resolved configuration.",
        "init": "Create safe first-run configuration and state.",
        "setup": "Guide first-run product configuration with an offline default.",
        "quickstart": "Complete a temporary deterministic first task offline.",
        "demo": "List, create, or preflight compact AgentBus demo repositories.",
        "cleanup": "Remove only proven AgentBus-owned stale runtime artifacts.",
        "logs": "Inspect bounded redacted product and run logs.",
        "support-bundle": "Create a sanitized local diagnostic ZIP.",
        "benchmark": "Measure bounded offline product performance.",
        "soak": "Exercise bounded offline reliability and leak checks.",
        "release-check": "Run non-publishing public beta release gates.",
        "doctor": "Run offline environment diagnostics.",
        "migrate": "Inspect and apply safe local database migrations.",
        "upgrade-check": "Check local package, schema, config, and extension compatibility.",
        "serve": "Start the authenticated local control-plane daemon.",
        "daemon": "Inspect or safely manage local daemons.",
        "control-schema": "Export generated control-protocol artifacts.",
        "worktrees": "Inspect or explicitly clean owned worktrees.",
        "trace": "Inspect, verify, capture, export, import, or retain traces.",
        "replay": "Replay a run or archive without providers.",
        "compare": "Compare two persisted execution traces.",
        "index": "Build, inspect, verify, repair, or retain a local index.",
        "search": "Search indexed repository files and symbols.",
        "symbols": "Inspect bounded symbol metadata for a file or symbol.",
        "dependencies": "Query bounded symbol dependencies.",
        "dependents": "Query bounded symbol dependents.",
        "impact": "Analyze bounded change impact for paths or symbols.",
        "tests-for": "Select evidence-backed tests for paths or symbols.",
        "context-plan": "Build a deterministic, budgeted context plan.",
        "evaluate": "Run the evaluation harness.",
        "release-report": "Generate evidence-based release reports.",
        "version": "Print version metadata.",
    }
    for name in COMMANDS:
        commands.add_parser(name, help=help_text[name], add_help=False)
    return parser


def _legacy(arguments: list[str]) -> int:
    from agentbus.main import main as legacy_main

    return legacy_main(arguments)


def _single_id_operation(name: str, legacy_flag: str, arguments: list[str]) -> int:
    parser = argparse.ArgumentParser(prog=f"agentbus {name}")
    parser.add_argument("run_id")
    parser.add_argument("--config")
    args = parser.parse_args(arguments)
    forwarded = [legacy_flag, args.run_id]
    if args.config:
        forwarded.extend(["--config", args.config])
    return _legacy(forwarded)


def _no_id_operation(name: str, legacy_flag: str, arguments: list[str]) -> int:
    parser = argparse.ArgumentParser(prog=f"agentbus {name}")
    parser.add_argument("--config")
    args = parser.parse_args(arguments)
    forwarded = [legacy_flag]
    if args.config:
        forwarded.extend(["--config", args.config])
    return _legacy(forwarded)


def _decision_operation(command: str, arguments: list[str]) -> int:
    parser = argparse.ArgumentParser(prog=f"agentbus {command}")
    parser.add_argument("target", help="RUN_ID:TASK_ID")
    parser.add_argument("--reason")
    parser.add_argument("--config")
    args = parser.parse_args(arguments)
    forwarded = [f"--{command}", args.target]
    if args.reason:
        forwarded.extend(["--reason", args.reason])
    if args.config:
        forwarded.extend(["--config", args.config])
    return _legacy(forwarded)


def _providers_command(arguments: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="agentbus providers")
    commands = parser.add_subparsers(dest="provider_command")
    commands.add_parser("list", help="List supported providers.")
    show = commands.add_parser("show", help="Show redacted routing configuration.")
    show.add_argument("--config")
    check = commands.add_parser("check", help="Validate one provider locally.")
    check.add_argument("provider", choices=SUPPORTED_PROVIDERS)
    check.add_argument("--live", action="store_true")
    check.add_argument("--config")
    args = parser.parse_args(arguments)
    if args.provider_command in {None, "list"}:
        return _legacy(["--list-providers"])
    if args.provider_command == "show":
        forwarded = ["--show-model-config"]
    else:
        forwarded = ["--check-provider", args.provider]
        if args.live:
            forwarded.append("--live")
    if args.config:
        forwarded.extend(["--config", args.config])
    return _legacy(forwarded)


def _worktrees_command(arguments: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="agentbus worktrees")
    commands = parser.add_subparsers(dest="worktree_command", required=True)
    listing = commands.add_parser("list")
    listing.add_argument("run_id", nargs="?")
    listing.add_argument("--config")
    cleanup = commands.add_parser("cleanup")
    cleanup.add_argument("run_id")
    cleanup.add_argument("--config")
    args = parser.parse_args(arguments)
    if args.worktree_command == "list":
        forwarded = ["--list-worktrees"]
        if args.run_id:
            forwarded.append(args.run_id)
    else:
        forwarded = ["--cleanup-worktrees", args.run_id]
    if args.config:
        forwarded.extend(["--config", args.config])
    return _legacy(forwarded)


def _config_command(arguments: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="agentbus config")
    commands = parser.add_subparsers(dest="config_command", required=True)
    for name in ("show", "validate", "path", "paths"):
        command = commands.add_parser(name)
        command.add_argument("--config")
        command.add_argument("--workspace")
        command.add_argument("--scope", choices=("user", "workspace"), default="user")
        command.add_argument("--json", action="store_true")
    for name in ("get", "explain"):
        command = commands.add_parser(name)
        command.add_argument("key")
        command.add_argument("--config")
        command.add_argument("--workspace")
        command.add_argument("--json", action="store_true")
    setting = commands.add_parser("set")
    setting.add_argument("key")
    setting.add_argument("value")
    setting.add_argument("--config")
    setting.add_argument("--workspace")
    setting.add_argument("--scope", choices=("user", "workspace"), default="user")
    setting.add_argument("--json", action="store_true")
    unsetting = commands.add_parser("unset")
    unsetting.add_argument("key")
    unsetting.add_argument("--config")
    unsetting.add_argument("--workspace")
    unsetting.add_argument("--scope", choices=("user", "workspace"), default="user")
    unsetting.add_argument("--json", action="store_true")
    args = parser.parse_args(arguments)
    try:
        from agentbus.product.config_store import (
            config_target_path,
            ensure_safe_config_target,
            parse_config_value,
            set_config_value,
            unset_config_value,
        )

        workspace = args.workspace or "."
        overrides = {"workspace_dir": args.workspace}
        if args.config_command in {"set", "unset"}:
            target = Path(args.config).expanduser() if args.config else config_target_path(
                args.scope,
                workspace=workspace,
            )
            target = ensure_safe_config_target(
                target,
                workspace=workspace if args.scope == "workspace" and not args.config else None,
            )
            if args.config_command == "set":
                mutation = set_config_value(
                    target,
                    args.key,
                    parse_config_value(args.key, args.value),
                )
            else:
                mutation = unset_config_value(target, args.key)
            payload = {
                "ok": True,
                "operation": args.config_command,
                "key": args.key,
                "path": str(mutation.path),
                "changed": mutation.changed,
            }
            if args.json:
                print(json.dumps(payload, indent=2, sort_keys=True))
            else:
                state = "Updated" if mutation.changed else "Unchanged"
                print(f"{state}: {args.key} ({mutation.path})")
            return 0

        resolved = resolve_configuration(
            config_file=args.config,
            cli_overrides=overrides,
            workspace=args.workspace,
        )
        if args.config_command == "show":
            payload = {
                "valid": True,
                "config_file": str(resolved.config_file) if resolved.config_file else None,
                "values": resolved.safe_values(),
            }
        elif args.config_command in {"path", "paths"}:
            payload = configuration_paths(resolved)
            payload["selected_scope"] = args.scope
            payload["selected_path"] = str(
                Path(args.config).expanduser().absolute()
                if args.config
                else config_target_path(args.scope, workspace=workspace)
            )
        elif args.config_command in {"get", "explain"}:
            values = resolved.safe_values()
            if args.key not in values:
                raise ValueError(f"Unsupported AgentBus configuration key: {args.key}")
            item = values[args.key]
            payload = {
                "key": args.key,
                "value": item["value"],
                "source": item["source"],
            }
            if args.config_command == "explain":
                payload["precedence"] = [
                    "default",
                    "user",
                    "workspace",
                    "explicit",
                    "cli",
                    "environment",
                ]
        else:
            payload = {
                "valid": True,
                "config_file": str(resolved.config_file) if resolved.config_file else None,
                "message": "Configuration is valid; no dotenv parent search was performed.",
            }
    except (OSError, ValueError) as exc:
        payload = {"valid": False, "error": str(exc)}
        print(json.dumps(payload, indent=2, sort_keys=True) if args.json else f"FAIL: {exc}")
        return 2
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    elif args.config_command == "show":
        for name, item in payload["values"].items():
            print(f"{name} = {item['value']!r}  ({item['source']})")
    elif args.config_command in {"path", "paths"}:
        for name, value in payload.items():
            print(f"{name}: {value or '[not configured]'}")
    elif args.config_command == "get":
        print(repr(payload["value"]))
    elif args.config_command == "explain":
        print(f"{payload['key']} = {payload['value']!r}")
        print(f"Source: {payload['source']}")
        print("Precedence: " + " < ".join(payload["precedence"]))
    else:
        print("PASS: " + payload["message"])
    return 0


def _init_command(arguments: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="agentbus init")
    parser.add_argument("--local", action="store_true")
    parser.add_argument(
        "--provider",
        choices=SUPPORTED_PROVIDERS,
        default="ollama",
    )
    parser.add_argument("--workspace", default=".")
    parser.add_argument("--root", help="Explicit configuration root (primarily for automation).")
    parser.add_argument("--with-env-example", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(arguments)
    try:
        result = initialize(
            workspace=args.workspace,
            local=args.local,
            provider=args.provider,
            dry_run=args.dry_run,
            force=args.force,
            with_env_example=args.with_env_example,
            root=args.root,
        )
    except (BootstrapError, OSError, ValueError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}) if args.json else f"Init error: {exc}")
        return 2
    payload = {
        "ok": True,
        "dry_run": result.dry_run,
        "provider": result.provider,
        "root": str(result.root),
        "config_file": str(result.config_file),
        "state_database": str(result.state_database),
        "created": [str(path) for path in result.created],
        "planned": [str(path) for path in result.planned],
        "network_used": False,
        "credentials_created": False,
    }
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        action = "Would create" if result.dry_run else "Initialized"
        print(f"{action} AgentBus configuration at {result.root}")
        for path in result.planned if result.dry_run else result.created:
            print(f"  {path}")
        print("No credentials or provider requests were created.")
        print(
            "Next: configure Ollama locally."
            if result.provider == "ollama"
            else "Next: set Azure endpoint, key, and deployment in the process environment."
        )
    return 0


def _doctor_command(arguments: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="agentbus doctor")
    parser.add_argument("--config")
    parser.add_argument("--workspace")
    parser.add_argument("--live-provider", choices=SUPPORTED_PROVIDERS)
    parser.add_argument("--provider", choices=SUPPORTED_PROVIDERS)
    parser.add_argument("--verbose", action="store_true")
    parser.add_argument("--repair", action="store_true")
    parser.add_argument("--registry-path")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(arguments)
    try:
        resolved = resolve_configuration(
            config_file=args.config,
            cli_overrides={"workspace_dir": args.workspace},
        )
        report = run_doctor(
            resolved.config,
            live_provider=args.live_provider,
            provider=args.provider,
            repair=args.repair,
            registry_path=args.registry_path,
        )
    except (OSError, ValueError) as exc:
        payload = {"status": "FAIL", "error": str(exc), "network_used": False}
        print(json.dumps(payload, indent=2, sort_keys=True) if args.json else f"Doctor error: {exc}")
        return 2
    print(
        json.dumps(report.to_dict(), indent=2, sort_keys=True)
        if args.json
        else render_doctor(report, verbose=args.verbose)
    )
    return 1 if report.status == CheckStatus.FAIL else 0


def _migration_command(arguments: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="agentbus migrate")
    commands = parser.add_subparsers(dest="migration_command", required=True)
    for name in ("status", "plan", "verify"):
        command = commands.add_parser(name)
        command.add_argument("--config")
        command.add_argument("--workspace")
        command.add_argument("--json", action="store_true")
    apply = commands.add_parser("apply")
    apply.add_argument("--config")
    apply.add_argument("--workspace")
    apply.add_argument("--dry-run", action="store_true")
    apply.add_argument("--json", action="store_true")
    args = parser.parse_args(arguments)
    try:
        from agentbus.product.migrations import MigrationCoordinator

        resolved = resolve_configuration(
            config_file=args.config,
            cli_overrides={"workspace_dir": args.workspace},
            workspace=args.workspace,
        )
        coordinator = MigrationCoordinator(resolved.config)
        if args.migration_command == "status":
            report = coordinator.status()
        elif args.migration_command == "plan":
            report = coordinator.plan()
        elif args.migration_command == "verify":
            report = coordinator.verify()
        else:
            report = coordinator.apply(dry_run=args.dry_run)
    except (OSError, RuntimeError, ValueError) as exc:
        payload = {"ok": False, "error": str(exc), "network_used": False}
        print(json.dumps(payload, indent=2, sort_keys=True) if args.json else f"Migration error: {exc}")
        return 2
    payload = report.to_dict()
    payload["network_used"] = False
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        print(f"Migration {report.operation}: {'OK' if report.ok else 'FAILED'}")
        for target in report.targets:
            version = "absent" if target.current_version is None else target.current_version
            print(
                f"  {target.name}: {target.state.value} "
                f"({version} -> {target.target_version})"
            )
            print(f"    {target.message}")
        for backup in report.backups:
            print(f"  Backup: {backup}")
    return 0 if report.ok else 2


def _upgrade_check_command(arguments: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="agentbus upgrade-check")
    parser.add_argument("--config")
    parser.add_argument("--workspace")
    parser.add_argument("--extension-package")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(arguments)
    try:
        from agentbus.product.upgrade import run_upgrade_check

        resolved = resolve_configuration(
            config_file=args.config,
            cli_overrides={"workspace_dir": args.workspace},
            workspace=args.workspace,
        )
        report = run_upgrade_check(
            resolved.config,
            extension_package=args.extension_package,
        )
    except (OSError, RuntimeError, ValueError) as exc:
        payload = {"ok": False, "error": str(exc), "network_used": False}
        print(json.dumps(payload, indent=2, sort_keys=True) if args.json else f"Upgrade check error: {exc}")
        return 2
    payload = report.to_dict()
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        print(f"AgentBus upgrade check: {'OK' if report.ok else 'FAILED'}")
        for check in report.checks:
            print(f"  [{check.status.value}] {check.name}: {check.message}")
            if check.action:
                print(f"    Action: {check.action}")
    return 0 if report.ok else 2


def _serve_command(arguments: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="agentbus serve")
    parser.add_argument("--config")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=0)
    parser.add_argument("--json-ready", action="store_true")
    parser.add_argument("--idle-timeout", type=float, default=86_400)
    parser.add_argument("--foreground", action="store_true")
    parser.add_argument("--registry-path")
    parser.add_argument("--daemon-id", help=argparse.SUPPRESS)
    parser.add_argument(
        "--log-level",
        choices=["critical", "error", "warning", "info"],
        default="warning",
    )
    args = parser.parse_args(arguments)
    if args.port < 0 or args.port > 65535:
        parser.error("--port must be between 0 and 65535")
    if args.idle_timeout < 0:
        parser.error("--idle-timeout must not be negative")
    try:
        _daemon_startup_stage("configuration-resolving")
        config = resolve_configuration(config_file=args.config).config
        _daemon_startup_stage("configuration-ready")
        from agentbus.control.server import serve

        _daemon_startup_stage("control-server-imported")
        return serve(
            config=config,
            host=args.host,
            port=args.port,
            json_ready=args.json_ready,
            idle_timeout=args.idle_timeout,
            registry_path=args.registry_path,
            daemon_id=args.daemon_id,
            log_level=args.log_level,
        )
    except (OSError, RuntimeError, ValueError) as exc:
        stream = sys.stderr if args.json_ready else sys.stdout
        print(f"Control-plane error: {exc}", file=stream)
        return 2


def _daemon_startup_stage(stage: str) -> None:
    import os

    if os.environ.get("AGENTBUS_DAEMON_STARTUP_DIAGNOSTICS") == "1":
        print(f"agentbus-daemon-stage:{stage}", file=sys.stderr, flush=True)


def _daemon_command(arguments: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="agentbus daemon")
    parser.add_argument("--registry-path")
    parser.add_argument("--json", action="store_true")
    commands = parser.add_subparsers(dest="daemon_command", required=True)
    status = commands.add_parser("status")
    status.add_argument("--config")
    status.add_argument("--workspace")
    start = commands.add_parser("start")
    start.add_argument("--config")
    start.add_argument("--workspace")
    start.add_argument("--idle-timeout", type=float)
    start.add_argument("--log-level", choices=("error", "warning", "info", "debug", "trace"))
    restart = commands.add_parser("restart")
    restart.add_argument("daemon_id", nargs="?")
    restart.add_argument("--config")
    restart.add_argument("--workspace")
    restart.add_argument("--idle-timeout", type=float)
    restart.add_argument("--log-level", choices=("error", "warning", "info", "debug", "trace"))
    stop = commands.add_parser("stop")
    stop.add_argument("daemon_id", nargs="?")
    commands.add_parser("registry")
    commands.add_parser("cleanup-stale")
    logs = commands.add_parser("logs")
    logs.add_argument("--tail", type=int, default=100)
    logs.add_argument("--config")
    logs.add_argument("--workspace")
    args = parser.parse_args(arguments)
    from agentbus.control.registry import DaemonRegistry, process_matches
    from agentbus.product.daemon import (
        daemon_status,
        read_daemon_logs,
        restart_daemon,
        start_daemon,
        stop_daemon,
    )

    registry = DaemonRegistry(args.registry_path)
    if args.daemon_command in {"start", "restart"}:
        try:
            resolved = resolve_configuration(
                config_file=args.config,
                cli_overrides={"workspace_dir": args.workspace},
                workspace=args.workspace,
            )
            operation = start_daemon if args.daemon_command == "start" else restart_daemon
            kwargs = {
                "config_file": args.config,
                "registry_path": registry.path,
                "idle_timeout": args.idle_timeout,
                "log_level": args.log_level,
            }
            if args.daemon_command == "restart":
                kwargs["daemon_id"] = args.daemon_id
            result = operation(resolved.config, **kwargs)
            payload = result.to_dict()
        except (OSError, RuntimeError, ValueError) as exc:
            print(f"Daemon {args.daemon_command} refused: {exc}", file=sys.stderr)
            return 2
    elif args.daemon_command == "logs":
        try:
            resolved = resolve_configuration(
                config_file=args.config,
                cli_overrides={"workspace_dir": args.workspace},
                workspace=args.workspace,
            )
            log_path = resolved.config.state_database_path.resolve().parent / "logs" / "daemon.log"
            lines = read_daemon_logs(log_path, tail=args.tail)
            payload = {"log_path": str(log_path), "lines": lines, "count": len(lines)}
        except (OSError, ValueError) as exc:
            print(f"Daemon logs error: {exc}", file=sys.stderr)
            return 2
    elif args.daemon_command == "cleanup-stale":
        removed = registry.cleanup_stale()
        payload = {"removed": removed, "count": len(removed)}
    elif args.daemon_command == "registry":
        payload = {
            "registry_path": str(registry.path),
            "daemons": [
                item.model_dump(mode="json", exclude_none=True)
                for item in registry.list()
            ],
        }
    elif args.daemon_command == "status":
        payload = {
            "registry_path": str(registry.path),
            "daemons": [
                {
                    **daemon_status(item),
                }
                for item in registry.list()
            ],
        }
        payload["count"] = len(payload["daemons"])
    else:
        entries = registry.list()
        daemon_id = args.daemon_id
        if daemon_id is None:
            active = [item for item in entries if process_matches(item)]
            if len(active) != 1:
                print(
                    "Daemon stop requires DAEMON_ID unless exactly one active "
                    "daemon is registered.",
                    file=sys.stderr,
                )
                return 2
            daemon_id = active[0].daemon_id
        try:
            stop_daemon(registry_path=registry.path, daemon_id=daemon_id)
        except (OSError, RuntimeError) as exc:
            print(f"Daemon stop refused: {exc}", file=sys.stderr)
            return 2
        payload = {"stopped": daemon_id}
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    elif args.daemon_command == "status":
        print(f"Registry: {payload['registry_path']}")
        print(f"Registered daemons: {payload['count']}")
        for item in payload["daemons"]:
            state = item["lifecycle"]
            print(
                f"{item['daemon_id']}  {state}  pid={item['pid']}  "
                f"{item['bound_address']}  uptime={item['uptime_seconds']:.1f}s  "
                f"runs={item['active_runs']} indexes={item['active_index_jobs']}"
            )
    elif args.daemon_command == "registry":
        print(json.dumps(payload, indent=2, sort_keys=True))
    elif args.daemon_command == "cleanup-stale":
        print(f"Removed stale daemon registrations: {payload['count']}")
    elif args.daemon_command in {"start", "restart"}:
        state = "Started" if payload["started"] else "Already running"
        print(f"{state}: {payload['daemon']['daemon_id']}")
        print(f"Bound: {payload['daemon']['bound_address']}")
        print(f"Logs: {payload['log_path']}")
    elif args.daemon_command == "logs":
        print(f"Daemon log: {payload['log_path']}")
        for line in payload["lines"]:
            print(line)
    else:
        print(f"Stop signal sent to daemon: {payload['stopped']}")
    return 0


def _control_schema_command(arguments: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="agentbus control-schema")
    commands = parser.add_subparsers(dest="schema_command", required=True)
    export = commands.add_parser("export")
    export.add_argument("--output-dir", default="protocol")
    export.add_argument("--check", action="store_true")
    args = parser.parse_args(arguments)
    try:
        from agentbus.control.protocol import export_protocol

        changed = export_protocol(Path(args.output_dir), check=args.check)
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"Protocol export error: {exc}", file=sys.stderr)
        return 2
    if args.check:
        print("Control protocol artifacts are current.")
    else:
        print(f"Exported control protocol artifacts ({changed} changed).")
    return 0


def _version_command(arguments: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="agentbus version")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(arguments)
    from agentbus.product.compatibility import compatibility_manifest

    payload = compatibility_manifest().to_dict()
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        print(f"agentbus {payload['version']}")
        print("Supported Python: " + ", ".join(payload["supported_python"]))
        print(f"Control protocol: {payload['protocols']['control']}")
        print(f"Tool protocol: {payload['protocols']['tool']}")
        print(f"State schema: {payload['schemas']['state']}")
        print(f"Trace schema: {payload['schemas']['trace']}")
        print(f"Extension compatibility: {payload['extension_compatibility']}")
    return 0


def _setup_command(arguments: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="agentbus setup")
    parser.add_argument("--workspace", default=".")
    parser.add_argument("--provider", choices=SUPPORTED_PROVIDERS)
    parser.add_argument("--scope", choices=("user", "workspace"), default="user")
    parser.add_argument("--root", help="Explicit configuration root for automation.")
    parser.add_argument("--non-interactive", action="store_true")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--no-durable", action="store_true")
    parser.add_argument("--no-index", action="store_true")
    parser.add_argument("--mcp", action="store_true")
    parser.add_argument("--cleanup-worktrees", action="store_true")
    args = parser.parse_args(arguments)
    provider = args.provider
    if provider is None and not args.non_interactive:
        answer = input("Provider [deterministic/ollama/azure] (deterministic): ").strip().lower()
        provider = answer or "deterministic"
    provider = provider or "deterministic"
    try:
        from agentbus.product.setup import run_setup

        result = run_setup(
            workspace=args.workspace,
            provider=provider,
            scope=args.scope,
            durable=not args.no_durable,
            repository_index=not args.no_index,
            enable_mcp=args.mcp,
            keep_worktrees=not args.cleanup_worktrees,
            config_root=args.root,
            dry_run=args.dry_run,
            force=args.force,
        )
    except (OSError, RuntimeError, ValueError) as exc:
        payload = {"ok": False, "error": str(exc), "network_used": False}
        print(json.dumps(payload, indent=2, sort_keys=True) if args.json else f"Setup error: {exc}")
        return 2
    payload = result.to_dict()
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        action = "Would configure" if result.dry_run else "Configured"
        if result.existing_configuration_preserved:
            action = "Preserved existing configuration at"
        print(f"{action} {result.config_file}")
        print(f"Provider: {result.provider} (no live request performed)")
        for detection in result.detections:
            label = "OK" if detection.available else ("OPTIONAL" if detection.optional else "MISSING")
            print(f"  [{label}] {detection.name}: {detection.detail}")
        print("Next: run `agentbus doctor`, then `agentbus quickstart`.")
    return 0


def _demo_command(arguments: list[str]) -> int:
    from agentbus.product.demos import DEMO_LANGUAGES, create_demo, demo_definitions, run_demo

    parser = argparse.ArgumentParser(prog="agentbus demo")
    commands = parser.add_subparsers(dest="demo_command", required=True)
    listing = commands.add_parser("list")
    listing.add_argument("--json", action="store_true")
    creating = commands.add_parser("create")
    creating.add_argument("language", choices=DEMO_LANGUAGES)
    creating.add_argument("--output")
    creating.add_argument("--force", action="store_true")
    creating.add_argument("--json", action="store_true")
    running = commands.add_parser("run")
    running.add_argument("language", choices=DEMO_LANGUAGES)
    running.add_argument("--workspace")
    running.add_argument("--timeout", type=float, default=60.0)
    running.add_argument("--json", action="store_true")
    args = parser.parse_args(arguments)
    if args.demo_command == "list":
        payload = {
            "demos": [
                {
                    "language": item.language,
                    "title": item.title,
                    "task": item.task,
                }
                for item in demo_definitions().values()
            ],
            "network_used": False,
        }
    else:
        try:
            if args.demo_command == "create":
                output = args.output or f"agentbus-{args.language}-demo"
                result = create_demo(args.language, output, force=args.force)
            else:
                result = run_demo(
                    args.language,
                    workspace=args.workspace,
                    timeout_seconds=args.timeout,
                )
            payload = result.to_dict()
        except (OSError, RuntimeError, subprocess.SubprocessError, ValueError) as exc:
            print(json.dumps({"ok": False, "error": str(exc)}) if args.json else f"Demo error: {exc}")
            return 2
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    elif args.demo_command == "list":
        for item in payload["demos"]:
            print(f"{item['language']}: {item['title']} - {item['task']}")
    else:
        print(f"Demo: {payload['language']} at {payload['workspace']}")
        print("Task: " + demo_definitions()[payload["language"]].task)
        print("Test: " + " ".join(payload["test_command"]))
        if payload["test_executed"]:
            print(f"Intentional initial test exit code: {payload['test_exit_code']}")
    return 0


def _quickstart_command(arguments: list[str]) -> int:
    from agentbus.product.quickstart import run_quickstart

    parser = argparse.ArgumentParser(prog="agentbus quickstart")
    parser.add_argument(
        "--keep-demo",
        action="store_true",
        help="Retain the temporary demo repository and runtime state for inspection.",
    )
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(arguments)
    result = run_quickstart(keep_demo=args.keep_demo)
    payload = result.to_dict()
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        print("AgentBus deterministic quickstart")
        for step in result.steps:
            label = {
                "passed": "OK",
                "skipped": "SKIP",
                "failed": "ERROR",
            }.get(step.status, step.status.upper())
            print(f"  [{label}] {step.name}: {step.detail}")
        if result.changed_files:
            print("Changed files: " + ", ".join(result.changed_files))
        if result.report:
            print("Report: " + result.report)
        if result.kept_demo and result.workspace:
            print(f"Demo retained at: {result.workspace}")
        elif result.cleaned:
            print("Temporary demo and runtime state were removed.")
        if result.error:
            print(f"{result.error['code']}: {result.error['message']}")
            print("Recommended action: " + str(result.error["recommended_action"]))
    return 0 if result.ok else 2


def _cleanup_command(arguments: list[str]) -> int:
    from agentbus.product.cleanup import CleanupMode, RuntimeCleanup

    parser = argparse.ArgumentParser(prog="agentbus cleanup")
    parser.add_argument("--config")
    parser.add_argument("--registry-path")
    parser.add_argument("--dry-run", action="store_true")
    scope = parser.add_mutually_exclusive_group()
    scope.add_argument("--stale", action="store_true")
    scope.add_argument("--all-runtime-state", action="store_true")
    parser.add_argument(
        "--yes",
        action="store_true",
        help="Confirm execution of --all-runtime-state cleanup.",
    )
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(arguments)
    if args.all_runtime_state and not args.dry_run and not args.yes:
        message = (
            "--all-runtime-state requires --yes when cleanup is not a dry-run. "
            "No files were removed."
        )
        if args.json:
            print(json.dumps({"ok": False, "error": message, "network_used": False}))
        else:
            print(f"Cleanup refused: {message}")
        return 2
    if args.yes and not args.all_runtime_state:
        parser.error("--yes is only valid with --all-runtime-state")
    mode = (
        CleanupMode.ALL_RUNTIME_STATE
        if args.all_runtime_state
        else CleanupMode.STALE
        if args.stale
        else CleanupMode.NORMAL
    )
    try:
        config = resolve_configuration(config_file=args.config).config
        result = RuntimeCleanup(
            config,
            registry_path=args.registry_path,
        ).run(mode=mode, dry_run=args.dry_run)
    except (OSError, RuntimeError, ValueError) as exc:
        payload = {"ok": False, "error": str(exc), "network_used": False}
        print(json.dumps(payload, indent=2, sort_keys=True) if args.json else f"Cleanup error: {exc}")
        return 2
    payload = result.to_dict()
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        operation = "Cleanup plan" if result.dry_run else "Cleanup result"
        print(f"{operation} ({result.mode.value})")
        if not result.items:
            print("  No eligible AgentBus runtime artifacts found.")
        for item in result.items:
            location = f" at {item.location}" if item.location else ""
            print(
                f"  [{item.status.upper()}] {item.category}:{item.identifier}"
                f"{location} - {item.reason}"
            )
        print("Protected: " + ", ".join(result.protected_data))
    return 0 if result.ok else 1


def _logs_command(arguments: list[str]) -> int:
    from agentbus.product.logging import read_product_logs

    parser = argparse.ArgumentParser(prog="agentbus logs")
    parser.add_argument("--config")
    parser.add_argument(
        "--tail",
        nargs="?",
        const=100,
        default=100,
        type=int,
        help="Show the last N bounded records (default: 100).",
    )
    parser.add_argument("--run", help="Show records for one validated run ID.")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(arguments)
    try:
        config = resolve_configuration(config_file=args.config).config
        entries = read_product_logs(config, tail=args.tail, run_id=args.run)
    except (OSError, ValueError) as exc:
        payload = {"ok": False, "error": str(exc), "network_used": False}
        print(json.dumps(payload, indent=2, sort_keys=True) if args.json else f"Logs error: {exc}")
        return 2
    payload = {
        "ok": True,
        "tail": args.tail,
        "run_id": args.run,
        "entries": [entry.to_dict() for entry in entries],
        "truncated": len(entries) >= args.tail,
        "network_used": False,
    }
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    elif not entries:
        print("No matching AgentBus logs found.")
    else:
        for entry in entries:
            identifiers = "/".join(
                value
                for value in (entry.run_id, entry.task_id, entry.invocation_id)
                if value
            )
            context = f" {identifiers}" if identifiers else ""
            timestamp = entry.timestamp or "[time unavailable]"
            print(
                f"{timestamp} {entry.level.upper():<7} {entry.component}"
                f"{context}: {entry.message}"
            )
    return 0


def _support_bundle_command(arguments: list[str]) -> int:
    from agentbus.product.support import create_support_bundle

    parser = argparse.ArgumentParser(prog="agentbus support-bundle")
    parser.add_argument("--config")
    parser.add_argument("--output")
    parser.add_argument("--registry-path")
    parser.add_argument(
        "--include-run",
        metavar="RUN_ID",
        help="Include a bounded sanitized run-log tail after explicit consent.",
    )
    parser.add_argument(
        "--consent-source-derived",
        action="store_true",
        help="Consent to include potentially source-derived run metadata.",
    )
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(arguments)
    try:
        config = resolve_configuration(config_file=args.config).config
        result = create_support_bundle(
            config,
            output=args.output,
            include_run=args.include_run,
            consent_source_derived=args.consent_source_derived,
            registry_path=args.registry_path,
        )
    except (OSError, RuntimeError, ValueError) as exc:
        payload = {"ok": False, "error": str(exc), "network_used": False}
        print(
            json.dumps(payload, indent=2, sort_keys=True)
            if args.json
            else f"Support bundle error: {exc}"
        )
        return 2
    payload = result.to_dict()
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        print(f"Support bundle: {result.output}")
        print(f"Entries: {len(result.entries)}; bytes: {result.byte_size}")
        print(f"SHA-256: {result.sha256}")
        print(
            "Source-derived run metadata: "
            + ("included with consent" if result.source_derived_included else "excluded")
        )
    return 0


def _benchmark_command(arguments: list[str]) -> int:
    from agentbus.product.benchmark import (
        BENCHMARK_GROUPS,
        run_benchmark,
        write_benchmark_report,
    )
    from agentbus.product.synthetic import SYNTHETIC_SIZES

    parser = argparse.ArgumentParser(prog="agentbus benchmark")
    parser.add_argument(
        "group",
        nargs="?",
        choices=(*BENCHMARK_GROUPS, "all"),
        default="all",
    )
    parser.add_argument("--size", choices=tuple(SYNTHETIC_SIZES), default="small")
    parser.add_argument("--files", type=int)
    parser.add_argument("--iterations", type=int, default=5)
    parser.add_argument("--seed", type=int, default=2026)
    parser.add_argument("--output", help="Write an atomic JSON benchmark report.")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(arguments)
    try:
        report = run_benchmark(
            args.group,
            profile=args.size,
            file_count=args.files,
            iterations=args.iterations,
            seed=args.seed,
        )
        report_path = (
            write_benchmark_report(report, args.output) if args.output else None
        )
    except (OSError, RuntimeError, ValueError) as exc:
        payload = {"ok": False, "error": str(exc), "network_used": False}
        print(
            json.dumps(payload, indent=2, sort_keys=True)
            if args.json
            else f"Benchmark error: {exc}"
        )
        return 2
    payload = report.to_dict()
    payload["report_path"] = str(report_path) if report_path else None
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        repository = payload["repository"]
        print(
            "AgentBus benchmark "
            f"({args.group}, {repository['file_count']} generated files)"
        )
        for operation in report.operations:
            if operation.status == "skipped":
                print(f"  [SKIP] {operation.name}: {operation.detail}")
                continue
            print(
                f"  [{'OK' if operation.budget_passed else 'FAIL'}] "
                f"{operation.name}: median={operation.median_ms:.3f}ms "
                f"p95={operation.p95_ms:.3f}ms max={operation.max_ms:.3f}ms "
                f"n={operation.operation_count} budget={operation.budget_ms:.0f}ms"
            )
        print(f"Environment: {report.environment_fingerprint}")
        print(
            f"Peak memory: {report.peak_memory_bytes} / "
            f"{report.memory_budget_bytes} bytes"
        )
        if report_path:
            print(f"Report: {report_path}")
    return 0 if report.passed else 1


def _soak_command(arguments: list[str]) -> int:
    from agentbus.product.soak import run_soak

    parser = argparse.ArgumentParser(prog="agentbus soak")
    parser.add_argument("--duration", type=float, default=30.0, metavar="SECONDS")
    parser.add_argument("--runs", type=int, default=10)
    parser.add_argument("--parallelism", type=int, default=2)
    parser.add_argument("--seed", type=int, default=2026)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(arguments)
    try:
        report = run_soak(
            duration_seconds=args.duration,
            runs=args.runs,
            parallelism=args.parallelism,
            seed=args.seed,
        )
    except (OSError, RuntimeError, ValueError) as exc:
        payload = {"ok": False, "error": str(exc), "network_used": False}
        print(
            json.dumps(payload, indent=2, sort_keys=True)
            if args.json
            else f"Soak error: {exc}"
        )
        return 2
    payload = report.to_dict()
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        print(
            f"AgentBus offline soak: {report.completed_runs}/{report.requested_runs} "
            f"cycles in {report.duration_seconds:.3f}s"
        )
        print(
            f"  successful={report.successful_runs} "
            f"cancelled={report.intentional_cancellations} failed={report.failed_runs}"
        )
        print(
            f"  events={report.event_count} gaps={report.event_gap_count} "
            f"stale_leases={report.stale_lease_count}"
        )
        print(
            f"  leaked_worktrees={report.leaked_worktree_count} "
            f"leaked_processes={report.leaked_process_count} "
            f"cleanup_failures={report.failed_cleanup_count}"
        )
        print(
            f"  memory_growth={report.memory_growth_bytes} "
            f"budget={report.memory_budget_bytes} bytes"
        )
        if report.stopped_by_duration:
            print("  duration limit stopped scheduling additional cycles")
    return 0 if report.ok else 1


def _release_check_command(arguments: list[str]) -> int:
    from agentbus.product.release_check import run_release_check

    parser = argparse.ArgumentParser(prog="agentbus release-check")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--fast", action="store_true")
    mode.add_argument("--full", action="store_true")
    parser.add_argument("--root", default=".")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(arguments)
    selected = "full" if args.full else "fast"
    try:
        report = run_release_check(mode=selected, root=args.root)
    except (OSError, RuntimeError, ValueError) as exc:
        payload = {"ok": False, "error": str(exc), "network_used": False}
        print(
            json.dumps(payload, indent=2, sort_keys=True)
            if args.json
            else f"Release-check error: {exc}"
        )
        return 2
    if args.json:
        print(json.dumps(report.to_dict(), indent=2, sort_keys=True))
    else:
        print(
            f"AgentBus {report.version} release-check ({report.mode}): "
            f"{'PASS' if report.ok else 'FAIL'}"
        )
        for gate in report.gates:
            print(f"  [{gate.status.value.upper()}] {gate.title}: {gate.summary}")
        print("No artifacts were published and no network provider was used.")
    return 0 if report.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
