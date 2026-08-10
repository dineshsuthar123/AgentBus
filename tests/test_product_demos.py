import json

import pytest

from agentbus import cli
from agentbus.product.demos import DEMO_LANGUAGES, create_demo, run_demo


@pytest.mark.parametrize("language", DEMO_LANGUAGES)
def test_demo_creation_is_small_owned_and_task_oriented(tmp_path, language):
    result = create_demo(language, tmp_path / language)

    assert result.language == language
    assert len(result.created_files) <= 8
    assert ".agentbus-demo.json" in result.created_files
    assert "AGENTBUS_TASK.md" in result.created_files
    assert (result.workspace / "AGENTBUS_TASK.md").is_file()
    assert result.to_dict()["network_used"] is False


def test_python_demo_preflight_observes_intentional_failure(tmp_path):
    workspace = tmp_path / "python"
    create_demo("python", workspace)

    result = run_demo("python", workspace=workspace)

    assert result.test_executed is True
    assert result.test_exit_code != 0
    assert result.to_dict()["ready"] is True


def test_demo_refuses_unmanaged_nonempty_destination(tmp_path):
    destination = tmp_path / "existing"
    destination.mkdir()
    unrelated = destination / "user.txt"
    unrelated.write_text("preserve me", encoding="utf-8")

    with pytest.raises(ValueError, match="not owned"):
        create_demo("python", destination, force=True)

    assert unrelated.read_text(encoding="utf-8") == "preserve me"


def test_force_only_updates_marker_owned_demo_and_preserves_unrelated_file(tmp_path):
    destination = tmp_path / "python"
    create_demo("python", destination)
    unrelated = destination / "notes.txt"
    unrelated.write_text("keep", encoding="utf-8")

    create_demo("python", destination, force=True)

    assert unrelated.read_text(encoding="utf-8") == "keep"


def test_demo_cli_list_and_create_are_machine_readable(tmp_path, capsys):
    assert cli.main(["demo", "list", "--json"]) == 0
    listing = json.loads(capsys.readouterr().out)
    assert {item["language"] for item in listing["demos"]} == set(DEMO_LANGUAGES)

    output = tmp_path / "go-demo"
    assert cli.main(["demo", "create", "go", "--output", str(output), "--json"]) == 0
    created = json.loads(capsys.readouterr().out)
    assert created["workspace"] == str(output.resolve())
    assert created["network_used"] is False
