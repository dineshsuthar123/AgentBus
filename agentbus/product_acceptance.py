from __future__ import annotations

import argparse
import json
from pathlib import Path

from agentbus.product.acceptance import (
    AcceptanceKind,
    installed_origin_payload,
    run_clean_install_acceptance,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m agentbus.product_acceptance",
        description="Run the offline AgentBus clean-install product acceptance.",
    )
    parser.add_argument("--root", default=".")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--verify-install", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if args.verify_install:
        try:
            payload = installed_origin_payload(Path(args.verify_install))
        except (OSError, RuntimeError, ValueError) as exc:
            payload = {"ok": False, "error": type(exc).__name__}
        print(json.dumps(payload, indent=2, sort_keys=True))
        return 0 if payload.get("ok") else 1

    report = run_clean_install_acceptance(
        AcceptanceKind.PRODUCT,
        root=args.root,
    )
    payload = report.to_dict()
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        print(
            "AgentBus clean-install product acceptance: "
            + ("PASS" if report.ok else "FAIL")
        )
        for step in report.steps:
            print(f"  [{step.status.upper()}] {step.name}: {step.detail}")
        if report.error:
            print("Failure: " + report.error)
        print("No package was published and no live provider was called.")
    return 0 if report.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
