from __future__ import annotations

from dataclasses import dataclass
from pathlib import PurePosixPath

from agentbus.intelligence.models import (
    DependencyKind,
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
    ParsedReference,
    ParserDescriptor,
    ParserLimits,
)
from agentbus.intelligence.parsers.common import (
    LineMap,
    cancellation_requested,
    finalize_result,
)


_GO_NON_CALL_IDENTIFIERS = {
    "case",
    "defer",
    "else",
    "for",
    "func",
    "go",
    "if",
    "import",
    "package",
    "range",
    "select",
    "switch",
    "type",
}
_GO_NON_COMPOSITE_IDENTIFIERS = {
    "func",
    "interface",
    "map",
    "select",
    "struct",
    "switch",
}
_GO_COMPOSITE_PREFIXES = {
    "&",
    "(",
    ",",
    ":",
    ":=",
    "=",
    "[",
    "{",
    "case",
    "return",
}


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


@dataclass(frozen=True)
class _ReceiverDeclaration:
    token_index: int
    receiver: str
    qualified_name: str
    pointer: bool


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
        reference_scanner = _GoReferenceScanner(
            request,
            tokens,
            pairs,
            tuple(parser.definitions),
            tuple(parser.spans),
            parser.declaration_openings,
            parser.declaration_bodies,
            tuple(parser.receiver_declarations),
            active_limits,
            cancellation,
        )
        reference_scanner.scan()
        diagnostics.extend(reference_scanner.diagnostics)
        return finalize_result(
            self.descriptor,
            request,
            definitions=parser.definitions,
            references=reference_scanner.references,
            diagnostics=diagnostics,
            limits=active_limits,
            partial=(
                partial
                or bool(pair_diagnostics)
                or parser.partial
                or reference_scanner.partial
                or cancelled
            ),
            cancelled=cancelled or reference_scanner.cancelled,
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
        self.declaration_bodies: set[int] = set()
        self.receiver_declarations: list[_ReceiverDeclaration] = []
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
            self.declaration_bodies.add(body)
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
        receiver_pointer = False
        receiver_token_index: int | None = None
        if self._value(cursor) == "(" and cursor in self.pairs:
            closing = self.pairs[cursor]
            receiver, receiver_pointer, receiver_token_index = _receiver_details(
                self.tokens,
                cursor + 1,
                closing,
            )
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
        if body is not None:
            self.declaration_bodies.add(body)
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
                "receiver_pointer": receiver_pointer,
            },
        )
        if receiver is not None and receiver_token_index is not None:
            self.receiver_declarations.append(
                _ReceiverDeclaration(
                    token_index=receiver_token_index,
                    receiver=receiver,
                    qualified_name=qualified_name,
                    pointer=receiver_pointer,
                )
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


class _GoReferenceScanner:
    def __init__(
        self,
        request: ParseRequest,
        tokens: tuple[_Token, ...],
        pairs: dict[int, int],
        definitions: tuple[ParsedDefinition, ...],
        spans: tuple[_DefinitionSpan, ...],
        declaration_openings: set[int],
        declaration_bodies: set[int],
        receiver_declarations: tuple[_ReceiverDeclaration, ...],
        limits: ParserLimits,
        cancellation: CancellationSignal | None,
    ) -> None:
        self.request = request
        self.tokens = tokens
        self.pairs = pairs
        self.definitions = definitions
        self.spans = spans
        self.declaration_openings = declaration_openings
        self.declaration_bodies = declaration_bodies
        self.receiver_declarations = receiver_declarations
        self.limits = limits
        self.cancellation = cancellation
        self.lines = LineMap(request.relative_path, request.content)
        self.package_name = (
            _package_name(tokens)
            or PurePosixPath(request.relative_path).stem
        )
        self.references: list[ParsedReference] = []
        self.diagnostics: list[IndexDiagnostic] = []
        self.partial = False
        self.cancelled = False
        self._handled_openings: set[int] = set()
        self._handled_composites: set[int] = set()

    def scan(self) -> None:
        index = 0
        while index < len(self.tokens) and not self.partial:
            if (
                index % self.limits.cancellation_check_interval == 0
                and cancellation_requested(self.cancellation)
            ):
                self.partial = True
                self.cancelled = True
                return
            value = self.tokens[index].value
            if value == "import":
                index = self._scan_import(index)
                continue
            if self.tokens[index].kind == "identifier":
                self._scan_call(index)
                self._scan_composite_literal(index)
            index += 1
        if not self.partial:
            self._scan_receiver_relationships()
        if not self.partial:
            self._scan_structural_implementations()

    def _scan_import(self, index: int) -> int:
        cursor = index + 1
        while self._kind(cursor) == "newline":
            cursor += 1
        if self._value(cursor) == "(" and cursor in self.pairs:
            closing = self.pairs[cursor]
            spec_start = cursor + 1
            token_index = spec_start
            while token_index < closing:
                if self.tokens[token_index].kind == "string":
                    self._add_import(spec_start, token_index)
                if self.tokens[token_index].value in {"\n", ";"}:
                    spec_start = token_index + 1
                token_index += 1
            return closing + 1
        terminal = self._line_end(cursor, len(self.tokens))
        string_index = next(
            (
                candidate
                for candidate in range(cursor, terminal)
                if self.tokens[candidate].kind == "string"
            ),
            None,
        )
        if string_index is not None:
            self._add_import(cursor, string_index)
        return terminal

    def _add_import(self, start: int, string_index: int) -> None:
        alias: str | None = None
        alias_tokens = [
            token
            for token in self.tokens[start:string_index]
            if token.kind == "identifier" or token.value == "."
        ]
        if alias_tokens:
            alias = alias_tokens[-1].value
        target = self.tokens[string_index].value
        if not target:
            return
        self._add_reference_token(
            string_index,
            target=target,
            kind=DependencyKind.IMPORTS,
            confidence=0.9 if "\\" in target else 1.0,
            explanation="Static Go import declaration.",
            source=self.package_name,
            attributes={
                "alias": alias,
                "blank": alias == "_",
                "dot": alias == ".",
                "side_effect_only": alias == "_",
            },
        )

    def _scan_call(self, index: int) -> None:
        target, target_index = self._qualified_target(index, len(self.tokens))
        opening = target_index + 1
        if self._value(opening) == "[" and opening in self.pairs:
            opening = self.pairs[opening] + 1
        if self._value(opening) != "(":
            return
        if (
            opening in self.declaration_openings
            or opening in self._handled_openings
            or target.rsplit(".", 1)[-1] in _GO_NON_CALL_IDENTIFIERS
        ):
            return
        self._handled_openings.add(opening)
        if target in {"make", "new"}:
            instantiated = self._first_argument_type(opening)
            if instantiated:
                self._add_reference_token(
                    index,
                    target=instantiated,
                    kind=DependencyKind.INSTANTIATES,
                    confidence=0.8,
                    explanation=(
                        "Go built-in allocation with a statically "
                        "identifiable type."
                    ),
                    attributes={
                        "builtin": target,
                        "heuristic": True,
                    },
                )
            return
        self._add_reference_token(
            index,
            target=target,
            kind=DependencyKind.CALLS,
            confidence=0.75,
            explanation="Syntactically identifiable Go call target.",
            attributes={"heuristic": True},
        )

    def _scan_composite_literal(self, index: int) -> None:
        target, target_index = self._qualified_target(index, len(self.tokens))
        opening = target_index + 1
        if self._value(opening) == "[" and opening in self.pairs:
            opening = self.pairs[opening] + 1
        if (
            self._value(opening) != "{"
            or opening in self.declaration_bodies
            or opening in self._handled_composites
            or target.rsplit(".", 1)[-1] in _GO_NON_COMPOSITE_IDENTIFIERS
        ):
            return
        previous = self._value(index - 1)
        if (
            not target.rsplit(".", 1)[-1][:1].isupper()
            and previous not in _GO_COMPOSITE_PREFIXES
        ):
            return
        self._handled_composites.add(opening)
        self._add_reference_token(
            index,
            target=target,
            kind=DependencyKind.INSTANTIATES,
            confidence=0.85,
            explanation="Statically identifiable Go composite literal.",
            attributes={"composite_literal": True},
        )

    def _scan_receiver_relationships(self) -> None:
        for index, declaration in enumerate(self.receiver_declarations):
            if (
                index % self.limits.cancellation_check_interval == 0
                and cancellation_requested(self.cancellation)
            ):
                self.partial = True
                self.cancelled = True
                return
            target = self._qualified_local_type(declaration.receiver)
            self._add_reference_token(
                declaration.token_index,
                target=target,
                kind=DependencyKind.REFERENCES,
                confidence=1.0,
                explanation="Explicit Go method receiver type.",
                source=declaration.qualified_name,
                attributes={
                    "receiver": True,
                    "pointer": declaration.pointer,
                },
            )

    def _scan_structural_implementations(self) -> None:
        interface_methods: dict[str, set[tuple[str, str | None]]] = {}
        receiver_methods: dict[
            str,
            dict[tuple[str, str | None], bool],
        ] = {}
        for definition in self.definitions:
            parent = definition.parent_qualified_name
            if not parent:
                continue
            signature = (definition.name, definition.signature)
            if definition.attributes.get("interface_method") is True:
                interface_methods.setdefault(parent, set()).add(signature)
                continue
            receiver = definition.attributes.get("receiver")
            if (
                definition.kind == SymbolKind.METHOD
                and isinstance(receiver, str)
                and receiver
            ):
                concrete = self._qualified_local_type(receiver)
                methods = receiver_methods.setdefault(concrete, {})
                methods[signature] = (
                    methods.get(signature, False)
                    or definition.attributes.get("receiver_pointer") is True
                )
        comparisons = 0
        maximum_comparisons = min(
            100_000,
            max(1_024, self.limits.maximum_references * 4),
        )
        for concrete in sorted(receiver_methods):
            methods = receiver_methods[concrete]
            available = set(methods)
            for interface in sorted(interface_methods):
                comparisons += 1
                if comparisons > maximum_comparisons:
                    self.partial = True
                    self.diagnostics.append(
                        _diagnostic(
                            self.request,
                            "parser.go_relationship_limit",
                            "Go interface matching reached its configured limit.",
                        )
                    )
                    return
                if (
                    comparisons % self.limits.cancellation_check_interval == 0
                    and cancellation_requested(self.cancellation)
                ):
                    self.partial = True
                    self.cancelled = True
                    return
                required = interface_methods[interface]
                if not required or concrete == interface:
                    continue
                if not required.issubset(available):
                    continue
                span = next(
                    (
                        item
                        for item in self.spans
                        if item.qualified_name == concrete
                    ),
                    None,
                )
                if span is None:
                    continue
                self._add_reference_offset(
                    span.start,
                    min(span.end, span.start + 512),
                    target=interface,
                    kind=DependencyKind.IMPLEMENTS,
                    confidence=0.65,
                    explanation=(
                        "Heuristic Go interface satisfaction based on "
                        "locally indexed method names and parameter counts."
                    ),
                    source=concrete,
                    attributes={
                        "heuristic": True,
                        "pointer_receiver_required": any(
                            methods[signature]
                            for signature in required
                        ),
                        "matched_methods": tuple(
                            sorted(name for name, _ in required)
                        )[:64],
                    },
                )

    def _first_argument_type(self, opening: int) -> str | None:
        closing = self.pairs.get(opening)
        if closing is None:
            return None
        index = opening + 1
        while index < closing:
            if self.tokens[index].kind == "identifier":
                target, _ = self._qualified_target(index, closing)
                return target
            index += 1
        return None

    def _qualified_local_type(self, receiver: str) -> str:
        name = receiver.rsplit(".", 1)[-1]
        return f"{self.package_name}.{name}"[:2_048]

    def _source_for(self, offset: int) -> str:
        containing = [
            span
            for span in self.spans
            if span.start <= offset <= span.end
        ]
        if not containing:
            return self.package_name
        return min(
            containing,
            key=lambda item: (
                item.end - item.start,
                item.qualified_name,
            ),
        ).qualified_name

    def _add_reference_token(
        self,
        token_index: int,
        *,
        target: str,
        kind: DependencyKind,
        confidence: float,
        explanation: str,
        source: str | None = None,
        attributes: dict[str, object] | None = None,
    ) -> None:
        token = self.tokens[token_index]
        self._add_reference_offset(
            token.start,
            token.end,
            target=target,
            kind=kind,
            confidence=confidence,
            explanation=explanation,
            source=source,
            attributes=attributes,
        )

    def _add_reference_offset(
        self,
        start: int,
        end: int,
        *,
        target: str,
        kind: DependencyKind,
        confidence: float,
        explanation: str,
        source: str | None = None,
        attributes: dict[str, object] | None = None,
    ) -> None:
        if not target:
            return
        if len(self.references) > self.limits.maximum_references:
            self.partial = True
            return
        self.references.append(
            ParsedReference(
                target=target[:2_048],
                kind=kind,
                location=self.lines.location(start, end),
                source_qualified_name=(
                    source or self._source_for(start)
                )[:2_048],
                confidence=confidence,
                explanation=explanation,
                attributes=attributes or {},
            )
        )

    def _qualified_target(
        self,
        start: int,
        end: int,
    ) -> tuple[str, int]:
        if self._kind(start) != "identifier":
            return "", start
        values = [self.tokens[start].value]
        index = start + 1
        terminal = start
        while (
            index + 1 < end
            and self.tokens[index].value == "."
            and self.tokens[index + 1].kind == "identifier"
        ):
            values.append(self.tokens[index + 1].value)
            terminal = index + 1
            index += 2
        return ".".join(values), terminal

    def _line_end(self, start: int, end: int) -> int:
        index = start
        while index < end:
            if self.tokens[index].value in {"\n", ";"}:
                return index
            if (
                self.tokens[index].value in {"(", "[", "{"}
                and index in self.pairs
            ):
                index = self.pairs[index] + 1
            else:
                index += 1
        return end

    def _kind(self, index: int) -> str | None:
        return self.tokens[index].kind if 0 <= index < len(self.tokens) else None

    def _value(self, index: int) -> str | None:
        return self.tokens[index].value if 0 <= index < len(self.tokens) else None


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


def _receiver_details(
    tokens: tuple[_Token, ...],
    start: int,
    end: int,
) -> tuple[str | None, bool, int | None]:
    identifiers = [
        index
        for index in range(start, end)
        if tokens[index].kind == "identifier"
    ]
    if not identifiers:
        return None, False, None
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
    pointer = any(
        tokens[index].value == "*"
        for index in range(start, target_start)
    )
    return ".".join(values)[:2_048], pointer, target_start


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
