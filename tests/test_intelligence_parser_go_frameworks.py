from __future__ import annotations

from agentbus.intelligence import file_id, repository_identity
from agentbus.intelligence.models import SourceLanguage, SymbolKind
from agentbus.intelligence.parsers import GoStaticParser, ParseRequest


def _parse(
    content: str,
    path: str = "service/service.go",
):
    repository = repository_identity("example/go-frameworks")
    request = ParseRequest.from_content(
        repository_id=repository.repository_id,
        file_id=file_id(repository.repository_id, path),
        relative_path=path,
        language=SourceLanguage.GO,
        content=content,
    )
    return GoStaticParser().parse(request)


def test_identifies_go_tests_benchmarks_fuzzers_and_examples() -> None:
    result = _parse(
        """
package service

func TestHealth(t *testing.T) {}
func BenchmarkHealth(b *testing.B) {}
func FuzzHealth(f *testing.F) {}
func ExampleHealth() {}
func Testhelper(t *testing.T) {}
func helper() {}
""".lstrip(),
        path="service/service_test.go",
    )
    by_name = {item.name: item for item in result.definitions}

    assert by_name["TestHealth"].kind == SymbolKind.TEST
    assert by_name["TestHealth"].attributes["test_kind"] == "test"
    assert by_name["BenchmarkHealth"].attributes["test_kind"] == "benchmark"
    assert by_name["FuzzHealth"].attributes["test_kind"] == "fuzz"
    assert by_name["ExampleHealth"].attributes["test_kind"] == "example"
    assert by_name["Testhelper"].test is False
    assert by_name["helper"].test is False
    assert {item.name for item in result.tests} == {
        "BenchmarkHealth",
        "ExampleHealth",
        "FuzzHealth",
        "TestHealth",
    }


def test_does_not_mark_production_test_prefixes_as_go_tests() -> None:
    result = _parse(
        """
package service
func TestBusinessRule() {}
""".lstrip()
    )
    function = next(
        item
        for item in result.definitions
        if item.name == "TestBusinessRule"
    )

    assert function.kind == SymbolKind.FUNCTION
    assert function.test is False


def test_identifies_static_go_http_handlers_and_routes() -> None:
    result = _parse(
        """
package service

func health(w http.ResponseWriter, r *http.Request) {}
func items(w http.ResponseWriter, r *http.Request) {}
func createItem(c *gin.Context) {}

func register() {
    http.HandleFunc("/health", health)
    http.HandleFunc("HEAD /ready", health)
    mux.HandleFunc("/items", items).Methods("GET", "POST")
    mux.HandleFunc("/dynamic-method", items).Methods(methodName)
    router.POST("/items", createItem)
    router.MethodFunc("DELETE", "/items/{id}", deleteItem)
    router.GET(dynamicPath, dynamicHandler)
}
""".lstrip()
    )
    by_name = {item.name: item for item in result.definitions}
    endpoints = {item.endpoint: item for item in result.endpoints}

    assert by_name["health"].attributes["http_handler"] is True
    assert by_name["items"].attributes["http_handler"] is True
    assert by_name["createItem"].attributes["http_handler"] is False
    assert set(endpoints) == {
        "ANY /health",
        "DELETE /items/{id}",
        "GET|POST /items",
        "HEAD /ready",
        "POST /items",
        "UNKNOWN /dynamic-method",
    }
    assert endpoints["ANY /health"].attributes["framework"] == "net/http"
    assert endpoints["ANY /health"].attributes["handler"] == "health"
    assert endpoints[
        "GET|POST /items"
    ].attributes["registration_target"] == "mux.HandleFunc"
    assert endpoints["POST /items"].confidence < 1.0
    assert all(item.kind == SymbolKind.ENDPOINT for item in result.endpoints)
    assert any(
        item.code == "parser.go_dynamic_endpoint"
        for item in result.diagnostics
    )
    assert any(
        item.code == "parser.go_dynamic_endpoint_method"
        for item in result.diagnostics
    )
