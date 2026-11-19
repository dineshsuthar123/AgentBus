"""Disposable local PostgreSQL process for integration verification, never production."""
from contextlib import contextmanager
import json
import os
from pathlib import Path
import secrets
import shutil
import socket
import subprocess
import uuid

from agentbus.security.redaction import redact_diagnostic_text


def _available_loopback_port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return listener.getsockname()[1]


def _owned_postmaster(data: Path, port: int) -> bool:
    try:
        lines = (data / "postmaster.pid").read_text(encoding="utf-8").splitlines()
        return int(lines[0]) > 0 and Path(lines[1]).resolve() == data and int(lines[3]) == port
    except (OSError, ValueError, IndexError):
        return False


def _postgres_diagnostics(directory: Path, password: str, stage: str, exc: Exception) -> None:
    target = Path(os.environ.get("SYNDRA_TEST_POSTGRES_DIAGNOSTICS", str(directory)))
    target.mkdir(parents=True, exist_ok=True)
    logs = {}
    for name in ("initdb", "pg_ctl-start", "postgres", "pg_ctl-stop"):
        source = directory / f"{name}.log"
        if source.is_file():
            with source.open("rb") as stream:
                stream.seek(max(0, source.stat().st_size - 8_192))
                tail = stream.read(8_192).decode("utf-8", errors="replace")
            logs[name] = redact_diagnostic_text(tail.replace(password, "[REDACTED]"), max_chars=8_192)
    diagnostic = {"stage": stage, "error_type": type(exc).__name__, "logs": logs}
    (target / f"{directory.name}-{stage}.json").write_text(
        json.dumps(diagnostic, indent=2) + "\n", encoding="utf-8"
    )


@contextmanager
def disposable_postgres(root):
    binaries = shutil.which("initdb")
    if binaries is None:
        raise RuntimeError("Install PostgreSQL 16-18 with initdb/pg_ctl on PATH for the real service tests.")
    pg_ctl = str(Path(binaries).with_name("pg_ctl.exe" if os.name == "nt" else "pg_ctl"))
    directory = Path(root).resolve() / ("postgres-" + uuid.uuid4().hex)
    directory.mkdir(mode=0o700, parents=True)
    data, log = directory / "data", directory / "postgres.log"
    port = _available_loopback_port()
    password: str = secrets.token_urlsafe(32)
    flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    environment = {key: value for key, value in os.environ.items() if not key.upper().startswith("PG")}

    def run(stage: str, command: list[str], timeout: int) -> None:
        # File redirection prevents a server descendant from keeping a pipe open.
        try:
            with (directory / f"{stage}.log").open("wb") as output:
                subprocess.run(command, stdout=output, stderr=subprocess.STDOUT,
                               timeout=timeout, env=environment, creationflags=flags,
                               shell=False, check=True)
        except (OSError, subprocess.SubprocessError) as exc:
            failure = RuntimeError(f"Disposable PostgreSQL {stage} failed. Inspect its owned startup diagnostics.")
            try:
                _postgres_diagnostics(directory, password, stage, exc)
            except OSError as diagnostic_error:
                failure.add_note(f"Could not save diagnostic: {type(diagnostic_error).__name__}")
            raise failure from exc

    def write_private(path: Path, content: str) -> None:
        with os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "w") as stream:
            stream.write(content)

    password_file = directory / "init-password"
    write_private(password_file, password + "\n")
    try:
        run("initdb", [binaries, "-D", str(data), "-U", "syndra_test", "-A", "scram-sha-256",
                       "--pwfile", str(password_file), "--no-locale", "--no-instructions", "-E", "UTF8"], 40)
    finally:
        password_file.unlink(missing_ok=True)

    def stop() -> None:
        if not (data / "postmaster.pid").exists():
            return
        if not _owned_postmaster(data, port):
            error = RuntimeError("Disposable PostgreSQL shutdown could not verify cluster ownership.")
            _postgres_diagnostics(directory, password, "shutdown-ownership", error)
            raise error
        run("pg_ctl-stop", [pg_ctl, "-D", str(data), "-m", "fast", "-w", "-t", "10", "stop"], 15)

    passfile = directory / "pgpass"
    write_private(passfile, f"127.0.0.1:{port}:postgres:syndra_test:{password}\n")
    escaped_passfile = str(passfile).replace("\\", "\\\\").replace("'", "\\'")
    try:
        # pg_ctl has its own deadline, shorter than the outer command deadline.
        run("pg_ctl-start", [pg_ctl, "-D", str(data), "-l", str(log), "-o",
                             f"-h 127.0.0.1 -p {port} -c unix_socket_directories=",
                             "-w", "-t", "30", "start"], 40)
        if not _owned_postmaster(data, port):
            error = RuntimeError("Disposable PostgreSQL did not register its owned data directory and port.")
            _postgres_diagnostics(directory, password, "ownership", error)
            raise error
        yield f"host=127.0.0.1 port={port} dbname=postgres user=syndra_test passfile='{escaped_passfile}'", directory
    except BaseException as exc:
        try:
            stop()
        except RuntimeError as stop_error:
            exc.add_note(str(stop_error))
        raise
    else:
        stop()
    finally:
        passfile.unlink(missing_ok=True)
