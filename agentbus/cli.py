from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from agentbus import __version__
from agentbus.bootstrap import BootstrapError, initialize
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
    "doctor",
    "serve",
    "daemon",
    "control-schema",
    "worktrees",
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
    if command == "evaluate":
        from agentbus.eval import main as evaluation_main

        return evaluation_main(rest)
    if command == "config":
        return _config_command(rest)
    if command == "init":
        return _init_command(rest)
    if command == "doctor":
        return _doctor_command(rest)
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
        "doctor": "Run offline environment diagnostics.",
        "serve": "Start the authenticated local control-plane daemon.",
        "daemon": "Inspect or safely manage local daemons.",
        "control-schema": "Export generated control-protocol artifacts.",
        "worktrees": "Inspect or explicitly clean owned worktrees.",
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
    check.add_argument("provider", choices=["ollama", "azure"])
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
    for name in ("show", "validate", "paths"):
        command = commands.add_parser(name)
        command.add_argument("--config")
        command.add_argument("--json", action="store_true")
    args = parser.parse_args(arguments)
    try:
        resolved = resolve_configuration(config_file=args.config)
        if args.config_command == "show":
            payload = {
                "valid": True,
                "config_file": str(resolved.config_file) if resolved.config_file else None,
                "values": resolved.safe_values(),
            }
        elif args.config_command == "paths":
            payload = configuration_paths(resolved)
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
    elif args.config_command == "paths":
        for name, value in payload.items():
            print(f"{name}: {value or '[not configured]'}")
    else:
        print("PASS: " + payload["message"])
    return 0


def _init_command(arguments: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="agentbus init")
    parser.add_argument("--local", action="store_true")
    parser.add_argument("--provider", choices=["ollama", "azure"], default="ollama")
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
    parser.add_argument("--live-provider", choices=["ollama", "azure"])
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(arguments)
    try:
        resolved = resolve_configuration(
            config_file=args.config,
            cli_overrides={"workspace_dir": args.workspace},
        )
        report = run_doctor(resolved.config, live_provider=args.live_provider)
    except (OSError, ValueError) as exc:
        payload = {"status": "FAIL", "error": str(exc), "network_used": False}
        print(json.dumps(payload, indent=2, sort_keys=True) if args.json else f"Doctor error: {exc}")
        return 2
    print(json.dumps(report.to_dict(), indent=2, sort_keys=True) if args.json else render_doctor(report))
    return 1 if report.status == CheckStatus.FAIL else 0


def _serve_command(arguments: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="agentbus serve")
    parser.add_argument("--config")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=0)
    parser.add_argument("--json-ready", action="store_true")
    parser.add_argument("--idle-timeout", type=float, default=86_400)
    parser.add_argument("--foreground", action="store_true")
    parser.add_argument("--registry-path")
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
        config = resolve_configuration(config_file=args.config).config
        from agentbus.control.server import serve

        return serve(
            config=config,
            host=args.host,
            port=args.port,
            json_ready=args.json_ready,
            idle_timeout=args.idle_timeout,
            registry_path=args.registry_path,
            log_level=args.log_level,
        )
    except (OSError, RuntimeError, ValueError) as exc:
        stream = sys.stderr if args.json_ready else sys.stdout
        print(f"Control-plane error: {exc}", file=stream)
        return 2


def _daemon_command(arguments: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="agentbus daemon")
    parser.add_argument("--registry-path")
    parser.add_argument("--json", action="store_true")
    commands = parser.add_subparsers(dest="daemon_command", required=True)
    commands.add_parser("status")
    stop = commands.add_parser("stop")
    stop.add_argument("daemon_id", nargs="?")
    commands.add_parser("registry")
    commands.add_parser("cleanup-stale")
    args = parser.parse_args(arguments)
    from agentbus.control.registry import (
        DaemonRegistry,
        process_matches,
        terminate_registered_daemon,
    )

    registry = DaemonRegistry(args.registry_path)
    if args.daemon_command == "cleanup-stale":
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
                    **item.model_dump(mode="json", exclude_none=True),
                    "process_matches": process_matches(item),
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
            terminate_registered_daemon(registry, daemon_id)
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
            state = "active" if item["process_matches"] else "stale"
            print(
                f"{item['daemon_id']}  {state}  pid={item['pid']}  "
                f"{item['host']}:{item['port']}"
            )
    elif args.daemon_command == "registry":
        print(json.dumps(payload, indent=2, sort_keys=True))
    elif args.daemon_command == "cleanup-stale":
        print(f"Removed stale daemon registrations: {payload['count']}")
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
    if args.json:
        print(json.dumps({"package": "agentbus", "version": __version__}, sort_keys=True))
    else:
        print(f"agentbus {__version__}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
