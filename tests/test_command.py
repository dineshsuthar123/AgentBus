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
                "print(os.environ.get('AGENTBUS_INHERITED_MARKER') == 'present')\n"
                "print(os.environ.get('PYTHONDONTWRITEBYTECODE'))"
            ),
        ],
        environment_overrides={"PYTHONDONTWRITEBYTECODE": "1"},
    )

    assert result["passed"] is True
    assert "True" in result["stdout"]
    assert "1" in result["stdout"]
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
