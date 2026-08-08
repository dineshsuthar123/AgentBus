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


_DECLARATION_MODIFIERS = {
    "abstract",
    "async",
    "declare",
    "default",
    "export",
    "private",
    "protected",
    "public",
    "readonly",
    "static",
}
_CLASS_MEMBER_MODIFIERS = _DECLARATION_MODIFIERS | {
    "get",
    "override",
    "set",
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


class TypeScriptStaticParser:
    descriptor = ParserDescriptor(
        name="typescript-static",
        version="1.2.0",
        languages=(
            SourceLanguage.TYPESCRIPT,
            SourceLanguage.JAVASCRIPT,
        ),
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
        partial = partial or bool(pair_diagnostics)
        parser = _DeclarationParser(
            request,
            tokens,
            pairs,
            active_limits,
        )
        parser.parse()
        reference_scanner = _ReferenceScanner(
            request,
            tokens,
            pairs,
            parser.spans,
            parser.declaration_parentheses,
            active_limits,
            cancellation,
        )
        reference_scanner.scan()
        definitions = _apply_frontend_conventions(
            (*parser.definitions, *reference_scanner.definitions),
            request.relative_path,
        )
        diagnostics.extend(parser.diagnostics)
        diagnostics.extend(reference_scanner.diagnostics)
        return finalize_result(
            self.descriptor,
            request,
            definitions=definitions,
            references=reference_scanner.references,
            diagnostics=diagnostics,
            limits=active_limits,
            partial=(
                partial
                or parser.partial
                or reference_scanner.partial
                or reference_scanner.cancelled
                or cancelled
            ),
            cancelled=cancelled or reference_scanner.cancelled,
        )


class _DeclarationParser:
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
        self.module_name = _module_name(request.relative_path)
        self.definitions: list[ParsedDefinition] = []
        self.spans: list[_DefinitionSpan] = []
        self.declaration_parentheses: set[int] = set()
        self.diagnostics: list[IndexDiagnostic] = []
        self.partial = False

    def parse(self) -> None:
        self._add_definition(
            name=self.module_name.rsplit(".", 1)[-1],
            qualified_name=self.module_name,
            kind=SymbolKind.MODULE,
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
            exported = False
            while (
                index < end
                and self.tokens[index].value in _DECLARATION_MODIFIERS
            ):
                exported = exported or self.tokens[index].value == "export"
                index += 1
            if index >= end:
                return
            value = self.tokens[index].value
            if value in {"class", "interface", "enum"}:
                index = self._parse_type_declaration(
                    declaration_start,
                    index,
                    end,
                    scope,
                    exported,
                )
                continue
            if value == "type":
                index = self._parse_type_alias(
                    declaration_start,
                    index,
                    end,
                    scope,
                    exported,
                )
                continue
            if value == "function":
                index = self._parse_function(
                    declaration_start,
                    index,
                    end,
                    scope,
                    exported,
                )
                continue
            if value in {"const", "let", "var"}:
                index = self._parse_variables(
                    declaration_start,
                    index,
                    end,
                    scope,
                    exported,
                )
                continue
            if scope and scope[-1][1] in {
                SymbolKind.CLASS,
                SymbolKind.INTERFACE,
            }:
                member_end = self._parse_class_member(index, end, scope)
                if member_end > index:
                    index = member_end
                    continue
            if value == "{" and index in self.pairs:
                index = self.pairs[index] + 1
            else:
                index = max(index + 1, declaration_start + 1)

    def _parse_type_declaration(
        self,
        declaration_start: int,
        keyword_index: int,
        end: int,
        scope: tuple[tuple[str, SymbolKind], ...],
        exported: bool,
    ) -> int:
        name_index = keyword_index + 1
        if not self._is_identifier(name_index, end):
            return keyword_index + 1
        name = self.tokens[name_index].value
        keyword = self.tokens[keyword_index].value
        kind = {
            "class": SymbolKind.CLASS,
            "interface": SymbolKind.INTERFACE,
            "enum": SymbolKind.ENUM,
        }[keyword]
        body_index = self._find_value("{", name_index + 1, end)
        body_end = self.pairs.get(body_index) if body_index is not None else None
        terminal = (
            self.tokens[body_end].end
            if body_end is not None
            else self.tokens[name_index].end
        )
        qualified_name = self._qualified(scope, name)
        self._add_definition(
            name=name,
            qualified_name=qualified_name,
            kind=kind,
            start=self.tokens[declaration_start].start,
            end=terminal,
            parent=self._scope_name(scope),
            signature=_type_signature(
                self.tokens,
                name_index + 1,
                body_index if body_index is not None else name_index + 1,
            ),
            exported=exported,
        )
        if body_index is not None and body_end is not None:
            self._parse_range(
                body_index + 1,
                body_end,
                (*scope, (name, kind)),
            )
            return body_end + 1
        return name_index + 1

    def _parse_type_alias(
        self,
        declaration_start: int,
        keyword_index: int,
        end: int,
        scope: tuple[tuple[str, SymbolKind], ...],
        exported: bool,
    ) -> int:
        name_index = keyword_index + 1
        if not self._is_identifier(name_index, end):
            return keyword_index + 1
        terminal_index = self._statement_end(name_index + 1, end)
        name = self.tokens[name_index].value
        self._add_definition(
            name=name,
            qualified_name=self._qualified(scope, name),
            kind=SymbolKind.TYPE_ALIAS,
            start=self.tokens[declaration_start].start,
            end=self.tokens[terminal_index - 1].end,
            parent=self._scope_name(scope),
            exported=exported,
        )
        return terminal_index

    def _parse_function(
        self,
        declaration_start: int,
        keyword_index: int,
        end: int,
        scope: tuple[tuple[str, SymbolKind], ...],
        exported: bool,
    ) -> int:
        name_index = keyword_index + 1
        if self._value(name_index) == "*":
            name_index += 1
        if not self._is_identifier(name_index, end):
            return keyword_index + 1
        name = self.tokens[name_index].value
        parameters = self._find_value("(", name_index + 1, end)
        parameter_end = self.pairs.get(parameters) if parameters is not None else None
        if parameters is not None:
            self.declaration_parentheses.add(parameters)
        body_index = (
            self._find_value("{", parameter_end + 1, end)
            if parameter_end is not None
            else None
        )
        body_end = self.pairs.get(body_index) if body_index is not None else None
        terminal = (
            self.tokens[body_end].end
            if body_end is not None
            else self.tokens[parameter_end].end
            if parameter_end is not None
            else self.tokens[name_index].end
        )
        kind = (
            SymbolKind.METHOD
            if scope and scope[-1][1] == SymbolKind.CLASS
            else SymbolKind.FUNCTION
        )
        self._add_definition(
            name=name,
            qualified_name=self._qualified(scope, name),
            kind=kind,
            start=self.tokens[declaration_start].start,
            end=terminal,
            parent=self._scope_name(scope),
            signature=_parameter_count_signature(
                self.tokens,
                parameters,
                parameter_end,
            ),
            exported=exported,
            attributes={
                "async": any(
                    token.value == "async"
                    for token in self.tokens[declaration_start:keyword_index]
                ),
                "react_component_candidate": name[:1].isupper(),
            },
        )
        if body_index is not None and body_end is not None:
            self._parse_range(
                body_index + 1,
                body_end,
                (*scope, (name, kind)),
            )
            return body_end + 1
        return max(name_index + 1, (parameter_end or name_index) + 1)

    def _parse_variables(
        self,
        declaration_start: int,
        keyword_index: int,
        end: int,
        scope: tuple[tuple[str, SymbolKind], ...],
        exported: bool,
    ) -> int:
        terminal = self._statement_end(keyword_index + 1, end)
        index = keyword_index + 1
        while index < terminal:
            if not self._is_identifier(index, terminal):
                index += 1
                continue
            name = self.tokens[index].value
            comma = self._find_value(",", index + 1, terminal)
            declarator_end = comma if comma is not None else terminal
            equals = self._find_value("=", index + 1, declarator_end)
            arrow = (
                self._find_value("=>", equals + 1, declarator_end)
                if equals is not None
                else None
            )
            kind = (
                SymbolKind.FUNCTION
                if arrow is not None
                else SymbolKind.CONSTANT
                if self.tokens[keyword_index].value == "const" and name.isupper()
                else SymbolKind.VARIABLE
            )
            self._add_definition(
                name=name,
                qualified_name=self._qualified(scope, name),
                kind=kind,
                start=self.tokens[declaration_start].start,
                end=self.tokens[terminal - 1].end,
                parent=self._scope_name(scope),
                exported=exported,
                confidence=0.85 if kind == SymbolKind.FUNCTION else 1.0,
                attributes={
                    "arrow": kind == SymbolKind.FUNCTION,
                    "react_component_candidate": (
                        kind == SymbolKind.FUNCTION
                        and name[:1].isupper()
                    ),
                },
            )
            if comma is None:
                break
            index = comma + 1
        return terminal

    def _parse_class_member(
        self,
        start: int,
        end: int,
        scope: tuple[tuple[str, SymbolKind], ...],
    ) -> int:
        index = start
        while (
            index < end
            and self.tokens[index].value in _CLASS_MEMBER_MODIFIERS
        ):
            index += 1
        if not self._is_identifier(index, end):
            return start
        name = self.tokens[index].value
        next_value = self._value(index + 1)
        if next_value == "(":
            self.declaration_parentheses.add(index + 1)
            parameter_end = self.pairs.get(index + 1)
            if parameter_end is None:
                return index + 1
            body_index = self._find_value("{", parameter_end + 1, end)
            body_end = self.pairs.get(body_index) if body_index is not None else None
            kind = (
                SymbolKind.CONSTRUCTOR
                if name == "constructor"
                else SymbolKind.METHOD
            )
            terminal = body_end if body_end is not None else parameter_end
            self._add_definition(
                name=name,
                qualified_name=self._qualified(scope, name),
                kind=kind,
                start=self.tokens[start].start,
                end=self.tokens[terminal].end,
                parent=self._scope_name(scope),
                signature=_parameter_count_signature(
                    self.tokens,
                    index + 1,
                    parameter_end,
                ),
                attributes={
                    "modifiers": tuple(
                        token.value
                        for token in self.tokens[start:index]
                    )
                },
            )
            if body_index is not None and body_end is not None:
                method_scope = (*scope, (name, kind))
                self._parse_range(body_index + 1, body_end, method_scope)
            return terminal + 1
        if next_value in {":", "=", ";", "?"}:
            terminal = self._statement_end(index + 1, end)
            self._add_definition(
                name=name,
                qualified_name=self._qualified(scope, name),
                kind=SymbolKind.FIELD,
                start=self.tokens[start].start,
                end=self.tokens[terminal - 1].end,
                parent=self._scope_name(scope),
                attributes={
                    "modifiers": tuple(
                        token.value
                        for token in self.tokens[start:index]
                    )
                },
            )
            return terminal
        return start

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
                end=end,
                qualified_name=qualified_name[:2_048],
                kind=kind,
            )
        )

    def _statement_end(self, start: int, end: int) -> int:
        index = start
        while index < end:
            value = self.tokens[index].value
            if value == ";":
                return index + 1
            if value in {"(", "[", "{"} and index in self.pairs:
                index = self.pairs[index] + 1
                continue
            index += 1
        return max(start + 1, end)

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


class _ReferenceScanner:
    def __init__(
        self,
        request: ParseRequest,
        tokens: tuple[_Token, ...],
        pairs: dict[int, int],
        spans: list[_DefinitionSpan],
        declaration_parentheses: set[int],
        limits: ParserLimits,
        cancellation: CancellationSignal | None,
    ) -> None:
        self.request = request
        self.tokens = tokens
        self.pairs = pairs
        self.spans = tuple(spans)
        self.declaration_parentheses = declaration_parentheses
        self.limits = limits
        self.cancellation = cancellation
        self.lines = LineMap(request.relative_path, request.content)
        self.references: list[ParsedReference] = []
        self.definitions: list[ParsedDefinition] = []
        self.diagnostics: list[IndexDiagnostic] = []
        self.partial = False
        self.cancelled = False
        self.stopped = False
        self._handled_calls: set[int] = set()
        self._pseudo_definition_count = 0

    def scan(self) -> None:
        index = 0
        iterations = 0
        while index < len(self.tokens) and not self.stopped:
            iterations += 1
            if (
                iterations % self.limits.cancellation_check_interval == 0
                and cancellation_requested(self.cancellation)
            ):
                self.partial = True
                self.cancelled = True
                self.stopped = True
                break
            value = self.tokens[index].value
            if value == "import":
                index = self._scan_import(index)
                continue
            if value == "export":
                index = self._scan_export(index)
                continue
            if value in {"class", "interface"}:
                self._scan_type_relationships(index)
            if value == "require":
                self._scan_require(index)
            if value in {"module", "exports"}:
                self._scan_commonjs_export(index)
            if value == "process":
                self._scan_process_environment(index)
            if self._is_call(index):
                self._scan_call(index)
            index += 1

    def _scan_import(self, index: int) -> int:
        if self._value(index + 1) == "(":
            self._scan_dynamic_import(index)
            return index + 1
        terminal = self._statement_end(index + 1)
        string_index = next(
            (
                candidate
                for candidate in range(index + 1, terminal)
                if self.tokens[candidate].kind == "string"
            ),
            None,
        )
        if string_index is not None:
            imported = tuple(
                token.value
                for token in self.tokens[index + 1 : string_index]
                if token.kind == "identifier"
                and token.value not in {"as", "from", "type"}
            )[:64]
            self._add_reference(
                string_index,
                target=self.tokens[string_index].value,
                kind=DependencyKind.IMPORTS,
                confidence=1.0,
                explanation="Static ECMAScript module import.",
                attributes={"imported_names": imported},
            )
        return terminal

    def _scan_dynamic_import(self, index: int) -> None:
        opening = index + 1
        closing = self.pairs.get(opening)
        target_index = opening + 1
        if (
            closing is not None
            and target_index < closing
            and self.tokens[target_index].kind == "string"
        ):
            self._handled_calls.add(opening)
            self._add_reference(
                target_index,
                target=self.tokens[target_index].value,
                kind=DependencyKind.IMPORTS,
                confidence=0.9,
                explanation="Static dynamic-import module specifier.",
                attributes={"dynamic": True},
            )

    def _scan_export(self, index: int) -> int:
        terminal = self._statement_end(index + 1)
        from_index = self._find_value("from", index + 1, terminal)
        if from_index is not None:
            target_index = from_index + 1
            if (
                target_index < terminal
                and self.tokens[target_index].kind == "string"
            ):
                self._add_reference(
                    target_index,
                    target=self.tokens[target_index].value,
                    kind=DependencyKind.EXPORTS,
                    confidence=1.0,
                    explanation="Static ECMAScript module re-export.",
                    attributes={"reexport": True},
                )
        brace = self._find_value("{", index + 1, terminal)
        brace_end = self.pairs.get(brace) if brace is not None else None
        if brace is not None and brace_end is not None:
            for candidate in range(brace + 1, brace_end):
                token = self.tokens[candidate]
                if token.kind != "identifier" or token.value == "as":
                    continue
                if self._value(candidate - 1) == "as":
                    continue
                self._add_reference(
                    candidate,
                    target=token.value,
                    kind=DependencyKind.EXPORTS,
                    confidence=0.9,
                    explanation="Named ECMAScript export.",
                )
        return terminal

    def _scan_type_relationships(self, index: int) -> None:
        name_index = index + 1
        if self._kind(name_index) != "identifier":
            return
        source = self._source_for(self.tokens[name_index].start)
        body = self._find_value("{", name_index + 1, len(self.tokens))
        terminal = body if body is not None else self._statement_end(name_index + 1)
        cursor = name_index + 1
        relationship: DependencyKind | None = None
        generic_depth = 0
        while cursor < terminal:
            value = self.tokens[cursor].value
            if value == "<" and relationship is not None:
                generic_depth += 1
                cursor += 1
                continue
            if value == ">" and generic_depth:
                generic_depth -= 1
                cursor += 1
                continue
            if generic_depth:
                cursor += 1
                continue
            if value == "extends":
                relationship = DependencyKind.INHERITS
            elif value == "implements":
                relationship = DependencyKind.IMPLEMENTS
            elif relationship is not None and self.tokens[cursor].kind == "identifier":
                target, target_end = self._qualified_target(cursor, terminal)
                self._add_reference(
                    cursor,
                    target=target,
                    kind=relationship,
                    confidence=0.9,
                    explanation=(
                        "Static TypeScript type relationship; "
                        "cross-file resolution is deferred."
                    ),
                    source=source,
                )
                cursor = target_end
                continue
            cursor += 1

    def _scan_require(self, index: int) -> None:
        opening = index + 1
        closing = self.pairs.get(opening)
        target_index = opening + 1
        if (
            self._value(opening) == "("
            and closing is not None
            and target_index < closing
            and self.tokens[target_index].kind == "string"
        ):
            self._handled_calls.add(opening)
            self._add_reference(
                target_index,
                target=self.tokens[target_index].value,
                kind=DependencyKind.IMPORTS,
                confidence=1.0,
                explanation="Static CommonJS require module specifier.",
                attributes={"commonjs": True},
            )

    def _scan_call(self, index: int) -> None:
        target, opening = self._qualified_target(index, len(self.tokens))
        if not target:
            return
        previous = self._value(index - 1)
        kind = (
            DependencyKind.INSTANTIATES
            if previous == "new"
            else DependencyKind.CALLS
        )
        self._handled_calls.add(opening)
        self._add_reference(
            index,
            target=target,
            kind=kind,
            confidence=0.85 if kind == DependencyKind.INSTANTIATES else 0.7,
            explanation=(
                "Static JavaScript new-expression target."
                if kind == DependencyKind.INSTANTIATES
                else "Syntactically identifiable JavaScript call target."
            ),
            attributes={"heuristic": True},
        )
        self._scan_test_call(index, opening, target)
        self._scan_route_call(index, opening, target)

    def _scan_test_call(self, index: int, opening: int, target: str) -> None:
        function = target.rsplit(".", 1)[-1]
        if function not in {"describe", "it", "test"}:
            return
        title = self._static_argument(opening)
        if title is None:
            return
        source = self._source_for(self.tokens[index].start)
        self._add_pseudo_definition(
            token_index=index,
            name=title,
            qualified_name=f"{source}.__test__.{self._next_pseudo_id()}",
            kind=SymbolKind.TEST,
            test=True,
            confidence=0.9,
            attributes={
                "framework": "jest-or-vitest",
                "suite": function == "describe",
                "test_function": target,
            },
        )

    def _scan_route_call(self, index: int, opening: int, target: str) -> None:
        method = target.rsplit(".", 1)[-1].casefold()
        owner = (
            target.rsplit(".", 1)[0].rsplit(".", 1)[-1].casefold()
            if "." in target
            else ""
        )
        if method not in {
            "delete",
            "get",
            "head",
            "options",
            "patch",
            "post",
            "put",
        } or not (
            owner in {"api", "app", "router", "server"}
            or owner.endswith("router")
        ):
            return
        path = self._static_argument(opening)
        if path is None:
            self.partial = True
            self.diagnostics.append(
                IndexDiagnostic(
                    code="parser.typescript_dynamic_endpoint",
                    severity=DiagnosticSeverity.INFO,
                    message="JavaScript route path could not be resolved statically.",
                    relative_path=self.request.relative_path,
                    parser_name=TypeScriptStaticParser.descriptor.name,
                    recoverable=True,
                )
            )
            return
        source = self._source_for(self.tokens[index].start)
        endpoint = f"{method.upper()} {path}"[:2_048]
        self._add_pseudo_definition(
            token_index=index,
            name=endpoint,
            qualified_name=f"{source}.__endpoint__.{self._next_pseudo_id()}",
            kind=SymbolKind.ENDPOINT,
            endpoint=endpoint,
            confidence=0.8,
            attributes={
                "framework": "express-compatible",
                "heuristic": True,
                "route_call": target,
            },
        )

    def _scan_commonjs_export(self, index: int) -> None:
        target, terminal = self._qualified_target(index, len(self.tokens))
        if (
            target not in {"module.exports"}
            and not target.startswith("exports.")
        ):
            return
        if self._value(terminal) != "=":
            return
        self._add_reference(
            index,
            target=target,
            kind=DependencyKind.EXPORTS,
            confidence=0.9,
            explanation="Static CommonJS export assignment.",
            attributes={"commonjs": True},
        )

    def _scan_process_environment(self, index: int) -> None:
        target, _ = self._qualified_target(index, len(self.tokens))
        if not target.startswith("process.env."):
            return
        key = target.removeprefix("process.env.")
        if not key:
            return
        self._add_reference(
            index,
            target=f"env.{key}",
            kind=DependencyKind.CONFIGURES,
            confidence=1.0,
            explanation="Static process.env configuration reference.",
        )

    def _static_argument(self, opening: int) -> str | None:
        closing = self.pairs.get(opening)
        argument = opening + 1
        if (
            closing is None
            or argument >= closing
            or self.tokens[argument].kind != "string"
        ):
            return None
        value = self.tokens[argument].value.strip()
        return value[:2_048] if value else None

    def _add_pseudo_definition(
        self,
        *,
        token_index: int,
        name: str,
        qualified_name: str,
        kind: SymbolKind,
        test: bool = False,
        endpoint: str | None = None,
        confidence: float,
        attributes: dict[str, object],
    ) -> None:
        if len(self.definitions) > self.limits.maximum_definitions:
            self.partial = True
            self.stopped = True
            return
        token = self.tokens[token_index]
        self.definitions.append(
            ParsedDefinition(
                name=name[:512],
                qualified_name=qualified_name[:2_048],
                kind=kind,
                location=self.lines.location(token.start, token.end),
                parent_qualified_name=self._source_for(token.start)[:2_048],
                test=test,
                endpoint=endpoint,
                confidence=confidence,
                attributes=attributes,
            )
        )

    def _next_pseudo_id(self) -> int:
        self._pseudo_definition_count += 1
        return self._pseudo_definition_count

    def _is_call(self, index: int) -> bool:
        if self.tokens[index].kind != "identifier":
            return False
        if self._value(index - 1) in {".", "?."}:
            return False
        target, terminal = self._qualified_target(index, len(self.tokens))
        del target
        if self._value(terminal) != "(":
            return False
        if terminal in self.declaration_parentheses or terminal in self._handled_calls:
            return False
        if self.tokens[index].value in {
            "catch",
            "for",
            "function",
            "if",
            "import",
            "require",
            "switch",
            "while",
            "with",
        }:
            return False
        return True

    def _qualified_target(self, start: int, end: int) -> tuple[str, int]:
        values = [self.tokens[start].value]
        index = start + 1
        while (
            index + 1 < end
            and self.tokens[index].value in {".", "?."}
            and self.tokens[index + 1].kind == "identifier"
        ):
            values.append(self.tokens[index + 1].value)
            index += 2
        return ".".join(values), index

    def _source_for(self, offset: int) -> str:
        containing = [
            span
            for span in self.spans
            if span.start <= offset <= span.end
        ]
        if not containing:
            return _module_name(self.request.relative_path)
        return min(
            containing,
            key=lambda item: (item.end - item.start, item.qualified_name),
        ).qualified_name

    def _add_reference(
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
        if not target:
            return
        if len(self.references) > self.limits.maximum_references:
            self.partial = True
            self.stopped = True
            return
        token = self.tokens[token_index]
        self.references.append(
            ParsedReference(
                target=target[:2_048],
                kind=kind,
                location=self.lines.location(token.start, token.end),
                source_qualified_name=(
                    source or self._source_for(token.start)
                )[:2_048],
                confidence=confidence,
                explanation=explanation,
                attributes=attributes or {},
            )
        )

    def _statement_end(self, start: int) -> int:
        index = start
        while index < len(self.tokens):
            if self.tokens[index].value == ";":
                return index + 1
            if self.tokens[index].value in {"(", "[", "{"} and index in self.pairs:
                index = self.pairs[index] + 1
            else:
                index += 1
        return len(self.tokens)

    def _find_value(
        self,
        value: str,
        start: int,
        end: int,
    ) -> int | None:
        return next(
            (
                index
                for index in range(start, end)
                if self.tokens[index].value == value
            ),
            None,
        )

    def _value(self, index: int) -> str | None:
        if 0 <= index < len(self.tokens):
            return self.tokens[index].value
        return None

    def _kind(self, index: int) -> str | None:
        if 0 <= index < len(self.tokens):
            return self.tokens[index].kind
        return None


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
    partial = False
    cancelled = False
    iterations = 0
    while index < len(content):
        iterations += 1
        if len(tokens) > limits.maximum_syntax_nodes:
            diagnostics.append(
                _diagnostic(
                    request,
                    "parser.typescript_token_limit",
                    "TypeScript tokenization reached its configured limit.",
                )
            )
            partial = True
            break
        if (
            iterations % limits.cancellation_check_interval == 0
            and cancellation_requested(cancellation)
        ):
            cancelled = True
            partial = True
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
                        "parser.typescript_syntax_error",
                        "TypeScript source contains an unterminated comment.",
                    )
                )
                partial = True
                break
            index = close + 2
            continue
        if character in {'"', "'", "`"}:
            token, index, closed = _string_token(content, index)
            tokens.append(token)
            if not closed:
                diagnostics.append(
                    _diagnostic(
                        request,
                        "parser.typescript_syntax_error",
                        "TypeScript source contains an unterminated string.",
                    )
                )
                partial = True
                break
            continue
        if _identifier_start(character):
            end = index + 1
            while end < len(content) and _identifier_part(content[end]):
                end += 1
            tokens.append(_Token("identifier", content[index:end], index, end))
            index = end
            continue
        if character.isdigit():
            end = index + 1
            while end < len(content) and (
                content[end].isalnum() or content[end] in "._"
            ):
                end += 1
            tokens.append(_Token("number", content[index:end], index, end))
            index = end
            continue
        operator = next(
            (
                value
                for value in ("...", "=>", "?.", "??", "===", "!==", "::")
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
    dynamic_template = False
    while index < len(content):
        character = content[index]
        if escaped:
            escaped = False
        elif character == "\\":
            escaped = True
        elif quote == "`" and content.startswith("${", index):
            dynamic_template = True
        elif character == quote:
            end = index + 1
            value = content[start + 1 : index]
            return (
                _Token(
                    "template" if dynamic_template else "string",
                    value,
                    start,
                    end,
                ),
                end,
                True,
            )
        elif character in "\r\n" and quote != "`":
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
                        "parser.typescript_syntax_error",
                        "TypeScript source contains unmatched delimiters.",
                    )
                ]
            _, start = stack.pop()
            pairs[start] = index
            pairs[index] = start
    if stack:
        return pairs, [
            _diagnostic(
                request,
                "parser.typescript_syntax_error",
                "TypeScript source contains incomplete delimiters.",
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
        parser_name=TypeScriptStaticParser.descriptor.name,
        recoverable=True,
    )


def _module_name(relative_path: str) -> str:
    path = PurePosixPath(relative_path)
    stem = path.name
    for suffix in (".d.ts", ".tsx", ".ts", ".jsx", ".js", ".mjs", ".cjs"):
        if stem.endswith(suffix):
            stem = stem[: -len(suffix)]
            break
    parts = (*path.parent.parts, stem)
    return ".".join(part for part in parts if part and part != ".")


def _apply_frontend_conventions(
    definitions: tuple[ParsedDefinition, ...],
    relative_path: str,
) -> tuple[ParsedDefinition, ...]:
    route_path = _next_route_path(relative_path)
    route_methods = {
        "DELETE",
        "GET",
        "HEAD",
        "OPTIONS",
        "PATCH",
        "POST",
        "PUT",
    }
    normalized: list[ParsedDefinition] = []
    for definition in definitions:
        update: dict[str, object] = {}
        attributes = dict(definition.attributes)
        if (
            definition.kind == SymbolKind.FUNCTION
            and definition.name[:1].isupper()
            and attributes.get("react_component_candidate")
        ):
            attributes.update({"framework": "react", "heuristic": True})
            update.update({"attributes": attributes, "confidence": 0.7})
        if (
            route_path is not None
            and definition.exported
            and definition.name in route_methods
            and definition.parent_qualified_name is None
        ):
            endpoint = f"{definition.name} {route_path}"[:2_048]
            attributes.update({"framework": "nextjs", "heuristic": True})
            update.update(
                {
                    "kind": SymbolKind.ENDPOINT,
                    "endpoint": endpoint,
                    "confidence": 0.85,
                    "attributes": attributes,
                }
            )
        if update:
            definition = ParsedDefinition.model_validate(
                definition.model_copy(update=update).model_dump(mode="python")
            )
        normalized.append(definition)
    return tuple(normalized)


def _next_route_path(relative_path: str) -> str | None:
    path = PurePosixPath(relative_path)
    if not path.name.startswith("route."):
        return None
    parts = list(path.parent.parts)
    if "app" not in parts:
        return None
    parts = parts[parts.index("app") + 1 :]
    route_parts: list[str] = []
    for part in parts:
        if part.startswith("(") and part.endswith(")"):
            continue
        if part.startswith("[") and part.endswith("]"):
            route_parts.append(f":{part[1:-1]}")
        else:
            route_parts.append(part)
    return "/" + "/".join(route_parts)


def _type_signature(
    tokens: tuple[_Token, ...],
    start: int,
    end: int,
) -> str | None:
    values = [
        token.value
        for token in tokens[start:end]
        if token.value in {"extends", "implements"}
        or token.kind == "identifier"
    ]
    signature = " ".join(values).strip()
    return signature[:4_096] or None


def _parameter_count_signature(
    tokens: tuple[_Token, ...],
    start: int | None,
    end: int | None,
) -> str | None:
    if start is None or end is None or end <= start:
        return None
    count = 0
    has_content = False
    depth = 0
    for token in tokens[start + 1 : end]:
        if token.value in {"(", "[", "{"}:
            depth += 1
        elif token.value in {")", "]", "}"}:
            depth = max(0, depth - 1)
        elif token.value == "," and depth == 0:
            count += 1
        else:
            has_content = True
    return f"({count + 1 if has_content else 0} parameters)"


def _identifier_start(character: str) -> bool:
    return character in "_$" or character.isalpha()


def _identifier_part(character: str) -> bool:
    return character in "_$" or character.isalnum()
