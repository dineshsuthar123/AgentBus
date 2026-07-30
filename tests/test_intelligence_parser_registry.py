from __future__ import annotations

import pytest

from agentbus.intelligence import (
    ParserCompatibilityError,
    ParserUnavailableError,
    file_id,
    repository_identity,
)
from agentbus.intelligence.models import SourceLanguage
from agentbus.intelligence.parsers import (
    ParseRequest,
    ParserDescriptor,
    ParserRegistry,
    finalize_result,
)


class FakeParser:
    def __init__(
        self,
        name: str,
        version: str,
        languages: tuple[SourceLanguage, ...],
    ) -> None:
        self.descriptor = ParserDescriptor(
            name=name,
            version=version,
            languages=languages,
        )

    def parse(self, request, *, limits=None, cancellation=None):
        del cancellation
        return finalize_result(
            self.descriptor,
            request,
            limits=limits,
        )


def _request(language: SourceLanguage = SourceLanguage.PYTHON) -> ParseRequest:
    repository = repository_identity("example/registry")
    return ParseRequest.from_content(
        repository_id=repository.repository_id,
        file_id=file_id(repository.repository_id, "example.py"),
        relative_path="example.py",
        language=language,
        content="value = 1\n",
    )


def test_registry_rejects_duplicate_language_ownership() -> None:
    registry = ParserRegistry(
        (
            FakeParser(
                "python-ast",
                "1.0.0",
                (SourceLanguage.PYTHON,),
            ),
        )
    )

    with pytest.raises(ParserCompatibilityError, match="already owned"):
        registry.register(
            FakeParser(
                "other-python",
                "1.0.0",
                (SourceLanguage.PYTHON,),
            )
        )


def test_registry_enforces_required_and_snapshot_versions() -> None:
    parser = FakeParser(
        "python-ast",
        "2.0.0",
        (SourceLanguage.PYTHON,),
    )
    with pytest.raises(ParserCompatibilityError, match="required version"):
        ParserRegistry(
            (parser,),
            required_versions={"python-ast": "1.0.0"},
        )

    registry = ParserRegistry((parser,))
    with pytest.raises(ParserCompatibilityError, match="snapshot version"):
        registry.resolve(
            SourceLanguage.PYTHON,
            required_version="1.0.0",
        )


def test_registry_reports_optional_parser_unavailability() -> None:
    registry = ParserRegistry()

    assert registry.supports(SourceLanguage.JAVA) is False
    with pytest.raises(ParserUnavailableError, match="java"):
        registry.resolve(SourceLanguage.JAVA)


def test_registry_revalidates_parser_result_identity() -> None:
    class InvalidResultParser(FakeParser):
        def parse(self, request, *, limits=None, cancellation=None):
            result = super().parse(
                request,
                limits=limits,
                cancellation=cancellation,
            )
            return result.model_copy(update={"source_hash": "0" * 64})

    registry = ParserRegistry(
        (
            InvalidResultParser(
                "python-ast",
                "1.0.0",
                (SourceLanguage.PYTHON,),
            ),
        )
    )

    with pytest.raises(ParserCompatibilityError, match="source hash"):
        registry.parse(_request())


def test_registry_rejects_descriptor_mutation_after_registration() -> None:
    parser = FakeParser(
        "python-ast",
        "1.0.0",
        (SourceLanguage.PYTHON,),
    )
    registry = ParserRegistry((parser,))
    parser.descriptor = ParserDescriptor(
        name="python-ast",
        version="2.0.0",
        languages=(SourceLanguage.PYTHON,),
    )

    with pytest.raises(ParserCompatibilityError, match="changed"):
        registry.parse(_request())


def test_registry_exposes_deterministic_versions_and_parses() -> None:
    registry = ParserRegistry(
        (
            FakeParser(
                "typescript-local",
                "1.2.0",
                (SourceLanguage.TYPESCRIPT, SourceLanguage.JAVASCRIPT),
            ),
            FakeParser(
                "python-ast",
                "1.0.0",
                (SourceLanguage.PYTHON,),
            ),
        )
    )

    result = registry.parse(_request())

    assert result.parser_name == "python-ast"
    assert list(registry.versions()) == ["python-ast", "typescript-local"]
