from __future__ import annotations

from pathlib import Path

from hypothesis import given, settings, strategies as st

from agentbus.intelligence import file_id, repository_identity
from agentbus.intelligence.models import SourceLanguage
from agentbus.intelligence.parsers import ParseRequest, ParserLimits, PythonAstParser


FUZZ_SETTINGS = settings(
    max_examples=100,
    deadline=None,
    derandomize=True,
    database=None,
)
FUZZ_LIMITS = ParserLimits(
    maximum_source_bytes=16_384,
    maximum_definitions=64,
    maximum_references=128,
    maximum_diagnostics=32,
    maximum_syntax_nodes=5_000,
    cancellation_check_interval=1,
)
_REPOSITORY = repository_identity("fuzz/python-parser")
_UNICODE_TEXT = st.text(
    alphabet=st.characters(blacklist_categories=("Cs",)),
    max_size=2_048,
)
_UNICODE_IDENTIFIER = st.text(
    alphabet=st.characters(
        min_codepoint=128,
        whitelist_categories=("Ll", "Lu", "Lt", "Lm", "Lo", "Nl"),
    ),
    min_size=1,
    max_size=64,
)


@st.composite
def _python_sources(draw: st.DrawFn) -> str:
    case = draw(
        st.sampled_from(
            (
                "arbitrary",
                "tokens",
                "truncated",
                "nested",
                "long_identifier",
                "comments",
                "repeated",
                "unicode",
                "generated",
            )
        )
    )
    if case == "arbitrary":
        return draw(_UNICODE_TEXT)
    if case == "tokens":
        tokens = draw(
            st.lists(
                st.sampled_from(
                    (
                        "def",
                        "class",
                        "async",
                        "await",
                        "import",
                        "from",
                        "(",
                        ")",
                        "[",
                        "]",
                        "{",
                        "}",
                        ":",
                        ",",
                        "=",
                        "name",
                        "0",
                        "None",
                    )
                ),
                max_size=160,
            )
        )
        return " ".join(tokens)
    if case == "truncated":
        return draw(
            st.sampled_from(
                (
                    "def unfinished(",
                    "class MissingBase(",
                    "from package import (name,",
                    "match value:\n    case [first,",
                    "value = f'{name",
                )
            )
        )
    if case == "nested":
        depth = draw(st.integers(min_value=1, max_value=300))
        return "value = " + "(" * depth + "0" + ")" * depth
    if case == "long_identifier":
        length = draw(st.integers(min_value=1, max_value=1_024))
        return f"{'identifier_' + 'x' * length} = 1"
    if case == "comments":
        return "# " + draw(_UNICODE_TEXT).replace("\n", "\n# ")
    if case == "repeated":
        count = draw(st.integers(min_value=1, max_value=150))
        return "\n".join(f"def repeated_{index}(): pass" for index in range(count))
    if case == "unicode":
        identifier = draw(_UNICODE_IDENTIFIER)
        return f"{identifier} = 1\nclass {identifier}Type: pass"
    return (
        "# generated file; do not edit\n"
        + "\n".join(f"GENERATED_{index} = {index}" for index in range(120))
    )


def _request(content: str) -> ParseRequest:
    relative_path = "src/fuzz_target.py"
    return ParseRequest.from_content(
        repository_id=_REPOSITORY.repository_id,
        file_id=file_id(_REPOSITORY.repository_id, relative_path),
        relative_path=relative_path,
        language=SourceLanguage.PYTHON,
        content=content,
    )


@FUZZ_SETTINGS
@given(source=_python_sources())
def test_python_parser_is_bounded_and_deterministic_for_hostile_source(
    source: str,
) -> None:
    parser = PythonAstParser()
    request = _request(source)

    first = parser.parse(request, limits=FUZZ_LIMITS)
    second = parser.parse(request, limits=FUZZ_LIMITS)

    assert first == second
    assert first.language is SourceLanguage.PYTHON
    assert first.parser_name == parser.descriptor.name
    assert first.parser_version == parser.descriptor.version
    assert first.source_hash == request.source_hash
    assert len(first.definitions) <= FUZZ_LIMITS.maximum_definitions
    assert len(first.references) <= FUZZ_LIMITS.maximum_references
    assert len(first.diagnostics) <= FUZZ_LIMITS.maximum_diagnostics


class _Cancelled:
    def is_set(self) -> bool:
        return True


def test_python_parser_honors_cancellation_and_source_limits() -> None:
    parser = PythonAstParser()
    cancelled = parser.parse(
        _request("value = 1"),
        limits=FUZZ_LIMITS,
        cancellation=_Cancelled(),
    )
    tiny_limits = ParserLimits(maximum_source_bytes=16)
    oversized = parser.parse(_request("value = " + "1" * 32), limits=tiny_limits)

    assert cancelled.cancelled is True
    assert cancelled.partial is True
    assert any(item.code == "parser.cancelled" for item in cancelled.diagnostics)
    assert oversized.partial is True
    assert oversized.definitions == ()
    assert any(
        item.code == "parser.source_too_large" for item in oversized.diagnostics
    )


def test_python_parser_never_executes_source(tmp_path: Path) -> None:
    marker = tmp_path / "parser-executed"
    source = (
        "from pathlib import Path\n"
        f"Path({str(marker)!r}).write_text('unexpected', encoding='utf-8')\n"
    )

    result = PythonAstParser().parse(_request(source), limits=FUZZ_LIMITS)

    assert result.partial is False
    assert not marker.exists()
