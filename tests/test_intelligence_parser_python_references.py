from __future__ import annotations

from agentbus.intelligence import file_id, repository_identity
from agentbus.intelligence.models import DependencyKind, SourceLanguage
from agentbus.intelligence.parsers import (
    ParseRequest,
    ParserLimits,
    PythonAstParser,
)


def _parse(content: str, path: str = "package/service.py"):
    repository = repository_identity("example/python-references")
    request = ParseRequest.from_content(
        repository_id=repository.repository_id,
        file_id=file_id(repository.repository_id, path),
        relative_path=path,
        language=SourceLanguage.PYTHON,
        content=content,
    )
    return PythonAstParser().parse(request)


def test_indexes_python_imports_aliases_and_star_uncertainty() -> None:
    result = _parse(
        """
import json
import package.client as client
from .models import User as Account
from ..shared import *
""".lstrip()
    )
    imports = {reference.target: reference for reference in result.imports}

    assert imports["json"].confidence == 1.0
    assert imports["package.client"].attributes["alias"] == "client"
    assert imports[".models.User"].attributes["alias"] == "Account"
    assert imports["..shared.*"].confidence == 0.35
    assert imports["..shared.*"].attributes["star"] is True


def test_indexes_python_inheritance_calls_and_scoped_references() -> None:
    result = _parse(
        """
class Service(BaseService, Generic[Item]):
    def run(self) -> None:
        client.send(self.payload)
        Worker()
        self.worker = Worker()
""".lstrip()
    )

    inheritance = {reference.target: reference for reference in result.inheritance}
    calls = {
        reference.target: reference
        for reference in result.references
        if reference.kind
        in {DependencyKind.CALLS, DependencyKind.INSTANTIATES}
    }

    assert inheritance["BaseService"].confidence == 1.0
    assert inheritance["Generic[Item]"].confidence == 0.7
    assert calls["client.send"].kind == DependencyKind.CALLS
    assert calls["Worker"].kind == DependencyKind.INSTANTIATES
    assert all(
        reference.source_qualified_name == "package.service.Service.run"
        for reference in calls.values()
    )
    assert any(
        reference.target == "self.payload"
        and reference.kind == DependencyKind.REFERENCES
        for reference in result.references
    )
    assert any(
        reference.target == "self.worker"
        and reference.kind == DependencyKind.WRITES
        for reference in result.references
    )
    assert sum(
        reference.target == "Worker"
        and reference.kind == DependencyKind.INSTANTIATES
        for reference in result.references
    ) == 2


def test_static_dunder_all_controls_python_exports() -> None:
    result = _parse(
        """
__all__ = ["public"]

def public():
    pass

def visible_by_name_but_not_exported():
    pass
""".lstrip()
    )
    definitions = {item.name: item for item in result.definitions}

    assert definitions["public"].exported is True
    assert definitions["visible_by_name_but_not_exported"].exported is False
    assert [reference.target for reference in result.exports] == ["public"]


def test_python_reference_output_is_bounded() -> None:
    content = "\n".join(f"function_{index}()" for index in range(20)) + "\n"
    repository = repository_identity("example/python-reference-limit")
    request = ParseRequest.from_content(
        repository_id=repository.repository_id,
        file_id=file_id(repository.repository_id, "calls.py"),
        relative_path="calls.py",
        language=SourceLanguage.PYTHON,
        content=content,
    )

    result = PythonAstParser().parse(
        request,
        limits=ParserLimits(maximum_references=3),
    )

    assert len(result.references) == 3
    assert result.partial is True
    assert any(
        diagnostic.code == "parser.result_limit"
        for diagnostic in result.diagnostics
    )
