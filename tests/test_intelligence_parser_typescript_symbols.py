from __future__ import annotations

from agentbus.execution.cancellation import CancellationToken
from agentbus.intelligence import file_id, repository_identity
from agentbus.intelligence.models import SourceLanguage, SymbolKind
from agentbus.intelligence.parsers import (
    ParseRequest,
    ParserLimits,
    TypeScriptStaticParser,
)


def _request(
    content: str,
    path: str = "src/service.ts",
    language: SourceLanguage = SourceLanguage.TYPESCRIPT,
) -> ParseRequest:
    repository = repository_identity("example/typescript-parser")
    return ParseRequest.from_content(
        repository_id=repository.repository_id,
        file_id=file_id(repository.repository_id, path),
        relative_path=path,
        language=language,
        content=content,
    )


def test_indexes_typescript_declarations_and_class_members() -> None:
    result = TypeScriptStaticParser().parse(
        _request(
            """
export interface Runner extends BaseRunner {
    run(value: string): Promise<void>;
}

export type Identifier = string | number;
export enum State { Ready, Running }

export class Service extends BaseService implements Runner {
    private readonly name: string;

    constructor(name: string) {
        this.name = name;
    }

    async run(value: string): Promise<void> {
        const nested = () => value;
    }
}

export function createService(name: string): Service {
    return new Service(name);
}

export const Dashboard = () => <main />;
const MAX_RETRIES = 3, factory = () => new Service("sample");
""".lstrip()
        )
    )
    by_name = {
        (item.qualified_name, item.kind): item
        for item in result.definitions
    }

    assert ("src.service.Runner", SymbolKind.INTERFACE) in by_name
    assert ("src.service.Identifier", SymbolKind.TYPE_ALIAS) in by_name
    assert ("src.service.State", SymbolKind.ENUM) in by_name
    assert ("src.service.Service", SymbolKind.CLASS) in by_name
    assert (
        "src.service.Service.constructor",
        SymbolKind.CONSTRUCTOR,
    ) in by_name
    assert ("src.service.Service.run", SymbolKind.METHOD) in by_name
    assert ("src.service.createService", SymbolKind.FUNCTION) in by_name
    dashboard = by_name[("src.service.Dashboard", SymbolKind.FUNCTION)]
    assert dashboard.exported is True
    assert dashboard.attributes["react_component_candidate"] is True
    assert ("src.service.MAX_RETRIES", SymbolKind.CONSTANT) in by_name
    assert ("src.service.factory", SymbolKind.FUNCTION) in by_name
    assert result.partial is False


def test_indexes_javascript_without_running_module_code() -> None:
    result = TypeScriptStaticParser().parse(
        _request(
            """
throw new Error("must not execute");
export function safe(value) { return value; }
const helper = (value) => value;
""".lstrip(),
            path="lib/safe.js",
            language=SourceLanguage.JAVASCRIPT,
        )
    )

    assert any(item.name == "safe" for item in result.definitions)
    assert any(item.name == "helper" for item in result.definitions)


def test_typescript_parser_returns_partial_results_for_broken_source() -> None:
    result = TypeScriptStaticParser().parse(
        _request(
            """
export function complete() { return true; }
export class Incomplete {
""".lstrip()
        )
    )

    assert result.partial is True
    assert any(item.name == "complete" for item in result.definitions)
    assert any(
        item.code == "parser.typescript_syntax_error"
        for item in result.diagnostics
    )


def test_typescript_parser_honors_cancellation_and_token_limits() -> None:
    request = _request(
        "\n".join(f"const value{index} = {index};" for index in range(50))
    )
    cancellation = CancellationToken()
    cancellation.request("test")
    cancelled = TypeScriptStaticParser().parse(
        request,
        cancellation=cancellation,
    )
    limited = TypeScriptStaticParser().parse(
        request,
        limits=ParserLimits(
            maximum_syntax_nodes=8,
            cancellation_check_interval=1,
        ),
    )

    assert cancelled.cancelled is True
    assert limited.partial is True
    assert any(
        item.code == "parser.typescript_token_limit"
        for item in limited.diagnostics
    )
