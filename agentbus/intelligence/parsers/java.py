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


_MODIFIERS = {
    "abstract",
    "default",
    "final",
    "native",
    "private",
    "protected",
    "public",
    "sealed",
    "static",
    "strictfp",
    "synchronized",
    "transient",
    "volatile",
}
_TYPE_KEYWORDS = {
    "class": SymbolKind.CLASS,
    "enum": SymbolKind.ENUM,
    "interface": SymbolKind.INTERFACE,
    "record": SymbolKind.RECORD,
}


@dataclass(frozen=True)
class _Token:
    kind: str
    value: str
    start: int
    end: int


class JavaStaticParser:
    descriptor = ParserDescriptor(
        name="java-static",
        version="1.0.0",
        languages=(SourceLanguage.JAVA,),
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
        parser = _JavaDeclarationParser(
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


class _JavaDeclarationParser:
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
        self.package_name = _package_name(tokens)
        self.module_name = (
            self.package_name
            or PurePosixPath(request.relative_path).stem
        )
        self.definitions: list[ParsedDefinition] = []
        self.diagnostics: list[IndexDiagnostic] = []
        self.partial = False

    def parse(self) -> None:
        self._add_definition(
            name=self.module_name.rsplit(".", 1)[-1],
            qualified_name=self.module_name,
            kind=SymbolKind.PACKAGE
            if self.package_name
            else SymbolKind.MODULE,
            start=0,
            end=len(self.request.content),
            exported=True,
        )
        self._parse_range(0, len(self.tokens), ())

    def _parse_range(
        self,
        start: int,
        end: int,
        scope: tuple[tuple[str, SymbolKind], ...],
    ) -> None:
        index = start
        while index < end and not self.partial:
            declaration_start = index
            annotations, index = self._consume_annotations(index, end)
            while index < end and self.tokens[index].value in _MODIFIERS:
                index += 1
            if index >= end:
                return
            value = self.tokens[index].value
            if value in _TYPE_KEYWORDS:
                index = self._parse_type(
                    declaration_start,
                    index,
                    end,
                    scope,
                    annotations,
                )
                continue
            if scope and scope[-1][1] in set(_TYPE_KEYWORDS.values()):
                member_end = self._parse_member(
                    declaration_start,
                    index,
                    end,
                    scope,
                    annotations,
                )
                if member_end > index:
                    index = member_end
                    continue
            if value == "{" and index in self.pairs:
                index = self.pairs[index] + 1
            else:
                index = max(index + 1, declaration_start + 1)

    def _parse_type(
        self,
        declaration_start: int,
        keyword_index: int,
        end: int,
        scope: tuple[tuple[str, SymbolKind], ...],
        annotations: tuple[str, ...],
    ) -> int:
        name_index = keyword_index + 1
        if not self._is_identifier(name_index, end):
            return keyword_index + 1
        name = self.tokens[name_index].value
        kind = _TYPE_KEYWORDS[self.tokens[keyword_index].value]
        body = self._find_value("{", name_index + 1, end)
        body_end = self.pairs.get(body) if body is not None else None
        terminal = (
            self.tokens[body_end].end
            if body_end is not None
            else self.tokens[name_index].end
        )
        self._add_definition(
            name=name,
            qualified_name=self._qualified(scope, name),
            kind=kind,
            start=self.tokens[declaration_start].start,
            end=terminal,
            parent=self._scope_name(scope),
            signature=_type_signature(
                self.tokens,
                name_index + 1,
                body if body is not None else name_index + 1,
            ),
            exported=self._has_public_modifier(declaration_start, keyword_index),
            attributes={"annotations": annotations},
        )
        if body is not None and body_end is not None:
            self._parse_range(
                body + 1,
                body_end,
                (*scope, (name, kind)),
            )
            return body_end + 1
        return name_index + 1

    def _parse_member(
        self,
        declaration_start: int,
        start: int,
        end: int,
        scope: tuple[tuple[str, SymbolKind], ...],
        annotations: tuple[str, ...],
    ) -> int:
        statement_end = self._member_end(start, end)
        if statement_end <= start:
            return start
        opening = self._find_value("(", start, statement_end)
        if opening is not None:
            name_index = opening - 1
            if not self._is_identifier(name_index, statement_end):
                return statement_end
            closing = self.pairs.get(opening)
            if closing is None:
                self.partial = True
                return statement_end
            name = self.tokens[name_index].value
            body = self._find_value("{", closing + 1, statement_end)
            body_end = self.pairs.get(body) if body is not None else None
            terminal_index = body_end if body_end is not None else closing
            owner = scope[-1][0]
            kind = (
                SymbolKind.CONSTRUCTOR
                if name == owner
                else SymbolKind.METHOD
            )
            self._add_definition(
                name=name,
                qualified_name=self._qualified(scope, name),
                kind=kind,
                start=self.tokens[declaration_start].start,
                end=self.tokens[terminal_index].end,
                parent=self._scope_name(scope),
                signature=_parameter_count_signature(
                    self.tokens,
                    opening,
                    closing,
                ),
                exported=self._has_public_modifier(
                    declaration_start,
                    name_index,
                ),
                attributes={
                    "annotations": annotations,
                    "modifiers": self._modifiers(
                        declaration_start,
                        name_index,
                    ),
                },
            )
            if body is not None and body_end is not None:
                self._parse_range(
                    body + 1,
                    body_end,
                    (*scope, (name, kind)),
                )
            return terminal_index + 1
        return self._parse_fields(
            declaration_start,
            start,
            statement_end,
            scope,
            annotations,
        )

    def _parse_fields(
        self,
        declaration_start: int,
        start: int,
        end: int,
        scope: tuple[tuple[str, SymbolKind], ...],
        annotations: tuple[str, ...],
    ) -> int:
        segment_start = start
        while segment_start < end:
            comma = self._find_value(",", segment_start, end)
            segment_end = comma if comma is not None else end
            equals = self._find_value("=", segment_start, segment_end)
            search_end = equals if equals is not None else segment_end
            candidates = [
                index
                for index in range(segment_start, search_end)
                if self.tokens[index].kind == "identifier"
                and self.tokens[index].value not in _MODIFIERS
            ]
            if len(candidates) >= 2:
                name_index = candidates[-1]
                name = self.tokens[name_index].value
                self._add_definition(
                    name=name,
                    qualified_name=self._qualified(scope, name),
                    kind=SymbolKind.CONSTANT
                    if name.isupper()
                    else SymbolKind.FIELD,
                    start=self.tokens[declaration_start].start,
                    end=self.tokens[end - 1].end,
                    parent=self._scope_name(scope),
                    exported=self._has_public_modifier(
                        declaration_start,
                        name_index,
                    ),
                    attributes={
                        "annotations": annotations,
                        "modifiers": self._modifiers(
                            declaration_start,
                            name_index,
                        ),
                    },
                )
            if comma is None:
                break
            segment_start = comma + 1
        return end

    def _consume_annotations(
        self,
        start: int,
        end: int,
    ) -> tuple[tuple[str, ...], int]:
        annotations: list[str] = []
        index = start
        while index < end and self.tokens[index].value == "@":
            name_index = index + 1
            if not self._is_identifier(name_index, end):
                break
            values = [self.tokens[name_index].value]
            index = name_index + 1
            while (
                index + 1 < end
                and self.tokens[index].value == "."
                and self.tokens[index + 1].kind == "identifier"
            ):
                values.append(self.tokens[index + 1].value)
                index += 2
            annotations.append(".".join(values)[:512])
            if self._value(index) == "(" and index in self.pairs:
                index = self.pairs[index] + 1
        return tuple(annotations[:64]), index

    def _member_end(self, start: int, end: int) -> int:
        index = start
        while index < end:
            value = self.tokens[index].value
            if value == ";":
                return index + 1
            if value == "{" and index in self.pairs:
                return self.pairs[index] + 1
            if value in {"(", "["} and index in self.pairs:
                index = self.pairs[index] + 1
            else:
                index += 1
        return end

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
                confidence=confidence,
                attributes=attributes or {},
            )
        )

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
            if self.tokens[index].value in {"(", "[", "{"} and index in self.pairs:
                index = self.pairs[index] + 1
            else:
                index += 1
        return None

    def _is_identifier(self, index: int, end: int) -> bool:
        return index < end and self.tokens[index].kind == "identifier"

    def _value(self, index: int) -> str | None:
        return self.tokens[index].value if index < len(self.tokens) else None

    def _qualified(
        self,
        scope: tuple[tuple[str, SymbolKind], ...],
        name: str,
    ) -> str:
        return ".".join((self.module_name, *(item[0] for item in scope), name))

    def _scope_name(
        self,
        scope: tuple[tuple[str, SymbolKind], ...],
    ) -> str | None:
        if not scope:
            return None
        return ".".join((self.module_name, *(item[0] for item in scope)))

    def _has_public_modifier(self, start: int, end: int) -> bool:
        return any(
            token.value == "public"
            for token in self.tokens[start:end]
        )

    def _modifiers(self, start: int, end: int) -> tuple[str, ...]:
        return tuple(
            token.value
            for token in self.tokens[start:end]
            if token.value in _MODIFIERS
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
                    "parser.java_token_limit",
                    "Java tokenization reached its configured limit.",
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
        if character.isspace():
            index += 1
            continue
        if content.startswith("//", index):
            newline = content.find("\n", index + 2)
            index = len(content) if newline < 0 else newline + 1
            continue
        if content.startswith("/*", index):
            close = content.find("*/", index + 2)
            if close < 0:
                diagnostics.append(
                    _diagnostic(
                        request,
                        "parser.java_syntax_error",
                        "Java source contains an unterminated comment.",
                    )
                )
                partial = True
                break
            index = close + 2
            continue
        if character == '"' and content.startswith('"""', index):
            close = content.find('"""', index + 3)
            if close < 0:
                diagnostics.append(
                    _diagnostic(
                        request,
                        "parser.java_syntax_error",
                        "Java source contains an unterminated text block.",
                    )
                )
                partial = True
                break
            tokens.append(_Token("string", "", index, close + 3))
            index = close + 3
            continue
        if character in {'"', "'"}:
            token, index, closed = _string_token(content, index)
            tokens.append(token)
            if not closed:
                diagnostics.append(
                    _diagnostic(
                        request,
                        "parser.java_syntax_error",
                        "Java source contains an unterminated literal.",
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
                for value in ("...", "->", "::", "==", "!=", "<=", ">=")
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
        if escaped:
            escaped = False
        elif character == "\\":
            escaped = True
        elif character == quote:
            return (
                _Token("string", "", start, index + 1),
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
                        "parser.java_syntax_error",
                        "Java source contains unmatched delimiters.",
                    )
                ]
            _, start = stack.pop()
            pairs[start] = index
            pairs[index] = start
    if stack:
        return pairs, [
            _diagnostic(
                request,
                "parser.java_syntax_error",
                "Java source contains incomplete delimiters.",
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
        parser_name=JavaStaticParser.descriptor.name,
        recoverable=True,
    )


def _package_name(tokens: tuple[_Token, ...]) -> str | None:
    for index, token in enumerate(tokens[:256]):
        if token.value != "package":
            continue
        values: list[str] = []
        cursor = index + 1
        while cursor < len(tokens) and tokens[cursor].value != ";":
            if tokens[cursor].kind == "identifier":
                values.append(tokens[cursor].value)
            cursor += 1
        return ".".join(values)[:2_048] or None
    return None


def _type_signature(
    tokens: tuple[_Token, ...],
    start: int,
    end: int,
) -> str | None:
    values = [
        token.value
        for token in tokens[start:end]
        if token.value in {"extends", "implements", "permits"}
        or token.kind == "identifier"
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
    for token in tokens[start + 1 : end]:
        if token.value in {"(", "[", "{"}:
            depth += 1
        elif token.value in {")", "]", "}"}:
            depth = max(0, depth - 1)
        elif token.value == "," and depth == 0:
            count += 1
        else:
            content = True
    return f"({count + 1 if content else 0} parameters)"


def _identifier_start(character: str) -> bool:
    return character in "_$" or character.isalpha()


def _identifier_part(character: str) -> bool:
    return character in "_$" or character.isalnum()
