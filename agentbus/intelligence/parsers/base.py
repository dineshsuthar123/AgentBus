from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Protocol

from pydantic import Field, field_validator, model_validator

from agentbus.intelligence.fingerprints import content_hash
from agentbus.intelligence.models import (
    DependencyKind,
    IndexDiagnostic,
    IntelligenceModel,
    SourceLanguage,
    SymbolKind,
    SymbolLocation,
    _hash,
    _identity,
    _relative_path,
)


_PARSER_NAME_PATTERN = re.compile(r"^[a-z][a-z0-9_.-]{0,127}$")
_PARSER_VERSION_PATTERN = re.compile(
    r"^[0-9]+\.[0-9]+\.[0-9]+(?:[-+][A-Za-z0-9.-]+)?$"
)


@dataclass(frozen=True)
class ParserLimits:
    maximum_source_bytes: int = 10_000_000
    maximum_definitions: int = 50_000
    maximum_references: int = 100_000
    maximum_diagnostics: int = 1_000
    maximum_documentation_chars: int = 8_192
    cancellation_check_interval: int = 256

    def __post_init__(self) -> None:
        _bounded(
            self.maximum_source_bytes,
            "maximum_source_bytes",
            1,
            100_000_000,
        )
        _bounded(
            self.maximum_definitions,
            "maximum_definitions",
            1,
            500_000,
        )
        _bounded(
            self.maximum_references,
            "maximum_references",
            1,
            1_000_000,
        )
        _bounded(
            self.maximum_diagnostics,
            "maximum_diagnostics",
            1,
            10_000,
        )
        _bounded(
            self.maximum_documentation_chars,
            "maximum_documentation_chars",
            1,
            8_192,
        )
        _bounded(
            self.cancellation_check_interval,
            "cancellation_check_interval",
            1,
            100_000,
        )


class ParserDescriptor(IntelligenceModel):
    name: str
    version: str
    languages: tuple[SourceLanguage, ...] = Field(min_length=1, max_length=16)
    maximum_source_bytes: int = Field(default=10_000_000, ge=1, le=100_000_000)
    deterministic: bool = True
    supports_partial_results: bool = True

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        if not _PARSER_NAME_PATTERN.fullmatch(value):
            raise ValueError("parser name must be a portable lowercase identifier")
        return value

    @field_validator("version")
    @classmethod
    def validate_version(cls, value: str) -> str:
        if not _PARSER_VERSION_PATTERN.fullmatch(value):
            raise ValueError("parser version must use semantic version syntax")
        return value

    @field_validator("languages")
    @classmethod
    def validate_languages(
        cls,
        values: tuple[SourceLanguage, ...],
    ) -> tuple[SourceLanguage, ...]:
        if SourceLanguage.UNKNOWN in values:
            raise ValueError("a parser cannot own the unknown language")
        if len(set(values)) != len(values):
            raise ValueError("parser languages must be unique")
        return values

    @model_validator(mode="after")
    def require_safe_contract(self) -> ParserDescriptor:
        if not self.deterministic:
            raise ValueError("repository parsers must be deterministic")
        if not self.supports_partial_results:
            raise ValueError("repository parsers must support partial results")
        return self


class ParseRequest(IntelligenceModel):
    repository_id: str
    file_id: str
    project_id: str | None = None
    module_id: str | None = None
    relative_path: str
    language: SourceLanguage
    source_hash: str
    content: str = Field(max_length=100_000_000, repr=False)

    @classmethod
    def from_content(
        cls,
        *,
        repository_id: str,
        file_id: str,
        relative_path: str,
        language: SourceLanguage,
        content: str,
        project_id: str | None = None,
        module_id: str | None = None,
    ) -> ParseRequest:
        return cls(
            repository_id=repository_id,
            file_id=file_id,
            project_id=project_id,
            module_id=module_id,
            relative_path=relative_path,
            language=language,
            source_hash=content_hash(content),
            content=content,
        )

    @field_validator("repository_id")
    @classmethod
    def validate_repository_id(cls, value: str) -> str:
        return _identity(value, "repo")

    @field_validator("file_id")
    @classmethod
    def validate_file_id(cls, value: str) -> str:
        return _identity(value, "file")

    @field_validator("project_id")
    @classmethod
    def validate_project_id(cls, value: str | None) -> str | None:
        return _identity(value, "project") if value else None

    @field_validator("module_id")
    @classmethod
    def validate_module_id(cls, value: str | None) -> str | None:
        return _identity(value, "module") if value else None

    @field_validator("relative_path")
    @classmethod
    def validate_relative_path(cls, value: str) -> str:
        return _relative_path(value)

    @field_validator("source_hash")
    @classmethod
    def validate_source_hash(cls, value: str) -> str:
        return _hash(value)

    @model_validator(mode="after")
    def validate_content_hash(self) -> ParseRequest:
        if content_hash(self.content) != self.source_hash:
            raise ValueError("parse request content does not match its source hash")
        return self


class ParsedDefinition(IntelligenceModel):
    name: str = Field(min_length=1, max_length=512)
    qualified_name: str = Field(min_length=1, max_length=2_048)
    kind: SymbolKind
    location: SymbolLocation
    signature: str | None = Field(default=None, max_length=4_096)
    documentation: str | None = Field(default=None, max_length=8_192)
    parent_qualified_name: str | None = Field(default=None, max_length=2_048)
    exported: bool = False
    test: bool = False
    endpoint: str | None = Field(default=None, max_length=2_048)
    confidence: float = Field(default=1.0, ge=0, le=1)
    attributes: dict[str, Any] = Field(default_factory=dict)

    @field_validator("attributes")
    @classmethod
    def validate_attributes(cls, value: dict[str, Any]) -> dict[str, Any]:
        if len(value) > 64:
            raise ValueError("parsed definition attributes exceed the entry limit")
        return value


class ParsedReference(IntelligenceModel):
    target: str = Field(min_length=1, max_length=2_048)
    kind: DependencyKind
    location: SymbolLocation
    source_qualified_name: str | None = Field(default=None, max_length=2_048)
    confidence: float = Field(default=1.0, ge=0, le=1)
    explanation: str = Field(min_length=1, max_length=2_048)
    attributes: dict[str, Any] = Field(default_factory=dict)

    @field_validator("attributes")
    @classmethod
    def validate_attributes(cls, value: dict[str, Any]) -> dict[str, Any]:
        if len(value) > 32:
            raise ValueError("parsed reference attributes exceed the entry limit")
        return value


class ParseResult(IntelligenceModel):
    language: SourceLanguage
    parser_name: str
    parser_version: str
    source_hash: str
    definitions: tuple[ParsedDefinition, ...] = Field(
        default=(),
        max_length=500_000,
    )
    references: tuple[ParsedReference, ...] = Field(
        default=(),
        max_length=1_000_000,
    )
    diagnostics: tuple[IndexDiagnostic, ...] = Field(
        default=(),
        max_length=10_000,
    )
    partial: bool = False
    cancelled: bool = False

    @field_validator("parser_name")
    @classmethod
    def validate_parser_name(cls, value: str) -> str:
        if not _PARSER_NAME_PATTERN.fullmatch(value):
            raise ValueError("parse result has an invalid parser name")
        return value

    @field_validator("parser_version")
    @classmethod
    def validate_parser_version(cls, value: str) -> str:
        if not _PARSER_VERSION_PATTERN.fullmatch(value):
            raise ValueError("parse result has an invalid parser version")
        return value

    @field_validator("source_hash")
    @classmethod
    def validate_source_hash(cls, value: str) -> str:
        return _hash(value)

    @model_validator(mode="after")
    def validate_partial_state(self) -> ParseResult:
        if self.cancelled and not self.partial:
            raise ValueError("a cancelled parse result must be partial")
        return self

    @property
    def imports(self) -> tuple[ParsedReference, ...]:
        return self._references_of_kind(DependencyKind.IMPORTS)

    @property
    def exports(self) -> tuple[ParsedReference, ...]:
        return self._references_of_kind(DependencyKind.EXPORTS)

    @property
    def inheritance(self) -> tuple[ParsedReference, ...]:
        return tuple(
            item
            for item in self.references
            if item.kind in {
                DependencyKind.INHERITS,
                DependencyKind.IMPLEMENTS,
            }
        )

    @property
    def calls(self) -> tuple[ParsedReference, ...]:
        return self._references_of_kind(DependencyKind.CALLS)

    @property
    def tests(self) -> tuple[ParsedDefinition, ...]:
        return tuple(item for item in self.definitions if item.test)

    @property
    def endpoints(self) -> tuple[ParsedDefinition, ...]:
        return tuple(item for item in self.definitions if item.endpoint)

    def _references_of_kind(
        self,
        kind: DependencyKind,
    ) -> tuple[ParsedReference, ...]:
        return tuple(item for item in self.references if item.kind == kind)


class LanguageParser(Protocol):
    descriptor: ParserDescriptor

    def parse(
        self,
        request: ParseRequest,
        *,
        limits: ParserLimits | None = None,
        cancellation: CancellationSignal | None = None,
    ) -> ParseResult:
        """Parse source without executing workspace code."""


class CancellationSignal(Protocol):
    def is_set(self) -> bool:
        """Return whether cooperative cancellation was requested."""


def _bounded(value: int, name: str, minimum: int, maximum: int) -> None:
    if value < minimum or value > maximum:
        raise ValueError(f"{name} must be between {minimum} and {maximum}")
