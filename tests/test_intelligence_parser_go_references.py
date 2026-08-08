from __future__ import annotations

from agentbus.intelligence import file_id, repository_identity
from agentbus.intelligence.models import DependencyKind, SourceLanguage
from agentbus.intelligence.parsers import GoStaticParser, ParseRequest


def _request(content: str) -> ParseRequest:
    repository = repository_identity("example/go-references")
    return ParseRequest.from_content(
        repository_id=repository.repository_id,
        file_id=file_id(repository.repository_id, "service/service.go"),
        relative_path="service/service.go",
        language=SourceLanguage.GO,
        content=content,
    )


def test_resolves_go_imports_receivers_calls_and_composite_literals() -> None:
    result = GoStaticParser().parse(
        _request(
            """
package service

import "fmt"
import (
    "context"
    json "github.com/example/json"
    _ "net/http/pprof"
    . "github.com/example/testing"
)

type Runner interface {
    Run(ctx context.Context) error
}

type Writer interface {
    Write(value []byte) (int, error)
}

type Service struct{}
type Widget struct{}

func (s *Service) Run(ctx context.Context) error {
    fmt.Println(ctx)
    helper()
    first := &Widget{}
    second := new(Widget)
    values := make([]Widget, 0)
    _, _, _ = first, second, values
    return nil
}

func (s Service) Write(value []byte, flush bool) (int, error) {
    return 0, nil
}

func helper() {}
""".lstrip()
        )
    )
    references = {
        (item.target, item.kind): item
        for item in result.references
    }

    assert ("fmt", DependencyKind.IMPORTS) in references
    assert ("context", DependencyKind.IMPORTS) in references
    assert (
        "github.com/example/json",
        DependencyKind.IMPORTS,
    ) in references
    assert references[
        ("github.com/example/json", DependencyKind.IMPORTS)
    ].attributes["alias"] == "json"
    assert references[
        ("net/http/pprof", DependencyKind.IMPORTS)
    ].attributes["side_effect_only"] is True
    assert references[
        ("github.com/example/testing", DependencyKind.IMPORTS)
    ].attributes["dot"] is True

    receiver_edges = {
        item.source_qualified_name: item
        for item in result.references
        if item.target == "service.Service"
        and item.kind == DependencyKind.REFERENCES
    }
    assert receiver_edges[
        "service.Service.Run"
    ].attributes["pointer"] is True
    assert receiver_edges[
        "service.Service.Write"
    ].attributes["pointer"] is False
    assert (
        "fmt.Println",
        DependencyKind.CALLS,
    ) in references
    assert ("helper", DependencyKind.CALLS) in references
    assert references[
        ("fmt.Println", DependencyKind.CALLS)
    ].source_qualified_name == "service.Service.Run"
    assert ("Widget", DependencyKind.INSTANTIATES) in references

    implementation = references[
        ("service.Runner", DependencyKind.IMPLEMENTS)
    ]
    assert implementation.source_qualified_name == "service.Service"
    assert implementation.confidence < 1.0
    assert implementation.attributes["heuristic"] is True
    assert (
        "service.Writer",
        DependencyKind.IMPLEMENTS,
    ) not in references


def test_go_receiver_and_interface_edges_are_explainably_heuristic() -> None:
    result = GoStaticParser().parse(
        _request(
            """
package service

type Closer interface {
    Close() error
}

type Resource struct{}

func (Resource) Close() error {
    return nil
}
""".lstrip()
        )
    )
    implementation = next(
        item
        for item in result.references
        if item.kind == DependencyKind.IMPLEMENTS
    )

    assert implementation.target == "service.Closer"
    assert "Heuristic Go interface satisfaction" in implementation.explanation
    assert implementation.attributes["pointer_receiver_required"] is False
