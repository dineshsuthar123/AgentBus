from __future__ import annotations

import hashlib
import threading
from pathlib import Path
from types import SimpleNamespace
from zipfile import ZipFile

import pytest

import agentbus.intelligence.search as search_module
import agentbus.intelligence.traversal as traversal_module
import agentbus.product.support as support_module
import agentbus.trace.archive as trace_archive_module
from agentbus.config import AgentBusConfig
from agentbus.intelligence import (
    DependencyEdge,
    DependencyGraph,
    DependencyKind,
    QueryLimitError,
    RepositoryLexicalIndex,
    SearchQuery,
    SourceFile,
    SourceLanguage,
    TraversalLimits,
    edge_id,
    file_id,
    repository_identity,
)
from agentbus.intelligence.parsers import base as parser_base
from agentbus.mcp import McpStdioTransport
from agentbus.mcp.errors import McpOutputLimitExceeded
from agentbus.product.support import create_support_bundle
from agentbus.sandbox import BoundedProcessOutput
from agentbus.tools.protocol import ToolOutputStream, ToolResourceBudget
from agentbus.trace import (
    ContentAddressedStore,
    TraceArchiveError,
    TraceArchiveImporter,
)


def test_tool_output_retains_only_budgeted_bytes_from_large_chunk() -> None:
    capture = BoundedProcessOutput(
        ToolResourceBudget(
            stdout_bytes=1_024,
            stderr_bytes=0,
            combined_output_bytes=1_024,
        )
    )

    capture.consume(ToolOutputStream.STDOUT, b"x" * (256 * 1_024))
    result = capture.finalize()

    assert result.stdout_bytes == 256 * 1_024
    assert result.retained_stdout_bytes == 1_024
    assert len(result.stdout) == 1_024
    assert result.stdout_truncated is True


def test_parser_byte_limit_short_circuits_oversized_character_input() -> None:
    class EncodeMustNotRun(str):
        def encode(self, *args: object, **kwargs: object) -> bytes:
            raise AssertionError("oversized parser input was encoded in full")

    source = EncodeMustNotRun("x" * 4_097)

    assert parser_base.source_exceeds_byte_limit(source, 4_096) is True


def test_index_query_retains_only_requested_rank_window(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = repository_identity("memory/index-results")
    files = tuple(
        SourceFile(
            file_id=file_id(
                repository.repository_id,
                f"generated/match-{index:04}.py",
            ),
            repository_id=repository.repository_id,
            relative_path=f"generated/match-{index:04}.py",
            language=SourceLanguage.PYTHON,
            content_hash=hashlib.sha256(str(index).encode()).hexdigest(),
            size_bytes=1,
            parser_name="fixture",
            parser_version="1.0.0",
        )
        for index in range(512)
    )
    index = RepositoryLexicalIndex((), files, (), ())
    tracker = {"live": 0, "peak": 0}
    real_score = search_module._score

    class TrackedScore:
        def __init__(self, item: object) -> None:
            self.document = item.document
            self.score = item.score
            self.components = item.components
            self.matched_terms = item.matched_terms
            tracker["live"] += 1
            tracker["peak"] = max(tracker["peak"], tracker["live"])

        def __del__(self) -> None:
            tracker["live"] -= 1

    def tracked_score(document: object, terms: object) -> object:
        scored = real_score(document, terms)
        return TrackedScore(scored) if scored is not None else None

    monkeypatch.setattr(search_module, "_score", tracked_score)

    results = index.search(SearchQuery(text="match", offset=3, limit=4))

    assert len(results) == 4
    assert [result.rank for result in results] == [4, 5, 6, 7]
    assert tracker["peak"] <= 16


def test_graph_constructor_stops_after_hard_record_bound(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        traversal_module,
        "_MAX_GRAPH_RECORDS",
        32,
        raising=False,
    )
    edge = DependencyEdge(
        edge_id=edge_id("source", "target", DependencyKind.CALLS.value),
        kind=DependencyKind.CALLS,
        source_id="source",
        target_id="target",
        confidence=1.0,
        parser_name="fixture",
        parser_version="1.0.0",
        explanation="Bounded graph fixture.",
        resolved=True,
    )
    consumed = 0

    def generated_edges():
        nonlocal consumed
        for index in range(34):
            consumed += 1
            if index == 33:
                raise AssertionError("graph consumed beyond its hard record bound")
            yield edge

    with pytest.raises(QueryLimitError, match="hard edge storage limit"):
        DependencyGraph(generated_edges())

    assert consumed == 33


def test_graph_traversal_stops_after_start_node_bound() -> None:
    graph = DependencyGraph((), limits=TraversalLimits(maximum_nodes=3))
    consumed = 0

    def generated_starts():
        nonlocal consumed
        for index in range(5):
            consumed += 1
            if index == 4:
                raise AssertionError("traversal consumed beyond its start-node bound")
            yield f"node-{index}"

    with pytest.raises(QueryLimitError, match="start set exceeds the node limit"):
        graph.transitive_dependencies(generated_starts())

    assert consumed == 4


def test_trace_archive_rejects_entry_count_before_zip_materialization(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    archive_path = tmp_path / "too-many.agentbus-trace"
    with ZipFile(archive_path, mode="w") as archive:
        for index in range(4):
            archive.writestr(f"entry-{index}.json", b"{}")

    def fail_zip_open(*args: object, **kwargs: object) -> None:
        raise AssertionError("ZipFile parsed an already oversized central directory")

    monkeypatch.setattr(trace_archive_module, "ZipFile", fail_zip_open)
    importer = TraceArchiveImporter(
        ContentAddressedStore(tmp_path / "objects"),
        max_entries=3,
    )

    with pytest.raises(TraceArchiveError, match="entry bound"):
        importer.inspect(archive_path)


def test_support_bundle_bounds_uncompressed_content_before_archive_write(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    config = AgentBusConfig(
        workspace_dir=str(workspace),
        state_dir=str(tmp_path / "state"),
        state_db="state.db",
        runs_dir=str(tmp_path / "runs"),
        provider_name="deterministic",
    )
    output = tmp_path / "support.zip"

    def fail_archive_write(*args: object, **kwargs: object) -> None:
        raise AssertionError("oversized support content reached ZIP creation")

    monkeypatch.setattr(support_module, "_MAX_BUNDLE_BYTES", 512)
    monkeypatch.setattr(support_module, "_write_bundle", fail_archive_write)

    with pytest.raises(RuntimeError, match="uncompressed content"):
        create_support_bundle(
            config,
            output=output,
            registry_path=tmp_path / "registry.json",
        )

    assert output.exists() is False


def test_mcp_output_reader_caps_line_before_json_decode() -> None:
    transport = object.__new__(McpStdioTransport)
    transport.config = SimpleNamespace(
        maximum_server_output_bytes=64,
        maximum_tool_output_bytes=32,
    )
    transport.shutdown_grace_seconds = 0.01
    transport._closing = threading.Event()
    transport._stderr_drained = threading.Event()
    failures: list[BaseException] = []
    decoded: list[bytes] = []
    requested_sizes: list[int] = []

    class OversizedLine:
        def readline(self, maximum: int) -> bytes:
            requested_sizes.append(maximum)
            return b"x" * maximum

    transport._account_output = lambda size: None
    transport._decode_and_offer = decoded.append
    transport._fail = failures.append

    transport._read_stdout(OversizedLine())

    assert requested_sizes == [34]
    assert decoded == []
    assert len(failures) == 1
    assert isinstance(failures[0], McpOutputLimitExceeded)
