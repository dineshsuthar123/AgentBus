import pytest

from agentbus.intelligence.fingerprints import (
    content_hash,
    file_set_fingerprint,
    parser_versions_fingerprint,
)
from agentbus.intelligence.identities import (
    edge_id,
    file_id,
    project_id,
    repository_identity,
    stable_hash,
    symbol_id,
    workspace_identity,
)
from agentbus.intelligence.models import ProjectKind, SymbolKind


def test_repository_identity_is_portable_and_case_normalized():
    first = repository_identity("Example/AgentBus", display_name="AgentBus")
    second = repository_identity("example/agentbus", display_name="Different clone")

    assert first.repository_id == second.repository_id
    assert first.key_hash == second.key_hash
    assert first.display_name == "AgentBus"


@pytest.mark.parametrize(
    "key",
    [
        "C:/Users/person/repository",
        r"C:\Users\person\repository",
        "/home/person/repository",
        r"\\server\share\repository",
        "file:///home/person/repository",
    ],
)
def test_repository_identity_rejects_absolute_personal_paths(key):
    with pytest.raises(ValueError, match="portable"):
        repository_identity(key)


def test_workspace_and_entity_ids_ignore_input_order():
    repository = repository_identity("example/monorepo")
    first = workspace_identity(
        repository.repository_id,
        ["services/api", "", "packages/ui"],
    )
    second = workspace_identity(
        repository.repository_id,
        ["packages/ui", "services/api", ""],
    )
    project = project_id(
        repository.repository_id,
        "services/api",
        ProjectKind.PYTHON,
        name="api",
    )
    source = file_id(repository.repository_id, "services/api/app.py")
    symbol = symbol_id(
        source,
        "api.app.create",
        SymbolKind.FUNCTION,
        signature="(request)",
    )

    assert first == second
    assert project.startswith("project_")
    assert source.startswith("file_")
    assert symbol.startswith("symbol_")


def test_symbol_and_edge_ids_change_with_semantic_identity():
    source_file = f"file_{'a' * 64}"
    first = symbol_id(source_file, "module.run", SymbolKind.FUNCTION)
    second = symbol_id(source_file, "module.run", SymbolKind.METHOD)

    assert first != second
    assert edge_id(first, second, "calls") != edge_id(second, first, "calls")


def test_fingerprints_are_deterministic_and_order_independent():
    first = file_set_fingerprint(
        {
            "src/a.py": content_hash("a"),
            "src/b.py": content_hash("b"),
        }
    )
    second = file_set_fingerprint(
        {
            "src/b.py": content_hash("b"),
            "src/a.py": content_hash("a"),
        }
    )
    parsers_a = parser_versions_fingerprint({"python": "1", "go": "2"})
    parsers_b = parser_versions_fingerprint({"go": "2", "python": "1"})

    assert first == second
    assert parsers_a == parsers_b
    assert stable_hash({"b": 2, "a": 1}) == stable_hash({"a": 1, "b": 2})


def test_fingerprint_rejects_repository_traversal():
    with pytest.raises(ValueError, match="traverse"):
        file_set_fingerprint({"../private.txt": content_hash("private")})
