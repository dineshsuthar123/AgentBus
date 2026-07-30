from __future__ import annotations

from agentbus.intelligence import file_id, repository_identity
from agentbus.intelligence.models import (
    DependencyKind,
    SourceLanguage,
    SymbolKind,
)
from agentbus.intelligence.parsers import ParseRequest, TypeScriptStaticParser


def _parse(content: str, path: str = "src/api.ts"):
    repository = repository_identity("example/typescript-frameworks")
    request = ParseRequest.from_content(
        repository_id=repository.repository_id,
        file_id=file_id(repository.repository_id, path),
        relative_path=path,
        language=SourceLanguage.TYPESCRIPT,
        content=content,
    )
    return TypeScriptStaticParser().parse(request)


def test_identifies_jest_vitest_tests_and_suites() -> None:
    result = _parse(
        """
describe("service", () => {
    test("returns data", () => {});
    it("handles errors", () => {});
});
""".lstrip(),
        path="src/service.test.ts",
    )

    assert {item.name for item in result.tests} == {
        "handles errors",
        "returns data",
        "service",
    }
    assert any(
        item.attributes["suite"] is True
        for item in result.tests
        if item.name == "service"
    )


def test_identifies_express_routes_commonjs_and_environment_references() -> None:
    result = _parse(
        """
app.get("/health", health);
router.post("/items", createItem);
router.get(dynamicPath, dynamicHandler);
cache.get(dynamicKey);
module.exports = app;
exports.handler = health;
const token = process.env.API_TOKEN;
""".lstrip()
    )
    endpoints = {item.endpoint: item for item in result.endpoints}
    references = {
        (item.kind, item.target): item
        for item in result.references
    }

    assert set(endpoints) == {"GET /health", "POST /items"}
    assert endpoints["GET /health"].confidence == 0.8
    assert (
        DependencyKind.EXPORTS,
        "module.exports",
    ) in references
    assert (
        DependencyKind.EXPORTS,
        "exports.handler",
    ) in references
    assert (
        DependencyKind.CONFIGURES,
        "env.API_TOKEN",
    ) in references
    assert any(
        item.code == "parser.typescript_dynamic_endpoint"
        for item in result.diagnostics
    )
    assert sum(
        item.code == "parser.typescript_dynamic_endpoint"
        for item in result.diagnostics
    ) == 1


def test_identifies_nextjs_route_handlers_and_react_candidates() -> None:
    result = _parse(
        """
export function GET() {
    return Response.json({});
}

export const POST = async () => Response.json({});
export const Dashboard = () => <main />;
""".lstrip(),
        path="src/app/users/[id]/route.ts",
    )
    by_name = {item.name: item for item in result.definitions}

    assert by_name["GET"].kind == SymbolKind.ENDPOINT
    assert by_name["GET"].endpoint == "GET /users/:id"
    assert by_name["POST"].kind == SymbolKind.ENDPOINT
    assert by_name["POST"].endpoint == "POST /users/:id"
    assert by_name["Dashboard"].attributes["framework"] == "react"
    assert by_name["Dashboard"].confidence == 0.7
