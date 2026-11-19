import os
from pathlib import Path
import shutil
import socket
import subprocess
from contextlib import contextmanager

import pytest

from agentbus.repair.integration import testing


@pytest.mark.parametrize("failed_stage", ["initdb", "pg_ctl-start", "pg_ctl-stop"])
def test_fixture_retains_redacted_diagnostics_and_stops_only_owned_cluster(tmp_path, monkeypatch, failed_stage):
    diagnostics = tmp_path / "diagnostics"
    monkeypatch.setenv("SYNDRA_TEST_POSTGRES_DIAGNOSTICS", str(diagnostics))
    monkeypatch.setenv("PGOPTIONS", "untrusted ambient options")
    monkeypatch.setattr(testing.shutil, "which", lambda _: str(tmp_path / "bin" / "initdb"))
    monkeypatch.setattr(testing, "_available_loopback_port", lambda: 43123)
    monkeypatch.setattr(testing.secrets, "token_urlsafe", lambda _: "private-fixture-password")
    calls = []

    def controlled_run(command, **kwargs):
        data = Path(command[command.index("-D") + 1])
        assert data.parent.parent == tmp_path
        assert "PGOPTIONS" not in kwargs["env"]
        assert "private-fixture-password" not in " ".join(command)
        stage = "initdb" if command[0].endswith("initdb") else f"pg_ctl-{command[-1]}"
        calls.append(stage)
        if stage == "initdb":
            data.mkdir()
            assert "scram-sha-256" in command
        if stage == "pg_ctl-start":
            assert "-h 127.0.0.1 -p 43123 -c unix_socket_directories=" in command
            (data / "postmaster.pid").write_text(f"123\n{data}\n0\n43123\n")
            (data.parent / "postgres.log").write_text("startup log\n")
        if stage == failed_stage:
            kwargs["stdout"].write(b"startup private-fixture-password\n")
            if stage == "pg_ctl-start":
                raise subprocess.TimeoutExpired(command, kwargs["timeout"])
            raise subprocess.CalledProcessError(1, command)

    monkeypatch.setattr(testing.subprocess, "run", controlled_run)
    with pytest.raises(RuntimeError, match=failed_stage):
        with testing.disposable_postgres(tmp_path) as (dsn, _directory):
            assert "password=" not in dsn
    assert calls == (["initdb"] if failed_stage == "initdb" else ["initdb", "pg_ctl-start", "pg_ctl-stop"])
    artifact, = diagnostics.glob("*.json")
    content = artifact.read_text(encoding="utf-8")
    assert "[REDACTED]" in content and "private-fixture-password" not in content
    assert not list(tmp_path.glob("postgres-*/init-password"))
    assert not list(tmp_path.glob("postgres-*/pgpass"))


@pytest.fixture
def real_postgres(monkeypatch):
    if os.environ.get("SYNDRA_TEST_POSTGRES") != "1":
        pytest.skip("Run with SYNDRA_TEST_POSTGRES=1 for the owned cluster fixture.")
    psycopg = pytest.importorskip("psycopg")
    monkeypatch.delenv("PGPASSWORD", raising=False)
    return psycopg


def test_loopback_cluster_requires_password_and_restarts(tmp_path, real_postgres):
    with testing.disposable_postgres(tmp_path) as (dsn, directory):
        with real_postgres.connect(dsn) as connection:
            assert connection.execute("SHOW listen_addresses").fetchone() == ("127.0.0.1",)
            assert connection.execute("SHOW unix_socket_directories").fetchone() == ("",)
            connection.execute("CREATE TABLE fixture_lifecycle (id int)")
            connection.execute("INSERT INTO fixture_lifecycle VALUES (1)")
        with pytest.raises(real_postgres.OperationalError):
            real_postgres.connect(dsn, password="wrong-owned-password", connect_timeout=2)
        pg_ctl = Path(shutil.which("initdb")).with_name("pg_ctl.exe" if os.name == "nt" else "pg_ctl")
        subprocess.run([str(pg_ctl), "-D", str(directory / "data"), "-m", "fast", "-w", "-t", "20", "restart"],
                       check=True, timeout=30, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        with real_postgres.connect(dsn) as connection:
            assert connection.execute("SELECT id FROM fixture_lifecycle").fetchone() == (1,)
    assert not (directory / "data" / "postmaster.pid").exists()
    assert not (directory / "pgpass").exists()


def test_loopback_port_conflict_preserves_existing_listener(tmp_path, monkeypatch, real_postgres):
    monkeypatch.setenv("SYNDRA_TEST_POSTGRES_DIAGNOSTICS", str(tmp_path / "diag"))
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        port = listener.getsockname()[1]
        monkeypatch.setattr(testing, "_available_loopback_port", lambda: port)
        with pytest.raises(RuntimeError, match="pg_ctl-start"):
            with testing.disposable_postgres(tmp_path):
                pytest.fail("Startup succeeded on an occupied port")
        with socket.create_connection(("127.0.0.1", port), timeout=2):
            client, _address = listener.accept()
            client.close()
    artifact, = (tmp_path / "diag").glob("*-pg_ctl-start.json")
    assert "postgres" in artifact.read_text(encoding="utf-8")


@pytest.mark.skipif(os.name == "nt", reason="POSIX virtual environments use interpreter symlinks")
def test_installed_acceptance_keeps_virtualenv_interpreter_path(tmp_path, monkeypatch):
    from agentbus.repair.integration import acceptance

    base_python = tmp_path / "base-python"
    base_python.touch()
    interpreter = tmp_path / "venv" / "bin" / "python"
    interpreter.parent.mkdir(parents=True)
    interpreter.symlink_to(base_python)
    monkeypatch.chdir(tmp_path)

    @contextmanager
    def owned_cluster(_root):
        yield "host=127.0.0.1", tmp_path

    class ObservedInterpreter(Exception):
        pass

    def observe(command, **_kwargs):
        assert command[0] == str(interpreter)
        assert "--target" in command
        raise ObservedInterpreter

    monkeypatch.setattr(testing, "disposable_postgres", owned_cluster)
    monkeypatch.setattr(subprocess, "run", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(subprocess, "Popen", observe)
    with pytest.raises(ObservedInterpreter):
        acceptance.main(["--python", "venv/bin/python", "--output", str(tmp_path / "evidence")])
