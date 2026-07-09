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