from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

from agentbus.execution.models import AttemptStatus, RunStatus, TaskStatus
from agentbus.execution.state_store import StateStore
from agentbus.tools.protocol import ToolInvocationStatus


def test_cli_run_approve_resume_preserves_verifier_attempt(tmp_path: Path) -> None:
    workspace = _repository(tmp_path / "workspace")
    marker = tmp_path / "maven-invocations.txt"
    executable_dir = _fake_maven(tmp_path / "bin", marker)
    config = _config(workspace)
    environment = {
        **os.environ,
        "AGENTBUS_PROVIDER": "deterministic",
        "AGENTBUS_DETERMINISTIC_PROFILE": "tool-source-patch",
        "PATH": os.pathsep.join((str(executable_dir), os.environ.get("PATH", ""))),
        "PYTHONDONTWRITEBYTECODE": "1",
    }

    started = _cli(
        workspace,
        environment,
        "run",
        "--config",
        str(config),
        "--workflow",
        "multi",
        "--provider",
        "deterministic",
        "--durable",
        "--max-steps",
        "2",
        "Patch module.py once and verify it with the repository test command.",
    )
    run_id = _run_id(started.stdout)
    store = StateStore(workspace / ".agentbus" / "state.db")
    first_report = store.get_run(run_id)
    first_attempts = store.list_attempts(run_id, "step-1")

    assert started.returncode == 0, started.stdout + started.stderr
    assert first_report.status == RunStatus.WAITING_FOR_APPROVAL
    assert len(first_attempts) == 1
    assert first_attempts[0].attempt_number == 1
    assert first_attempts[0].status == AttemptStatus.WAITING_FOR_APPROVAL
    assert _continuation(first_attempts[0])
    assert "task_review" not in first_attempts[0].metadata
    assert first_report.reviewer_status == "not_run"
    assert store.get_task(run_id, "step-1").status == TaskStatus.WAITING_FOR_APPROVAL
    assert store.load_snapshot(run_id).approvals == []
    pending = store.list_tool_approvals(run_id)
    assert len(pending) == 1
    assert pending[0].disposition is None
    assert pending[0].request.tool_name == "test.execute"
    assert (workspace / "module.py").read_text(encoding="utf-8") == "VALUE = 2\n"
    assert marker.exists() is False
    assert not any(
        event["event_type"] in {"task_review_completed", "final_review_completed"}
        for event in store.list_events(run_id)
    )

    approved = _cli(
        workspace,
        environment,
        "approve",
        f"{run_id}:step-1",
        "--config",
        str(config),
        "--reason",
        "Approve only the exact offline Maven verifier invocation.",
    )
    second_store = StateStore(workspace / ".agentbus" / "state.db")
    approved_attempts = second_store.list_attempts(run_id, "step-1")
    approvals = second_store.list_tool_approvals(run_id)

    assert approved.returncode == 0, approved.stdout + approved.stderr
    assert len(approved_attempts) == 1
    assert approved_attempts[0].attempt_id == first_attempts[0].attempt_id
    assert approved_attempts[0].attempt_number == 1
    assert _continuation(approved_attempts[0])
    assert approvals[0].disposition == "approved"
    assert second_store.load_snapshot(run_id).approvals == []

    generated_output = workspace / "target" / "surefire-reports" / "result.txt"
    generated_output.parent.mkdir(parents=True)
    generated_output.write_text("ignored build output\n", encoding="utf-8")

    resumed = _cli(
        workspace,
        environment,
        "resume",
        run_id,
        "--config",
        str(config),
    )
    final_store = StateStore(workspace / ".agentbus" / "state.db")
    attempts = final_store.list_attempts(run_id, "step-1")
    invocations = final_store.list_tool_invocations(run_id)
    events = final_store.list_events(run_id)

    assert resumed.returncode == 0, resumed.stdout + resumed.stderr
    assert final_store.get_run(run_id).status == RunStatus.SUCCEEDED
    assert final_store.get_task(run_id, "step-1").status == TaskStatus.SUCCEEDED
    assert len(attempts) == 1
    assert attempts[0].attempt_id == first_attempts[0].attempt_id
    assert attempts[0].status == AttemptStatus.SUCCEEDED
    assert marker.read_text(encoding="utf-8") == "1\n"
    assert (workspace / "module.py").read_text(encoding="utf-8") == "VALUE = 2\n"
    verifier_calls = [
        invocation
        for invocation in invocations
        if invocation.tool_name == "test.execute"
        and invocation.caller_role == "verifier"
    ]
    assert len(verifier_calls) == 1
    assert verifier_calls[0].status == ToolInvocationStatus.SUCCEEDED
    assert sum(
        event["event_type"] == "task_attempt_started" for event in events
    ) == 1
    assert sum(
        event["event_type"] == "task_attempt_resumed_after_tool_approval"
        for event in events
    ) == 1
    assert sum(event["event_type"] == "final_review_completed" for event in events) == 1
    assert not (workspace / "runs").exists()
    assert list((workspace / ".agentbus" / "runs").glob(f"*{run_id}.jsonl"))
    assert _git(workspace, "status", "--short") == " M module.py"


def test_cli_retry_review_uses_original_task_baseline(tmp_path: Path) -> None:
    workspace = _retry_repository(tmp_path / "workspace")
    config = _config(workspace, profile="tool-source-patch-review-retry")
    environment = {
        **os.environ,
        "AGENTBUS_PROVIDER": "deterministic",
        "AGENTBUS_DETERMINISTIC_PROFILE": "tool-source-patch-review-retry",
        "PYTHONDONTWRITEBYTECODE": "1",
    }

    completed = _cli(
        workspace,
        environment,
        "run",
        "--config",
        str(config),
        "--workflow",
        "multi",
        "--provider",
        "deterministic",
        "--durable",
        "--max-steps",
        "2",
        "Patch module.py once, retain it across review retry, and verify it.",
    )
    run_id = _run_id(completed.stdout)
    store = StateStore(workspace / ".agentbus" / "state.db")
    attempts = store.list_attempts(run_id, "step-1")

    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert store.get_run(run_id).status == RunStatus.SUCCEEDED
    assert len(attempts) == 2
    assert attempts[0].error_category is not None
    assert attempts[0].error_category.value == "reviewer_rejection"
    assert attempts[1].status == AttemptStatus.SUCCEEDED
    assert attempts[1].metadata["artifact_hygiene"]["review_files"] == [
        "module.py"
    ]
    assert (workspace / "module.py").read_text(encoding="utf-8") == "VALUE = 2\n"
    assert _git(workspace, "status", "--short") == " M module.py"


def test_cli_verifier_resume_fails_closed_after_tracked_source_divergence(
    tmp_path: Path,
) -> None:
    workspace = _repository(tmp_path / "workspace")
    marker = tmp_path / "maven-invocations.txt"
    executable_dir = _fake_maven(tmp_path / "bin", marker)
    config = _config(workspace)
    environment = {
        **os.environ,
        "AGENTBUS_PROVIDER": "deterministic",
        "AGENTBUS_DETERMINISTIC_PROFILE": "tool-source-patch",
        "PATH": os.pathsep.join((str(executable_dir), os.environ.get("PATH", ""))),
        "PYTHONDONTWRITEBYTECODE": "1",
    }

    started = _cli(
        workspace,
        environment,
        "run",
        "--config",
        str(config),
        "--workflow",
        "multi",
        "--provider",
        "deterministic",
        "--durable",
        "--max-steps",
        "2",
        "Patch module.py once and verify it with the repository test command.",
    )
    run_id = _run_id(started.stdout)
    first_store = StateStore(workspace / ".agentbus" / "state.db")
    attempt_id = first_store.list_attempts(run_id, "step-1")[0].attempt_id
    approved = _cli(
        workspace,
        environment,
        "approve",
        f"{run_id}:step-1",
        "--config",
        str(config),
        "--reason",
        "Approve the exact offline verifier invocation.",
    )
    assert started.returncode == 0, started.stdout + started.stderr
    assert approved.returncode == 0, approved.stdout + approved.stderr

    (workspace / "module.py").write_text("VALUE = 3\n", encoding="utf-8")
    resumed = _cli(
        workspace,
        environment,
        "resume",
        run_id,
        "--config",
        str(config),
    )
    final_store = StateStore(workspace / ".agentbus" / "state.db")
    attempts = final_store.list_attempts(run_id, "step-1")
    events = final_store.list_events(run_id)

    assert resumed.returncode == 1
    assert final_store.get_run(run_id).status == RunStatus.FAILED
    assert len(attempts) == 1
    assert attempts[0].attempt_id == attempt_id
    assert attempts[0].status == AttemptStatus.FAILED
    assert attempts[0].error_category is not None
    assert attempts[0].error_category.value == "resumability_failure"
    assert marker.exists() is False
    assert not any(
        event["event_type"] == "final_review_completed" for event in events
    )


def test_cli_malformed_verifier_continuation_fails_without_retry(
    tmp_path: Path,
) -> None:
    workspace = _repository(tmp_path / "workspace")
    marker = tmp_path / "maven-invocations.txt"
    executable_dir = _fake_maven(tmp_path / "bin", marker)
    config = _config(workspace)
    environment = {
        **os.environ,
        "AGENTBUS_PROVIDER": "deterministic",
        "AGENTBUS_DETERMINISTIC_PROFILE": "tool-source-patch",
        "PATH": os.pathsep.join((str(executable_dir), os.environ.get("PATH", ""))),
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    started = _cli(
        workspace,
        environment,
        "run",
        "--config",
        str(config),
        "--workflow",
        "multi",
        "--provider",
        "deterministic",
        "--durable",
        "--max-steps",
        "2",
        "Patch module.py once and verify it with the repository test command.",
    )
    run_id = _run_id(started.stdout)
    approved = _cli(
        workspace,
        environment,
        "approve",
        f"{run_id}:step-1",
        "--config",
        str(config),
        "--reason",
        "Approve the exact offline verifier invocation.",
    )
    assert started.returncode == 0, started.stdout + started.stderr
    assert approved.returncode == 0, approved.stdout + approved.stderr

    store = StateStore(workspace / ".agentbus" / "state.db")
    attempt = store.list_attempts(run_id, "step-1")[0]
    metadata = dict(attempt.metadata)
    internal = dict(metadata["_agentbus"])
    internal["task_continuation"] = {"stage": "verifier"}
    metadata["_agentbus"] = internal
    with store._write_transaction() as connection:
        connection.execute(
            "UPDATE attempts SET metadata_json = ? WHERE attempt_id = ?",
            (json.dumps(metadata, sort_keys=True), attempt.attempt_id),
        )

    resumed = _cli(
        workspace,
        environment,
        "resume",
        run_id,
        "--config",
        str(config),
    )
    final_store = StateStore(workspace / ".agentbus" / "state.db")
    attempts = final_store.list_attempts(run_id, "step-1")

    assert resumed.returncode == 1
    assert final_store.get_run(run_id).status == RunStatus.FAILED
    assert len(attempts) == 1
    assert attempts[0].attempt_id == attempt.attempt_id
    assert attempts[0].status == AttemptStatus.FAILED
    assert attempts[0].error_category is not None
    assert attempts[0].error_category.value == "resumability_failure"
    assert marker.exists() is False


def _continuation(attempt) -> dict:
    internal = attempt.metadata.get("_agentbus", {})
    if not isinstance(internal, dict):
        return {}
    value = internal.get("task_continuation") or internal.get("loop_continuation")
    return value if isinstance(value, dict) else {}


def _repository(path: Path) -> Path:
    path.mkdir()
    _git(path, "init", "-q")
    _git(path, "config", "user.name", "AgentBus Tests")
    _git(path, "config", "user.email", "agentbus@example.invalid")
    (path / ".gitignore").write_text(".agentbus/\ntarget/\n", encoding="utf-8")
    (path / "module.py").write_text("VALUE = 1\n", encoding="utf-8")
    (path / "pom.xml").write_text("<project></project>\n", encoding="utf-8")
    _git(path, "add", ".gitignore", "module.py", "pom.xml")
    _git(path, "commit", "-q", "-m", "test: initialize CLI fixture")
    return path.resolve()


def _retry_repository(path: Path) -> Path:
    path.mkdir()
    _git(path, "init", "-q")
    _git(path, "config", "user.name", "AgentBus Tests")
    _git(path, "config", "user.email", "agentbus@example.invalid")
    (path / ".gitignore").write_text(
        ".agentbus/\n.pytest_cache/\n__pycache__/\n",
        encoding="utf-8",
    )
    (path / "module.py").write_text("VALUE = 1\n", encoding="utf-8")
    (path / "test_module.py").write_text(
        "from module import VALUE\n\n\ndef test_value():\n    assert VALUE == 2\n",
        encoding="utf-8",
    )
    _git(path, "add", ".gitignore", "module.py", "test_module.py")
    _git(path, "commit", "-q", "-m", "test: initialize retry fixture")
    return path.resolve()


def _config(workspace: Path, *, profile: str = "tool-source-patch") -> Path:
    runtime = workspace / ".agentbus"
    runtime.mkdir()
    config = runtime / "config.toml"
    config.write_text(
        "\n".join(
            (
                "[agentbus]",
                'provider_name = "deterministic"',
                f"deterministic_profile = {json.dumps(profile)}",
                f"workspace_dir = {json.dumps(str(workspace))}",
                f"state_dir = {json.dumps(str(runtime))}",
                'state_db = "state.db"',
                f"runs_dir = {json.dumps(str(runtime / 'runs'))}",
                "durable_execution = true",
                "repository_intelligence = false",
                "max_steps = 2",
                "",
            )
        ),
        encoding="utf-8",
    )
    return config


def _fake_maven(directory: Path, marker: Path) -> Path:
    directory.mkdir()
    script = directory / "fake_maven.py"
    script.write_text(
        "\n".join(
            (
                "from pathlib import Path",
                f"marker = Path({str(marker)!r})",
                "count = int(marker.read_text(encoding='utf-8')) if marker.exists() else 0",
                "marker.write_text(f'{count + 1}\\n', encoding='utf-8')",
                "print('BUILD SUCCESS')",
                "",
            )
        ),
        encoding="utf-8",
    )
    if os.name == "nt":
        wrapper = directory / "mvn.cmd"
        wrapper.write_text(
            f'@"{sys.executable}" "{script}" %*\n',
            encoding="utf-8",
        )
    else:
        wrapper = directory / "mvn"
        wrapper.write_text(
            f"#!{sys.executable}\nexec(compile(open({str(script)!r}, encoding='utf-8').read(), {str(script)!r}, 'exec'))\n",
            encoding="utf-8",
        )
        wrapper.chmod(0o755)
    return directory


def _cli(
    workspace: Path,
    environment: dict[str, str],
    *arguments: str,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "agentbus.cli", *arguments],
        cwd=workspace,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
        shell=False,
        timeout=120,
    )


def _run_id(output: str) -> str:
    match = re.search(r"^Run ID: ([a-f0-9]+)$", output, flags=re.MULTILINE)
    assert match is not None, output
    return match.group(1)


def _git(path: Path, *arguments: str) -> str:
    return subprocess.run(
        ["git", *arguments],
        cwd=path,
        check=True,
        capture_output=True,
        text=True,
        shell=False,
        env={**os.environ, "GIT_CONFIG_NOSYSTEM": "1"},
    ).stdout.rstrip("\r\n")
