from __future__ import annotations

from agentbus.execution.cancellation import CancellationToken
from agentbus.intelligence import file_id, repository_identity
from agentbus.intelligence.models import SourceLanguage, SymbolKind
from agentbus.intelligence.parsers import (
    ParseRequest,
    ParserLimits,
    PythonAstParser,
)


def _request(content: str, path: str = "sample/module.py") -> ParseRequest:
    repository = repository_identity("example/python-parser")
    return ParseRequest.from_content(
        repository_id=repository.repository_id,
        file_id=file_id(repository.repository_id, path),
        relative_path=path,
        language=SourceLanguage.PYTHON,
        content=content,
    )


def test_indexes_python_definitions_signatures_and_hierarchy() -> None:
    result = PythonAstParser().parse(
        _request(
            '''
"""Module documentation."""

MAX_RETRIES = 3
value: int = 1

@dataclass
class Service(BaseService):
    """Service documentation."""

    endpoint: str = "/health"

    def __init__(self, name: str) -> None:
        self.name = name

    @property
    def label(self) -> str:
        return self.name

    async def run(self, retries: int = 1) -> bool:
        def nested() -> None:
            pass
        return True

def helper(value: int) -> str:
    return str(value)
'''.strip()
            + "\n"
        )
    )
    by_name = {
        (definition.qualified_name, definition.kind): definition
        for definition in result.definitions
    }

    module = by_name[("sample.module", SymbolKind.MODULE)]
    assert module.documentation == "Module documentation."
    assert ("sample.module.MAX_RETRIES", SymbolKind.CONSTANT) in by_name
    assert ("sample.module.value", SymbolKind.VARIABLE) in by_name
    service = by_name[("sample.module.Service", SymbolKind.CLASS)]
    assert service.signature == "(BaseService)"
    assert service.documentation == "Service documentation."
    assert service.attributes["decorators"] == ("dataclass",)
    assert (
        "sample.module.Service.__init__",
        SymbolKind.CONSTRUCTOR,
    ) in by_name
    assert ("sample.module.Service.label", SymbolKind.PROPERTY) in by_name
    run = by_name[("sample.module.Service.run", SymbolKind.METHOD)]
    assert run.signature.startswith("async ")
    assert run.parent_qualified_name == "sample.module.Service"
    assert (
        "sample.module.Service.run.nested",
        SymbolKind.FUNCTION,
    ) in by_name
    assert ("sample.module.helper", SymbolKind.FUNCTION) in by_name
    assert by_name[
        ("sample.module.helper", SymbolKind.FUNCTION)
    ].signature == "(value: int) -> str"
    assert result.partial is False


def test_python_parser_is_deterministic_and_does_not_execute_source() -> None:
    request = _request(
        """
raise RuntimeError("must not execute")

class Safe:
    pass
""".strip()
        + "\n"
    )
    parser = PythonAstParser()

    first = parser.parse(request)
    second = parser.parse(request)

    assert first == second
    assert any(item.name == "Safe" for item in first.definitions)


def test_python_parser_recovers_complete_prefix_after_syntax_error() -> None:
    result = PythonAstParser().parse(
        _request(
            """
def complete() -> bool:
    return True

def incomplete(
""".lstrip()
        )
    )

    assert result.partial is True
    assert any(item.name == "complete" for item in result.definitions)
    assert any(
        diagnostic.code == "parser.python_syntax_error"
        for diagnostic in result.diagnostics
    )


def test_python_parser_honors_cancellation_and_syntax_limits() -> None:
    request = _request(
        "\n".join(f"value_{index} = {index}" for index in range(20)) + "\n"
    )
    cancellation = CancellationToken()
    cancellation.request("test")
    cancelled = PythonAstParser().parse(
        request,
        cancellation=cancellation,
    )
    limited = PythonAstParser().parse(
        request,
        limits=ParserLimits(
            maximum_definitions=2,
            maximum_syntax_nodes=4,
            cancellation_check_interval=1,
        ),
    )

    assert cancelled.cancelled is True
    assert cancelled.definitions == ()
    assert limited.partial is True
    assert any(
        diagnostic.code in {
            "parser.python_node_limit",
            "parser.result_limit",
        }
        for diagnostic in limited.diagnostics
    )
