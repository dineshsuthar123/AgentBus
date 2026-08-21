from __future__ import annotations

import string
import unicodedata

from hypothesis import assume, given, settings, strategies as st

from agentbus.intelligence.identities import (
    file_id,
    module_id,
    reference_id,
    repository_identity,
    stable_hash,
    symbol_id,
    workspace_identity,
)
from agentbus.intelligence.models import (
    RepositoryIdentity,
    SymbolKind,
    WorkspaceIdentity,
)


PROPERTY_SETTINGS = settings(
    max_examples=100,
    deadline=None,
    derandomize=True,
    database=None,
)
_KEY_SEGMENT = st.text(
    string.ascii_letters + string.digits + "._-",
    min_size=1,
    max_size=32,
).filter(lambda value: value.strip("._-") != "")
_PATH_SEGMENT = st.text(
    string.ascii_letters + string.digits + "_-",
    min_size=1,
    max_size=24,
)
_QUALIFIED = st.text(
    string.ascii_letters + string.digits + "._",
    min_size=1,
    max_size=80,
).filter(lambda value: value.strip("._") != "")
_BACKSLASH = chr(92)


@PROPERTY_SETTINGS
@given(segments=st.lists(_KEY_SEGMENT, min_size=1, max_size=6))
def test_repository_identity_is_case_separator_and_serialization_stable(
    segments: list[str],
) -> None:
    portable = "/".join(segments)
    alternate = _BACKSLASH.join(
        segment.swapcase() for segment in segments
    )

    expected = repository_identity(portable)
    observed = repository_identity(alternate)
    restored = RepositoryIdentity.model_validate_json(expected.model_dump_json())

    assert observed.repository_id == expected.repository_id
    assert observed.key_hash == expected.key_hash
    assert restored == expected
    assert expected.repository_id.startswith("repo_")
    assert len(expected.key_hash) == 64


@PROPERTY_SETTINGS
@given(
    first=st.lists(_KEY_SEGMENT, min_size=1, max_size=4),
    second=st.lists(_KEY_SEGMENT, min_size=1, max_size=4),
)
def test_distinct_portable_repository_keys_do_not_alias(
    first: list[str],
    second: list[str],
) -> None:
    first_key = "/".join(first).casefold()
    second_key = "/".join(second).casefold()
    assume(first_key != second_key)

    assert (
        repository_identity(first_key).repository_id
        != repository_identity(second_key).repository_id
    )


@PROPERTY_SETTINGS
@given(roots=st.lists(_PATH_SEGMENT, min_size=1, max_size=12))
def test_workspace_identity_is_order_and_duplicate_independent(
    roots: list[str],
) -> None:
    repository = repository_identity("property/workspace")
    forward = workspace_identity(repository.repository_id, roots)
    reordered = workspace_identity(
        repository.repository_id,
        [*reversed(roots), *roots],
    )
    restored = WorkspaceIdentity.model_validate_json(forward.model_dump_json())

    assert reordered == forward
    assert restored == forward
    assert forward.roots == tuple(sorted(set(roots)))


@PROPERTY_SETTINGS
@given(
    parts=st.lists(_PATH_SEGMENT, min_size=1, max_size=8),
    qualified_name=_QUALIFIED,
    ordinal=st.integers(min_value=0, max_value=32),
)
def test_file_module_and_symbol_ids_are_portable_and_deterministic(
    parts: list[str],
    qualified_name: str,
    ordinal: int,
) -> None:
    repository = repository_identity("property/symbols")
    relative_path = "/".join(parts) + ".py"
    alternate_path = _BACKSLASH.join(parts) + ".py"

    expected_file = file_id(repository.repository_id, relative_path)
    observed_file = file_id(repository.repository_id, alternate_path)
    expected_module = module_id(
        "project_property",
        relative_path,
        qualified_name,
    )
    observed_module = module_id(
        "project_property",
        alternate_path,
        f" {qualified_name} ",
    )
    expected_symbol = symbol_id(
        expected_file,
        qualified_name,
        SymbolKind.FUNCTION,
        signature=f"{qualified_name}()",
        ordinal=ordinal,
    )
    observed_symbol = symbol_id(
        observed_file,
        f" {qualified_name} ",
        SymbolKind.FUNCTION,
        signature=f" {qualified_name}() ",
        ordinal=ordinal,
    )

    assert observed_file == expected_file
    assert observed_module == expected_module
    assert observed_symbol == expected_symbol
    assert expected_symbol.startswith("symbol_")
    assert symbol_id(
        expected_file,
        qualified_name,
        SymbolKind.FUNCTION,
        signature=f"{qualified_name}()",
        ordinal=ordinal + 1,
    ) != expected_symbol


@PROPERTY_SETTINGS
@given(
    composed=st.sampled_from(
        (
            "caf\u00e9",
            "\uff21\uff30\uff29",
            "\u212aernel",
            "\u00e9",
        )
    )
)
def test_symbol_identity_uses_nfkc_canonical_text(composed: str) -> None:
    decomposed = unicodedata.normalize("NFD", composed)
    compatible = unicodedata.normalize("NFKC", composed)
    source = file_id("repo_property", "source.py")

    assert symbol_id(
        source,
        decomposed,
        SymbolKind.FUNCTION,
    ) == symbol_id(
        source,
        compatible,
        SymbolKind.FUNCTION,
    )


@PROPERTY_SETTINGS
@given(
    line=st.integers(min_value=1, max_value=1_000_000),
    column=st.integers(min_value=0, max_value=10_000),
    target=_QUALIFIED,
)
def test_reference_identity_is_stable_and_location_sensitive(
    line: int,
    column: int,
    target: str,
) -> None:
    source = file_id("repo_property", "source.py")
    expected = reference_id(
        source,
        "source.py",
        line,
        column,
        target,
        "call",
    )
    repeated = reference_id(
        source,
        "source.py",
        line,
        column,
        target,
        "call",
    )
    moved = reference_id(
        source,
        "source.py",
        line + 1,
        column,
        target,
        "call",
    )

    assert repeated == expected
    assert moved != expected


@PROPERTY_SETTINGS
@given(values=st.dictionaries(_KEY_SEGMENT, st.integers(), max_size=30))
def test_stable_hash_is_mapping_order_independent(values: dict[str, int]) -> None:
    reversed_items = dict(reversed(tuple(values.items())))

    assert stable_hash(values) == stable_hash(reversed_items)
    assert len(stable_hash(values)) == 64
