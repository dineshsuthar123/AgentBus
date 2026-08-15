from __future__ import annotations

import argparse
import json

from agentbus.product.acceptance import AcceptanceKind, run_clean_install_acceptance


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m agentbus.rc_acceptance",
        description="Run the entirely local AgentBus release-candidate acceptance.",
    )
    parser.add_argument("--root", default=".")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    report = run_clean_install_acceptance(AcceptanceKind.RC, root=args.root)
    payload = report.to_dict()
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        print(
            "AgentBus release-candidate acceptance: "
            + ("PASS" if report.ok else "FAIL")
        )
        for step in report.steps:
            print(f"  [{step.status.upper()}] {step.name}: {step.detail}")
        if report.error:
            print("Failure: " + report.error)
        print(
            "No package was published, no live provider was called, and no "
            "external security target was contacted."
        )
    return 0 if report.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
