import json
from pathlib import Path

import pytest

from agentbus import cli
from agentbus.config import AgentBusConfig
from agentbus.execution.models import RunRecord
from agentbus.execution.state_store import StateStore
from agentbus.trace import (
    ContentAddressedStore,
    RuntimeTrace,
    TraceNotFoundError,
    TraceSpanType,
    TraceStatus,
)
from agentbus.trace.sealing import seal_run_provenance


def _configured_runtime(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    state_db = tmp_path / "state.db"
    config_file = tmp_path / "agentbus.json"
    config_file.write_text(
        json.dumps(
            {
                "agentbus": {
                    "workspace_dir": str(workspace),
                    "state_db": str(state_db),
                }
            }
        ),
        encoding="utf-8",
    )
    config = AgentBusConfig(
        workspace_dir=str(workspace),
        state_db=str(state_db),
    )
    store = StateStore(state_db)
    return config_file, config, store


def _record_run(config: AgentBusConfig, store: StateStore, run_id: str):
    store.create_run(
        RunRecord(
            run_id=run_id,
            original_task=f"CLI trace {run_id}",
            model="deterministic",
            workspace=str(config.workspace_path),
            graph_data={"version": 1, "tasks": []},
        )
    )
    runtime = RuntimeTrace.open(
        store,
        run_id,
        object_root=config.trace_store_path,
        workspace=config.workspace_path,
    )
    with runtime.scope(runtime.root_context):
        runtime.call(
            TraceSpanType.VERIFIER,
            "final verifier",
            lambda: {"passed": True, "exit_code": 0},
            capture="json",
        )
        runtime.call(
            TraceSpanType.REVIEWER,
            "final reviewer",
            lambda: {"approved": True, "issues": []},
            capture="json",
        )
    trace = runtime.finish(status=TraceStatus.SUCCEEDED)
    assert trace is not None
    seal_run_provenance(
        trace,
        state_store=store,
        object_store=runtime.object_store,
        configuration={"provider": "deterministic"},
        task_graph={"version": 1, "tasks": []},
        final_repository_tree_sha256="b" * 64,
    )
    return trace


def test_root_help_lists_trace_replay_and_compare(capsys) -> None:
    assert cli.main(["--help"]) == 0

    output = capsys.readouterr().out
    assert "trace" in output
    assert "replay" in output
    assert "compare" in output


def test_trace_list_inspect_verify_and_replay_are_providerless(
    tmp_path,
    capsys,
) -> None:
    config_file, config, store = _configured_runtime(tmp_path)
    trace = _record_run(config, store, "run-cli")

    assert cli.main(
        ["trace", "list", "--config", str(config_file), "--json"]
    ) == 0
    listing = json.loads(capsys.readouterr().out)
    assert listing["count"] == 1
    assert listing["traces"][0]["trace_id"] == trace.trace_id

    assert cli.main(
        [
            "trace",
            "inspect",
            trace.run_id,
            "--config",
            str(config_file),
            "--json",
        ]
    ) == 0
    inspected = json.loads(capsys.readouterr().out)
    assert inspected["trace"]["trace_id"] == trace.trace_id
    assert inspected["replayability"]["replayable_offline"] is True

    assert cli.main(
        [
            "trace",
            "verify",
            trace.trace_id,
            "--config",
            str(config_file),
            "--json",
        ]
    ) == 0
    verified = json.loads(capsys.readouterr().out)
    assert verified["valid"] is True

    assert cli.main(
        [
            "replay",
            trace.run_id,
            "--config",
            str(config_file),
            "--json",
        ]
    ) == 0
    replayed = json.loads(capsys.readouterr().out)
    assert replayed["session"]["status"] == "succeeded"
    assert replayed["session"]["provider_calls"] == 0
    assert replayed["session"]["network_calls"] == 0
    assert len(store.list_replay_sessions()) == 1


def test_archive_import_does_not_execute_and_fixture_replays_offline(
    tmp_path,
    capsys,
) -> None:
    config_file, config, store = _configured_runtime(tmp_path)
    trace = _record_run(config, store, "run-archive-cli")
    archive = tmp_path / "trace.agentbus-trace"
    fixture = tmp_path / "fixture.agentbus-trace"

    assert cli.main(
        [
            "trace",
            "export",
            trace.run_id,
            "--output",
            str(archive),
            "--config",
            str(config_file),
            "--json",
        ]
    ) == 0
    capsys.readouterr()
    assert cli.main(
        [
            "trace",
            "import",
            str(archive),
            "--config",
            str(config_file),
            "--json",
        ]
    ) == 0
    imported = json.loads(capsys.readouterr().out)
    assert imported["execution_started"] is False
    assert store.list_replay_sessions() == []

    assert cli.main(
        [
            "trace",
            "capture",
            trace.run_id,
            "--output",
            str(fixture),
            "--config",
            str(config_file),
            "--json",
        ]
    ) == 0
    captured = json.loads(capsys.readouterr().out)
    assert captured["kind"] == "regression fixture"
    assert cli.main(
        [
            "replay",
            str(fixture),
            "--config",
            str(config_file),
            "--json",
        ]
    ) == 0
    replayed = json.loads(capsys.readouterr().out)
    assert replayed["replay"]["session"]["status"] == "succeeded"
    assert replayed["fixture_assertions"]["passed"] is True
    assert replayed["replay"]["session"]["provider_calls"] == 0


def test_compare_fork_and_gc_require_explicit_inputs_and_execution(
    tmp_path,
    capsys,
) -> None:
    config_file, config, store = _configured_runtime(tmp_path)
    left = _record_run(config, store, "run-left-cli")
    right = _record_run(config, store, "run-right-cli")

    assert cli.main(
        [
            "compare",
            left.run_id,
            right.trace_id,
            "--config",
            str(config_file),
            "--json",
        ]
    ) == 0
    comparison = json.loads(capsys.readouterr().out)
    assert comparison["left_trace_id"] == left.trace_id
    assert comparison["right_trace_id"] == right.trace_id

    assert cli.main(
        [
            "replay",
            left.run_id,
            "--fork",
            "--change",
            'task_text="changed offline"',
            "--config",
            str(config_file),
            "--json",
        ]
    ) == 0
    fork = json.loads(capsys.readouterr().out)
    assert fork["replay"]["session"]["provider_calls"] == 0
    assert fork["fork_trace"]["trace_id"] != left.trace_id

    objects = ContentAddressedStore(config.trace_store_path)
    orphan = objects.put_json(
        {"orphan": True},
        producing_span_id=left.root_span_id,
    )
    assert cli.main(
        ["trace", "gc", "--config", str(config_file), "--json"]
    ) == 0
    plan = json.loads(capsys.readouterr().out)
    assert plan["dry_run"] is True
    assert objects.get(orphan.sha256).metadata.sha256 == orphan.sha256

    assert cli.main(
        [
            "trace",
            "gc",
            "--execute",
            "--config",
            str(config_file),
            "--json",
        ]
    ) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["deleted_objects"] >= 1
    with pytest.raises(TraceNotFoundError):
        objects.get(orphan.sha256)
