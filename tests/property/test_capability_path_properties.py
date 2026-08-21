from __future__ import annotations

import string
from pathlib import Path

import pytest
from hypothesis import given, settings, strategies as st

from agentbus.tools.filesystem_security import (
    UnsafeFileSystemPath,
    normalize_relative_tool_path,
)
from agentbus.tools.protocol import (
    CapabilityScope,
    ToolCapability,
    ToolCapabilityName,
    capability_fingerprint,
    capability_set_contains,
    scope_contains,
)


PROPERTY_SETTINGS = settings(
    max_examples=100,
    deadline=None,
    derandomize=True,
    database=None,
)
_SEGMENT_ALPHABET = string.ascii_letters + string.digits + "_-"
_RESERVED_NAMES = {
    "aux",
    "clock$",
    "con",
    "nul",
    "prn",
    *(f"com{index}" for index in range(1, 10)),
    *(f"lpt{index}" for index in range(1, 10)),
}
_SEGMENT = st.text(_SEGMENT_ALPHABET, min_size=1, max_size=24).filter(
    lambda value: value.lower().split(".", 1)[0] not in _RESERVED_NAMES
)
_SCOPE_VALUE = st.text(
    string.ascii_letters + string.digits + "._-",
    min_size=1,
    max_size=32,
).filter(lambda value: value not in {"all", "unrestricted"})
_BACKSLASH = chr(92)
_AMBIGUOUS_PATHS = (
    "..",
    "../outside",
    "safe/../outside",
    "/absolute",
    _BACKSLASH + "absolute",
    "C:" + _BACKSLASH + "absolute",
    "C:drive-relative",
    _BACKSLASH * 2 + "server" + _BACKSLASH + "share",
    _BACKSLASH * 2 + "?" + _BACKSLASH + "C:" + _BACKSLASH + "device",
    "safe//double",
    "safe/",
    "safe.",
    "safe ",
    "safe:stream",
    "CON",
    "aux.txt",
)


@PROPERTY_SETTINGS
@given(
    parts=st.lists(_SEGMENT, min_size=1, max_size=8),
    separator=st.sampled_from(("/", _BACKSLASH)),
)
def test_normalized_relative_paths_never_escape_root(
    parts: list[str],
    separator: str,
) -> None:
    normalized = normalize_relative_tool_path(separator.join(parts))
    root = (Path.cwd() / ".property-root").resolve()
    candidate = root.joinpath(*normalized.split("/")).resolve(strict=False)

    assert normalized == "/".join(parts)
    assert candidate.is_relative_to(root)
    assert not Path(normalized).is_absolute()


@PROPERTY_SETTINGS
@given(prefix=st.sampled_from(_AMBIGUOUS_PATHS))
def test_ambiguous_or_escaping_paths_fail_closed(prefix: str) -> None:
    with pytest.raises(UnsafeFileSystemPath):
        normalize_relative_tool_path(prefix)


@st.composite
def _scope_subsets(draw):
    allowed = draw(st.sets(_SCOPE_VALUE, min_size=1, max_size=12))
    requested = draw(
        st.sets(
            st.sampled_from(sorted(allowed)),
            max_size=len(allowed),
        )
    )
    return tuple(sorted(allowed)), tuple(sorted(requested))


@st.composite
def _scope_with_outside(draw):
    allowed = draw(st.sets(_SCOPE_VALUE, min_size=1, max_size=12))
    outside = draw(_SCOPE_VALUE.filter(lambda value: value not in allowed))
    return tuple(sorted(allowed)), outside


@PROPERTY_SETTINGS
@given(environment=_scope_subsets(), operations=_scope_subsets())
def test_capability_serialization_never_widens_scope(
    environment: tuple[tuple[str, ...], tuple[str, ...]],
    operations: tuple[tuple[str, ...], tuple[str, ...]],
) -> None:
    allowed_environment, requested_environment = environment
    allowed_operations, requested_operations = operations
    allowed = ToolCapability(
        name=ToolCapabilityName.GIT_WRITE,
        scope=CapabilityScope(
            environment_keys=allowed_environment,
            git_operations=allowed_operations,
        ),
    )
    requested = ToolCapability(
        name=ToolCapabilityName.GIT_WRITE,
        scope=CapabilityScope(
            environment_keys=requested_environment,
            git_operations=requested_operations,
        ),
    )

    restored = ToolCapability.model_validate_json(requested.model_dump_json())

    assert restored == requested
    assert capability_fingerprint((restored,)) == capability_fingerprint((requested,))
    assert capability_set_contains((allowed,), (restored,))
    assert scope_contains(requested.scope, restored.scope)
    assert scope_contains(restored.scope, requested.scope)


@PROPERTY_SETTINGS
@given(scope=_scope_with_outside())
def test_unapproved_scope_entries_remain_rejected_after_round_trip(
    scope: tuple[tuple[str, ...], str],
) -> None:
    allowed_values, outside = scope
    allowed = CapabilityScope(environment_keys=allowed_values)
    escalated = CapabilityScope(environment_keys=(outside,))
    restored = CapabilityScope.model_validate_json(escalated.model_dump_json())

    assert not scope_contains(allowed, escalated)
    assert not scope_contains(allowed, restored)
    assert not scope_contains(
        CapabilityScope(network_allowed=False),
        CapabilityScope(network_allowed=True),
    )


@PROPERTY_SETTINGS
@given(parts=st.lists(_SEGMENT, min_size=1, max_size=6))
def test_root_capabilities_contain_children_but_not_siblings(
    parts: list[str],
) -> None:
    base = Path.cwd() / ".property-capabilities"
    root = base / "allowed"
    child = root.joinpath(*parts)
    sibling = base / "outside" / parts[-1]

    assert scope_contains(
        CapabilityScope(roots=(str(root),)),
        CapabilityScope(roots=(str(child),)),
    )
    assert not scope_contains(
        CapabilityScope(roots=(str(root),)),
        CapabilityScope(roots=(str(sibling),)),
    )
