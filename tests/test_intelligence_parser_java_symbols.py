from __future__ import annotations

from agentbus.execution.cancellation import CancellationToken
from agentbus.intelligence import file_id, repository_identity
from agentbus.intelligence.models import SourceLanguage, SymbolKind
from agentbus.intelligence.parsers import (
    JavaStaticParser,
    ParseRequest,
    ParserLimits,
)


def _request(content: str) -> ParseRequest:
    repository = repository_identity("example/java-parser")
    return ParseRequest.from_content(
        repository_id=repository.repository_id,
        file_id=file_id(
            repository.repository_id,
            "src/main/java/example/Service.java",
        ),
        relative_path="src/main/java/example/Service.java",
        language=SourceLanguage.JAVA,
        content=content,
    )


def test_indexes_java_packages_types_records_and_members() -> None:
    result = JavaStaticParser().parse(
        _request(
            """
package example.service;

@Deprecated
public class Service<T> extends BaseService implements Runner {
    public static final String NAME = "service";
    private final String value;

    public Service(String value) {
        this.value = value;
    }

    @Override
    public void run() {
        class LocalWorker {
            void work() {}
        }
    }

    public interface Nested {
        void execute();
    }
}

public record Result(String value, int count) {}
enum State { READY, RUNNING }
""".lstrip()
        )
    )
    by_name = {
        (item.qualified_name, item.kind): item
        for item in result.definitions
    }

    assert ("example.service", SymbolKind.PACKAGE) in by_name
    service = by_name[("example.service.Service", SymbolKind.CLASS)]
    assert service.attributes["annotations"] == ("Deprecated",)
    assert service.exported is True
    assert ("example.service.Service.NAME", SymbolKind.CONSTANT) in by_name
    assert ("example.service.Service.value", SymbolKind.FIELD) in by_name
    assert (
        "example.service.Service.Service",
        SymbolKind.CONSTRUCTOR,
    ) in by_name
    assert ("example.service.Service.run", SymbolKind.METHOD) in by_name
    assert (
        "example.service.Service.run.LocalWorker",
        SymbolKind.CLASS,
    ) in by_name
    assert (
        "example.service.Service.Nested",
        SymbolKind.INTERFACE,
    ) in by_name
    assert ("example.service.Result", SymbolKind.RECORD) in by_name
    assert ("example.service.State", SymbolKind.ENUM) in by_name
    assert result.partial is False


def test_java_parser_does_not_execute_static_initializers() -> None:
    result = JavaStaticParser().parse(
        _request(
            """
package example;
class Unsafe {
    static { Runtime.getRuntime().exec("must-not-run"); }
}
""".lstrip()
        )
    )

    assert any(item.name == "Unsafe" for item in result.definitions)


def test_java_parser_recovers_declarations_from_incomplete_source() -> None:
    result = JavaStaticParser().parse(
        _request(
            """
package example;
class Complete {}
class Incomplete {
""".lstrip()
        )
    )

    assert result.partial is True
    assert any(item.name == "Complete" for item in result.definitions)
    assert any(
        item.code == "parser.java_syntax_error"
        for item in result.diagnostics
    )


def test_java_parser_honors_cancellation_and_token_limits() -> None:
    request = _request(
        "package example;\n"
        + "\n".join(
            f"class Type{index} {{ int value; }}"
            for index in range(30)
        )
    )
    cancellation = CancellationToken()
    cancellation.request("test")
    cancelled = JavaStaticParser().parse(
        request,
        cancellation=cancellation,
    )
    limited = JavaStaticParser().parse(
        request,
        limits=ParserLimits(
            maximum_syntax_nodes=10,
            cancellation_check_interval=1,
        ),
    )

    assert cancelled.cancelled is True
    assert limited.partial is True
    assert any(
        item.code == "parser.java_token_limit"
        for item in limited.diagnostics
    )
