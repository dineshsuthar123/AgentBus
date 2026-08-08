from __future__ import annotations

from agentbus.execution.cancellation import CancellationToken
from agentbus.intelligence import file_id, repository_identity
from agentbus.intelligence.models import SourceLanguage, SymbolKind
from agentbus.intelligence.parsers import (
    GoStaticParser,
    ParseRequest,
    ParserLimits,
)


def _request(content: str, path: str = "service/service.go") -> ParseRequest:
    repository = repository_identity("example/go-parser")
    return ParseRequest.from_content(
        repository_id=repository.repository_id,
        file_id=file_id(repository.repository_id, path),
        relative_path=path,
        language=SourceLanguage.GO,
        content=content,
    )


def test_indexes_go_packages_types_functions_and_values() -> None:
    result = GoStaticParser().parse(
        _request(
            """
package service

const Version = "1.0"
const (
    Ready = 1
    Running
)

var defaultName string
var (
    Count int
    Enabled = true
    Primary, Secondary pkg.Value
)

type UserID string
type Alias = string

type Service[T comparable] struct {
    Name string
    X, Y int
    Client *http.Client
    First, Second pkg.Value
    Metadata struct {
        Value string
    }
}

type Reader interface {
    Read(p []byte) (n int, err error)
    Close() error
    io.Reader
}

type (
    Token string
    Payload struct {
        Value string
    }
)

func NewService(name string, count int) *Service {
    return &Service{Name: name}
}

func (s *Service[T]) DisplayName() string {
    return s.Name
}
""".lstrip()
        )
    )
    by_identity = {
        (item.qualified_name, item.kind): item
        for item in result.definitions
    }

    assert ("service", SymbolKind.PACKAGE) in by_identity
    assert ("service.Service", SymbolKind.CLASS) in by_identity
    assert ("service.Reader", SymbolKind.INTERFACE) in by_identity
    assert ("service.UserID", SymbolKind.TYPE_ALIAS) in by_identity
    alias = by_identity[("service.Alias", SymbolKind.TYPE_ALIAS)]
    assert alias.attributes["alias"] is True
    assert ("service.Token", SymbolKind.TYPE_ALIAS) in by_identity
    assert ("service.Payload", SymbolKind.CLASS) in by_identity
    assert ("service.Service.Name", SymbolKind.FIELD) in by_identity
    assert ("service.Service.X", SymbolKind.FIELD) in by_identity
    assert ("service.Service.Y", SymbolKind.FIELD) in by_identity
    assert ("service.Service.First", SymbolKind.FIELD) in by_identity
    assert ("service.Service.Second", SymbolKind.FIELD) in by_identity
    assert ("service.Payload.Value", SymbolKind.FIELD) in by_identity
    assert ("service.Reader.Read", SymbolKind.METHOD) in by_identity
    assert ("service.Reader.Close", SymbolKind.METHOD) in by_identity
    assert ("service.NewService", SymbolKind.FUNCTION) in by_identity
    method = by_identity[
        ("service.Service.DisplayName", SymbolKind.METHOD)
    ]
    assert method.parent_qualified_name == "service.Service"
    assert method.attributes["receiver"] == "Service"
    assert by_identity[
        ("service.NewService", SymbolKind.FUNCTION)
    ].signature == "(2 parameters)"
    assert ("service.Version", SymbolKind.CONSTANT) in by_identity
    assert ("service.Ready", SymbolKind.CONSTANT) in by_identity
    assert ("service.Running", SymbolKind.CONSTANT) in by_identity
    assert ("service.defaultName", SymbolKind.VARIABLE) in by_identity
    assert ("service.Count", SymbolKind.VARIABLE) in by_identity
    assert ("service.Enabled", SymbolKind.VARIABLE) in by_identity
    assert ("service.Primary", SymbolKind.VARIABLE) in by_identity
    assert ("service.Secondary", SymbolKind.VARIABLE) in by_identity
    assert ("service.pkg", SymbolKind.VARIABLE) not in by_identity
    assert result.partial is False


def test_go_parser_never_executes_package_initializers() -> None:
    result = GoStaticParser().parse(
        _request(
            """
package unsafe

var Exploit = exec.Command("must-not-run")

func init() {
    panic("must-not-run")
}
""".lstrip()
        )
    )

    assert any(item.name == "Exploit" for item in result.definitions)
    assert any(item.name == "init" for item in result.definitions)


def test_go_parser_recovers_symbols_from_incomplete_source() -> None:
    result = GoStaticParser().parse(
        _request(
            """
package example
type Complete struct {}
type Incomplete struct {
""".lstrip()
        )
    )

    assert result.partial is True
    assert any(item.name == "Complete" for item in result.definitions)
    assert any(
        item.code == "parser.go_syntax_error"
        for item in result.diagnostics
    )


def test_go_parser_honors_cancellation_and_token_limits() -> None:
    request = _request(
        "package example\n"
        + "\n".join(
            f"type Type{index} struct {{ Value int }}"
            for index in range(30)
        )
    )
    cancellation = CancellationToken()
    cancellation.request("test")
    cancelled = GoStaticParser().parse(
        request,
        cancellation=cancellation,
    )
    limited = GoStaticParser().parse(
        request,
        limits=ParserLimits(
            maximum_syntax_nodes=10,
            cancellation_check_interval=1,
        ),
    )

    assert cancelled.cancelled is True
    assert limited.partial is True
    assert any(
        item.code == "parser.go_token_limit"
        for item in limited.diagnostics
    )
