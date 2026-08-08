from __future__ import annotations

from agentbus.intelligence import file_id, repository_identity
from agentbus.intelligence.models import SourceLanguage, SymbolKind
from agentbus.intelligence.parsers import JavaStaticParser, ParseRequest


def _parse(
    content: str,
    path: str = "src/main/java/example/ApiController.java",
):
    repository = repository_identity("example/java-frameworks")
    request = ParseRequest.from_content(
        repository_id=repository.repository_id,
        file_id=file_id(repository.repository_id, path),
        relative_path=path,
        language=SourceLanguage.JAVA,
        content=content,
    )
    return JavaStaticParser().parse(request)


def test_identifies_junit_tests_without_executing_annotations() -> None:
    result = _parse(
        """
package example;

@SpringBootTest
class ServiceTests {
    @Test
    void returnsData() {}

    @ParameterizedTest(name = "{0}")
    void acceptsValues(String value) {}

    void testLegacyConvention() {}
    void helper() {}
}
""".lstrip(),
        path="src/test/java/example/ServiceTests.java",
    )
    by_name = {item.name: item for item in result.definitions}

    assert by_name["ServiceTests"].kind == SymbolKind.TEST
    assert by_name["ServiceTests"].test is True
    assert by_name["ServiceTests"].attributes["original_kind"] == "class"
    assert by_name["returnsData"].kind == SymbolKind.TEST
    assert by_name["acceptsValues"].kind == SymbolKind.TEST
    assert by_name["testLegacyConvention"].test is True
    assert by_name["helper"].test is False
    assert {item.name for item in result.tests} == {
        "ServiceTests",
        "acceptsValues",
        "returnsData",
        "testLegacyConvention",
    }


def test_identifies_static_spring_controller_endpoints() -> None:
    result = _parse(
        """
package example;

@RestController
@RequestMapping(path = "/api")
class ItemController {
    @GetMapping("/items/{id}")
    Item getItem() { return null; }

    @PostMapping(path = {"/items", "/legacy-items"})
    Item createItem() { return null; }

    @RequestMapping(
        path = "/health",
        method = {RequestMethod.GET, RequestMethod.HEAD}
    )
    String health() { return "ok"; }

    @GetMapping(ITEM_PATH)
    Item dynamicItem() { return null; }
}

class NotAController {
    @GetMapping("/ignored")
    void ignored() {}

    void testBusinessRule() {}
}
""".lstrip()
    )
    by_name = {item.name: item for item in result.definitions}

    assert by_name["getItem"].kind == SymbolKind.ENDPOINT
    assert by_name["getItem"].endpoint == "GET /api/items/{id}"
    assert by_name["getItem"].attributes["framework"] == "spring"
    assert by_name["createItem"].endpoint == "POST /api/items"
    assert by_name["createItem"].attributes["route_paths"] == (
        "/api/items",
        "/api/legacy-items",
    )
    assert by_name["health"].endpoint == "GET|HEAD /api/health"
    assert by_name["dynamicItem"].endpoint is None
    assert by_name["ignored"].endpoint is None
    assert by_name["testBusinessRule"].test is False
    assert any(
        item.code == "parser.java_dynamic_endpoint"
        for item in result.diagnostics
    )


def test_supports_root_spring_routes_and_qualified_annotations() -> None:
    result = _parse(
        """
package example;

@org.springframework.stereotype.Controller
@RequestMapping(produces = "application/json")
class HealthController {
    @org.springframework.web.bind.annotation.GetMapping
    String health() { return "ok"; }
}

@RestController
@RequestMapping(path = {"/v1", "/v2"})
class VersionedController {
    @GetMapping
    String versioned() { return "ok"; }
}
""".lstrip()
    )
    by_name = {item.name: item for item in result.definitions}

    assert by_name["health"].endpoint == "GET /"
    assert by_name["health"].attributes["mapping_annotation"] == "GetMapping"
    assert by_name["versioned"].endpoint == "GET /v1"
    assert by_name["versioned"].attributes["route_paths"] == (
        "/v1",
        "/v2",
    )
