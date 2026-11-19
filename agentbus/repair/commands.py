from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from pydantic import ValidationError

from agentbus.configuration import resolve_configuration
from agentbus.repair.demo import demo_snapshot
from agentbus.repair.models import RepairPackage, RepairSnapshot
from agentbus.repair.service import RepairError, RepairService, verify_package


def repair_command(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="syndra repair", description="Inspect customer consequences of a purchase-to-access bug fix.")
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--workspace")
    common.add_argument("--config")
    common.add_argument("--state-dir")
    common.add_argument("--json", action="store_true")
    commands = parser.add_subparsers(dest="action", required=True)
    commands.add_parser("demo", parents=[common], help="Create an owned synthetic application with 100 incident records.")
    imported = commands.add_parser("import", parents=[common], help="Analyze an exported snapshot without changing its source.")
    imported.add_argument("path")
    commands.add_parser("cases", parents=[common])
    for name in ("show", "preview", "results"):
        commands.add_parser(name, parents=[common]).add_argument("case_id")
    applied = commands.add_parser("apply", parents=[common], help="Apply reviewed corrections to an owned local demo only.")
    applied.add_argument("case_id")
    applied.add_argument("--preview-id", required=True)
    applied.add_argument("--fingerprint", required=True)
    applied.add_argument("--request-id", required=True)
    applied.add_argument("--purchase", action="append", required=True)
    applied.add_argument("--confirm", required=True, choices=["APPLY_SELECTED_CORRECTIONS"])
    result = commands.add_parser("result", parents=[common], help="Look up a saved request after an interrupted response.")
    result.add_argument("case_id")
    result.add_argument("request_id")
    exported = commands.add_parser("export", parents=[common])
    exported.add_argument("case_id")
    exported.add_argument("--preview-id", required=True)
    verified = commands.add_parser("verify", parents=[common], help="Check package consistency; this does not authenticate its source.")
    verified.add_argument("path")
    connection_add = commands.add_parser("connection-add", parents=[common], help="Register an owner-reviewed destination and credential reference locally; never a secret value.")
    connection_add.add_argument("path")
    commands.add_parser("connections", parents=[common])
    connection_check = commands.add_parser("connection-check", parents=[common])
    connection_check.add_argument("connection_id")
    connected_preview = commands.add_parser("connected-preview", parents=[common], help="Rehydrate candidate IDs from connected authorities; CSV is not write authority.")
    connected_preview.add_argument("case_id")
    connected_preview.add_argument("--connection", required=True)
    connected_preview.add_argument("--policy", required=True)
    connected_preview.add_argument("--csv", help="Optional UTF-8 CSV with exactly one purchase_id column, up to 10000 rows.")
    connected_plan = commands.add_parser("connected-plan", parents=[common])
    connected_plan.add_argument("case_id")
    connected_plan.add_argument("--scan-id", required=True)
    connected_plan.add_argument("--purchase", action="append", required=True)
    connected_plan.add_argument("--trust-workspace", action="store_true")
    connected_approve = commands.add_parser("connected-approve", parents=[common])
    connected_approve.add_argument("plan_id")
    connected_approve.add_argument("--digest", required=True, help="The exact reviewed plan digest, including its selected set and write cap.")
    purge = commands.add_parser("connected-purge-evidence", parents=[common], help="Purge only unreferenced authoritative scan data; preserve all plans, operation intents and receipts.")
    purge.add_argument("case_id")
    purge.add_argument("--confirm", choices=["PURGE_UNREFERENCED_EVIDENCE"], required=True)
    for name in ("connected-show", "connected-execute", "connected-stop", "connected-reconcile", "connected-pause", "connected-continue"):
        action_parser = commands.add_parser(name, parents=[common])
        action_parser.add_argument("plan_id")
        if name == "connected-execute":
            action_parser.add_argument("--retry-unknown", action="store_true", help="Explicit bounded same-operation retry after receipt lookup, never a new key.")
    conformance = commands.add_parser("conformance", parents=[common], help="Read-only deployed contract check; does not certify all application writer hooks.")
    conformance.add_argument("connection_id")
    conformance.add_argument("--purchase", action="append", default=[])
    args = parser.parse_args(argv)
    try:
        if args.action == "verify":
            valid = verify_package(RepairPackage.model_validate(_read_json(args.path, limit=32_000_000)))
            print(json.dumps({"valid": valid, "scope": "Package consistency only; source truth and authorship are not authenticated."}))
            return 0 if valid else 1
        config = resolve_configuration(config_file=args.config, workspace=args.workspace,
                                       cli_overrides={"workspace_dir": args.workspace, "state_dir": args.state_dir}).config
        service = RepairService(config.state_directory_path / "customer-repair")
        if args.action.startswith("connected-") or args.action in {"connections", "connection-add", "connection-check", "conformance"}:
            from agentbus.execution.state_store import StateStore
            from agentbus.repair.live.contracts import ConnectionManifest, RepairPolicy
            from agentbus.repair.live.service import ConnectedRepairService
            connected = ConnectedRepairService(service, config.workspace_path, StateStore(config.state_database_path))
            if args.action == "connection-add":
                value = connected.configure(ConnectionManifest.model_validate(_read_json(args.path)))
            elif args.action == "connections":
                value = {"connections": [item.model_dump(mode="json") for item in connected.connections()]}
            elif args.action == "connection-check":
                value = connected.check(args.connection_id)
            elif args.action == "connected-preview":
                policy = RepairPolicy.model_validate(_read_json(args.policy))
                value = connected.refresh_ids(args.case_id, _read_candidates(args.csv), args.connection, policy) if args.csv else connected.refresh(args.case_id, args.connection, policy)
            elif args.action == "connected-plan":
                value = connected.create_plan(args.case_id, args.scan_id, args.purchase, trusted=args.trust_workspace)
            elif args.action == "connected-purge-evidence":
                value = connected.purge_scan_evidence(args.case_id)
            elif args.action == "connected-approve":
                value = connected.approve(args.plan_id, args.digest)
            elif args.action == "connected-show":
                value = connected.plan(args.plan_id)
            elif args.action == "connected-execute":
                value = connected.execute(args.plan_id, retry_unknown=args.retry_unknown)
            elif args.action == "connected-stop":
                value = connected.stop(args.plan_id)
            elif args.action == "connected-reconcile":
                value = connected.reconcile(args.plan_id)
            elif args.action in {"connected-pause", "connected-continue"}:
                value = connected.pause(args.plan_id, resume=args.action == "connected-continue")
            else:
                from agentbus.repair.integration.conformance import check_conformance
                value = check_conformance(connected.manifest(args.connection_id), args.purchase)
        elif args.action == "demo":
            value = service.create(demo_snapshot(), demo=True)
        elif args.action == "import":
            data = _read_json(args.path)
            if isinstance(data, dict) and set(data) == {"snapshot"}:
                data = data["snapshot"]
            value = service.create(RepairSnapshot.model_validate(data))
        elif args.action == "cases":
            value = {"cases": [case.model_dump(mode="json") for case in service.list()]}
        elif args.action == "show":
            value = service.get(args.case_id)
        elif args.action == "preview":
            value = service.preview(args.case_id)
        elif args.action == "apply":
            value = service.apply(args.case_id, preview_id=args.preview_id, preview_fingerprint=args.fingerprint,
                                  request_id=args.request_id, purchase_ids=args.purchase)
        elif args.action == "result":
            value = service.batch(args.case_id, args.request_id)
        elif args.action == "results":
            value = {"batches": [batch.model_dump(mode="json") for batch in service.batches(args.case_id)]}
        else:
            value = service.export(args.case_id, args.preview_id)
        payload = value.model_dump(mode="json") if hasattr(value, "model_dump") else value
        print(json.dumps(payload, indent=2, ensure_ascii=True))
        return 0
    except RepairError as exc:
        print(json.dumps({"error": {"code": exc.code, "message": str(exc)}}), file=sys.stderr)
        return 1
    except (ValidationError, ValueError, OSError, RecursionError):
        print(json.dumps({"error": {"code": "invalid_repair_input", "message": "The repair input is invalid, unsupported, unreadable, or exceeds its limit. Use the documented snapshot format."}}), file=sys.stderr)
        return 1


def _read_json(path, *, limit=750_000):
    with Path(path).open("rb") as source:
        value = source.read(limit + 1)
    if len(value) > limit:
        raise ValueError("Repair input exceeds its bound.")
    return json.loads(value)


def _read_candidates(path):
    import csv
    from pydantic import TypeAdapter
    from agentbus.repair.models import Identifier
    identifiers = []
    with Path(path).open(encoding="utf-8-sig", newline="") as source:
        reader = csv.DictReader(source)
        if reader.fieldnames != ["purchase_id"]:
            raise ValueError("Use exactly one purchase_id column.")
        for row in reader:
            if set(row) != {"purchase_id"} or len(identifiers) >= 10000:
                raise ValueError("Candidate CSV exceeds its bound.")
            identifiers.append(TypeAdapter(Identifier).validate_python(row["purchase_id"]))
    if not identifiers or len(set(identifiers)) != len(identifiers):
        raise ValueError("Candidate IDs must be nonempty and unique.")
    return identifiers
