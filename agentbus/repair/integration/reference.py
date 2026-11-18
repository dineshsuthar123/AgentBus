from __future__ import annotations

import argparse
from datetime import timedelta
import json
import os

from pydantic import SecretStr

from agentbus.repair.integration.postgres import PostgresApplication
from agentbus.repair.integration.server import Principal, create_correction_app
from agentbus.repair.live.contracts import Identity, PurchaseState, now


def fixture(identity: Identity, count=100):
    at = now()
    for index in range(count):
        yield PurchaseState(identity=identity, purchase_id=f"purchase-{index:05}", payment_id=f"payment-{index:05}",
                            account_id=f"account-{index:05}" if index % 100 < 90 else None, feature="workspace",
                            currency="usd", amount_minor=2000, refunded_minor=2000 if 80 <= index % 100 < 90 else 0,
                            payment_state="paid", purchased_at=at - timedelta(days=1), period_start=at - timedelta(days=2),
                            period_end=at + timedelta(days=28), access_present=60 <= index % 100 < 80,
                            mapping_verified=index % 100 < 90, history_complete=True, disputed=False,
                            pending_payment_change=False, cancellation="none", restriction="none", revision=1,
                            observed_at=at, evidence_ids=[f"evidence-{index:05}"])


def main(argv=None):
    parser = argparse.ArgumentParser(description="Separate PostgreSQL reference application; not the Syndra daemon.")
    parser.add_argument("action", choices=["migrate", "seed-disposable", "serve", "contract"])
    parser.add_argument("--port", type=int, default=8767)
    parser.add_argument("--records", type=int, default=100)
    parser.add_argument("--identity", required=True, help="Application-owned identity JSON file; no credentials.")
    parser.add_argument("--no-outbox", action="store_true", help="Disposable propagation-failure exercise.")
    args = parser.parse_args(argv)
    from pathlib import Path
    identity = Identity.model_validate_json(Path(args.identity).read_text())
    application = PostgresApplication(os.environ["SYNDRA_REPAIR_DATABASE_URL"], identity)
    if args.action == "migrate":
        application.migrate()
    elif args.action == "seed-disposable":
        if identity.environment != "sandbox" or os.environ.get("SYNDRA_REPAIR_DISPOSABLE") != "1" or not 1 <= args.records <= 10000:
            parser.error("Seed requires an explicitly disposable sandbox and 1-10000 records.")
        application.seed(fixture(identity, args.records))
        while application.consume_outbox():
            pass
    else:
        principal = Principal("syndra-service", SecretStr(os.environ["SYNDRA_REPAIR_APPLICATION_TOKEN"]),
                              frozenset({"read", "approve", "repair", "verify", "revoke"}))
        app = create_correction_app(application, [principal], process_outbox=not args.no_outbox)
        if args.action == "contract":
            print(json.dumps(app.openapi(), indent=2))
        else:
            import uvicorn
            uvicorn.run(app, host="127.0.0.1", port=args.port, access_log=False, log_level="warning")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
