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
