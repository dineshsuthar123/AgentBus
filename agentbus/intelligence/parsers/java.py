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
    source_exceeds_byte_limit,
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
_NON_CALL_IDENTIFIERS = {
    "catch",
    "do",
    "for",
    "if",
    "return",
    "switch",
    "synchronized",
    "throw",
    "try",
    "while",
}
_JAVA_TEST_METHOD_ANNOTATIONS = {
    "ParameterizedTest",
    "RepeatedTest",
    "Test",
    "TestFactory",
    "TestTemplate",
    "Theory",
}
_JAVA_TEST_TYPE_ANNOTATIONS = {
    "RunWith",
    "SpringBootTest",
}
_SPRING_CONTROLLER_ANNOTATIONS = {
    "Controller",
    "RestController",
}
_SPRING_ROUTE_METHODS = {
    "DeleteMapping": ("DELETE",),
    "GetMapping": ("GET",),
    "PatchMapping": ("PATCH",),
    "PostMapping": ("POST",),
    "PutMapping": ("PUT",),
}


@dataclass(frozen=True)
class _Token:
    kind: str
    value: str
    start: int
    end: int


@dataclass(frozen=True)
class _Annotation:
    name: str
    argument_start: int | None = None
    argument_end: int | None = None


@dataclass(frozen=True)
class _DefinitionSpan:
    start: int
    end: int
    qualified_name: str
    kind: SymbolKind


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
        if source_exceeds_byte_limit(
            request.content,
            min(
                active_limits.maximum_source_bytes,
                self.descriptor.maximum_source_bytes,
            ),
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
        reference_scanner = _JavaReferenceScanner(
            request,
            tokens,
            pairs,
            tuple(parser.spans),
            parser.declaration_openings,
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
        self.spans: list[_DefinitionSpan] = []
        self.declaration_openings: set[int] = set()
        self.controllers: set[str] = set()
        self.controller_prefixes: dict[str, tuple[str, ...] | None] = {}
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
        annotations: tuple[_Annotation, ...],
    ) -> int:
        name_index = keyword_index + 1
        if not self._is_identifier(name_index, end):
            return keyword_index + 1
        name = self.tokens[name_index].value
        kind = _TYPE_KEYWORDS[self.tokens[keyword_index].value]
        qualified_name = self._qualified(scope, name)
        test = _is_java_test_type(
            name,
            self.request.relative_path,
            annotations,
        )
        definition_kind = SymbolKind.TEST if test else kind
        body = self._find_value("{", name_index + 1, end)
        body_end = self.pairs.get(body) if body is not None else None
        header_opening = self._find_value(
            "(",
            name_index + 1,
            body if body is not None else end,
        )
        if header_opening is not None:
            self.declaration_openings.add(header_opening)
        terminal = (
            self.tokens[body_end].end
            if body_end is not None
            else self.tokens[name_index].end
        )
        self._add_definition(
            name=name,
            qualified_name=qualified_name,
            kind=definition_kind,
            start=self.tokens[declaration_start].start,
            end=terminal,
            parent=self._scope_name(scope),
            signature=_type_signature(
                self.tokens,
                name_index + 1,
                body if body is not None else name_index + 1,
            ),
            exported=self._has_public_modifier(declaration_start, keyword_index),
            test=test,
            attributes={
                "annotations": _annotation_names(annotations),
                "original_kind": kind.value,
            },
        )
        self._record_controller(qualified_name, annotations)
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
        annotations: tuple[_Annotation, ...],
    ) -> int:
        statement_end = self._member_end(start, end)
        if statement_end <= start:
            return start
        opening = self._find_value("(", start, statement_end)
        if opening is not None:
            self.declaration_openings.add(opening)
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
            original_kind = (
                SymbolKind.CONSTRUCTOR
                if name == owner
                else SymbolKind.METHOD
            )
            test = (
                original_kind == SymbolKind.METHOD
                and _is_java_test_method(
                    name,
                    self.request.relative_path,
                    annotations,
                )
            )
            endpoint, endpoint_attributes = self._spring_endpoint(
                scope,
                annotations,
            )
            kind = original_kind
            if endpoint:
                kind = SymbolKind.ENDPOINT
            elif test:
                kind = SymbolKind.TEST
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
                test=test,
                endpoint=endpoint,
                confidence=0.9 if endpoint or test else 1.0,
                attributes={
                    "annotations": _annotation_names(annotations),
                    "modifiers": self._modifiers(
                        declaration_start,
                        name_index,
                    ),
                    "original_kind": original_kind.value,
                    **endpoint_attributes,
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
        annotations: tuple[_Annotation, ...],
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
                        "annotations": _annotation_names(annotations),
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
    ) -> tuple[tuple[_Annotation, ...], int]:
        annotations: list[_Annotation] = []
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
            annotation_name = ".".join(values)[:512]
            argument_start: int | None = None
            argument_end: int | None = None
            if self._value(index) == "(" and index in self.pairs:
                argument_start = index + 1
                argument_end = self.pairs[index]
                index = argument_end + 1
            annotations.append(
                _Annotation(
                    name=annotation_name,
                    argument_start=argument_start,
                    argument_end=argument_end,
                )
            )
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

    def _record_controller(
        self,
        qualified_name: str,
        annotations: tuple[_Annotation, ...],
    ) -> None:
        names = {_annotation_short_name(item) for item in annotations}
        if not names.intersection(_SPRING_CONTROLLER_ANNOTATIONS):
            return
        self.controllers.add(qualified_name)
        mapping = next(
            (
                item
                for item in annotations
                if _annotation_short_name(item) == "RequestMapping"
            ),
            None,
        )
        if mapping is None:
            self.controller_prefixes[qualified_name] = ("",)
            return
        paths = self._annotation_route_paths(mapping)
        if paths is None:
            self.controller_prefixes[qualified_name] = None
            self.diagnostics.append(
                _diagnostic(
                    self.request,
                    "parser.java_dynamic_endpoint",
                    "Spring controller path could not be resolved statically.",
                )
            )
            return
        self.controller_prefixes[qualified_name] = paths

    def _spring_endpoint(
        self,
        scope: tuple[tuple[str, SymbolKind], ...],
        annotations: tuple[_Annotation, ...],
    ) -> tuple[str | None, dict[str, object]]:
        owner = self._scope_name(scope)
        if owner is None or owner not in self.controllers:
            return None, {}
        mapping = next(
            (
                item
                for item in annotations
                if _annotation_short_name(item) in {
                    *_SPRING_ROUTE_METHODS,
                    "RequestMapping",
                }
            ),
            None,
        )
        if mapping is None:
            return None, {}
        prefixes = self.controller_prefixes.get(owner)
        paths = self._annotation_route_paths(mapping)
        if prefixes is None or paths is None:
            if paths is None:
                self.diagnostics.append(
                    _diagnostic(
                        self.request,
                        "parser.java_dynamic_endpoint",
                        "Spring endpoint path could not be resolved statically.",
                    )
                )
            return None, {}
        annotation_name = _annotation_short_name(mapping)
        methods = _SPRING_ROUTE_METHODS.get(
            annotation_name,
            self._request_mapping_methods(mapping),
        )
        combined_paths = tuple(
            _join_route(prefix, path)
            for prefix in prefixes
            for path in paths
        )[:32]
        endpoint = f"{'|'.join(methods)} {combined_paths[0]}"[:2_048]
        return endpoint, {
            "framework": "spring",
            "http_methods": methods,
            "route_paths": combined_paths,
            "mapping_annotation": annotation_name,
        }

    def _annotation_route_paths(
        self,
        annotation: _Annotation,
    ) -> tuple[str, ...] | None:
        if (
            annotation.argument_start is None
            or annotation.argument_end is None
            or annotation.argument_start >= annotation.argument_end
        ):
            return ("",)
        start = annotation.argument_start
        end = annotation.argument_end
        for index in range(start, end - 1):
            if (
                self.tokens[index].value in {"path", "value"}
                and self.tokens[index + 1].value == "="
            ):
                value_end = self._argument_value_end(index + 2, end)
                values = self._static_strings(index + 2, value_end)
                return values or None
        first = self.tokens[start]
        if first.kind == "string":
            value = _java_string_value(self.request.content, first)
            return (value,) if value is not None else None
        if first.value == "{" and start in self.pairs:
            values = self._static_strings(start + 1, self.pairs[start])
            return values or None
        if any(
            self.tokens[index].value == "="
            for index in range(start, end)
        ):
            return ("",)
        return None

    def _request_mapping_methods(
        self,
        annotation: _Annotation,
    ) -> tuple[str, ...]:
        if (
            annotation.argument_start is None
            or annotation.argument_end is None
        ):
            return ("ANY",)
        methods: list[str] = []
        for index in range(
            annotation.argument_start,
            annotation.argument_end - 2,
        ):
            if (
                self.tokens[index].value == "RequestMethod"
                and self.tokens[index + 1].value == "."
                and self.tokens[index + 2].kind == "identifier"
            ):
                method = self.tokens[index + 2].value.upper()
                if method not in methods:
                    methods.append(method)
        return tuple(methods[:16]) or ("ANY",)

    def _argument_value_end(self, start: int, end: int) -> int:
        index = start
        while index < end:
            if self.tokens[index].value == ",":
                return index
            if (
                self.tokens[index].value in {"(", "[", "{"}
                and index in self.pairs
            ):
                index = self.pairs[index] + 1
            else:
                index += 1
        return end

    def _static_strings(self, start: int, end: int) -> tuple[str, ...]:
        values: list[str] = []
        for token in self.tokens[start:end]:
            if token.kind != "string":
                continue
            value = _java_string_value(self.request.content, token)
            if value is not None and value not in values:
                values.append(value)
        return tuple(values[:32])

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


class _JavaReferenceScanner:
    def __init__(
        self,
        request: ParseRequest,
        tokens: tuple[_Token, ...],
        pairs: dict[int, int],
        spans: tuple[_DefinitionSpan, ...],
        declaration_openings: set[int],
        limits: ParserLimits,
        cancellation: CancellationSignal | None,
    ) -> None:
        self.request = request
        self.tokens = tokens
        self.pairs = pairs
        self.spans = spans
        self.declaration_openings = declaration_openings
        self.limits = limits
        self.cancellation = cancellation
        self.lines = LineMap(request.relative_path, request.content)
        self.module_name = (
            _package_name(tokens)
            or PurePosixPath(request.relative_path).stem
        )
        self.references: list[ParsedReference] = []
        self.diagnostics: list[IndexDiagnostic] = []
        self.partial = False
        self.cancelled = False
        self._handled_openings: set[int] = set()
        self._handled_method_references: set[int] = set()

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
            if value in _TYPE_KEYWORDS:
                self._scan_type_relationships(index)
            if self.tokens[index].kind == "identifier":
                self._scan_method_reference(index)
                self._scan_call(index)
            index += 1

    def _scan_import(self, index: int) -> int:
        terminal = self._statement_end(index + 1)
        target_start = index + 1
        is_static = self._value(target_start) == "static"
        if is_static:
            target_start += 1
        target, target_index = self._qualified_target(
            target_start,
            terminal,
            allow_wildcard=True,
        )
        if target:
            self._add_reference(
                target_index,
                target=target,
                kind=DependencyKind.IMPORTS,
                confidence=0.65 if target.endswith(".*") else 1.0,
                explanation=(
                    "Static wildcard Java import with unresolved members."
                    if target.endswith(".*")
                    else "Explicit Java import declaration."
                ),
                source=self.module_name,
                attributes={
                    "static": is_static,
                    "wildcard": target.endswith(".*"),
                },
            )
        return terminal

    def _scan_type_relationships(self, index: int) -> None:
        name_index = index + 1
        if self._kind(name_index) != "identifier":
            return
        body = self._find_value("{", name_index + 1, len(self.tokens))
        terminal = body if body is not None else self._statement_end(name_index + 1)
        source = self._source_for(self.tokens[name_index].start)
        cursor = name_index + 1
        relationship: DependencyKind | None = None
        while cursor < terminal:
            value = self.tokens[cursor].value
            if value == "<":
                cursor = self._skip_angle_group(cursor, terminal)
                continue
            if value == "extends":
                relationship = DependencyKind.INHERITS
                cursor += 1
                continue
            if value == "implements":
                relationship = DependencyKind.IMPLEMENTS
                cursor += 1
                continue
            if (
                relationship is not None
                and self.tokens[cursor].kind == "identifier"
            ):
                target, target_index = self._qualified_target(cursor, terminal)
                self._add_reference(
                    target_index,
                    target=target,
                    kind=relationship,
                    confidence=0.9,
                    explanation=(
                        "Static Java type relationship; cross-file "
                        "resolution is deferred."
                    ),
                    source=source,
                )
                cursor = target_index + 1
                if self._value(cursor) == "<":
                    cursor = self._skip_angle_group(cursor, terminal)
                continue
            cursor += 1

    def _scan_method_reference(self, index: int) -> None:
        target, target_index = self._qualified_target(index, len(self.tokens))
        marker = target_index + 1
        if (
            self._value(marker) != "::"
            or marker in self._handled_method_references
        ):
            return
        member_index = target_index + 2
        if self._kind(member_index) != "identifier":
            return
        self._handled_method_references.add(marker)
        self._add_reference(
            index,
            target=f"{target}.{self.tokens[member_index].value}",
            kind=DependencyKind.REFERENCES,
            confidence=0.8,
            explanation="Syntactically identifiable Java method reference.",
            attributes={"method_reference": True},
        )

    def _scan_call(self, index: int) -> None:
        target, target_index = self._qualified_target(index, len(self.tokens))
        opening = target_index + 1
        if self._value(opening) == "<":
            opening = self._skip_angle_group(opening, len(self.tokens))
        if self._value(opening) != "(":
            return
        if (
            opening in self.declaration_openings
            or opening in self._handled_openings
            or self._is_annotation_target(index)
            or target.rsplit(".", 1)[-1] in _NON_CALL_IDENTIFIERS
        ):
            return
        self._handled_openings.add(opening)
        kind = (
            DependencyKind.INSTANTIATES
            if self._value(index - 1) == "new"
            else DependencyKind.CALLS
        )
        self._add_reference(
            index,
            target=target,
            kind=kind,
            confidence=0.9 if kind == DependencyKind.INSTANTIATES else 0.75,
            explanation=(
                "Static Java constructor invocation."
                if kind == DependencyKind.INSTANTIATES
                else "Syntactically identifiable Java call target."
            ),
            attributes={"heuristic": True},
        )

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

    def _source_for(self, offset: int) -> str:
        containing = [
            span
            for span in self.spans
            if span.start <= offset <= span.end
        ]
        if not containing:
            return self.module_name
        return min(
            containing,
            key=lambda item: (
                item.end - item.start,
                item.qualified_name,
            ),
        ).qualified_name

    def _statement_end(self, start: int) -> int:
        index = start
        while index < len(self.tokens):
            if self.tokens[index].value == ";":
                return index + 1
            if (
                self.tokens[index].value in {"(", "[", "{"}
                and index in self.pairs
            ):
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

    def _qualified_target(
        self,
        start: int,
        end: int,
        *,
        allow_wildcard: bool = False,
    ) -> tuple[str, int]:
        if self._kind(start) != "identifier":
            return "", start
        values = [self.tokens[start].value]
        index = start + 1
        terminal = start
        while index + 1 < end and self.tokens[index].value == ".":
            candidate = self.tokens[index + 1]
            if candidate.kind != "identifier" and not (
                allow_wildcard and candidate.value == "*"
            ):
                break
            values.append(candidate.value)
            terminal = index + 1
            index += 2
        return ".".join(values), terminal

    def _skip_angle_group(self, start: int, end: int) -> int:
        depth = 0
        index = start
        while index < end:
            value = self.tokens[index].value
            if value == "<":
                depth += 1
            elif value == ">":
                depth -= 1
                if depth == 0:
                    return index + 1
            index += 1
        return end

    def _is_annotation_target(self, index: int) -> bool:
        cursor = index - 1
        while (
            self._value(cursor) == "."
            and self._kind(cursor - 1) == "identifier"
        ):
            cursor -= 2
        return self._value(cursor) == "@"

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


def _annotation_short_name(annotation: _Annotation) -> str:
    return annotation.name.rsplit(".", 1)[-1]


def _annotation_names(
    annotations: tuple[_Annotation, ...],
) -> tuple[str, ...]:
    return tuple(item.name for item in annotations)


def _is_java_test_type(
    name: str,
    relative_path: str,
    annotations: tuple[_Annotation, ...],
) -> bool:
    annotation_names = {
        _annotation_short_name(item)
        for item in annotations
    }
    if annotation_names.intersection(_JAVA_TEST_TYPE_ANNOTATIONS):
        return True
    normalized = "/" + relative_path.lower().replace("\\", "/") + "/"
    return (
        "/src/test/" in normalized
        and name.endswith(("IT", "Test", "Tests"))
    )


def _is_java_test_method(
    name: str,
    relative_path: str,
    annotations: tuple[_Annotation, ...],
) -> bool:
    annotation_names = {
        _annotation_short_name(item)
        for item in annotations
    }
    if annotation_names.intersection(_JAVA_TEST_METHOD_ANNOTATIONS):
        return True
    normalized = "/" + relative_path.lower().replace("\\", "/") + "/"
    return "/src/test/" in normalized and name.startswith("test")


def _java_string_value(content: str, token: _Token) -> str | None:
    literal = content[token.start:token.end]
    if (
        len(literal) < 2
        or not literal.startswith('"')
        or not literal.endswith('"')
        or literal.startswith('"""')
    ):
        return None
    value: list[str] = []
    index = 1
    escapes = {
        '"': '"',
        "'": "'",
        "\\": "\\",
        "b": "\b",
        "f": "\f",
        "n": "\n",
        "r": "\r",
        "t": "\t",
    }
    while index < len(literal) - 1:
        character = literal[index]
        if character != "\\":
            value.append(character)
            index += 1
            continue
        if index + 1 >= len(literal) - 1:
            return None
        escaped = literal[index + 1]
        replacement = escapes.get(escaped)
        if replacement is None:
            return None
        value.append(replacement)
        index += 2
    return "".join(value)[:2_048]


def _join_route(prefix: str, path: str) -> str:
    prefix_value = prefix.strip()
    path_value = path.strip()
    if not prefix_value and not path_value:
        return "/"
    combined = "/".join(
        value.strip("/")
        for value in (prefix_value, path_value)
        if value.strip("/")
    )
    return f"/{combined}"[:2_048]


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
