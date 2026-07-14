import sys

import agentbus.tools.command as command_module
from agentbus.tools.command import CommandTools


def test_blocks_unknown_command(tmp_path):
    cmd = CommandTools(workspace=str(tmp_path))

    result = cmd.run_command(["unknown_command_123"])

    assert "Blocked command" in result


def test_runs_python_command(tmp_path):
    cmd = CommandTools(workspace=str(tmp_path))

    result = cmd.run_command(["python", "-c", "print('hello')"])

    assert "Exit code: 0" in result
    assert "hello" in result


def test_python_command_uses_the_active_interpreter_without_changing_reported_command(
    tmp_path,
    monkeypatch,
):
    captured = {}

    def fake_run(command, **kwargs):
        captured["command"] = command
        captured["kwargs"] = kwargs
        return type(
            "CompletedCommand",
            (),
            {"returncode": 0, "stdout": "active interpreter\n", "stderr": ""},
        )()

    monkeypatch.setattr(command_module.subprocess, "run", fake_run)
    cmd = CommandTools(workspace=str(tmp_path))

    result = cmd.run_command_result(["python", "-c", "print('active interpreter')"])

    assert captured["command"][0] == sys.executable
    assert captured["kwargs"]["shell"] is False
    assert result["command"][0] == "python"
    assert result["passed"] is True


def test_blocks_non_list_command(tmp_path):
    cmd = CommandTools(workspace=str(tmp_path))

    result = cmd.run_command("python -c print('bad')")

    assert "list[str]" in result


def test_blocks_shell_syntax(tmp_path):
    cmd = CommandTools(workspace=str(tmp_path))

    result = cmd.run_command(["python", "-c", "print('hello')", "&&", "pytest"])

    assert "Blocked shell syntax" in result


def test_blocks_destructive_git_args(tmp_path):
    cmd = CommandTools(workspace=str(tmp_path))

    result = cmd.run_command(["git", "reset", "--hard"])

    assert "Blocked unsafe argument" in result


def test_safe_environment_override_inherits_environment_without_exposing_secret(
    tmp_path,
    monkeypatch,
):
    secret = "command-secret-must-not-appear"
    monkeypatch.setenv("AGENTBUS_INHERITED_MARKER", "present")
    monkeypatch.setenv("AGENTBUS_TEST_SECRET", secret)
    cmd = CommandTools(workspace=str(tmp_path))

    result = cmd.run_command_result(
        [
            "python",
            "-c",
            (
                    "import os\n"
                    "secret_name = 'AGENTBUS_' + 'TEST_' + 'SECRET'\n"
                    "print(os.environ.get('AGENTBUS_INHERITED_MARKER') == 'present')\n"
                    "print(os.environ.get('PYTHONDONTWRITEBYTECODE'))\n"
                    "print(os.environ.get(secret_name) is None)"
                ),
        ],
        environment_overrides={"PYTHONDONTWRITEBYTECODE": "1"},
    )

    assert result["passed"] is True
    assert "True" in result["stdout"]
    assert "1" in result["stdout"]
    assert result["stdout"].splitlines()[-1] == "True"
    assert secret not in str(result)
    assert "AGENTBUS_TEST_SECRET" not in str(result)


def test_rejects_arbitrary_environment_override(tmp_path):
    cmd = CommandTools(workspace=str(tmp_path))

    result = cmd.run_command_result(
        ["python", "-c", "print('no-op')"],
        environment_overrides={"UNSAFE_MODEL_CONTROLLED_VALUE": "1"},
    )

    assert result["blocked"] is True
    assert "Blocked environment override" in result["output"]


def test_command_diagnostics_redact_sensitive_arguments_and_output(tmp_path):
    cmd = CommandTools(workspace=str(tmp_path))

    result = cmd.run_command_result(
        [
            "python",
            "-c",
            "print('api_key=output-secret')",
            "--token",
            "argument-secret",
        ]
    )

    assert result["passed"] is True
    assert result["command"][-1] == "[REDACTED]"
    assert "argument-secret" not in str(result)
    assert "output-secret" not in str(result)
    assert "api_key=[REDACTED]" in result["stdout"]
