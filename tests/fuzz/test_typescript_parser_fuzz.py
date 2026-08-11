from __future__ import annotations

from pathlib import Path

from hypothesis import given, settings, strategies as st

from agentbus.intelligence import file_id, repository_identity
from agentbus.intelligence.models import SourceLanguage
from agentbus.intelligence.parsers import (
    ParseRequest,
    ParserLimits,
    TypeScriptStaticParser,
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
_REPOSITORY = repository_identity("fuzz/typescript-parser")
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
def _script_sources(draw: st.DrawFn) -> str:
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
                        "export",
                        "import",
                        "class",
                        "interface",
                        "type",
                        "enum",
                        "function",
                        "async",
                        "extends",
                        "implements",
                        "<",
                        ">",
                        "(",
                        ")",
                        "[",
                        "]",
                        "{",
                        "}",
                        ":",
                        ";",
                        ",",
                        "name",
                        "unknown",
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
                    "export class Missing {",
                    "import { value, type Other from './module';",
                    "interface Broken<T extends { value:",
                    "const value = <T extends string(",
                    "export type Deep<T = Promise<Array<",
                )
            )
        )
    if case == "nested":
        depth = draw(st.integers(min_value=1, max_value=300))
        return "const value = " + "(" * depth + "0" + ")" * depth + ";"
    if case == "long_identifier":
        length = draw(st.integers(min_value=1, max_value=1_024))
        return f"export const {'identifier_' + 'x' * length} = 1;"
    if case == "comments":
        return "// " + draw(_UNICODE_TEXT).replace("\n", "\n// ")
    if case == "repeated":
        count = draw(st.integers(min_value=1, max_value=150))
        return "\n".join(
            f"export function repeated{index}(): number {{ return {index}; }}"
            for index in range(count)
        )
    if case == "unicode":
        identifier = draw(_UNICODE_IDENTIFIER)
        return f"export const {identifier} = 1;\nclass {identifier}Type {{}}"
    return (
        "// generated file; do not edit\n"
        + "\n".join(
            f"export const GENERATED_{index} = {index};" for index in range(120)
        )
    )


def _request(content: str, language: SourceLanguage) -> ParseRequest:
    suffix = "ts" if language is SourceLanguage.TYPESCRIPT else "js"
    relative_path = f"src/fuzz_target.{suffix}"
    return ParseRequest.from_content(
        repository_id=_REPOSITORY.repository_id,
        file_id=file_id(_REPOSITORY.repository_id, relative_path),
        relative_path=relative_path,
        language=language,
        content=content,
    )


@FUZZ_SETTINGS
@given(
    source=_script_sources(),
    language=st.sampled_from(
        (SourceLanguage.TYPESCRIPT, SourceLanguage.JAVASCRIPT)
    ),
)
def test_typescript_parser_is_bounded_and_deterministic_for_hostile_source(
    source: str,
    language: SourceLanguage,
) -> None:
    parser = TypeScriptStaticParser()
    request = _request(source, language)

    first = parser.parse(request, limits=FUZZ_LIMITS)
    second = parser.parse(request, limits=FUZZ_LIMITS)

    assert first == second
    assert first.language is language
    assert first.parser_name == parser.descriptor.name
    assert first.parser_version == parser.descriptor.version
    assert first.source_hash == request.source_hash
    assert len(first.definitions) <= FUZZ_LIMITS.maximum_definitions
    assert len(first.references) <= FUZZ_LIMITS.maximum_references
    assert len(first.diagnostics) <= FUZZ_LIMITS.maximum_diagnostics


class _Cancelled:
    def is_set(self) -> bool:
        return True


def test_typescript_parser_honors_cancellation_and_source_limits() -> None:
    parser = TypeScriptStaticParser()
    cancelled = parser.parse(
        _request("export const value = 1;", SourceLanguage.TYPESCRIPT),
        limits=FUZZ_LIMITS,
        cancellation=_Cancelled(),
    )
    tiny_limits = ParserLimits(maximum_source_bytes=16)
    oversized = parser.parse(
        _request("export const value = " + "1" * 32, SourceLanguage.TYPESCRIPT),
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


def test_typescript_parser_never_executes_source(tmp_path: Path) -> None:
    marker = tmp_path / "parser-executed"
    source = (
        "const fs = require('node:fs');\n"
        f"fs.writeFileSync({marker.as_posix()!r}, 'unexpected');\n"
    )

    result = TypeScriptStaticParser().parse(
        _request(source, SourceLanguage.TYPESCRIPT),
        limits=FUZZ_LIMITS,
    )

    assert result.source_hash == _request(
        source,
        SourceLanguage.TYPESCRIPT,
    ).source_hash
    assert not marker.exists()
