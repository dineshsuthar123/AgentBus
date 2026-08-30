import json
import os
import subprocess

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


def test_payment_demo_contains_concurrent_retry_proof(tmp_path):
    result = create_demo("payment", tmp_path / "payment")
    source = (
        result.workspace
        / "src/main/java/com/agentbus/demo/PaymentService.java"
    ).read_text(encoding="utf-8")
    test_source = (
        result.workspace
        / "src/test/java/com/agentbus/demo/PaymentServiceTest.java"
    ).read_text(encoding="utf-8")

    assert "confirmedPaymentIds.add(paymentId);\n        return 1;" in source
    assert "CountDownLatch" in test_source
    assert "assertEquals(1, accepted)" in test_source
    assert result.test_command == ("mvn", "-q", "-o", "test")


def test_demo_git_initialization_creates_clean_isolated_baseline(tmp_path):
    result = create_demo(
        "payment",
        tmp_path / "payment-git",
        initialize_git=True,
    )
    status = subprocess.run(
        ["git", "status", "--short"],
        cwd=result.workspace,
        capture_output=True,
        text=True,
        shell=False,
        check=False,
    )

    assert result.git_initialized is True
    assert status.returncode == 0
    assert status.stdout == ""


def test_demo_git_initialization_refuses_nonempty_destination(tmp_path):
    destination = tmp_path / "occupied"
    destination.mkdir()
    unrelated = destination / "notes.txt"
    unrelated.write_text("preserve", encoding="utf-8")

    with pytest.raises(ValueError, match="new empty"):
        create_demo("payment", destination, initialize_git=True)

    assert unrelated.read_text(encoding="utf-8") == "preserve"


@pytest.mark.skipif(os.name != "nt", reason="Windows command shim regression")
def test_demo_run_executes_windows_command_shim_with_shell_disabled(
    tmp_path,
    monkeypatch,
):
    workspace = tmp_path / "payment"
    create_demo("payment", workspace)
    executable_dir = tmp_path / "bin"
    executable_dir.mkdir()
    (executable_dir / "mvn.cmd").write_text(
        "@echo PAYMENT_DEMO_SHIM\r\n@exit /b 7\r\n",
        encoding="utf-8",
    )
    monkeypatch.setenv(
        "PATH",
        os.pathsep.join((str(executable_dir), os.environ.get("PATH", ""))),
    )

    result = run_demo("payment", workspace=workspace)

    assert result.test_executed is True
    assert result.test_exit_code == 7


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


def test_demo_cli_can_create_clean_payment_git_repository(tmp_path, capsys):
    output = tmp_path / "payment-demo"

    assert cli.main(
        [
            "demo",
            "create",
            "payment",
            "--output",
            str(output),
            "--git",
            "--json",
        ]
    ) == 0

    created = json.loads(capsys.readouterr().out)
    assert created["git_initialized"] is True
    assert subprocess.run(
        ["git", "status", "--short"],
        cwd=output,
        capture_output=True,
        text=True,
        shell=False,
        check=False,
    ).stdout == ""
