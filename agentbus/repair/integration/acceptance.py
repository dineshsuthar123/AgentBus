"""Owned PostgreSQL + separate target + installed Studio acceptance environment."""
from __future__ import annotations

import argparse
from datetime import timedelta
import json
import os
from pathlib import Path
import queue
import secrets
import socket
import subprocess
import sys
import threading


def _ready(process):
    result = queue.Queue()
    threading.Thread(target=lambda: result.put(process.stdout.readline(32769)), daemon=True).start()
    try:
        line = result.get(timeout=40)
        if len(line) > 32768:
            raise ValueError
        return json.loads(line)
    except (queue.Empty, ValueError):
        raise RuntimeError("The owned service did not provide a valid bounded readiness handshake.") from None


def _target(identity_file):
    from pydantic import SecretStr
    from agentbus.repair.integration.postgres import PostgresApplication
    from agentbus.repair.integration.reference import fixture
    from agentbus.repair.integration.server import Principal, create_correction_app
    from agentbus.repair.live.contracts import Identity
    import uvicorn
    identity = Identity.model_validate_json(Path(identity_file).read_text())
    if identity.environment != "sandbox" or os.environ.get("SYNDRA_REPAIR_DISPOSABLE") != "1":
        raise RuntimeError("Acceptance may seed only an explicitly owned disposable sandbox.")
    application = PostgresApplication(os.environ["SYNDRA_REPAIR_DATABASE_URL"], identity)
    application.migrate()
    application.seed(fixture(identity))
    while application.consume_outbox():
        pass
    app = create_correction_app(application, [Principal("acceptance", SecretStr(os.environ["SYNDRA_REPAIR_APPLICATION_TOKEN"]),
        frozenset({"read", "approve", "repair", "verify", "revoke"}))])
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    class Server(uvicorn.Server):
        async def startup(self, *args, **kwargs):
            await super().startup(*args, **kwargs)
            print(json.dumps({"port": port, "mode": "disposable_sandbox"}), flush=True)
    Server(uvicorn.Config(app, log_level="critical", access_log=False)).run(sockets=[sock])


def _prepare(workspace, manifest_file):
    from agentbus.config import AgentBusConfig
    from agentbus.execution.state_store import StateStore
    from agentbus.repair.integration.reference import fixture
    from agentbus.repair.live.contracts import ConnectionManifest, now
    from agentbus.repair.live.service import ConnectedRepairService
    from agentbus.repair.models import FixReference, PurchaseEvidence, RepairSnapshot
    from agentbus.repair.service import RepairService
    manifest = ConnectionManifest.model_validate_json(Path(manifest_file).read_text())
    at = now()
    records = [PurchaseEvidence(purchase_id=row.purchase_id, payment_id=row.payment_id, tenant_id=row.identity.tenant,
        account_id=row.account_id, entitlement=row.feature, paid_at=row.purchased_at,
        payment_state="refunded" if row.refunded_minor else "paid", access_present=row.access_present,
        mapping_verified=row.mapping_verified, history_complete=row.history_complete, evidence_ids=row.evidence_ids, revision=row.revision)
        for row in fixture(manifest.identity)]
    snapshot = RepairSnapshot(title="Purchase access incident / connected PostgreSQL", incident_start=at - timedelta(days=2),
        incident_end=at - timedelta(hours=12), observed_at=at, coverage_complete=True,
        fix=FixReference(before_revision="a" * 40, after_revision="b" * 40, review_confirmed=True), purchases=records)
    config = AgentBusConfig(workspace_dir=workspace, state_dir=str(Path(workspace) / ".syndra"), provider_name="deterministic")
    base = RepairService(config.state_directory_path / "customer-repair")
    case = base.create(snapshot)
    service = ConnectedRepairService(base, config.workspace_path, StateStore(config.state_database_path))
    service.configure(manifest)
    print(json.dumps({"case_id": case.summary.case_id, "module": __file__, "python": sys.version.split()[0]}))


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--target")
    parser.add_argument("--prepare")
    parser.add_argument("--manifest")
    parser.add_argument("--python", default=sys.executable)
    parser.add_argument("--output", default=".tmp/production-repair-acceptance")
    parser.add_argument("--browser-script", default="studio/scripts/connected-repair-smoke.mjs")
    args = parser.parse_args(argv)
    if args.target:
        _target(args.target)
        return 0
    if args.prepare:
        _prepare(args.prepare, args.manifest)
        return 0
    from agentbus.product.release_check import _offline_environment
    from agentbus.repair.integration.testing import disposable_postgres
    from agentbus.repair.live.contracts import ConnectionManifest, Identity
    output = Path(args.output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    workspace = output / "owned-workspace"
    workspace.mkdir()
    for command in (["git", "init"], ["git", "config", "user.name", "Syndra Acceptance"], ["git", "config", "user.email", "offline@example.invalid"]):
        subprocess.run(command, cwd=workspace, check=True, capture_output=True, shell=False)
    (workspace / ".gitignore").write_text(".syndra/\n")
    subprocess.run(["git", "add", ".gitignore"], cwd=workspace, check=True, capture_output=True, shell=False)
    subprocess.run(["git", "commit", "-m", "Initialize disposable application integration workspace"], cwd=workspace, check=True, capture_output=True, shell=False)
    identity = Identity(application="owned-reference", tenant="acceptance", environment="sandbox", configuration_revision="v1")
    identity_file = output / "identity.json"
    identity_file.write_text(identity.model_dump_json())
    # A POSIX virtual environment can expose Python through a symlink. Its
    # invocation path must stay in the virtual environment to load the wheel.
    python = str(Path(args.python).expanduser().absolute())
    environment = _offline_environment()
    environment["PYTHONPATH"] = ""
    environment["SYNDRA_REPAIR_APPLICATION_TOKEN"] = secrets.token_urlsafe(40)
    environment["SYNDRA_REPAIR_DISPOSABLE"] = "1"
    flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    target = daemon = None
    with disposable_postgres(output) as (dsn, _directory):
        environment["SYNDRA_REPAIR_DATABASE_URL"] = dsn
        try:
            target = subprocess.Popen([python, "-m", "agentbus.repair.integration.acceptance", "--target", str(identity_file)],
                cwd=workspace, env=environment, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, creationflags=flags, shell=False)
            target_ready = _ready(target)
            manifest = ConnectionManifest(connection_id="owned-reference", identity=identity,
                destination=f"http://127.0.0.1:{target_ready['port']}/syndra-correction/v1", approved_addresses=["127.0.0.1"],
                credential_ref="env:SYNDRA_REPAIR_APPLICATION_TOKEN", revision=1, transport="disposable_loopback",
                permissions=["read", "approve", "repair", "verify", "revoke"])
            manifest_file = output / "manifest.json"
            manifest_file.write_text(manifest.model_dump_json())
            prepared = subprocess.run([python, "-m", "agentbus.repair.integration.acceptance", "--prepare", str(workspace), "--manifest", str(manifest_file)],
                                     cwd=workspace, env=environment, capture_output=True, text=True, shell=False, creationflags=flags, timeout=30)
            if prepared.returncode:
                raise RuntimeError("Installed integration preparation failed; no production target was involved.")
            prepared_data = json.loads(prepared.stdout)
            code = "from agentbus.config import AgentBusConfig; from agentbus.control.server import serve; import sys; c=AgentBusConfig(workspace_dir=sys.argv[1],provider_name='deterministic',enable_provider_fallback=False,state_dir=sys.argv[1]+'/.syndra'); serve(config=c,json_ready=True,port=0,registry_path=sys.argv[1]+'/.syndra/daemons.json',idle_timeout=300,log_level='error')"
            daemon = subprocess.Popen([python, "-c", code, str(workspace)], cwd=workspace, env=environment, stdout=subprocess.PIPE,
                                      stderr=subprocess.DEVNULL, text=True, shell=False, creationflags=flags)
            ready = _ready(daemon)
            import urllib.request
            origin = f"http://127.0.0.1:{ready['port']}"
            request = urllib.request.Request(origin + "/api/v1/studio/launch", data=b"{}", headers={"Authorization": "Bearer " + ready["bearer_token"], "Content-Type": "application/json"})
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            launch = json.load(opener.open(request, timeout=10))
            payload = {"launch": launch["launch_url"], "origin": origin, "case_id": prepared_data["case_id"], "output": str(output)}
            browser = subprocess.run(["node", str(Path(args.browser_script).resolve())], input=json.dumps(payload), text=True,
                                     env=environment, capture_output=True, shell=False, creationflags=flags, timeout=120)
            if browser.returncode:
                (output / "browser-error.log").write_text(browser.stderr[-6000:])
                raise RuntimeError("Installed Studio acceptance failed; inspect the owned redacted browser-error.log.")
            access = urllib.request.Request(f"http://127.0.0.1:{target_ready['port']}/application/access/purchase-00000",
                                           headers={"Authorization": "Bearer " + environment["SYNDRA_REPAIR_APPLICATION_TOKEN"]})
            normal_access = json.load(opener.open(access, timeout=10))
            assert normal_access["allowed"] is True and normal_access["propagated"] is True
            status = subprocess.run(["git", "status", "--porcelain"], cwd=workspace, capture_output=True, text=True, shell=False, check=True)
            assert status.stdout == ""
            report = {"ok": True, "installed_python": prepared_data["python"], "installed_module": prepared_data["module"],
                      "separate_target": True, "postgres": True, "normal_access": normal_access, "git_clean": True,
                      "provider_calls": 0, "production_mutations": 0}
            (output / "environment.json").write_text(json.dumps(report, indent=2))
            print(json.dumps(report, indent=2))
        finally:
            for process in (daemon, target):
                if process and process.poll() is None:
                    process.terminate()
                    process.wait(timeout=20)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
