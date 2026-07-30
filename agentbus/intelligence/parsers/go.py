from __future__ import annotations

from dataclasses import dataclass
from pathlib import PurePosixPath

from agentbus.intelligence.models import (
    DiagnosticSeverity,
    IndexDiagnostic,
    SourceLanguage,
    SymbolKind,
)
from agentbus.intelligence.parsers.base import (
    CancellationSignal,
    ParseRequest,
    ParseResult,
    ParsedDefinition,
    ParserDescriptor,
    ParserLimits,
)
from agentbus.intelligence.parsers.common import (
    LineMap,
    cancellation_requested,
    finalize_result,
)


@dataclass(frozen=True)
class _Token:
    kind: str
    value: str
    start: int
    end: int


@dataclass(frozen=True)
class _DefinitionSpan:
    start: int
    end: int
    qualified_name: str
    kind: SymbolKind


class GoStaticParser:
    descriptor = ParserDescriptor(
        name="go-static",
        version="1.0.0",
        languages=(SourceLanguage.GO,),
    )

    def parse(
        self,
        request: ParseRequest,
        *,
        limits: ParserLimits | None = None,
        cancellation: CancellationSignal | None = None,
    ) -> ParseResult:
        active_limits = limits or ParserLimits()
        if cancellation_requested(cancellation):
            return finalize_result(
                self.descriptor,
                request,
                limits=active_limits,
                partial=True,
                cancelled=True,
            )
        if len(request.content.encode("utf-8")) > min(
            active_limits.maximum_source_bytes,
            self.descriptor.maximum_source_bytes,
        ):
            return finalize_result(
                self.descriptor,
                request,
                limits=active_limits,
            )
        tokens, diagnostics, partial, cancelled = _tokenize(
            request,
            active_limits,
            cancellation,
        )
        pairs, pair_diagnostics = _matching_pairs(request, tokens)
        diagnostics.extend(pair_diagnostics)
        parser = _GoDeclarationParser(
            request,
            tokens,
            pairs,
            active_limits,
        )
        parser.parse()
        diagnostics.extend(parser.diagnostics)
        return finalize_result(
            self.descriptor,
            request,
            definitions=parser.definitions,
            diagnostics=diagnostics,
            limits=active_limits,
            partial=(
                partial
                or bool(pair_diagnostics)
                or parser.partial
                or cancelled
            ),
            cancelled=cancelled,
        )


class _GoDeclarationParser:
    def __init__(
        self,
        request: ParseRequest,
        tokens: tuple[_Token, ...],
        pairs: dict[int, int],
        limits: ParserLimits,
    ) -> None:
        self.request = request
        self.tokens = tokens
        self.pairs = pairs
        self.limits = limits
        self.lines = LineMap(request.relative_path, request.content)
        self.package_name = (
            _package_name(tokens)
            or PurePosixPath(request.relative_path).stem
        )
        self.definitions: list[ParsedDefinition] = []
        self.spans: list[_DefinitionSpan] = []
        self.declaration_openings: set[int] = set()
        self.diagnostics: list[IndexDiagnostic] = []
        self.partial = False

    def parse(self) -> None:
        self._add_definition(
            name=self.package_name.rsplit("/", 1)[-1],
            qualified_name=self.package_name,
            kind=SymbolKind.PACKAGE,
            start=0,
            end=len(self.request.content),
            exported=True,
        )
        index = 0
        while index < len(self.tokens) and not self.partial:
            index = self._skip_separators(index, len(self.tokens))
            if index >= len(self.tokens):
                break
            value = self.tokens[index].value
            if value == "type":
                index = self._parse_type(index, len(self.tokens))
            elif value == "func":
                index = self._parse_function(index, len(self.tokens))
            elif value in {"const", "var"}:
                index = self._parse_values(index, len(self.tokens), value)
            elif value == "{" and index in self.pairs:
                index = self.pairs[index] + 1
            else:
                index += 1

    def _parse_type(self, start: int, end: int) -> int:
        cursor = start + 1
        if self._value(cursor) == "(" and cursor in self.pairs:
            closing = self.pairs[cursor]
            for segment_start, segment_end in self._line_segments(
                cursor + 1,
                closing,
            ):
                name_index = self._next_identifier(
                    segment_start,
                    segment_end,
                )
                if name_index is not None:
                    self._parse_type_spec(
                        name_index,
                        segment_end,
                        segment_start,
                    )
            return closing + 1
        name_index = self._next_identifier(cursor, end)
        if name_index is None:
            return start + 1
        return self._parse_type_spec(name_index, end, start)

    def _parse_type_spec(
        self,
        name_index: int,
        end: int,
        declaration_start: int,
    ) -> int:
        name = self.tokens[name_index].value
        cursor = name_index + 1
        if self._value(cursor) == "[" and cursor in self.pairs:
            cursor = self.pairs[cursor] + 1
        alias = self._value(cursor) == "="
        if alias:
            cursor += 1
        type_token = self._value(cursor)
        body = (
            self._find_value("{", cursor + 1, end)
            if type_token in {"interface", "struct"}
            else None
        )
        body_end = self.pairs.get(body) if body is not None else None
        declaration_end = (
            body_end + 1
            if body_end is not None
            else self._line_end(cursor, end)
        )
        if type_token == "struct":
            kind = SymbolKind.CLASS
        elif type_token == "interface":
            kind = SymbolKind.INTERFACE
        else:
            kind = SymbolKind.TYPE_ALIAS
        qualified_name = self._qualified(name)
        self._add_definition(
            name=name,
            qualified_name=qualified_name,
            kind=kind,
            start=self.tokens[declaration_start].start,
            end=self._terminal_offset(declaration_end, name_index),
            signature=_token_signature(
                self.tokens,
                cursor,
                body if body is not None else declaration_end,
            ),
            exported=_go_exported(name),
            attributes={
                "go_kind": type_token or "defined",
                "alias": alias,
            },
        )
        if body is not None and body_end is not None:
            if kind == SymbolKind.CLASS:
                self._parse_struct_fields(
                    body + 1,
                    body_end,
                    qualified_name,
                )
            elif kind == SymbolKind.INTERFACE:
                self._parse_interface_methods(
                    body + 1,
                    body_end,
                    qualified_name,
                )
        return max(declaration_start + 1, declaration_end)

    def _parse_function(self, start: int, end: int) -> int:
        cursor = start + 1
        receiver: str | None = None
        if self._value(cursor) == "(" and cursor in self.pairs:
            closing = self.pairs[cursor]
            receiver = _receiver_type(self.tokens, cursor + 1, closing)
            cursor = closing + 1
        name_index = self._next_identifier(cursor, end)
        if name_index is None:
            return start + 1
        name = self.tokens[name_index].value
        cursor = name_index + 1
        if self._value(cursor) == "[" and cursor in self.pairs:
            cursor = self.pairs[cursor] + 1
        opening = self._find_value("(", cursor, end)
        if opening is None or opening not in self.pairs:
            self.partial = True
            return self._line_end(cursor, end)
        self.declaration_openings.add(opening)
        closing = self.pairs[opening]
        body = self._function_body(closing + 1, end)
        body_end = self.pairs.get(body) if body is not None else None
        declaration_end = (
            body_end + 1
            if body_end is not None
            else self._line_end(closing + 1, end)
        )
        qualified_name = self._qualified(
            f"{receiver}.{name}" if receiver else name
        )
        self._add_definition(
            name=name,
            qualified_name=qualified_name,
            kind=SymbolKind.METHOD if receiver else SymbolKind.FUNCTION,
            start=self.tokens[start].start,
            end=self._terminal_offset(declaration_end, closing),
            signature=_parameter_count_signature(
                self.tokens,
                opening,
                closing,
            ),
            parent=self._qualified(receiver) if receiver else None,
            exported=_go_exported(name),
            attributes={
                "receiver": receiver,
            },
        )
        return max(start + 1, declaration_end)

    def _parse_values(
        self,
        start: int,
        end: int,
        declaration_kind: str,
    ) -> int:
        cursor = start + 1
        kind = (
            SymbolKind.CONSTANT
            if declaration_kind == "const"
            else SymbolKind.VARIABLE
        )
        if self._value(cursor) == "(" and cursor in self.pairs:
            closing = self.pairs[cursor]
            for segment_start, segment_end in self._line_segments(
                cursor + 1,
                closing,
            ):
                self._parse_value_segment(
                    segment_start,
                    segment_end,
                    kind,
                )
            return closing + 1
        declaration_end = self._line_end(cursor, end)
        self._parse_value_segment(cursor, declaration_end, kind)
        return max(start + 1, declaration_end)

    def _parse_value_segment(
        self,
        start: int,
        end: int,
        kind: SymbolKind,
    ) -> None:
        meaningful = [
            index
            for index in range(start, end)
            if self.tokens[index].kind != "newline"
            and self.tokens[index].value != ";"
        ]
        if not meaningful:
            return
        names = self._leading_declared_names(start, end)
        for name_index in names[:128]:
            name = self.tokens[name_index].value
            if name == "_":
                continue
            self._add_definition(
                name=name,
                qualified_name=self._qualified(name),
                kind=kind,
                start=self.tokens[name_index].start,
                end=(
                    self.tokens[end - 1].end
                    if end > start
                    else self.tokens[name_index].end
                ),
                exported=_go_exported(name),
            )

    def _parse_struct_fields(
        self,
        start: int,
        end: int,
        parent: str,
    ) -> None:
        for segment_start, segment_end in self._line_segments(start, end):
            identifiers = self._leading_declared_names(
                segment_start,
                segment_end,
            )
            if len(identifiers) < 2:
                if (
                    not identifiers
                    or self._value(identifiers[0] + 1) == "."
                    or not any(
                        self.tokens[index].kind == "identifier"
                        for index in range(
                            identifiers[0] + 1,
                            segment_end,
                        )
                    )
                ):
                    continue
            if self._value(identifiers[0] + 1) == ".":
                continue
            for field_index in identifiers[:128]:
                name = self.tokens[field_index].value
                self._add_definition(
                    name=name,
                    qualified_name=f"{parent}.{name}",
                    kind=SymbolKind.FIELD,
                    start=self.tokens[field_index].start,
                    end=self.tokens[segment_end - 1].end,
                    parent=parent,
                    exported=_go_exported(name),
                )

    def _parse_interface_methods(
        self,
        start: int,
        end: int,
        parent: str,
    ) -> None:
        for segment_start, segment_end in self._line_segments(start, end):
            name_index = self._next_identifier(segment_start, segment_end)
            if name_index is None:
                continue
            opening = self._find_value(
                "(",
                name_index + 1,
                segment_end,
            )
            if opening is None or opening not in self.pairs:
                continue
            closing = self.pairs[opening]
            if closing > segment_end:
                continue
            self.declaration_openings.add(opening)
            name = self.tokens[name_index].value
            self._add_definition(
                name=name,
                qualified_name=f"{parent}.{name}",
                kind=SymbolKind.METHOD,
                start=self.tokens[name_index].start,
                end=self.tokens[segment_end - 1].end,
                parent=parent,
                signature=_parameter_count_signature(
                    self.tokens,
                    opening,
                    closing,
                ),
                exported=_go_exported(name),
                attributes={"interface_method": True},
            )

    def _line_segments(
        self,
        start: int,
        end: int,
    ) -> tuple[tuple[int, int], ...]:
        segments: list[tuple[int, int]] = []
        segment_start = self._skip_separators(start, end)
        index = segment_start
        while index < end:
            value = self.tokens[index].value
            if value in {"\n", ";"}:
                if segment_start < index:
                    if len(segments) >= 10_000:
                        self.partial = True
                        return tuple(segments)
                    segments.append((segment_start, index))
                segment_start = self._skip_separators(index + 1, end)
                index = segment_start
                continue
            if value in {"(", "[", "{"} and index in self.pairs:
                index = min(end, self.pairs[index] + 1)
            else:
                index += 1
        if segment_start < end:
            if len(segments) >= 10_000:
                self.partial = True
                return tuple(segments)
            segments.append((segment_start, end))
        return tuple(segments)

    def _function_body(self, start: int, end: int) -> int | None:
        index = start
        while index < end:
            value = self.tokens[index].value
            if value == "{":
                return index
            if value in {"(", "["} and index in self.pairs:
                index = self.pairs[index] + 1
            elif value in {"\n", ";"}:
                return None
            else:
                index += 1
        return None

    def _line_end(self, start: int, end: int) -> int:
        index = start
        while index < end:
            value = self.tokens[index].value
            if value in {"\n", ";"}:
                return index
            if value in {"(", "[", "{"} and index in self.pairs:
                index = self.pairs[index] + 1
            else:
                index += 1
        return end

    def _find_value(
        self,
        value: str,
        start: int,
        end: int,
    ) -> int | None:
        index = start
        while index < end:
            if self.tokens[index].value == value:
                return index
            if (
                self.tokens[index].value in {"(", "[", "{"}
                and index in self.pairs
            ):
                index = self.pairs[index] + 1
            else:
                index += 1
        return None

    def _next_identifier(self, start: int, end: int) -> int | None:
        return next(
            (
                index
                for index in range(start, end)
                if self.tokens[index].kind == "identifier"
            ),
            None,
        )

    def _leading_declared_names(
        self,
        start: int,
        end: int,
    ) -> tuple[int, ...]:
        first = self._next_identifier(start, end)
        if first is None:
            return ()
        names = [first]
        cursor = first + 1
        while (
            cursor + 1 < end
            and self.tokens[cursor].value == ","
            and self.tokens[cursor + 1].kind == "identifier"
        ):
            names.append(cursor + 1)
            cursor += 2
        return tuple(names)

    def _skip_separators(self, start: int, end: int) -> int:
        index = start
        while index < end and self.tokens[index].value in {"\n", ";"}:
            index += 1
        return index

    def _terminal_offset(self, terminal: int, fallback: int) -> int:
        if terminal > 0 and terminal <= len(self.tokens):
            return self.tokens[terminal - 1].end
        return self.tokens[fallback].end

    def _qualified(self, name: str | None) -> str:
        return (
            f"{self.package_name}.{name}"
            if name
            else self.package_name
        )

    def _value(self, index: int) -> str | None:
        return self.tokens[index].value if 0 <= index < len(self.tokens) else None

    def _add_definition(
        self,
        *,
        name: str,
        qualified_name: str,
        kind: SymbolKind,
        start: int,
        end: int,
        parent: str | None = None,
        signature: str | None = None,
        exported: bool = False,
        test: bool = False,
        endpoint: str | None = None,
        confidence: float = 1.0,
        attributes: dict[str, object] | None = None,
    ) -> None:
        if len(self.definitions) > self.limits.maximum_definitions:
            self.partial = True
            return
        self.definitions.append(
            ParsedDefinition(
                name=name[:512],
                qualified_name=qualified_name[:2_048],
                kind=kind,
                location=self.lines.location(start, end),
                signature=signature,
                parent_qualified_name=parent[:2_048] if parent else None,
                exported=exported,
                test=test,
                endpoint=endpoint,
                confidence=confidence,
                attributes=attributes or {},
            )
        )
        self.spans.append(
            _DefinitionSpan(
                start=start,
                end=max(start, end),
                qualified_name=qualified_name[:2_048],
                kind=kind,
            )
        )


def _tokenize(
    request: ParseRequest,
    limits: ParserLimits,
    cancellation: CancellationSignal | None,
) -> tuple[
    tuple[_Token, ...],
    list[IndexDiagnostic],
    bool,
    bool,
]:
    content = request.content
    tokens: list[_Token] = []
    diagnostics: list[IndexDiagnostic] = []
    index = 0
    iterations = 0
    partial = False
    cancelled = False
    while index < len(content):
        iterations += 1
        if len(tokens) > limits.maximum_syntax_nodes:
            diagnostics.append(
                _diagnostic(
                    request,
                    "parser.go_token_limit",
                    "Go tokenization reached its configured limit.",
                )
            )
            partial = True
            break
        if (
            iterations % limits.cancellation_check_interval == 0
            and cancellation_requested(cancellation)
        ):
            partial = True
            cancelled = True
            break
        character = content[index]
        if character in " \t\f\v":
            index += 1
            continue
        if character in "\r\n":
            terminal = (
                index + 2
                if content.startswith("\r\n", index)
                else index + 1
            )
            tokens.append(_Token("newline", "\n", index, terminal))
            index = terminal
            continue
        if content.startswith("//", index):
            newline = content.find("\n", index + 2)
            index = len(content) if newline < 0 else newline
            continue
        if content.startswith("/*", index):
            close = content.find("*/", index + 2)
            if close < 0:
                diagnostics.append(
                    _diagnostic(
                        request,
                        "parser.go_syntax_error",
                        "Go source contains an unterminated comment.",
                    )
                )
                partial = True
                break
            if "\n" in content[index:close]:
                tokens.append(_Token("newline", "\n", index, close + 2))
            index = close + 2
            continue
        if character in {'"', "'", "`"}:
            token, index, closed = _string_token(content, index)
            tokens.append(token)
            if not closed:
                diagnostics.append(
                    _diagnostic(
                        request,
                        "parser.go_syntax_error",
                        "Go source contains an unterminated literal.",
                    )
                )
                partial = True
                break
            continue
        if _identifier_start(character):
            terminal = index + 1
            while terminal < len(content) and _identifier_part(content[terminal]):
                terminal += 1
            tokens.append(
                _Token(
                    "identifier",
                    content[index:terminal],
                    index,
                    terminal,
                )
            )
            index = terminal
            continue
        if character.isdigit():
            terminal = index + 1
            while terminal < len(content) and (
                content[terminal].isalnum()
                or content[terminal] in "._"
            ):
                terminal += 1
            tokens.append(
                _Token("number", content[index:terminal], index, terminal)
            )
            index = terminal
            continue
        operator = next(
            (
                value
                for value in (
                    "<<=",
                    ">>=",
                    "&^=",
                    "...",
                    ":=",
                    "++",
                    "--",
                    "==",
                    "!=",
                    "<=",
                    ">=",
                    "&&",
                    "||",
                    "<-",
                    "<<",
                    ">>",
                    "&^",
                )
                if content.startswith(value, index)
            ),
            None,
        )
        value = operator or character
        tokens.append(_Token("punctuation", value, index, index + len(value)))
        index += len(value)
    return tuple(tokens), diagnostics, partial, cancelled


def _string_token(
    content: str,
    start: int,
) -> tuple[_Token, int, bool]:
    quote = content[start]
    index = start + 1
    escaped = False
    while index < len(content):
        character = content[index]
        if quote == "`" and character == "`":
            return (
                _Token(
                    "string",
                    content[start + 1:index][:2_048],
                    start,
                    index + 1,
                ),
                index + 1,
                True,
            )
        if quote != "`":
            if escaped:
                escaped = False
            elif character == "\\":
                escaped = True
            elif character == quote:
                return (
                    _Token(
                        "string",
                        content[start + 1:index][:2_048],
                        start,
                        index + 1,
                    ),
                    index + 1,
                    True,
                )
            elif character in "\r\n":
                break
        index += 1
    return _Token("string", "", start, index), index, False


def _matching_pairs(
    request: ParseRequest,
    tokens: tuple[_Token, ...],
) -> tuple[dict[int, int], list[IndexDiagnostic]]:
    opening = {"(": ")", "[": "]", "{": "}"}
    closing = {value: key for key, value in opening.items()}
    stack: list[tuple[str, int]] = []
    pairs: dict[int, int] = {}
    for index, token in enumerate(tokens):
        if token.value in opening:
            stack.append((token.value, index))
        elif token.value in closing:
            if not stack or stack[-1][0] != closing[token.value]:
                return pairs, [
                    _diagnostic(
                        request,
                        "parser.go_syntax_error",
                        "Go source contains unmatched delimiters.",
                    )
                ]
            _, start = stack.pop()
            pairs[start] = index
            pairs[index] = start
    if stack:
        return pairs, [
            _diagnostic(
                request,
                "parser.go_syntax_error",
                "Go source contains incomplete delimiters.",
            )
        ]
    return pairs, []


def _diagnostic(
    request: ParseRequest,
    code: str,
    message: str,
) -> IndexDiagnostic:
    return IndexDiagnostic(
        code=code,
        severity=DiagnosticSeverity.WARNING,
        message=message,
        relative_path=request.relative_path,
        parser_name=GoStaticParser.descriptor.name,
        recoverable=True,
    )


def _package_name(tokens: tuple[_Token, ...]) -> str | None:
    for index, token in enumerate(tokens[:256]):
        if token.value != "package":
            continue
        cursor = index + 1
        while cursor < len(tokens) and tokens[cursor].kind == "newline":
            cursor += 1
        if cursor < len(tokens) and tokens[cursor].kind == "identifier":
            return tokens[cursor].value[:512]
    return None


def _receiver_type(
    tokens: tuple[_Token, ...],
    start: int,
    end: int,
) -> str | None:
    identifiers = [
        index
        for index in range(start, end)
        if tokens[index].kind == "identifier"
    ]
    if not identifiers:
        return None
    target_start = identifiers[1] if len(identifiers) > 1 else identifiers[0]
    values = [tokens[target_start].value]
    index = target_start + 1
    while (
        index + 1 < end
        and tokens[index].value == "."
        and tokens[index + 1].kind == "identifier"
    ):
        values.append(tokens[index + 1].value)
        index += 2
    return ".".join(values)[:2_048]


def _token_signature(
    tokens: tuple[_Token, ...],
    start: int,
    end: int,
) -> str | None:
    values = [
        token.value
        for token in tokens[start:end]
        if token.kind != "newline"
    ]
    value = " ".join(values).strip()
    return value[:4_096] or None


def _parameter_count_signature(
    tokens: tuple[_Token, ...],
    start: int,
    end: int,
) -> str:
    count = 0
    content = False
    depth = 0
    for token in tokens[start + 1:end]:
        if token.value in {"(", "[", "{"}:
            depth += 1
        elif token.value in {")", "]", "}"}:
            depth = max(0, depth - 1)
        elif token.value == "," and depth == 0:
            count += 1
        elif token.kind != "newline":
            content = True
    return f"({count + 1 if content else 0} parameters)"


def _go_exported(name: str) -> bool:
    return bool(name and name[0].isupper())


def _identifier_start(character: str) -> bool:
    return character == "_" or character.isalpha()


def _identifier_part(character: str) -> bool:
    return character == "_" or character.isalnum()
