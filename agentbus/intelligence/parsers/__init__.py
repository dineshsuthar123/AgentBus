from agentbus.intelligence.parsers.base import (
    CancellationSignal,
    LanguageParser,
    ParseRequest,
    ParseResult,
    ParsedDefinition,
    ParsedReference,
    ParserDescriptor,
    ParserLimits,
)
from agentbus.intelligence.parsers.common import (
    LineMap,
    cancellation_requested,
    finalize_result,
    sanitize_documentation,
)
from agentbus.intelligence.parsers.go import GoStaticParser
from agentbus.intelligence.parsers.java import JavaStaticParser
from agentbus.intelligence.parsers.registry import ParserRegistry
from agentbus.intelligence.parsers.python import PythonAstParser
from agentbus.intelligence.parsers.typescript import TypeScriptStaticParser


def default_parser_registry() -> ParserRegistry:
    return ParserRegistry(
        (
            PythonAstParser(),
            TypeScriptStaticParser(),
            JavaStaticParser(),
            GoStaticParser(),
        )
    )


__all__ = [
    "CancellationSignal",
    "GoStaticParser",
    "LanguageParser",
    "JavaStaticParser",
    "LineMap",
    "ParseRequest",
    "ParseResult",
    "ParsedDefinition",
    "ParsedReference",
    "ParserDescriptor",
    "ParserLimits",
    "ParserRegistry",
    "PythonAstParser",
    "TypeScriptStaticParser",
    "cancellation_requested",
    "default_parser_registry",
    "finalize_result",
    "sanitize_documentation",
]
