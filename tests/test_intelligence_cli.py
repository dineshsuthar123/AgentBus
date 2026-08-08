from __future__ import annotations

import json
from pathlib import Path

from agentbus import cli


def _configured_workspace(tmp_path: Path) -> tuple[Path, Path]:
    workspace = tmp_path / "repository"
    workspace.mkdir()
    (workspace / "pyproject.toml").write_text(
        "[project]\nname = \"sample-cli\"\nversion = \"0.1.0\"\n",
        encoding="utf-8",
    )
    (workspace / "calculator.py").write_text(
        "def add(left: int, right: int) -> int:\n"
        "    \"\"\"cli-private-source-marker\"\"\"\n"
        "    return left + right\n\n"
        "def calculate() -> int:\n"
        "    return add(1, 2)\n",
        encoding="utf-8",
    )
    (workspace / "test_calculator.py").write_text(
        "from calculator import add\n\n"
        "def test_add() -> None:\n"
        "    assert add(2, 3) == 5\n",
        encoding="utf-8",
    )
    config = tmp_path / "agentbus.json"
    config.write_text(
        json.dumps(
            {
                "agentbus": {
                    "workspace_dir": str(workspace),
                    "state_db": str(tmp_path / "state" / "state.db"),
                }
            }
        ),
        encoding="utf-8",
    )
    return workspace, config


def _run_json(capsys, arguments: list[str]) -> dict:
    assert cli.main([*arguments, "--json"]) == 0
    return json.loads(capsys.readouterr().out)


def test_root_help_lists_repository_intelligence_commands(capsys) -> None:
    assert cli.main(["--help"]) == 0

    output = capsys.readouterr().out
    for command in (
        "index",
        "search",
        "symbols",
        "dependencies",
        "dependents",
        "impact",
        "tests-for",
        "context-plan",
    ):
        assert command in output


def test_index_cli_lifecycle_is_offline_and_confirmation_gated(
    tmp_path: Path,
    capsys,
) -> None:
    workspace, config = _configured_workspace(tmp_path)
    common = ["--config", str(config)]

    built = _run_json(capsys, ["index", "build", *common])
    status = _run_json(capsys, ["index", "status", *common])
    verified = _run_json(capsys, ["index", "verify", *common])

    assert built["operation"] == "build"
    assert built["status"]["state"] == "current"
    assert built["provider_calls"] == 0
    assert built["network_calls"] == 0
    assert "indexed_paths" not in built
    assert status["state"] == "current"
    assert verified["valid"] is True

    calculator = workspace / "calculator.py"
    calculator.write_text(
        calculator.read_text(encoding="utf-8") + "\nVALUE = 3\n",
        encoding="utf-8",
    )
    stale = _run_json(capsys, ["index", "status", *common, "--evidence"])
    updated = _run_json(capsys, ["index", "update", *common, "--evidence"])
    repaired = _run_json(capsys, ["index", "repair", *common])
    collected = _run_json(
        capsys,
        ["index", "gc", "--retain", "1", *common],
    )

    assert stale["state"] == "stale"
    assert stale["stale_paths"] == ["calculator.py"]
    assert updated["status"]["state"] == "current"
    assert "calculator.py" in updated["indexed_paths"]
    assert repaired["operation"] == "repair"
    assert collected["retained_snapshots"] == 1

    assert cli.main(["index", "clear", *common, "--json"]) == 2
    refused = json.loads(capsys.readouterr().out)
    assert refused["deleted"] is False
    assert "--yes" in refused["error"]

    cleared = _run_json(capsys, ["index", "clear", "--yes", *common])
    assert cleared["status"]["state"] == "absent"
    assert cleared["deleted_snapshot_count"] >= 1
    assert calculator.exists()


def test_query_cli_filters_evidence_and_source_content(
    tmp_path: Path,
    capsys,
) -> None:
    _, config = _configured_workspace(tmp_path)
    common = ["--config", str(config)]
    _run_json(capsys, ["index", "build", *common])

    search = _run_json(
        capsys,
        [
            "search",
            "add",
            "--project",
            "sample-cli",
            "--language",
            "python",
            *common,
        ],
    )
    search_evidence = _run_json(
        capsys,
        ["search", "add", "--evidence", *common],
    )
    symbols = _run_json(
        capsys,
        ["symbols", "calculator.py", "--language", "python", *common],
    )
    dependencies = _run_json(
        capsys,
        ["dependencies", "calculate", "--depth", "2", *common],
    )
    dependents = _run_json(
        capsys,
        ["dependents", "add", "--depth", "2", "--evidence", *common],
    )

    assert search["results"]
    assert "explanation" not in search["results"][0]
    assert "score_components" not in search["results"][0]
    assert search_evidence["results"][0]["explanation"]
    assert any(item["name"] == "add" for item in symbols["symbols"])
    assert all("signature" not in item for item in symbols["symbols"])
    assert dependencies["subject"]["name"] == "calculate"
    assert dependencies["edges"]
    assert dependents["subject"]["name"] == "add"
    assert dependents["edges"][0]["explanation"]
    output = json.dumps(
        {
            "search": search,
            "search_evidence": search_evidence,
            "symbols": symbols,
            "dependencies": dependencies,
            "dependents": dependents,
        },
        sort_keys=True,
    )
    assert "cli-private-source-marker" not in output
    assert '"content"' not in output
    assert '"documentation"' not in output


def test_impact_tests_and_context_plan_cli_are_bounded(
    tmp_path: Path,
    capsys,
) -> None:
    _, config = _configured_workspace(tmp_path)
    common = ["--config", str(config)]
    _run_json(capsys, ["index", "build", *common])

    impact = _run_json(
        capsys,
        ["impact", "calculator.py", "--depth", "3", *common],
    )
    tests = _run_json(
        capsys,
        ["tests-for", "calculator.py", "--depth", "3", *common],
    )
    context = _run_json(
        capsys,
        [
            "context-plan",
            "Change",
            "calculate",
            "safely",
            "--role",
            "coder",
            "--project",
            "sample-cli",
            "--byte-budget",
            "20000",
            "--token-budget",
            "4000",
            *common,
        ],
    )
    context_evidence = _run_json(
        capsys,
        [
            "context-plan",
            "Change calculate safely",
            "--evidence",
            *common,
        ],
    )

    assert impact["changed_paths"] == ["calculator.py"]
    assert "evidence" not in impact
    assert "test_calculator.py" in tests["selected_tests"]
    assert "evidence" not in tests
    assert context["role"] == "coder"
    assert any(item["selected"] for item in context["candidates"])
    assert all("reasons" not in item for item in context["candidates"])
    assert any(
        item.get("reasons")
        for item in context_evidence["candidates"]
        if item["selected"]
    )
    payload = json.dumps(context_evidence, sort_keys=True)
    assert "cli-private-source-marker" not in payload
    assert '"content"' not in payload


def test_human_search_output_has_a_hard_item_bound(
    tmp_path: Path,
    capsys,
) -> None:
    workspace, config = _configured_workspace(tmp_path)
    (workspace / "many.py").write_text(
        "\n\n".join(
            f"def searchable_{index}() -> int:\n    return {index}"
            for index in range(80)
        )
        + "\n",
        encoding="utf-8",
    )
    common = ["--config", str(config)]
    _run_json(capsys, ["index", "build", *common])

    assert cli.main(
        ["search", "searchable", "--limit", "200", *common]
    ) == 0
    lines = capsys.readouterr().out.splitlines()

    assert len(lines) <= 2 + 50
