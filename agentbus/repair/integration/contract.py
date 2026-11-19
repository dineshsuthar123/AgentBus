"""Generate the shipped application API contract from the same server router."""
import argparse
import json
from pathlib import Path


def contract():
    from agentbus.repair.integration.postgres import PostgresApplication
    from agentbus.repair.integration.server import create_correction_app
    from agentbus.repair.live.contracts import Identity
    application = PostgresApplication("", Identity(application="schema", tenant="schema", environment="sandbox", configuration_revision="v1"))
    schema = create_correction_app(application, [], process_outbox=False).openapi()
    schema["servers"] = [{"url": "https://application.example.invalid"}]
    return json.dumps(schema, indent=2, sort_keys=True) + "\n"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default=str(Path(__file__).with_name("correction-v1.openapi.json")))
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    content, destination = contract(), Path(args.output)
    if args.check:
        if not destination.exists() or destination.read_text(encoding="utf-8") != content:
            raise SystemExit("Application correction API contract is stale.")
    else:
        destination.write_text(content, encoding="utf-8")


if __name__ == "__main__":
    main()
