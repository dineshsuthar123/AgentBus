from __future__ import annotations

from agentbus.execution.cancellation import CancellationToken
from agentbus.intelligence import file_id, repository_identity
from agentbus.intelligence.models import (
    DependencyKind,
    SourceLanguage,
    SymbolKind,
)
from agentbus.intelligence.parsers import (
    LineMap,
    ParseRequest,
    ParsedDefinition,
    ParsedReference,
    ParserDescriptor,
    ParserLimits,
    cancellation_requested,
    finalize_result,
    sanitize_documentation,
)


def _request(content: str = "class Example:\n    pass\n") -> ParseRequest:
    repository = repository_identity("example/parser")
    return ParseRequest.from_content(
        repository_id=repository.repository_id,
        file_id=file_id(repository.repository_id, "example.py"),
        relative_path="example.py",
        language=SourceLanguage.PYTHON,
        content=content,
    )


def _descriptor() -> ParserDescriptor:
    return ParserDescriptor(
        name="python-ast",
        version="1.0.0",
        languages=(SourceLanguage.PYTHON,),
    )


def test_parse_request_verifies_content_hash() -> None:
    request = _request()
    payload = request.model_dump(mode="python")
    payload["content"] = "changed"

    try:
        ParseRequest.model_validate(payload)
    except ValueError as exc:
        assert "source hash" in str(exc)
    else:
        raise AssertionError("content hash mismatch should fail validation")


def test_line_map_produces_bounded_source_locations() -> None:
    mapping = LineMap("example.py", "one\ntwo\n")

    assert mapping.location(4, 7).model_dump() == {
        "relative_path": "example.py",
        "start_line": 2,
        "start_column": 0,
        "end_line": 2,
        "end_column": 3,
    }

    try:
        mapping.location(-1)
    except ValueError as exc:
        assert "offsets" in str(exc)
    else:
        raise AssertionError("negative offsets should be rejected")


def test_finalize_result_sorts_and_bounds_partial_output() -> None:
    request = _request()
    mapping = LineMap(request.relative_path, request.content)
    definitions = (
        ParsedDefinition(
            name="later",
            qualified_name="later",
            kind=SymbolKind.CLASS,
            location=mapping.location(6, 13),
        ),
        ParsedDefinition(
            name="first",
            qualified_name="first",
            kind=SymbolKind.CLASS,
            location=mapping.location(0, 5),
        ),
    )
    references = (
        ParsedReference(
            target="base.Type",
            kind=DependencyKind.INHERITS,
            location=mapping.location(0, 5),
            explanation="Static base-class expression.",
        ),
    )

    result = finalize_result(
        _descriptor(),
        request,
        definitions=definitions,
        references=references,
        limits=ParserLimits(maximum_definitions=1),
    )

    assert [item.name for item in result.definitions] == ["first"]
    assert result.inheritance == references
    assert result.partial is True
    assert result.diagnostics[0].code == "parser.result_limit"


def test_oversized_and_cancelled_results_are_recoverable() -> None:
    request = _request("value = 1\n")
    oversized = finalize_result(
        _descriptor(),
        request,
        limits=ParserLimits(maximum_source_bytes=1),
    )
    cancellation = CancellationToken()
    cancellation.request("test")
    cancelled = finalize_result(
        _descriptor(),
        request,
        cancelled=cancellation_requested(cancellation),
    )

    assert oversized.partial is True
    assert oversized.diagnostics[0].code == "parser.source_too_large"
    assert cancelled.cancelled is True
    assert cancelled.diagnostics[0].code == "parser.cancelled"


def test_documentation_sanitization_removes_controls_and_bounds_text() -> None:
    assert sanitize_documentation(
        "  safe\x00 docs\n",
        maximum_chars=8,
    ) == "safe doc"


def test_finalize_result_does_not_exhaust_unbounded_parser_output() -> None:
    request = _request()
    mapping = LineMap(request.relative_path, request.content)

    def definitions():
        for index in range(3):
            yield ParsedDefinition(
                name=f"item-{index}",
                qualified_name=f"item-{index}",
                kind=SymbolKind.VARIABLE,
                location=mapping.location(0),
            )
        raise AssertionError("bounded finalization consumed excess output")

    result = finalize_result(
        _descriptor(),
        request,
        definitions=definitions(),
        limits=ParserLimits(maximum_definitions=1),
    )

    assert len(result.definitions) == 1
    assert result.partial is True
