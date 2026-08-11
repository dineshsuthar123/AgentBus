from __future__ import annotations

import json
import os
import re
import sys
import uuid
from pathlib import Path

from agentbus import __version__
from agentbus.config import AgentBusConfig
from agentbus.control.app import ControlAppContext, create_app
from agentbus.control.authentication import (
    generate_session_token,
    validate_loopback_host,
)
from agentbus.control.lifecycle import (
    DaemonHeartbeat,
    IdleShutdownMonitor,
    bind_loopback_socket,
)
from agentbus.control.models import DaemonRegistryEntry, ReadyHandshake
from agentbus.control.replay_supervisor import BackgroundReplaySupervisor
from agentbus.control.registry import (
    DaemonRegistry,
    executable_identity,
    process_start_identity,
    utc_now,
)
from agentbus.control.services import ControlQueryService
from agentbus.control.supervisor import AgentBusRunBackend, BackgroundRunSupervisor
from agentbus.execution.state_store import StateStore


_DAEMON_ID = re.compile(r"^[a-f0-9]{32}$")


def serve(
    *,
    config: AgentBusConfig,
    host: str = "127.0.0.1",
    port: int = 0,
    json_ready: bool = False,
    idle_timeout: float = 86_400,
    registry_path: str | Path | None = None,
    daemon_id: str | None = None,
    log_level: str = "warning",
) -> int:
    _startup_stage("serve-entered")
    normalized_host = validate_loopback_host(host)
    try:
        import uvicorn
    except ImportError as exc:
        raise RuntimeError(
            'The control plane requires optional dependencies. Install "agentbus[ide]".'
        ) from exc
    _startup_stage("uvicorn-imported")

    listener = bind_loopback_socket(normalized_host, port)
    _startup_stage("listener-bound")
    actual_port = int(listener.getsockname()[1])
    daemon_id = _validated_daemon_id(daemon_id)
    token = generate_session_token()
    started_at = utc_now()
    registry = DaemonRegistry(registry_path)
    registry.cleanup_stale()
    store = StateStore(config.state_database_path)
    _startup_stage("state-ready")
    query = ControlQueryService(config, store)
    backend = AgentBusRunBackend(
        config,
        store,
        reconcile_interrupted=True,
    )
    supervisor = BackgroundRunSupervisor(backend)
    replay_supervisor = BackgroundReplaySupervisor(
        query,
        reconcile_interrupted=True,
    )
    _startup_stage("services-ready")
    context = ControlAppContext(
        daemon_id=daemon_id,
        host=normalized_host,
        port=actual_port,
        started_at=started_at,
        state_database=str(config.state_database_path.resolve()),
    )
    app = create_app(
        token=token,
        query_service=query,
        supervisor=supervisor,
        replay_supervisor=replay_supervisor,
        context=context,
    )
    _startup_stage("application-ready")
    entry = DaemonRegistryEntry(
        daemon_id=daemon_id,
        pid=os.getpid(),
        executable=executable_identity(),
        process_start_identity=process_start_identity(),
        host=normalized_host,
        port=actual_port,
        agentbus_version=__version__,
        started_at=started_at,
        heartbeat_at=started_at,
        state_database=context.state_database,
        registry_path=str(registry.path),
        idle_timeout_seconds=idle_timeout,
        log_path=str(config.state_database_path.resolve().parent / "logs" / "daemon.log"),
    )
    registry.register(entry)
    _startup_stage("registry-ready")
    heartbeat = DaemonHeartbeat(registry, daemon_id)
    server = uvicorn.Server(
        uvicorn.Config(
            app,
            host=normalized_host,
            port=actual_port,
            log_level=log_level,
            access_log=False,
            server_header=False,
            date_header=False,
        )
    )
    idle = IdleShutdownMonitor(
        server,
        app,
        idle_timeout,
        has_active_work=lambda: (
            supervisor.has_active_runs()
            or replay_supervisor.has_active_replays()
        ),
    )
    try:
        heartbeat.start()
        idle.start()
        if json_ready:
            handshake = ReadyHandshake(
                host=normalized_host,
                port=actual_port,
                daemon_id=daemon_id,
                pid=os.getpid(),
                agentbus_version=__version__,
                registry_path=str(registry.path),
                bearer_token=token,
            )
            sys.stdout.write(
                json.dumps(
                    handshake.model_dump(mode="json"),
                    ensure_ascii=True,
                    separators=(",", ":"),
                    sort_keys=True,
                )
                + "\n"
            )
            sys.stdout.flush()
        server.run(sockets=[listener])
        return 0
    finally:
        idle.stop()
        heartbeat.stop()
        replay_supervisor.shutdown(wait=True)
        supervisor.shutdown(wait=True)
        registry.remove(daemon_id)
        listener.close()


def _startup_stage(stage: str) -> None:
    if os.environ.get("AGENTBUS_DAEMON_STARTUP_DIAGNOSTICS") == "1":
        sys.stderr.write(f"agentbus-daemon-stage:{stage}\n")
        sys.stderr.flush()


def _validated_daemon_id(value: str | None) -> str:
    if value is None:
        return uuid.uuid4().hex
    if not _DAEMON_ID.fullmatch(value):
        raise ValueError("Daemon startup ID must be a lowercase UUID hex value.")
    return value


def main() -> int:
    return serve(config=AgentBusConfig.from_env(), json_ready=True)


if __name__ == "__main__":
    raise SystemExit(main())
