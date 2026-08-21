from __future__ import annotations

import os
import sqlite3
import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from agentbus._failure_injection import (
    DeterministicFailureInjector,
    FailureInjectionPoint,
    FailureRule,
)
from agentbus.config import AgentBusConfig
from agentbus.control.registry import DaemonRegistry
from agentbus.control.server import serve
from agentbus.execution.cancellation import CancellationRequested, CancellationToken
from agentbus.execution.models import RunRecord
from agentbus.execution.state_store import StateStore
from agentbus.git.repository import GitRepository, GitRepositoryError
from agentbus.intelligence import (
    IndexOperationState,
    IndexStore,
    IndexUnavailableError,
    ParserUnavailableError,
    RepositoryIndexer,
    file_id,
    repository_identity,
    workspace_identity,
)
from agentbus.intelligence.models import SourceLanguage
from agentbus.intelligence.parsers import (
    ParseRequest,
    ParserRegistry,
    PythonAstParser,
)
from agentbus.mcp import (
    McpClient,
    McpServerConfig,
    McpStdioTransport,
    mcp_server_capabilities,
)
from agentbus.mcp.errors import McpTransportError
from agentbus.models.deterministic import DeterministicProvider
from agentbus.models.errors import ModelServiceUnavailableError
from agentbus.models.types import ModelRole
from agentbus.sandbox import ControlledProcessSupervisor, ExecutableCatalog
from agentbus.tools.filesystem_operations import ContainedFileSystem
from agentbus.trace import ContentAddressedStore, TraceStorageError


_MCP_FIXTURE = Path(__file__).parent / "fixtures" / "mcp" / "fake_server.py"


def _injector(
    point: FailureInjectionPoint,
    *,
    occurrence: int = 1,
    scope: str | None = None,
) -> DeterministicFailureInjector:
    return DeterministicFailureInjector.for_testing(
        FailureRule(point, occurrence=occurrence, scope=scope)
    )


def test_failure_schedule_requires_explicit_bounded_opt_in() -> None:
    rule = FailureRule(FailureInjectionPoint.PROVIDER_FAILURE)

    with pytest.raises(TypeError, match="for_testing"):
        DeterministicFailureInjector((rule,))
    with pytest.raises(ValueError, match="duplicate"):
        DeterministicFailureInjector.for_testing(rule, rule)
    with pytest.raises(ValueError, match="control characters"):
        FailureRule(FailureInjectionPoint.PARSER_FAILURE, scope="python\nother")


def test_failure_schedule_is_thread_safe_and_instance_local() -> None:
    injected = _injector(
        FailureInjectionPoint.PROVIDER_FAILURE,
        occurrence=17,
        scope="summarizer",
    )
    isolated = _injector(
        FailureInjectionPoint.PROVIDER_FAILURE,
        occurrence=17,
        scope="summarizer",
    )

    with ThreadPoolExecutor(max_workers=8) as executor:
        results = list(
            executor.map(
                lambda _: injected(
                    FailureInjectionPoint.PROVIDER_FAILURE,
                    scope="summarizer",
                ),
                range(64),
            )
        )

    assert results.count(True) == 1
    assert injected.calls(
        FailureInjectionPoint.PROVIDER_FAILURE,
        scope="summarizer",
    ) == 64
    assert injected.all_rules_fired is True
    for index in range(1_000):
        assert isolated(
            FailureInjectionPoint.PROVIDER_FAILURE,
            scope=f"unplanned-{index}",
        ) is False
    assert isolated.calls(
        FailureInjectionPoint.PROVIDER_FAILURE,
        scope="unplanned-999",
    ) == 0
    for _ in range(17):
        fired = isolated(
            FailureInjectionPoint.PROVIDER_FAILURE,
            scope="summarizer",
        )
    assert fired is True
    assert isolated.all_rules_fired is True


def test_provider_and_parser_failures_are_one_shot_and_scoped() -> None:
    provider_probe = _injector(
        FailureInjectionPoint.PROVIDER_FAILURE,
        occurrence=2,
        scope="summarizer",
    )
    provider = DeterministicProvider(
        role=ModelRole.SUMMARIZER,
        failure_probe=provider_probe,
    )

    assert provider.generate_text("first").value
    with pytest.raises(ModelServiceUnavailableError, match="Controlled"):
        provider.generate_text("second")
    assert provider.generate_text("third").value
    assert DeterministicProvider(role=ModelRole.SUMMARIZER).generate_text(
        "isolated"
    ).value

    parser_probe = _injector(
        FailureInjectionPoint.PARSER_FAILURE,
        scope="python",
    )
    registry = ParserRegistry(
        (PythonAstParser(),),
        failure_probe=parser_probe,
    )
    request = _parse_request()

    with pytest.raises(ParserUnavailableError, match="Controlled parser failure"):
        registry.parse(request)
    assert registry.parse(request).parser_name == PythonAstParser.descriptor.name
    assert (
        ParserRegistry((PythonAstParser(),)).parse(request).parser_name
        == PythonAstParser.descriptor.name
    )
    assert provider_probe.all_rules_fired is True
    assert parser_probe.all_rules_fired is True


def test_sqlite_busy_retries_are_instance_scoped_without_global_sleep_patch(
    tmp_path: Path,
) -> None:
    probe = DeterministicFailureInjector.for_testing(
        FailureRule(
            FailureInjectionPoint.SQLITE_BUSY,
            scope="state-write",
        ),
        FailureRule(
            FailureInjectionPoint.SQLITE_BUSY,
            scope="index-write",
        ),
    )
    state = StateStore(
        tmp_path / "state.db",
        transaction_retry_delays=(0.0,),
        failure_probe=probe,
    )
    run = _run_record("controlled-busy", tmp_path)

    assert state.create_run(run) == run
    assert state.get_run(run.run_id) == run
    assert probe.calls(
        FailureInjectionPoint.SQLITE_BUSY,
        scope="state-write",
    ) == 2

    index = IndexStore(
        tmp_path / "index.db",
        transaction_retry_delays=(0.0,),
        failure_probe=probe,
    )
    with index._write_transaction() as connection:
        connection.execute("CREATE TABLE controlled_busy_probe(value TEXT)")

    with sqlite3.connect(index.database_path) as connection:
        assert connection.execute(
            "SELECT name FROM sqlite_master WHERE name = ?",
            ("controlled_busy_probe",),
        ).fetchone() == ("controlled_busy_probe",)
    assert probe.calls(
        FailureInjectionPoint.SQLITE_BUSY,
        scope="index-write",
    ) == 2
    assert probe.all_rules_fired is True


def test_filesystem_write_failure_preserves_original_and_cleans_temporary(
    tmp_path: Path,
) -> None:
    target = tmp_path / "important.txt"
    target.write_text("original", encoding="utf-8")
    probe = _injector(
        FailureInjectionPoint.FILESYSTEM_WRITE_FAILURE,
        scope="write",
    )
    filesystem = ContainedFileSystem(tmp_path, failure_probe=probe)

    with pytest.raises(OSError, match="controlled filesystem"):
        filesystem.write(
            "important.txt",
            "replacement",
            task_id="failure-matrix",
            invocation_id="filesystem-1",
        )

    assert target.read_text(encoding="utf-8") == "original"
    assert list(tmp_path.glob(".important.txt.agentbus-*.tmp")) == []
    normal = ContainedFileSystem(tmp_path)
    record = normal.write(
        "important.txt",
        "replacement",
        task_id="failure-matrix",
        invocation_id="filesystem-2",
    )
    assert record.atomic is True
    assert target.read_text(encoding="utf-8") == "replacement"
    assert probe.all_rules_fired is True


def test_git_command_failure_never_launches_the_selected_operation(
    tmp_path: Path,
) -> None:
    workspace = _initialized_repository(tmp_path / "repository")
    probe = _injector(
        FailureInjectionPoint.GIT_COMMAND_FAILURE,
        scope="status",
    )
    repository = GitRepository(str(workspace), failure_probe=probe)

    with pytest.raises(GitRepositoryError, match="Controlled Git command failure"):
        repository.changed_files()

    assert repository.changed_files() == []
    assert GitRepository(str(workspace)).current_branch()
    assert probe.calls(
        FailureInjectionPoint.GIT_COMMAND_FAILURE,
        scope="status",
    ) == 2
    assert probe.all_rules_fired is True


def test_supervised_timeout_terminates_only_the_owned_child(
    tmp_path: Path,
) -> None:
    probe = _injector(
        FailureInjectionPoint.SUBPROCESS_TIMEOUT,
        scope="python",
    )
    supervisor = ControlledProcessSupervisor(
        tmp_path,
        catalog=ExecutableCatalog.standard(("python",)),
        poll_interval_seconds=0.01,
        termination_grace_seconds=0.5,
        failure_probe=probe,
    )

    result = supervisor.run(
        "python",
        ("-c", "import time; time.sleep(60)"),
        timeout_seconds=30,
    )

    assert result.passed is False
    assert result.timed_out is True
    assert result.cancelled is False
    assert result.termination_reason == "controlled_timeout"
    assert result.exit_code is not None
    assert ControlledProcessSupervisor(
        tmp_path,
        catalog=ExecutableCatalog.standard(("python",)),
    ).run("python", ("-c", "print('isolated')")).stdout.strip() == "isolated"
    assert probe.all_rules_fired is True


def test_local_mcp_failure_closes_only_the_injected_peer(tmp_path: Path) -> None:
    probe = _injector(
        FailureInjectionPoint.MCP_FAILURE,
        scope="tools/call",
    )
    config, transport = _mcp_transport(tmp_path)
    client = McpClient(config, transport, failure_probe=probe)
    try:
        client.connect()
        assert {tool.name for tool in client.list_tools()} >= {"echo"}

        with pytest.raises(McpTransportError, match="Controlled local MCP failure"):
            client.call_tool("echo", {"message": "safe"})

        assert transport.is_running is True
    finally:
        client.close()

    assert transport.is_running is False
    normal_config, normal_transport = _mcp_transport(tmp_path)
    normal_client = McpClient(normal_config, normal_transport)
    try:
        normal_client.connect()
        normal_client.list_tools()
        assert normal_client.call_tool(
            "echo",
            {"message": "isolated"},
        ).structured_content == {"echo": "isolated"}
    finally:
        normal_client.close()
    assert normal_transport.is_running is False
    assert probe.all_rules_fired is True


def test_daemon_termination_unwinds_owned_lifecycle_without_public_switch(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    registry_path = tmp_path / "daemons.json"
    config = AgentBusConfig(
        provider_name="deterministic",
        workspace_dir=str(workspace),
        state_dir=str(tmp_path / "state"),
        state_db="state.db",
        runs_dir=str(tmp_path / "runs"),
    )
    probe = _injector(
        FailureInjectionPoint.DAEMON_TERMINATION,
        scope="before-server-run",
    )

    with pytest.raises(RuntimeError, match="Controlled AgentBus daemon termination"):
        serve(
            config=config,
            port=0,
            idle_timeout=30,
            registry_path=registry_path,
            daemon_id="d" * 32,
            log_level="error",
            failure_probe=probe,
        )

    assert DaemonRegistry(registry_path).list() == []
    assert not any(
        thread.is_alive()
        and thread.name
        in {"agentbus-daemon-heartbeat", "agentbus-daemon-idle-monitor"}
        for thread in threading.enumerate()
    )
    assert probe.all_rules_fired is True


def test_cancellation_failure_is_cooperative_and_token_local() -> None:
    probe = _injector(
        FailureInjectionPoint.CANCELLATION,
        scope="checkpoint",
    )
    token = CancellationToken(failure_probe=probe)

    with pytest.raises(CancellationRequested, match="Controlled cancellation"):
        token.checkpoint("failure-matrix", stage="before-work")

    state = token.snapshot()
    assert state.requested is True
    assert state.acknowledged is True
    assert state.acknowledgement_source == "failure-matrix"
    isolated = CancellationToken()
    isolated.checkpoint("unrelated")
    assert isolated.is_requested is False
    assert probe.all_rules_fired is True


def test_index_failure_marks_owned_operation_failed_and_allows_recovery(
    tmp_path: Path,
) -> None:
    (tmp_path / "service.py").write_text(
        "def controlled_failure_marker():\n    return True\n",
        encoding="utf-8",
    )
    repository = repository_identity("fixtures/controlled-failure")
    workspace = workspace_identity(repository.repository_id, [""])
    store = IndexStore(tmp_path / ".agentbus-test" / "index.sqlite3")
    probe = _injector(
        FailureInjectionPoint.INDEX_FAILURE,
        scope="build",
    )
    failed = RepositoryIndexer(
        tmp_path,
        repository,
        workspace,
        store,
        registry=ParserRegistry((PythonAstParser(),)),
        failure_probe=probe,
    )

    with pytest.raises(IndexUnavailableError, match="Controlled build index failure"):
        failed.build(operation_id="indexop_" + ("a" * 32))

    operation = store.get_index_operation(repository.repository_id)
    assert operation is not None
    assert operation.state == IndexOperationState.FAILED
    recovered = RepositoryIndexer(
        tmp_path,
        repository,
        workspace,
        store,
        registry=ParserRegistry((PythonAstParser(),)),
    ).build(operation_id="indexop_" + ("b" * 32))
    assert recovered.operation is not None
    assert recovered.operation.state == IndexOperationState.COMPLETED
    store.verify()
    assert probe.all_rules_fired is True


def test_trace_write_failure_cleans_unpublished_objects_and_is_store_local(
    tmp_path: Path,
) -> None:
    probe = _injector(
        FailureInjectionPoint.TRACE_WRITE_FAILURE,
        scope="object-write",
    )
    root = tmp_path / "injected-trace"
    store = ContentAddressedStore(root, failure_probe=probe)

    with pytest.raises(TraceStorageError, match="atomically write trace object"):
        store.put_text("safe local trace", producing_span_id="span-1")

    assert [path for path in root.rglob("*") if path.is_file()] == []
    assert list(root.rglob(".agentbus-tmp-*")) == []
    isolated = ContentAddressedStore(tmp_path / "isolated-trace")
    metadata = isolated.put_text("safe local trace", producing_span_id="span-2")
    assert isolated.get(metadata.sha256).data == b"safe local trace"
    assert probe.all_rules_fired is True


def test_environment_cannot_enable_failure_injection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AGENTBUS_FAILURE_INJECTION", "provider_failure")

    result = DeterministicProvider(role=ModelRole.SUMMARIZER).generate_text(
        "environment is not an activation boundary"
    )

    assert result.value


def _parse_request() -> ParseRequest:
    repository = repository_identity("fixtures/controlled-parser")
    return ParseRequest.from_content(
        repository_id=repository.repository_id,
        file_id=file_id(repository.repository_id, "example.py"),
        relative_path="example.py",
        language=SourceLanguage.PYTHON,
        content="value = 1\n",
    )


def _run_record(run_id: str, root: Path) -> RunRecord:
    return RunRecord(
        run_id=run_id,
        original_task="Exercise deterministic SQLite recovery",
        model="deterministic",
        workspace=str(root.resolve()),
    )


def _initialized_repository(path: Path) -> Path:
    path.mkdir(parents=True)
    environment = {
        **os.environ,
        "GIT_ATTR_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_SYSTEM": os.devnull,
        "GIT_TERMINAL_PROMPT": "0",
    }
    for arguments in (
        ("init", "-q"),
        ("config", "user.name", "AgentBus Failure Test"),
        ("config", "user.email", "agentbus@example.invalid"),
    ):
        subprocess.run(
            ["git", *arguments],
            cwd=path,
            check=True,
            capture_output=True,
            text=True,
            shell=False,
            env=environment,
        )
    (path / "README.md").write_text("baseline\n", encoding="utf-8")
    for arguments in (
        ("add", "--", "README.md"),
        ("commit", "-q", "-m", "test: baseline"),
    ):
        subprocess.run(
            ["git", *arguments],
            cwd=path,
            check=True,
            capture_output=True,
            text=True,
            shell=False,
            env=environment,
        )
    return path.resolve()


def _mcp_transport(root: Path) -> tuple[McpServerConfig, McpStdioTransport]:
    alias = "controlled-failure-mcp"
    config = McpServerConfig(
        server_id="controlled-failure",
        transport="stdio",
        executable_alias=alias,
        capability_map={
            "echo": mcp_server_capabilities("controlled-failure"),
            "write_note": mcp_server_capabilities("controlled-failure"),
        },
    )
    transport = McpStdioTransport(
        config,
        worktree=root,
        executable_catalog=ExecutableCatalog(
            {alias: (sys.executable, "-u", str(_MCP_FIXTURE), "--mode", "normal")}
        ),
        shutdown_grace_seconds=0.2,
    )
    return config, transport
