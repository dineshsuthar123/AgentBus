import json
import os
import tomllib

import pytest

from agentbus.configuration import resolve_configuration
from agentbus.product.config_store import (
    ConfigScope,
    config_target_path,
    ensure_safe_config_target,
    parse_config_value,
    read_config_document,
    render_toml,
    set_config_value,
    unset_config_value,
)


def test_config_store_atomically_sets_validated_values(tmp_path):
    path = tmp_path / "config.toml"

    first = set_config_value(path, "max_steps", 8)
    second = set_config_value(path, "log_level", "debug")
    resolved = resolve_configuration(config_file=path, discover=False, environ={})

    assert first.changed is True
    assert second.changed is True
    assert resolved.config.max_steps == 8
    assert resolved.config.log_level == "debug"
    assert not list(tmp_path.glob(".*.toml"))


def test_invalid_candidate_never_replaces_good_configuration(tmp_path):
    path = tmp_path / "config.toml"
    set_config_value(path, "max_steps", 8)
    before = path.read_bytes()

    with pytest.raises(ValueError, match="greater than 0"):
        set_config_value(path, "max_steps", 0)

    assert path.read_bytes() == before


def test_config_store_unsets_without_destroying_other_values(tmp_path):
    path = tmp_path / "config.toml"
    set_config_value(path, "max_steps", 8)
    set_config_value(path, "log_level", "info")

    result = unset_config_value(path, "max_steps")

    assert result.changed is True
    assert read_config_document(path) == {"log_level": "info"}
    assert unset_config_value(path, "max_steps").changed is False


def test_config_store_refuses_secret_fields(tmp_path):
    with pytest.raises(ValueError, match="secure store"):
        set_config_value(tmp_path / "config.toml", "azure_openai_api_key", "secret")


def test_cli_values_are_typed_without_evaluating_code():
    assert parse_config_value("max_steps", "9") == 9
    assert parse_config_value("parallel_execution", "yes") is True
    assert parse_config_value("model_name", "model; remove-everything") == (
        "model; remove-everything"
    )
    assert parse_config_value("deterministic_failure_calls", "[1, 3]") == [1, 3]


def test_toml_renderer_round_trips_nested_product_values():
    document = {
        "provider_name": "deterministic",
        "tool_resource_budget": {
            "wall_clock_seconds": 20,
            "invocations_per_task": 2,
        },
        "mcp_server_configs": [
            {
                "server_id": "fixture",
                "transport": "stdio",
                "arguments": ["-m", "fixture"],
            }
        ],
    }

    rendered = render_toml(document)

    assert tomllib.loads(rendered)["agentbus"] == document


def test_config_target_paths_are_scope_specific(tmp_path):
    user = config_target_path(
        ConfigScope.USER,
        environ={
            "APPDATA": str(tmp_path / "roaming"),
            "XDG_CONFIG_HOME": str(tmp_path / "xdg"),
        },
    )
    workspace = config_target_path(ConfigScope.WORKSPACE, workspace=tmp_path)

    expected_user = (
        tmp_path / "roaming" / "AgentBus" / "config.toml"
        if os.name == "nt"
        else tmp_path / "xdg" / "agentbus" / "config.toml"
    )
    assert user == expected_user
    assert workspace == tmp_path / ".agentbus" / "config.toml"


def test_workspace_config_target_rejects_external_directory_link(tmp_path):
    workspace = tmp_path / "repository"
    outside = tmp_path / "outside"
    workspace.mkdir()
    outside.mkdir()
    try:
        (workspace / ".agentbus").symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("directory symlinks are unavailable on this platform")

    with pytest.raises(ValueError, match="outside the workspace"):
        ensure_safe_config_target(
            workspace / ".agentbus" / "config.toml",
            workspace=workspace,
        )


def test_json_configuration_is_preserved_as_json(tmp_path):
    path = tmp_path / "config.json"
    set_config_value(path, "max_steps", 8)

    assert json.loads(path.read_text(encoding="utf-8")) == {
        "agentbus": {"max_steps": 8}
    }
