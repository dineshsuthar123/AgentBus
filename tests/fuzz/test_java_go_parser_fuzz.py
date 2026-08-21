from __future__ import annotations

from pathlib import Path

import pytest
from hypothesis import given, settings, strategies as st

from agentbus.intelligence import file_id, repository_identity
from agentbus.intelligence.models import SourceLanguage
from agentbus.intelligence.parsers import (
    GoStaticParser,
    JavaStaticParser,
    ParseRequest,
    ParserLimits,
)


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
_REPOSITORY = repository_identity("fuzz/java-go-parsers")
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
def _java_sources(draw: st.DrawFn) -> str:
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
                        "package",
                        "import",
                        "public",
                        "class",
                        "interface",
                        "record",
                        "enum",
                        "extends",
                        "implements",
                        "new",
                        "void",
                        "<",
                        ">",
                        "(",
                        ")",
                        "[",
                        "]",
                        "{",
                        "}",
                        ";",
                        ",",
                        ".",
                        "Name",
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
                    "package example; import java.util.{",
                    "public class Broken<T extends Comparable<",
                    "@Annotation(value = { public record Missing(",
                    "interface Service<T, U extends Map<String,",
                    "public sealed class Base permits Child,",
                )
            )
        )
    if case == "nested":
        depth = draw(st.integers(min_value=1, max_value=300))
        return (
            "class Nested { Object value = "
            + "(" * depth
            + "null"
            + ")" * depth
            + "; }"
        )
    if case == "long_identifier":
        length = draw(st.integers(min_value=1, max_value=1_024))
        return f"class {'Identifier' + 'X' * length} {{}}"
    if case == "comments":
        return "// " + draw(_UNICODE_TEXT).replace("\n", "\n// ")
    if case == "repeated":
        count = draw(st.integers(min_value=1, max_value=150))
        return "\n".join(
            f"class Repeated{index} {{ int value() {{ return {index}; }} }}"
            for index in range(count)
        )
    if case == "unicode":
        identifier = draw(_UNICODE_IDENTIFIER)
        return f"class {identifier} {{ int {identifier}Value = 1; }}"
    return (
        "// generated file; do not edit\n"
        + "\n".join(f"class Generated{index} {{}}" for index in range(120))
    )


@st.composite
def _go_sources(draw: st.DrawFn) -> str:
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
                        "package",
                        "import",
                        "type",
                        "struct",
                        "interface",
                        "func",
                        "var",
                        "const",
                        "chan",
                        "go",
                        "defer",
                        "[",
                        "]",
                        "(",
                        ")",
                        "{",
                        "}",
                        ";",
                        ",",
                        ".",
                        "Name",
                        ":=",
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
                    'package broken\nimport ("fmt"',
                    "package broken\ntype Set[T comparable struct {",
                    "package broken\nfunc Value[T interface{ ~int |",
                    "package broken\nvar values = map[string][]func(",
                    "package broken\ntype Service interface { Run(",
                )
            )
        )
    if case == "nested":
        depth = draw(st.integers(min_value=1, max_value=300))
        return (
            "package nested\nvar value = "
            + "(" * depth
            + "0"
            + ")" * depth
        )
    if case == "long_identifier":
        length = draw(st.integers(min_value=1, max_value=1_024))
        return f"package long\ntype {'Identifier' + 'X' * length} struct {{}}"
    if case == "comments":
        return "package comments\n// " + draw(_UNICODE_TEXT).replace(
            "\n",
            "\n// ",
        )
    if case == "repeated":
        count = draw(st.integers(min_value=1, max_value=150))
        return "package repeated\n" + "\n".join(
            f"func Repeated{index}() int {{ return {index} }}"
            for index in range(count)
        )
    if case == "unicode":
        identifier = draw(_UNICODE_IDENTIFIER)
        return f"package unicode\ntype {identifier} struct {{ {identifier}Value int }}"
    return (
        "// Code generated; DO NOT EDIT.\npackage generated\n"
        + "\n".join(f"const Generated{index} = {index}" for index in range(120))
    )


def _request(content: str, language: SourceLanguage) -> ParseRequest:
    if language is SourceLanguage.JAVA:
        relative_path = "src/main/java/example/FuzzTarget.java"
    else:
        relative_path = "internal/fuzz/fuzz_target.go"
    return ParseRequest.from_content(
        repository_id=_REPOSITORY.repository_id,
        file_id=file_id(_REPOSITORY.repository_id, relative_path),
        relative_path=relative_path,
        language=language,
        content=content,
    )


def _assert_contract(parser, request: ParseRequest) -> None:
    first = parser.parse(request, limits=FUZZ_LIMITS)
    second = parser.parse(request, limits=FUZZ_LIMITS)

    assert first == second
    assert first.language is request.language
    assert first.parser_name == parser.descriptor.name
    assert first.parser_version == parser.descriptor.version
    assert first.source_hash == request.source_hash
    assert len(first.definitions) <= FUZZ_LIMITS.maximum_definitions
    assert len(first.references) <= FUZZ_LIMITS.maximum_references
    assert len(first.diagnostics) <= FUZZ_LIMITS.maximum_diagnostics


@FUZZ_SETTINGS
@given(source=_java_sources())
def test_java_parser_is_bounded_and_deterministic_for_hostile_source(
    source: str,
) -> None:
    _assert_contract(
        JavaStaticParser(),
        _request(source, SourceLanguage.JAVA),
    )


@FUZZ_SETTINGS
@given(source=_go_sources())
def test_go_parser_is_bounded_and_deterministic_for_hostile_source(
    source: str,
) -> None:
    _assert_contract(
        GoStaticParser(),
        _request(source, SourceLanguage.GO),
    )


class _Cancelled:
    def is_set(self) -> bool:
        return True


@pytest.mark.parametrize(
    ("parser", "language", "source"),
    (
        (JavaStaticParser(), SourceLanguage.JAVA, "class Value {}"),
        (GoStaticParser(), SourceLanguage.GO, "package value"),
    ),
)
def test_java_and_go_parsers_honor_cancellation_and_source_limits(
    parser,
    language: SourceLanguage,
    source: str,
) -> None:
    cancelled = parser.parse(
        _request(source, language),
        limits=FUZZ_LIMITS,
        cancellation=_Cancelled(),
    )
    tiny_limits = ParserLimits(maximum_source_bytes=16)
    oversized = parser.parse(
        _request(source + "x" * 32, language),
        limits=tiny_limits,
    )

    assert cancelled.cancelled is True
    assert cancelled.partial is True
    assert any(item.code == "parser.cancelled" for item in cancelled.diagnostics)
    assert oversized.partial is True
    assert oversized.definitions == ()
    assert any(
        item.code == "parser.source_too_large" for item in oversized.diagnostics
    )


@pytest.mark.parametrize(
    ("parser", "language", "source_template"),
    (
        (
            JavaStaticParser(),
            SourceLanguage.JAVA,
            (
                "class Payload {{ void run() throws Exception {{ "
                "new java.io.File({path!r}).createNewFile(); }} }}"
            ),
        ),
        (
            GoStaticParser(),
            SourceLanguage.GO,
            (
                "package payload\nimport "
                + chr(96)
                + "os"
                + chr(96)
                + "\nfunc run() {{ os.WriteFile("
                + chr(96)
                + "{path}"
                + chr(96)
                + ", []byte("
                + chr(96)
                + "owned"
                + chr(96)
                + "), 0600) }}"
            ),
        ),
    ),
)
def test_java_and_go_parsers_never_execute_source(
    tmp_path: Path,
    parser,
    language: SourceLanguage,
    source_template: str,
) -> None:
    marker = tmp_path / "parser-executed"
    request = _request(
        source_template.format(path=marker.as_posix()),
        language,
    )

    result = parser.parse(request, limits=FUZZ_LIMITS)

    assert result.source_hash == request.source_hash
    assert not marker.exists()
