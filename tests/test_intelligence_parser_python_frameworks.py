from __future__ import annotations

from agentbus.intelligence import file_id, repository_identity
from agentbus.intelligence.models import (
    DependencyKind,
    SourceLanguage,
    SymbolKind,
)
from agentbus.intelligence.parsers import ParseRequest, PythonAstParser


def _parse(content: str, path: str = "tests/test_api.py"):
    repository = repository_identity("example/python-frameworks")
    request = ParseRequest.from_content(
        repository_id=repository.repository_id,
        file_id=file_id(repository.repository_id, path),
        relative_path=path,
        language=SourceLanguage.PYTHON,
        content=content,
    )
    return PythonAstParser().parse(request)


def test_identifies_pytest_tests_fixtures_and_parametrization() -> None:
    result = _parse(
        """
import pytest

@pytest.fixture
def client():
    return object()

@pytest.mark.parametrize("value", [1, 2])
def test_endpoint(client, value):
    def helper():
        return value
    assert value

class TestService:
    def test_method(self):
        pass
""".lstrip()
    )
    by_name = {item.name: item for item in result.definitions}

    assert by_name["client"].test is False
    assert by_name["client"].attributes["fixture"] is True
    assert by_name["test_endpoint"].kind == SymbolKind.TEST
    assert by_name["test_endpoint"].test is True
    assert by_name["test_endpoint"].attributes["parametrized"] is True
    assert by_name["helper"].kind == SymbolKind.FUNCTION
    assert by_name["helper"].test is False
    assert by_name["TestService"].kind == SymbolKind.TEST
    assert by_name["test_method"].kind == SymbolKind.TEST
    assert len(result.tests) == 3


def test_identifies_unittest_classes_and_static_api_endpoints() -> None:
    result = _parse(
        """
class ApiTests(unittest.TestCase):
    def test_health(self):
        pass

@router.get("/health")
async def health():
    return {"ok": True}

@app.route("/items", methods=["GET", "POST"])
def items():
    return []

dynamic_path = "/dynamic"

@router.get(dynamic_path)
def dynamic():
    return None
""".lstrip(),
        path="api.py",
    )
    by_name = {item.name: item for item in result.definitions}

    assert by_name["ApiTests"].test is True
    assert by_name["test_health"].test is True
    assert by_name["health"].kind == SymbolKind.ENDPOINT
    assert by_name["health"].endpoint == "GET /health"
    assert by_name["health"].confidence == 0.9
    assert by_name["health"].attributes["framework"] == "fastapi"
    assert by_name["items"].endpoint == "GET|POST /items"
    assert by_name["items"].attributes["framework"] == "flask"
    assert by_name["dynamic"].endpoint is None
    assert any(
        item.code == "parser.python_dynamic_endpoint"
        for item in result.diagnostics
    )


def test_identifies_static_django_routes_and_configuration_references() -> None:
    result = _parse(
        """
urlpatterns = [
    path("health/", health_view),
    re_path(r"^items/$", item_view),
]

SECRET = os.getenv("SECRET_KEY")
TOKEN = os.environ["TOKEN"]
DEBUG = settings.DEBUG
""".lstrip(),
        path="project/urls.py",
    )
    endpoints = {item.endpoint: item for item in result.endpoints}
    configurations = {
        item.target
        for item in result.references
        if item.kind == DependencyKind.CONFIGURES
    }

    assert set(endpoints) == {"ROUTE ^items/$", "ROUTE health/"}
    assert endpoints["ROUTE health/"].attributes["view"] == "health_view"
    assert configurations == {
        "env.SECRET_KEY",
        "env.TOKEN",
        "settings.DEBUG",
    }
