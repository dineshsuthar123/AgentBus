from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

from agentbus import cli
from agentbus import main as main_module
from agentbus.config import AgentBusConfig
from agentbus.configuration import resolve_configuration
from agentbus.execution.models import FailureCategory, RunStatus
from agentbus.execution.state_store import StateStore
from agentbus.git.repository import GitRepository
from agentbus.product.logging import read_product_logs
from agentbus.runtime.loop import AgentLoop
from agentbus.runtime.loop import PlannedCapabilityMismatchError
from agentbus.runtime.orchestrator import MultiAgentOrchestrator


PLAN = {
    "goal": "Create one deterministic result",
    "steps": [
        {
            "id": "step-1",
            "title": "Create result",
            "description": "Create result.py and verify it.",
            "risk": "low",
            "execution_kind": "implementation",
            "dependencies": [],
            "assigned_role": "coder",
            "maximum_attempts": 1,
            "expected_outputs": ["result.py"],
            "done_criteria": ["result.py exists"],
            "required_capabilities": [
                "filesystem.write",
                "filesystem.create",
                "test.execute",
                "process.execute",
                "git.read",
            ],
        }
    ],
    "test_strategy": "Use the offline fake verifier.",
    "done_criteria": ["The result is verified and reviewed."],
}


def _git(path: Path, *arguments: str) -> str:
    return subprocess.run(
        ["git", *arguments],
        cwd=path,
        check=True,
        capture_output=True,
        text=True,
        shell=False,
        env={**os.environ, "GIT_CONFIG_NOSYSTEM": "1"},
    ).stdout.strip()


def _repository(tmp_path: Path) -> tuple[Path, Path]:
    repository = tmp_path / "repository"
    repository.mkdir()
    _git(repository, "init", "-q")
    _git(repository, "config", "user.name", "AgentBus Tests")
    _git(repository, "config", "user.email", "tests@agentbus.invalid")
    (repository / ".gitignore").write_text(
        ".agentbus/\n__pycache__/\n*.py[cod]\n",
        encoding="utf-8",
    )
    (repository / "README.md").write_text("# Logging fixture\n", encoding="utf-8")
    _git(repository, "add", ".gitignore", "README.md")
    _git(repository, "commit", "-q", "-m", "baseline")

    runtime = repository / ".agentbus"
    runtime.mkdir()
    config_file = runtime / "config.toml"
    config_file.write_text(
        "[agentbus]\n"
        'provider_name = "deterministic"\n'
        f"workspace_dir = {json.dumps(str(repository.resolve()))}\n"
        f"state_dir = {json.dumps(str(runtime.resolve()))}\n"
        f"runs_dir = {json.dumps(str((runtime / 'runs').resolve()))}\n"
        'state_db = "state.db"\n'
        "repository_intelligence = false\n",
        encoding="utf-8",
    )
    return repository.resolve(), config_file.resolve()


class _Planner:
    def plan(self, *_args, **_kwargs):
        return PLAN


class _Coder:
    def __init__(self, workspace: Path, *, fail: bool) -> None:
        self.workspace = workspace
        self.fail = fail
        self.calls = 0

    def execute(self, *_args, **_kwargs) -> str:
        self.calls += 1
        if self.fail:
            raise PlannedCapabilityMismatchError(
                task_id="step-1",
                tool_name="filesystem.write",
                requested_capabilities=["filesystem.write", "filesystem.create"],
                declared_capabilities=["filesystem.write"],
            )
        (self.workspace / "result.py").write_text("RESULT = 1\n", encoding="utf-8")
        return "Created result.py"


class _Verifier:
    def verify(self, *_args, **_kwargs):
        return {
            "command": ["fake", "verify"],
            "exit_code": 0,
            "passed": True,
            "output": "offline verification passed",
            "reason": "offline fake",
        }


class _Reviewer:
    @staticmethod
    def _approval():
        return {
            "approved": True,
            "issues": [],
            "summary": "Offline review approved.",
            "required_fixes": [],
        }

    def review_task(self, **_kwargs):
        return self._approval()

    def review(self, *_args, **_kwargs):
        return self._approval()


def _clear_runtime_environment(monkeypatch, tmp_path: Path) -> None:
    for name in tuple(os.environ):
        if name.startswith("AGENTBUS_") or name.startswith("AZURE_OPENAI_"):
            monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.setattr(
        "agentbus.configuration.default_user_config_path",
        lambda _environ=None: tmp_path / "no-user-config.toml",
    )


def _install_offline_orchestrator(
    monkeypatch,
    workspace: Path,
    *,
    fail: bool,
) -> tuple[_Coder, list[MultiAgentOrchestrator]]:
    real_orchestrator = MultiAgentOrchestrator
    coder = _Coder(workspace, fail=fail)
    runners: list[MultiAgentOrchestrator] = []

    def factory(config, **kwargs):
        runner = real_orchestrator(
            config=config,
            planner=_Planner(),
            coder=coder,
            verifier=_Verifier(),
            reviewer=_Reviewer(),
            **kwargs,
        )
        runners.append(runner)
        return runner

    monkeypatch.setattr(main_module, "MultiAgentOrchestrator", factory)
    return coder, runners


def _persisted_run(runner: MultiAgentOrchestrator):
    runs = runner.state_store.list_runs()
    assert len(runs) == 1
    return runs[0]


def _run_log_records(path: Path, run_id: str) -> list[dict]:
    records = []
    for log_file in path.glob(f"*_{run_id}.jsonl"):
        records.extend(
            json.loads(line)
            for line in log_file.read_text(encoding="utf-8").splitlines()
        )
    return records


def test_failed_durable_cli_keeps_logs_out_of_repository_changes(
    tmp_path,
    monkeypatch,
    capsys,
):
    repository, config_file = _repository(tmp_path)
    _clear_runtime_environment(monkeypatch, tmp_path)
    monkeypatch.chdir(repository)
    _, runners = _install_offline_orchestrator(
        monkeypatch,
        repository,
        fail=True,
    )

    exit_code = cli.main(
        [
            "run",
            "Trigger an offline capability mismatch",
            "--workspace",
            str(repository),
            "--workflow",
            "multi",
            "--durable",
        ]
    )
    output = capsys.readouterr().out

    assert exit_code == 1
    run = _persisted_run(runners[0])
    report = runners[0].get_durable_report(run.run_id)
    assert report.status == RunStatus.FAILED
    assert report.task_failures[0]["category"] == FailureCategory.PLAN_CAPABILITY_MISMATCH.value
    assert report.changed_files == []
    assert report.generated_artifacts == []
    assert report.relevant_changed_files == []
    assert report.commit_eligible_files == []
    assert not (repository / "runs").exists()
    records = _run_log_records(repository / ".agentbus" / "runs", run.run_id)
    assert records
    assert {record["run_id"] for record in records} == {run.run_id}
    assert _git(repository, "status", "--short", "--untracked-files=all") == ""
    assert "runs/" not in output.replace(".agentbus/runs/", "")

    assert cli.main(["logs", "--run", run.run_id]) == 0
    log_output = capsys.readouterr().out
    assert "No matching AgentBus logs found." not in log_output
    resolved = resolve_configuration(config_file=config_file, environ={}).config
    entries = read_product_logs(resolved, run_id=run.run_id)
    assert entries
    assert {entry.run_id for entry in entries} == {run.run_id}


def test_successful_and_resumed_durable_run_share_managed_log_storage(
    tmp_path,
    monkeypatch,
    capsys,
):
    repository, _ = _repository(tmp_path)
    _clear_runtime_environment(monkeypatch, tmp_path)
    monkeypatch.chdir(repository)
    coder, runners = _install_offline_orchestrator(
        monkeypatch,
        repository,
        fail=False,
    )

    assert cli.main(
        [
            "run",
            "Create one offline result",
            "--workspace",
            str(repository),
            "--workflow",
            "multi",
            "--durable",
        ]
    ) == 0
    capsys.readouterr()
    run = _persisted_run(runners[0])
    first = runners[0].get_durable_report(run.run_id)
    assert first.status == RunStatus.SUCCEEDED
    assert first.changed_files == ["result.py"]
    assert first.relevant_changed_files == ["result.py"]
    assert first.commit_eligible_files == ["result.py"]

    assert cli.main(["resume", run.run_id]) == 0
    capsys.readouterr()
    resumed = runners[-1].get_durable_report(run.run_id)
    assert resumed.status == RunStatus.SUCCEEDED
    assert coder.calls == 1
    assert not (repository / "runs").exists()
    records = _run_log_records(repository / ".agentbus" / "runs", run.run_id)
    assert records
    assert {record["run_id"] for record in records} == {run.run_id}
    status = _git(repository, "status", "--short", "--untracked-files=all")
    assert status == "?? result.py"


def test_relative_runtime_paths_are_scoped_to_workspace_state(tmp_path):
    workspace = (tmp_path / "repository").resolve()
    workspace.mkdir()
    config = AgentBusConfig(
        workspace_dir=str(workspace),
        state_dir=".agentbus",
        state_db="state.db",
        runs_dir="runs",
    )

    assert config.state_directory_path == workspace / ".agentbus"
    assert config.state_database_path == workspace / ".agentbus" / "state.db"
    assert config.runs_path == workspace / ".agentbus" / "runs"


def test_run_logs_allow_only_managed_workspace_or_explicit_external_paths(tmp_path):
    workspace = (tmp_path / "repository").resolve()
    workspace.mkdir()
    external = (tmp_path / "external-runtime" / "runs").resolve()

    assert AgentBusConfig(
        workspace_dir=str(workspace),
        runs_dir=str(external),
    ).runs_path == external
    with pytest.raises(ValueError, match="inside the target repository"):
        AgentBusConfig(
            workspace_dir=str(workspace),
            runs_dir=str(workspace / "runs"),
        ).runs_path


def test_agent_loop_uses_managed_run_log_path(tmp_path):
    workspace = (tmp_path / "repository").resolve()
    workspace.mkdir()
    config = AgentBusConfig(
        workspace_dir=str(workspace),
        state_dir=".agentbus",
        runs_dir="runs",
    )

    class FinishModel:
        def generate_json(self, *_args, **_kwargs):
            return {"action": "finish", "summary": "finished offline"}

    loop = AgentLoop(config=config, model=FinishModel())

    assert loop.run("Finish without tools") == "finished offline"
    assert loop.logger.log_dir == config.runs_path
    assert not (workspace / "runs").exists()
    records = _run_log_records(config.runs_path, loop.logger.run_id)
    assert records
    assert {record["run_id"] for record in records} == {loop.logger.run_id}


def test_parallel_worker_logger_uses_source_runtime_directory(
    tmp_path,
    monkeypatch,
):
    repository, _ = _repository(tmp_path)
    worktree = tmp_path / "worker-worktree"
    worktree.mkdir()
    config = AgentBusConfig(
        workspace_dir=str(repository),
        state_dir=".agentbus",
        state_db="state.db",
        runs_dir="runs",
        provider_name="deterministic",
    )
    store = StateStore(repository / ".agentbus" / "state.db")
    captured: list[tuple[Path, str | None]] = []

    class RecordingLogger:
        def __init__(self, log_dir, run_id=None):
            self.log_dir = Path(log_dir).resolve()
            self.run_id = run_id or "recording-run"
            captured.append((self.log_dir, run_id))

        def log(self, *_args, **_kwargs):
            return None

    runner = MultiAgentOrchestrator(
        config=config,
        planner=_Planner(),
        coder=_Coder(repository, fail=False),
        verifier=_Verifier(),
        reviewer=_Reviewer(),
        logger=RecordingLogger(repository / ".agentbus" / "runs"),
        state_store=store,
        git_repository=GitRepository(str(repository)),
    )
    monkeypatch.setattr(
        "agentbus.runtime.orchestrator.RunLogger",
        RecordingLogger,
    )

    runner._parallel_task_executor(
        worktree,
        run_id=None,
        worker_id="worker-1",
    )

    assert captured[-1] == (config.runs_path, None)
    assert captured[-1][0] != worktree / ".agentbus" / "runs"
