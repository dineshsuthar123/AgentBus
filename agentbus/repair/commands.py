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
    args = parser.parse_args(argv)
    try:
        if args.action == "verify":
            valid = verify_package(RepairPackage.model_validate(_read_json(args.path, limit=32_000_000)))
            print(json.dumps({"valid": valid, "scope": "Package consistency only; source truth and authorship are not authenticated."}))
            return 0 if valid else 1
        config = resolve_configuration(config_file=args.config, workspace=args.workspace,
                                       cli_overrides={"workspace_dir": args.workspace, "state_dir": args.state_dir}).config
        service = RepairService(config.state_directory_path / "customer-repair")
        if args.action == "demo":
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
