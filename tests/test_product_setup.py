import json
import subprocess

from agentbus import cli
from agentbus.configuration import resolve_configuration
from agentbus.execution.schema import SCHEMA_VERSION
from agentbus.execution.state_store import StateStore
from agentbus.product.setup import detect_setup_environment, run_setup


def _repository(path):
    path.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=path, check=True, shell=False)
    return path


def test_noninteractive_setup_creates_offline_safe_product_state(tmp_path):
    workspace = _repository(tmp_path / "repository")
    root = tmp_path / "product"

    result = run_setup(
        workspace=workspace,
        provider="deterministic",
        config_root=root,
    )

    assert result.provider == "deterministic"
    assert result.to_dict()["network_used"] is False
    assert result.to_dict()["credentials_written"] is False
    assert StateStore(result.state_database).schema_version == SCHEMA_VERSION
    resolved = resolve_configuration(
        config_file=result.config_file,
        discover=False,
        environ={},
    )
    assert resolved.config.provider_name == "deterministic"
    assert resolved.config.durable_execution is True
    assert resolved.config.repository_intelligence is True
    assert "api_key" not in result.config_file.read_text(encoding="utf-8").lower()


def test_setup_dry_run_creates_nothing(tmp_path):
    workspace = _repository(tmp_path / "repository")
    root = tmp_path / "product"

    result = run_setup(workspace=workspace, config_root=root, dry_run=True)

    assert result.dry_run is True
    assert result.created == ()
    assert not root.exists()


def test_setup_preserves_existing_configuration_without_force(tmp_path):
    workspace = _repository(tmp_path / "repository")
    root = tmp_path / "product"
    first = run_setup(workspace=workspace, config_root=root)
    before = first.config_file.read_bytes()

    second = run_setup(
        workspace=workspace,
        provider="azure",
        config_root=root,
    )

    assert second.existing_configuration_preserved is True
    assert second.created == ()
    assert first.config_file.read_bytes() == before


def test_setup_environment_detection_never_reports_secret_values(tmp_path):
    workspace = _repository(tmp_path / "repository")
    secret = "azure-secret-must-not-appear"

    detections = detect_setup_environment(
        workspace,
        environ={
            "AZURE_OPENAI_ENDPOINT": "https://example.invalid",
            "AZURE_OPENAI_API_KEY": secret,
            "AZURE_OPENAI_DEFAULT_DEPLOYMENT": "fixture",
        },
    )

    rendered = json.dumps([item.to_dict() for item in detections])
    assert secret not in rendered
    assert next(item for item in detections if item.name == "azure-configuration").available


def test_setup_cli_noninteractive_json_uses_deterministic_provider(tmp_path, capsys):
    workspace = _repository(tmp_path / "repository")
    root = tmp_path / "product"

    assert cli.main(
        [
            "setup",
            "--non-interactive",
            "--workspace",
            str(workspace),
            "--root",
            str(root),
            "--json",
        ]
    ) == 0
    payload = json.loads(capsys.readouterr().out)

    assert payload["provider"] == "deterministic"
    assert payload["network_used"] is False
    assert payload["credentials_written"] is False
