"""Disposable local PostgreSQL process for integration verification, never production."""
from contextlib import contextmanager
import os
from pathlib import Path
import shutil
import socket
import subprocess
import uuid


@contextmanager
def disposable_postgres(root):
    binaries = shutil.which("initdb")
    if binaries is None:
        raise RuntimeError("Install PostgreSQL 16-18 with initdb/pg_ctl on PATH for the real service tests.")
    directory = Path(root).resolve() / ("postgres-" + uuid.uuid4().hex)
    directory.mkdir(parents=True)
    data, log = directory / "data", directory / "postgres.log"
    flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    bind = socket.socket()
    bind.bind(("127.0.0.1", 0))
    port = bind.getsockname()[1]
    bind.close()
    environment = {key: value for key, value in os.environ.items() if key not in {"PGPASSWORD", "PGSERVICE", "PGOPTIONS"}}
    commands = [[binaries, "-D", str(data), "-U", "syndra_test", "-A", "trust", "--no-locale", "-E", "UTF8"],
                [str(Path(binaries).with_name("pg_ctl.exe" if os.name == "nt" else "pg_ctl")), "-D", str(data), "-l", str(log), "-o", f"-h 127.0.0.1 -p {port}", "-w", "start"]]
    for index, command in enumerate(commands):
        # Server children on Windows inherit pipe handles even after pg_ctl exits.
        # File redirection avoids communicate() waiting for the whole cluster.
        with (directory / f"startup-{index}.log").open("wb") as output:
            completed = subprocess.run(command, stdout=output, stderr=subprocess.STDOUT,
                                       timeout=40, env=environment, creationflags=flags, shell=False)
            if completed.returncode:
                raise RuntimeError("Disposable PostgreSQL startup failed. Inspect its owned local directory.")
    try:
        yield f"host=127.0.0.1 port={port} dbname=postgres user=syndra_test", directory
    finally:
        with (directory / "shutdown.log").open("wb") as output:
            subprocess.run([commands[1][0], "-D", str(data), "-m", "fast", "-w", "stop"], stdout=output,
                           stderr=subprocess.STDOUT, timeout=40, creationflags=flags, shell=False, env=environment)
        # Preserve the owned evidence directory; never reset or clean a user database.
