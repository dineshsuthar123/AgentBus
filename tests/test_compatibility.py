import json
from pathlib import Path

from agentbus import cli
from agentbus.product.compatibility import (
    SUPPORTED_PYTHON_VERSIONS,
    compatibility_manifest,
    current_python_supported,
    extension_package_metadata,
    validate_extension_package,
)


ROOT = Path(__file__).resolve().parents[1]


def test_compatibility_manifest_covers_product_protocols_and_schemas():
    payload = compatibility_manifest().to_dict()

    assert payload["version"] == "0.6.0b1"
    assert payload["protocols"] == {
        "control": "1.0",
        "tool": "1.0",
        "repository_intelligence": "1.0",
    }
    assert payload["schemas"] == {
        "state": 6,
        "trace": 1,
        "repository_intelligence": 1,
    }
    assert payload["supported_python"] == list(SUPPORTED_PYTHON_VERSIONS)
    assert current_python_supported() is True


def test_extension_declares_matching_agentbus_compatibility():
    path = ROOT / "extensions" / "vscode" / "package.json"

    metadata = extension_package_metadata(path)

    assert metadata["version"] == "0.6.0-beta.1"
    assert metadata["python"] == ">=0.6.0b1,<0.7.0"
    assert validate_extension_package(path) == []


def test_version_cli_exposes_complete_machine_readable_contract(capsys):
    assert cli.main(["version", "--json"]) == 0

    payload = json.loads(capsys.readouterr().out)

    assert payload["package"] == "agentbus"
    assert payload["running_python_supported"] is True
    assert payload["extension_compatibility"] == ">=0.6.0-beta.1 <0.7.0"
