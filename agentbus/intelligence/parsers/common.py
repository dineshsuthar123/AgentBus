from __future__ import annotations

from bisect import bisect_right
from collections.abc import Iterable
from itertools import islice

from agentbus.intelligence.models import (
    DiagnosticSeverity,
    IndexDiagnostic,
    SymbolLocation,
)
from agentbus.intelligence.parsers.base import (
    CancellationSignal,
    ParseRequest,
    ParseResult,
    ParsedDefinition,
    ParsedReference,
    ParserDescriptor,
    ParserLimits,
)


class LineMap:
    def __init__(self, relative_path: str, content: str) -> None:
        self.relative_path = relative_path
        self.content_length = len(content)
        starts = [0]
        for index, character in enumerate(content):
            if character == "\n":
                starts.append(index + 1)
        self._starts = tuple(starts)

    def location(self, start: int, end: int | None = None) -> SymbolLocation:
        terminal = start if end is None else end
        if (
            start < 0
            or terminal < start
            or terminal > self.content_length
        ):
            raise ValueError("source offsets are outside the parsed content")
        start_line, start_column = self._position(start)
        end_line, end_column = self._position(terminal)
        return SymbolLocation(
            relative_path=self.relative_path,
            start_line=start_line,
            start_column=start_column,
            end_line=end_line,
            end_column=end_column,
        )

    def _position(self, offset: int) -> tuple[int, int]:
        line_index = bisect_right(self._starts, offset) - 1
        return line_index + 1, offset - self._starts[line_index]


def sanitize_documentation(
    value: str | None,
    *,
    maximum_chars: int = 8_192,
) -> str | None:
    if value is None:
        return None
    if maximum_chars < 1 or maximum_chars > 8_192:
        raise ValueError("documentation limit is outside parser bounds")
    sanitized = "".join(
        character
        for character in value
        if character in "\n\r\t" or ord(character) >= 32
    ).strip()
    return sanitized[:maximum_chars] or None


def cancellation_requested(
    cancellation: CancellationSignal | None,
) -> bool:
    return bool(cancellation is not None and cancellation.is_set())


def finalize_result(
    descriptor: ParserDescriptor,
    request: ParseRequest,
    *,
    definitions: Iterable[ParsedDefinition] = (),
    references: Iterable[ParsedReference] = (),
    diagnostics: Iterable[IndexDiagnostic] = (),
    limits: ParserLimits | None = None,
    partial: bool = False,
    cancelled: bool = False,
) -> ParseResult:
    active_limits = limits or ParserLimits(
        maximum_source_bytes=descriptor.maximum_source_bytes
    )
    if request.language not in descriptor.languages:
        raise ValueError("parser does not own the requested source language")
    source_bytes = len(request.content.encode("utf-8"))
    if source_bytes > min(
        active_limits.maximum_source_bytes,
        descriptor.maximum_source_bytes,
    ):
        return _source_too_large_result(descriptor, request)

    definition_items = list(
        islice(definitions, active_limits.maximum_definitions + 1)
    )
    reference_items = list(
        islice(references, active_limits.maximum_references + 1)
    )
    ordered_definitions = sorted(
        definition_items,
        key=lambda item: (
            item.location.start_line,
            item.location.start_column,
            item.qualified_name,
            item.kind.value,
        ),
    )[: active_limits.maximum_definitions]
    ordered_references = sorted(
        reference_items,
        key=lambda item: (
            item.location.start_line,
            item.location.start_column,
            item.target,
            item.kind.value,
        ),
    )[: active_limits.maximum_references]
    bounded_definitions = tuple(ordered_definitions)
    bounded_references = tuple(ordered_references)
    truncated = (
        len(definition_items) > len(bounded_definitions)
        or len(reference_items) > len(bounded_references)
    )
    bounded_diagnostics = list(
        islice(diagnostics, active_limits.maximum_diagnostics)
    )
    if truncated:
        _append_diagnostic(
            bounded_diagnostics,
            active_limits.maximum_diagnostics,
            IndexDiagnostic(
                code="parser.result_limit",
                severity=DiagnosticSeverity.WARNING,
                message="Parser output was truncated at configured limits.",
                relative_path=request.relative_path,
                parser_name=descriptor.name,
                recoverable=True,
            ),
        )
    if cancelled:
        _append_diagnostic(
            bounded_diagnostics,
            active_limits.maximum_diagnostics,
            IndexDiagnostic(
                code="parser.cancelled",
                severity=DiagnosticSeverity.INFO,
                message="Parser stopped after cooperative cancellation.",
                relative_path=request.relative_path,
                parser_name=descriptor.name,
                recoverable=True,
            ),
        )
    return ParseResult(
        language=request.language,
        parser_name=descriptor.name,
        parser_version=descriptor.version,
        source_hash=request.source_hash,
        definitions=bounded_definitions,
        references=bounded_references,
        diagnostics=tuple(bounded_diagnostics),
        partial=partial or truncated or cancelled,
        cancelled=cancelled,
    )


def _source_too_large_result(
    descriptor: ParserDescriptor,
    request: ParseRequest,
) -> ParseResult:
    return ParseResult(
        language=request.language,
        parser_name=descriptor.name,
        parser_version=descriptor.version,
        source_hash=request.source_hash,
        diagnostics=(
            IndexDiagnostic(
                code="parser.source_too_large",
                severity=DiagnosticSeverity.WARNING,
                message="Source file exceeds the configured parser byte limit.",
                relative_path=request.relative_path,
                parser_name=descriptor.name,
                recoverable=True,
            ),
        ),
        partial=True,
    )


def _append_diagnostic(
    diagnostics: list[IndexDiagnostic],
    maximum_diagnostics: int,
    diagnostic: IndexDiagnostic,
) -> None:
    if len(diagnostics) >= maximum_diagnostics:
        diagnostics[-1] = diagnostic
    else:
        diagnostics.append(diagnostic)
