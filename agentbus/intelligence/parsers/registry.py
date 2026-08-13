from __future__ import annotations

import threading
from collections.abc import Iterable, Mapping

from agentbus._failure_injection import (
    FailureInjectionPoint,
    FailureProbe,
    failure_due,
)
from agentbus.intelligence.errors import (
    ParserCompatibilityError,
    ParserUnavailableError,
)
from agentbus.intelligence.models import SourceLanguage
from agentbus.intelligence.parsers.base import (
    CancellationSignal,
    LanguageParser,
    ParseRequest,
    ParseResult,
    ParserDescriptor,
    ParserLimits,
)


class ParserRegistry:
    def __init__(
        self,
        parsers: Iterable[LanguageParser] = (),
        *,
        required_versions: Mapping[str, str] | None = None,
        failure_probe: FailureProbe | None = None,
    ) -> None:
        self._lock = threading.RLock()
        self._by_language: dict[SourceLanguage, LanguageParser] = {}
        self._by_name: dict[str, LanguageParser] = {}
        self._descriptors: dict[str, ParserDescriptor] = {}
        self._failure_probe = failure_probe
        self._required_versions = {
            str(name): str(version)
            for name, version in (required_versions or {}).items()
        }
        if len(self._required_versions) > 64:
            raise ValueError("required parser versions exceed the entry limit")
        for parser in parsers:
            self.register(parser)

    def register(self, parser: LanguageParser) -> ParserDescriptor:
        descriptor = _validated_descriptor(parser)
        parse_method = getattr(parser, "parse", None)
        if not callable(parse_method):
            raise ParserCompatibilityError(
                f"Parser '{descriptor.name}' does not provide parse()."
            )
        with self._lock:
            expected = self._required_versions.get(descriptor.name)
            if expected is not None and expected != descriptor.version:
                raise ParserCompatibilityError(
                    f"Parser '{descriptor.name}' version {descriptor.version} "
                    f"is incompatible with required version {expected}."
                )
            existing_name = self._by_name.get(descriptor.name)
            if existing_name is not None:
                existing = _validated_descriptor(existing_name)
                raise ParserCompatibilityError(
                    f"Parser name '{descriptor.name}' is already registered "
                    f"at version {existing.version}."
                )
            conflicts = [
                language
                for language in descriptor.languages
                if language in self._by_language
            ]
            if conflicts:
                owner = _validated_descriptor(
                    self._by_language[conflicts[0]]
                ).name
                languages = ", ".join(
                    language.value
                    for language in sorted(
                        conflicts,
                        key=lambda item: item.value,
                    )
                )
                raise ParserCompatibilityError(
                    f"Parser '{descriptor.name}' cannot own {languages}; "
                    f"the language is already owned by '{owner}'."
                )
            self._by_name[descriptor.name] = parser
            self._descriptors[descriptor.name] = descriptor
            for language in descriptor.languages:
                self._by_language[language] = parser
        return descriptor

    def resolve(
        self,
        language: SourceLanguage,
        *,
        required_version: str | None = None,
    ) -> LanguageParser:
        normalized = SourceLanguage(language)
        with self._lock:
            parser = self._by_language.get(normalized)
        if parser is None:
            raise ParserUnavailableError(
                f"No local parser is registered for {normalized.value}."
            )
        descriptor = _validated_descriptor(parser)
        with self._lock:
            registered = self._descriptors.get(descriptor.name)
        if registered != descriptor:
            raise ParserCompatibilityError(
                "Parser descriptor changed after registration."
            )
        if required_version is not None and descriptor.version != required_version:
            raise ParserCompatibilityError(
                f"Parser '{descriptor.name}' version {descriptor.version} "
                f"does not match snapshot version {required_version}."
            )
        return parser

    def parse(
        self,
        request: ParseRequest,
        *,
        required_version: str | None = None,
        limits: ParserLimits | None = None,
        cancellation: CancellationSignal | None = None,
    ) -> ParseResult:
        validated_request = ParseRequest.model_validate(
            request.model_dump(mode="python")
        )
        parser = self.resolve(
            validated_request.language,
            required_version=required_version,
        )
        descriptor = _validated_descriptor(parser)
        if failure_due(
            self._failure_probe,
            FailureInjectionPoint.PARSER_FAILURE,
            scope=validated_request.language.value,
        ):
            raise ParserUnavailableError(
                f"Controlled parser failure for {validated_request.language.value}."
            )
        result = parser.parse(
            validated_request,
            limits=limits,
            cancellation=cancellation,
        )
        if not isinstance(result, ParseResult):
            raise ParserCompatibilityError(
                f"Parser '{descriptor.name}' returned an invalid result type."
            )
        validated = ParseResult.model_validate(result.model_dump(mode="python"))
        _validate_result(descriptor, validated_request, validated)
        return validated

    def descriptors(self) -> tuple[ParserDescriptor, ...]:
        with self._lock:
            descriptors = list(self._descriptors.values())
        return tuple(
            sorted(descriptors, key=lambda item: (item.name, item.version))
        )

    def versions(self) -> dict[str, str]:
        return {
            descriptor.name: descriptor.version
            for descriptor in self.descriptors()
        }

    def supports(self, language: SourceLanguage) -> bool:
        with self._lock:
            return SourceLanguage(language) in self._by_language


def _validated_descriptor(parser: LanguageParser) -> ParserDescriptor:
    descriptor = getattr(parser, "descriptor", None)
    if not isinstance(descriptor, ParserDescriptor):
        raise ParserCompatibilityError(
            "Parser descriptor is missing or invalid."
        )
    return ParserDescriptor.model_validate(
        descriptor.model_dump(mode="python")
    )


def _validate_result(
    descriptor: ParserDescriptor,
    request: ParseRequest,
    result: ParseResult,
) -> None:
    if result.language != request.language:
        raise ParserCompatibilityError(
            f"Parser '{descriptor.name}' returned the wrong source language."
        )
    if (
        result.parser_name != descriptor.name
        or result.parser_version != descriptor.version
    ):
        raise ParserCompatibilityError(
            f"Parser '{descriptor.name}' returned incompatible identity metadata."
        )
    if result.source_hash != request.source_hash:
        raise ParserCompatibilityError(
            f"Parser '{descriptor.name}' returned a mismatched source hash."
        )
