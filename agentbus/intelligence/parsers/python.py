from __future__ import annotations

import ast
from pathlib import PurePosixPath

from agentbus.intelligence.models import (
    DependencyKind,
    DiagnosticSeverity,
    IndexDiagnostic,
    SourceLanguage,
    SymbolKind,
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
from agentbus.intelligence.parsers.common import (
    cancellation_requested,
    finalize_result,
    sanitize_documentation,
)


class PythonAstParser:
    descriptor = ParserDescriptor(
        name="python-ast",
        version="1.1.0",
        languages=(SourceLanguage.PYTHON,),
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

        tree, diagnostics, partial = _parse_tree(request)
        module_name = _module_name(request.relative_path)
        visitor = _PythonDefinitionVisitor(
            request,
            module_name,
            active_limits,
            cancellation,
        )
        visitor.add_module_definition(
            ast.get_docstring(tree, clean=False)
            if tree is not None
            else None
        )
        if tree is not None:
            try:
                visitor.visit(tree)
            except RecursionError:
                visitor.partial = True
                visitor.diagnostics.append(
                    IndexDiagnostic(
                        code="parser.python_traversal_error",
                        severity=DiagnosticSeverity.WARNING,
                        message="Python syntax traversal exceeded safe recursion.",
                        relative_path=request.relative_path,
                        parser_name=self.descriptor.name,
                        recoverable=True,
                    )
                )
        diagnostics.extend(visitor.diagnostics)
        definitions = _apply_explicit_exports(
            visitor.definitions,
            module_name,
            visitor.explicit_exports,
        )
        return finalize_result(
            self.descriptor,
            request,
            definitions=definitions,
            references=visitor.references,
            diagnostics=diagnostics,
            limits=active_limits,
            partial=partial or visitor.partial,
            cancelled=visitor.cancelled,
        )


class _PythonDefinitionVisitor(ast.NodeVisitor):
    def __init__(
        self,
        request: ParseRequest,
        module_name: str,
        limits: ParserLimits,
        cancellation: CancellationSignal | None,
    ) -> None:
        self.request = request
        self.module_name = module_name
        self.limits = limits
        self.cancellation = cancellation
        self.definitions: list[ParsedDefinition] = []
        self.references: list[ParsedReference] = []
        self.diagnostics: list[IndexDiagnostic] = []
        self.explicit_exports: set[str] | None = None
        self.scope: list[str] = []
        self.scope_kinds: list[SymbolKind] = []
        self.visited_nodes = 0
        self.partial = False
        self.cancelled = False
        self.stopped = False

    def add_module_definition(self, documentation: str | None) -> None:
        lines = self.request.content.splitlines()
        end_line = max(1, len(lines))
        end_column = len(lines[-1]) if lines else 0
        self.definitions.append(
            ParsedDefinition(
                name=self.module_name.rsplit(".", 1)[-1],
                qualified_name=self.module_name,
                kind=SymbolKind.MODULE,
                location=SymbolLocation(
                    relative_path=self.request.relative_path,
                    start_line=1,
                    start_column=0,
                    end_line=end_line,
                    end_column=end_column,
                ),
                documentation=sanitize_documentation(
                    documentation,
                    maximum_chars=self.limits.maximum_documentation_chars,
                ),
                exported=True,
            )
        )

    def generic_visit(self, node: ast.AST) -> None:
        if self._stop_before(node):
            return
        super().generic_visit(node)

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        if self._stop_before(node):
            return
        qualified_name = self._qualified(node.name)
        self._add_definition(
            node,
            name=node.name,
            qualified_name=qualified_name,
            kind=SymbolKind.CLASS,
            signature=_class_signature(node),
            documentation=ast.get_docstring(node, clean=False),
            exported=self._exported(node.name),
            attributes={"decorators": _decorator_names(node.decorator_list)},
        )
        for base in node.bases:
            target = _expression_name(base)
            if target:
                self._add_reference(
                    base,
                    target=target,
                    kind=DependencyKind.INHERITS,
                    source_qualified_name=qualified_name,
                    confidence=_expression_confidence(base),
                    explanation="Static Python base-class expression.",
                )
        self._visit_scope(node.body, node.name, SymbolKind.CLASS)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._visit_function(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._visit_function(node)

    def visit_Assign(self, node: ast.Assign) -> None:
        if self._stop_before(node):
            return
        if self._indexes_assignments():
            self._record_exports(node.targets, node.value)
            for target in node.targets:
                for name in _assignment_names(target):
                    self._add_assignment(node, name, annotation=None)
        else:
            for target in node.targets:
                self.visit(target)
        self.visit(node.value)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        if self._stop_before(node):
            return
        if self._indexes_assignments():
            annotation = _safe_unparse(node.annotation)
            for name in _assignment_names(node.target):
                self._add_assignment(node, name, annotation=annotation)
        else:
            self.visit(node.target)
        if node.value is not None:
            self.visit(node.value)

    def visit_NamedExpr(self, node: ast.NamedExpr) -> None:
        if self._stop_before(node):
            return
        self.generic_visit(node.value)

    def visit_Import(self, node: ast.Import) -> None:
        if self._stop_before(node):
            return
        for alias in node.names[:256]:
            self._add_reference(
                node,
                target=alias.name,
                kind=DependencyKind.IMPORTS,
                confidence=1.0,
                explanation="Explicit Python import statement.",
                attributes={"alias": alias.asname},
            )

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        if self._stop_before(node):
            return
        prefix = "." * node.level
        module = node.module or ""
        base = f"{prefix}{module}"
        for alias in node.names[:256]:
            target = (
                f"{base}.{alias.name}"
                if base and module
                else f"{base}{alias.name}"
            )
            is_star = alias.name == "*"
            self._add_reference(
                node,
                target=target,
                kind=DependencyKind.IMPORTS,
                confidence=0.35 if is_star else 0.9,
                explanation=(
                    "Python star import with unresolved exported names."
                    if is_star
                    else "Explicit Python from-import statement."
                ),
                attributes={
                    "alias": alias.asname,
                    "relative_level": node.level,
                    "star": is_star,
                },
            )

    def visit_Call(self, node: ast.Call) -> None:
        if self._stop_before(node):
            return
        target = _expression_name(node.func)
        if target:
            last_component = target.rsplit(".", 1)[-1]
            likely_constructor = last_component[:1].isupper()
            self._add_reference(
                node.func,
                target=target,
                kind=(
                    DependencyKind.INSTANTIATES
                    if likely_constructor
                    else DependencyKind.CALLS
                ),
                confidence=0.65 if likely_constructor else 0.75,
                explanation=(
                    "Capitalized Python call may instantiate a type."
                    if likely_constructor
                    else "Statically identifiable Python call target."
                ),
                attributes={"heuristic": True},
            )
        for argument in node.args:
            self.visit(argument)
        for keyword in node.keywords:
            self.visit(keyword.value)

    def visit_Attribute(self, node: ast.Attribute) -> None:
        if self._stop_before(node):
            return
        target = _expression_name(node)
        if target:
            self._add_reference(
                node,
                target=target,
                kind=(
                    DependencyKind.WRITES
                    if isinstance(node.ctx, ast.Store)
                    else DependencyKind.REFERENCES
                ),
                confidence=0.55,
                explanation="Static Python attribute expression.",
            )

    def visit_Name(self, node: ast.Name) -> None:
        if self._stop_before(node):
            return
        if isinstance(node.ctx, ast.Load):
            self._add_reference(
                node,
                target=node.id,
                kind=DependencyKind.REFERENCES,
                confidence=0.45,
                explanation="Unresolved Python name reference.",
            )

    def visit_TypeAlias(self, node: ast.TypeAlias) -> None:
        if self._stop_before(node):
            return
        name = _safe_unparse(node.name)
        if name:
            self._add_definition(
                node,
                name=name,
                qualified_name=self._qualified(name),
                kind=SymbolKind.TYPE_ALIAS,
                signature=_bounded(_safe_unparse(node.value), 4_096),
                exported=self._exported(name),
            )

    def _visit_function(
        self,
        node: ast.FunctionDef | ast.AsyncFunctionDef,
    ) -> None:
        if self._stop_before(node):
            return
        parent_kind = self.scope_kinds[-1] if self.scope_kinds else None
        decorators = _decorator_names(node.decorator_list)
        if parent_kind == SymbolKind.CLASS and node.name == "__init__":
            kind = SymbolKind.CONSTRUCTOR
        elif parent_kind == SymbolKind.CLASS and "property" in decorators:
            kind = SymbolKind.PROPERTY
        elif parent_kind == SymbolKind.CLASS:
            kind = SymbolKind.METHOD
        else:
            kind = SymbolKind.FUNCTION
        qualified_name = self._qualified(node.name)
        self._add_definition(
            node,
            name=node.name,
            qualified_name=qualified_name,
            kind=kind,
            signature=_function_signature(node),
            documentation=ast.get_docstring(node, clean=False),
            exported=self._exported(node.name),
            attributes={
                "async": isinstance(node, ast.AsyncFunctionDef),
                "decorators": decorators,
            },
        )
        self._visit_scope(node.body, node.name, kind)

    def _visit_scope(
        self,
        body: list[ast.stmt],
        name: str,
        kind: SymbolKind,
    ) -> None:
        self.scope.append(name)
        self.scope_kinds.append(kind)
        try:
            for child in body:
                if self.stopped:
                    break
                self.visit(child)
        finally:
            self.scope_kinds.pop()
            self.scope.pop()

    def _add_assignment(
        self,
        node: ast.Assign | ast.AnnAssign,
        name: str,
        *,
        annotation: str | None,
    ) -> None:
        parent_kind = self.scope_kinds[-1] if self.scope_kinds else None
        kind = (
            SymbolKind.FIELD
            if parent_kind == SymbolKind.CLASS
            else SymbolKind.CONSTANT
            if name.isupper()
            else SymbolKind.VARIABLE
        )
        self._add_definition(
            node,
            name=name,
            qualified_name=self._qualified(name),
            kind=kind,
            signature=_bounded(annotation, 4_096),
            exported=self._exported(name),
        )

    def _add_definition(
        self,
        node: ast.AST,
        *,
        name: str,
        qualified_name: str,
        kind: SymbolKind,
        signature: str | None = None,
        documentation: str | None = None,
        exported: bool = False,
        attributes: dict[str, object] | None = None,
    ) -> None:
        if len(self.definitions) > self.limits.maximum_definitions:
            self.partial = True
            self.stopped = True
            return
        self.definitions.append(
            ParsedDefinition(
                name=name[:512],
                qualified_name=qualified_name[:2_048],
                kind=kind,
                location=_node_location(self.request.relative_path, node),
                signature=_bounded(signature, 4_096),
                documentation=sanitize_documentation(
                    documentation,
                    maximum_chars=self.limits.maximum_documentation_chars,
                ),
                parent_qualified_name=(
                    self._qualified(None)[:2_048]
                    if self.scope
                    else None
                ),
                exported=exported,
                attributes=attributes or {},
            )
        )

    def _add_reference(
        self,
        node: ast.AST,
        *,
        target: str,
        kind: DependencyKind,
        confidence: float,
        explanation: str,
        source_qualified_name: str | None = None,
        attributes: dict[str, object] | None = None,
    ) -> None:
        if len(self.references) > self.limits.maximum_references:
            self.partial = True
            self.stopped = True
            return
        self.references.append(
            ParsedReference(
                target=target[:2_048],
                kind=kind,
                location=_node_location(self.request.relative_path, node),
                source_qualified_name=(
                    source_qualified_name or self._qualified(None)
                )[:2_048],
                confidence=confidence,
                explanation=explanation,
                attributes=attributes or {},
            )
        )

    def _record_exports(
        self,
        targets: list[ast.expr],
        value: ast.expr,
    ) -> None:
        if not any(
            isinstance(target, ast.Name) and target.id == "__all__"
            for target in targets
        ):
            return
        exports = _string_collection(value)
        if exports is None:
            self.partial = True
            self.diagnostics.append(
                IndexDiagnostic(
                    code="parser.python_dynamic_exports",
                    severity=DiagnosticSeverity.INFO,
                    message="Python __all__ could not be resolved statically.",
                    relative_path=self.request.relative_path,
                    parser_name=PythonAstParser.descriptor.name,
                    recoverable=True,
                )
            )
            return
        self.explicit_exports = set(exports)
        for name in exports:
            self._add_reference(
                value,
                target=name,
                kind=DependencyKind.EXPORTS,
                confidence=1.0,
                explanation="Name declared in static Python __all__.",
                source_qualified_name=self.module_name,
            )

    def _qualified(self, name: str | None) -> str:
        parts = [self.module_name, *self.scope]
        if name:
            parts.append(name)
        return ".".join(parts)

    def _exported(self, name: str) -> bool:
        return not name.startswith("_") and len(self.scope) == 0

    def _indexes_assignments(self) -> bool:
        return not self.scope_kinds or self.scope_kinds[-1] == SymbolKind.CLASS

    def _stop_before(self, node: ast.AST) -> bool:
        if self.stopped:
            return True
        self.visited_nodes += 1
        if self.visited_nodes > self.limits.maximum_syntax_nodes:
            self.partial = True
            self.stopped = True
            self.diagnostics.append(
                IndexDiagnostic(
                    code="parser.python_node_limit",
                    severity=DiagnosticSeverity.WARNING,
                    message="Python syntax traversal reached its node limit.",
                    relative_path=self.request.relative_path,
                    parser_name=PythonAstParser.descriptor.name,
                    recoverable=True,
                )
            )
            return True
        if (
            self.visited_nodes % self.limits.cancellation_check_interval == 0
            and cancellation_requested(self.cancellation)
        ):
            self.partial = True
            self.cancelled = True
            self.stopped = True
            return True
        return False


def _parse_tree(
    request: ParseRequest,
) -> tuple[ast.Module | None, list[IndexDiagnostic], bool]:
    try:
        return (
            ast.parse(
                request.content,
                filename=request.relative_path,
                type_comments=True,
            ),
            [],
            False,
        )
    except SyntaxError as exc:
        diagnostic = _syntax_diagnostic(request, exc)
        recovered = _recover_prefix(request.content, exc.lineno)
        return recovered, [diagnostic], True
    except (RecursionError, ValueError) as exc:
        return (
            None,
            [
                IndexDiagnostic(
                    code="parser.python_syntax_unavailable",
                    severity=DiagnosticSeverity.WARNING,
                    message="Python syntax could not be parsed safely.",
                    relative_path=request.relative_path,
                    parser_name=PythonAstParser.descriptor.name,
                    recoverable=True,
                    details={"error_type": type(exc).__name__},
                )
            ],
            True,
        )


def _recover_prefix(content: str, error_line: int | None) -> ast.Module | None:
    lines = content.splitlines(keepends=True)
    cutoff = min(len(lines), max(0, (error_line or 1) - 1))
    for _ in range(32):
        if cutoff <= 0:
            return None
        prefix = "".join(lines[:cutoff])
        try:
            return ast.parse(prefix, type_comments=True)
        except (SyntaxError, RecursionError, ValueError):
            cutoff -= 1
    return None


def _syntax_diagnostic(
    request: ParseRequest,
    error: SyntaxError,
) -> IndexDiagnostic:
    details = {
        "error_type": type(error).__name__,
        "line": max(1, error.lineno or 1),
        "column": max(0, (error.offset or 1) - 1),
    }
    return IndexDiagnostic(
        code="parser.python_syntax_error",
        severity=DiagnosticSeverity.WARNING,
        message="Python source contains incomplete or invalid syntax.",
        relative_path=request.relative_path,
        parser_name=PythonAstParser.descriptor.name,
        recoverable=True,
        details=details,
    )


def _node_location(relative_path: str, node: ast.AST) -> SymbolLocation:
    start_line = max(1, int(getattr(node, "lineno", 1)))
    start_column = max(0, int(getattr(node, "col_offset", 0)))
    end_line = max(start_line, int(getattr(node, "end_lineno", start_line)))
    end_column = max(
        0,
        int(getattr(node, "end_col_offset", start_column)),
    )
    return SymbolLocation(
        relative_path=relative_path,
        start_line=start_line,
        start_column=start_column,
        end_line=end_line,
        end_column=end_column,
    )


def _module_name(relative_path: str) -> str:
    path = PurePosixPath(relative_path)
    if path.name == "__init__.py":
        parts = path.parent.parts
        return ".".join(parts) if parts else "__init__"
    return ".".join((*path.parent.parts, path.stem)).lstrip(".")


def _function_signature(
    node: ast.FunctionDef | ast.AsyncFunctionDef,
) -> str:
    arguments = _safe_unparse(node.args) or "()"
    if not arguments.startswith("("):
        arguments = f"({arguments})"
    returns = _safe_unparse(node.returns) if node.returns is not None else None
    prefix = "async " if isinstance(node, ast.AsyncFunctionDef) else ""
    value = f"{prefix}{arguments}"
    if returns:
        value = f"{value} -> {returns}"
    return _bounded(value, 4_096) or "()"


def _class_signature(node: ast.ClassDef) -> str | None:
    values = [
        value
        for value in (_safe_unparse(base) for base in node.bases)
        if value
    ]
    return _bounded(f"({', '.join(values)})", 4_096) if values else None


def _decorator_names(nodes: list[ast.expr]) -> tuple[str, ...]:
    values = [
        value
        for value in (_expression_name(node) for node in nodes[:64])
        if value
    ]
    return tuple(values)


def _expression_name(node: ast.AST) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        parent = _expression_name(node.value)
        return f"{parent}.{node.attr}" if parent else node.attr
    if isinstance(node, ast.Call):
        return _expression_name(node.func)
    return _bounded(_safe_unparse(node), 2_048)


def _expression_confidence(node: ast.AST) -> float:
    return 1.0 if isinstance(node, (ast.Name, ast.Attribute)) else 0.7


def _string_collection(node: ast.AST) -> tuple[str, ...] | None:
    if not isinstance(node, (ast.List, ast.Tuple, ast.Set)):
        return None
    values: list[str] = []
    for item in node.elts[:256]:
        if not (
            isinstance(item, ast.Constant)
            and isinstance(item.value, str)
            and item.value
        ):
            return None
        values.append(item.value[:512])
    return tuple(values)


def _apply_explicit_exports(
    definitions: list[ParsedDefinition],
    module_name: str,
    exports: set[str] | None,
) -> tuple[ParsedDefinition, ...]:
    if exports is None:
        return tuple(definitions)
    normalized: list[ParsedDefinition] = []
    for definition in definitions:
        top_level = definition.parent_qualified_name is None
        if top_level and definition.kind != SymbolKind.MODULE:
            expected = definition.qualified_name == (
                f"{module_name}.{definition.name}"
            )
            if expected:
                definition = ParsedDefinition.model_validate(
                    definition.model_copy(
                        update={"exported": definition.name in exports}
                    ).model_dump(mode="python")
                )
        normalized.append(definition)
    return tuple(normalized)


def _assignment_names(node: ast.AST) -> tuple[str, ...]:
    if isinstance(node, ast.Name):
        return (node.id,)
    if isinstance(node, (ast.Tuple, ast.List)):
        return tuple(
            name
            for item in node.elts
            for name in _assignment_names(item)
        )
    return ()


def _safe_unparse(node: ast.AST | None) -> str | None:
    if node is None:
        return None
    try:
        return ast.unparse(node)
    except (RecursionError, ValueError):
        return None


def _bounded(value: str | None, maximum: int) -> str | None:
    if value is None:
        return None
    normalized = value.strip()
    return normalized[:maximum] or None
