from __future__ import annotations

from agentbus.intelligence import file_id, repository_identity
from agentbus.intelligence.models import (
    DependencyKind,
    SourceLanguage,
    SymbolKind,
)
from agentbus.intelligence.parsers import JavaStaticParser, ParseRequest


def _request(content: str) -> ParseRequest:
    repository = repository_identity("example/java-references")
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


def test_resolves_java_imports_type_relationships_and_calls() -> None:
    result = JavaStaticParser().parse(
        _request(
            """
package example.service;

import java.util.List;
import static org.junit.jupiter.api.Assertions.assertEquals;

public class Service<T extends Marker>
        extends BaseService<T>
        implements Runner, AutoCloseable {
    public void run() {
        Helper helper = new Helper<String>();
        helper.execute();
        Utilities.audit();
        Runnable callback = Utilities::audit;
        assertEquals(1, 1);
    }

    public void close() {}
}

interface Child extends Parent {}
record Result(String value) {}
""".lstrip()
        )
    )
    references = {
        (item.target, item.kind): item
        for item in result.references
    }

    normal_import = references[
        ("java.util.List", DependencyKind.IMPORTS)
    ]
    static_import = references[
        (
            "org.junit.jupiter.api.Assertions.assertEquals",
            DependencyKind.IMPORTS,
        )
    ]
    assert normal_import.attributes["static"] is False
    assert static_import.attributes["static"] is True
    assert (
        "BaseService",
        DependencyKind.INHERITS,
    ) in references
    assert ("Runner", DependencyKind.IMPLEMENTS) in references
    assert ("AutoCloseable", DependencyKind.IMPLEMENTS) in references
    assert ("Parent", DependencyKind.INHERITS) in references
    assert ("Marker", DependencyKind.INHERITS) not in references

    constructor = references[
        ("Helper", DependencyKind.INSTANTIATES)
    ]
    call = references[("helper.execute", DependencyKind.CALLS)]
    static_call = references[
        ("Utilities.audit", DependencyKind.CALLS)
    ]
    method_reference = references[
        ("Utilities.audit", DependencyKind.REFERENCES)
    ]
    assert constructor.confidence < 1.0
    assert call.source_qualified_name == "example.service.Service.run"
    assert static_call.source_qualified_name == "example.service.Service.run"
    assert method_reference.attributes["method_reference"] is True
    assert all(
        item.target not in {"run", "close", "Result"}
        for item in result.references
        if item.kind == DependencyKind.CALLS
    )


def test_preserves_overloaded_java_methods_with_distinct_signatures() -> None:
    result = JavaStaticParser().parse(
        _request(
            """
package example;
class Converter {
    String convert(String value) { return value; }
    String convert(String value, int count) { return value; }
}
""".lstrip()
        )
    )
    overloads = [
        item
        for item in result.definitions
        if item.kind == SymbolKind.METHOD and item.name == "convert"
    ]

    assert [item.signature for item in overloads] == [
        "(1 parameters)",
        "(2 parameters)",
    ]


def test_marks_java_wildcard_imports_as_uncertain() -> None:
    result = JavaStaticParser().parse(
        _request(
            """
package example;
import static example.Helpers.*;
class UsesWildcard {}
""".lstrip()
        )
    )
    wildcard = next(
        item
        for item in result.references
        if item.kind == DependencyKind.IMPORTS
    )

    assert wildcard.target == "example.Helpers.*"
    assert wildcard.confidence < 1.0
    assert wildcard.attributes == {
        "static": True,
        "wildcard": True,
    }
