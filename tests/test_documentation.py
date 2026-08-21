from __future__ import annotations

import re
import shlex
from pathlib import Path

from agentbus.cli import _root_parser


ROOT = Path(__file__).resolve().parents[1]
VALIDATION_DOCUMENTS = (
    "docs/validation/real-repositories.md",
    "docs/validation/adversarial-testing.md",
    "docs/validation/reliability.md",
    "docs/validation/performance.md",
    "docs/validation/release-candidate.md",
)
REQUIRED_DOCUMENTS = (
    "docs/getting-started/install.md",
    "docs/getting-started/quickstart.md",
    "docs/getting-started/vscode.md",
    "docs/guides/providers.md",
    "docs/guides/repository-intelligence.md",
    "docs/guides/tools-and-approvals.md",
    "docs/guides/replay.md",
    "docs/guides/mcp.md",
    "docs/guides/workflows.md",
    "docs/reference/configuration.md",
    "docs/reference/cli.md",
    "docs/reference/environment-variables.md",
    "docs/reference/storage.md",
    "docs/troubleshooting/install.md",
    "docs/troubleshooting/daemon.md",
    "docs/troubleshooting/providers.md",
    "docs/troubleshooting/indexing.md",
    *VALIDATION_DOCUMENTS,
)
USER_GUIDES = tuple(Path(path) for path in REQUIRED_DOCUMENTS) + (
    Path("README.md"),
    Path("docs/README.md"),
    Path("docs/guides/performance.md"),
    Path("docs/reference/compatibility.md"),
)
MARKDOWN_LINK = re.compile(r"\[[^\]]+\]\(([^)]+)\)")
FENCED_BLOCK = re.compile(r"```[^\n]*\n(.*?)```", re.DOTALL)


def test_required_public_beta_documentation_exists() -> None:
    missing = [path for path in REQUIRED_DOCUMENTS if not (ROOT / path).is_file()]
    assert missing == []
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "0.6.0b1" in readme
    assert "agentbus quickstart --json" in readme
    assert "complete sandbox isolation" in readme


def test_relative_markdown_links_resolve() -> None:
    documents = [
        *ROOT.glob("*.md"),
        *ROOT.joinpath("docs").rglob("*.md"),
        *ROOT.joinpath("examples").rglob("*.md"),
    ]
    broken: list[str] = []
    for document in documents:
        text = document.read_text(encoding="utf-8")
        for raw_target in MARKDOWN_LINK.findall(text):
            target = raw_target.strip().split("#", 1)[0].split("?", 1)[0]
            if not target or ":" in target or target.startswith("/"):
                continue
            if not (document.parent / target).resolve().exists():
                broken.append(f"{document.relative_to(ROOT)} -> {raw_target}")
    assert broken == []


def test_documented_agentbus_commands_are_registered() -> None:
    parser = _root_parser()
    registered = next(
        action.choices
        for action in parser._actions
        if getattr(action, "choices", None)
    )
    invalid: list[str] = []
    for relative_path in USER_GUIDES:
        text = (ROOT / relative_path).read_text(encoding="utf-8")
        for block in FENCED_BLOCK.findall(text):
            for line in block.splitlines():
                stripped = line.strip()
                if not stripped.startswith("agentbus "):
                    continue
                tokens = shlex.split(stripped, posix=False)
                if len(tokens) < 2 or tokens[1] not in registered:
                    invalid.append(f"{relative_path}: {stripped}")
    assert invalid == []


def test_quickstart_examples_match_current_cli() -> None:
    quickstart = (ROOT / "docs/getting-started/quickstart.md").read_text(
        encoding="utf-8"
    )
    for snippet in (
        "agentbus setup --workspace . --provider deterministic",
        "agentbus doctor --workspace . --provider deterministic --json",
        "agentbus index build --workspace . --json",
        "agentbus replay <run-id> --mode offline --json",
        "agentbus cleanup --dry-run --stale --json",
    ):
        assert snippet in quickstart


def test_validation_documentation_preserves_evidence_boundaries() -> None:
    documents = {
        Path(path).name: (ROOT / path).read_text(encoding="utf-8")
        for path in VALIDATION_DOCUMENTS
    }

    assert "## Authorization boundary" in documents["real-repositories.md"]
    assert "## Resource limits" in documents["real-repositories.md"]
    assert "## Known gaps" in documents["real-repositories.md"]
    assert "## Fixture methodology" in documents["adversarial-testing.md"]
    assert "local defensive testing" in documents["adversarial-testing.md"]
    assert "## What this does not prove" in documents["adversarial-testing.md"]
    assert "## Bounded profiles" in documents["reliability.md"]
    assert "Windows and Linux" in documents["reliability.md"]
    assert "## Comparison policy" in documents["performance.md"]
    assert "50,000" in documents["performance.md"]
    assert "## Required gates" in documents["release-candidate.md"]
    assert "No live model provider" in documents["release-candidate.md"]
