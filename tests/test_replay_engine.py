import hashlib
from pathlib import Path

import pytest
from pydantic import BaseModel, ConfigDict

from agentbus.models.types import ModelResult, ModelRole
from agentbus.replay import (
    CheckpointKind,
    CheckpointManager,
    ReplayEngine,
    ReplayRequest,
    ReplaySessionStatus,
    ToolReplayStrategy,
    capture_model_envelope,
    ReplayIsolationManager,
)
from agentbus.trace import (
    ContentAddressedStore,
    IndexSnapshotTraceEvidence,
    IntelligenceDriftCategory,
    REPOSITORY_INTELLIGENCE_COMPONENT,
    REPOSITORY_INTELLIGENCE_EVIDENCE_MEDIA_TYPE,
    ReplayMode,
    RepositoryIntelligenceTraceEvidence,
    Trace,
    TraceInput,
    TraceOutput,
    TraceSpan,
    TraceSpanType,
    TraceStatus,
    TraceRecorder,
)
from agentbus.trace.redaction import canonical_json_bytes
from agentbus.execution.models import RunRecord
from agentbus.execution.state_store import StateStore


class ParsedOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    answer: int


def _fixture(tmp_path: Path, *, include_external=False):
    store = ContentAddressedStore(tmp_path / "objects")
    result = ModelResult(
        value={"answer": 42},
        provider="deterministic",
        model="fixture",
        role=ModelRole.CODER,
    )
    provider_output = capture_model_envelope(
        store,
        result,
        prompt="prompt",
        producing_span_id="provider",
        reference_id="request-1",
    )
    parse_input = TraceInput(
        **provider_output.model_dump(exclude={"replayable"}),
        required_for_replay=True,
    )
    tool_metadata = store.put_json(
        {"files": ["safe.py"]},
        producing_span_id="tool",
    )
    tool_output = store.reference_output(
        tool_metadata,
        reference_id="tool-result",
        name="tool result",
    )
    spans = [
        _span("root", TraceSpanType.RUN, 1, parent=None, outputs=[tool_output]),
        _span(
            "provider",
            TraceSpanType.PROVIDER_RESPONSE,
            2,
            outputs=[provider_output],
            attributes={"provider": "deterministic"},
        ),
        _span(
            "parse",
            TraceSpanType.MODEL_PARSE,
            3,
            inputs=[parse_input],
        ),
        _span(
            "policy",
            TraceSpanType.TOOL_POLICY,
            4,
            attributes={"result": {"outcome": "allow"}},
        ),
        _span(
            "tool",
            TraceSpanType.TOOL_INVOCATION,
            5,
            outputs=[tool_output],
            attributes={
                "tool_effect": "pure_read",
                "replay_strategy": "reuse_captured",
            },
        ),
        _span(
            "verifier",
            TraceSpanType.VERIFIER,
            6,
            attributes={"result": {"passed": True}},
        ),
    ]
    if include_external:
        spans.append(
            _span(
                "external",
                TraceSpanType.TOOL_INVOCATION,
                7,
                attributes={"tool_effect": "network"},
            )
        )
    trace = Trace(
        trace_id="trace-1",
        run_id="run-1",
        root_span_id="root",
        status=TraceStatus.SUCCEEDED,
        created_at=spans[0].started_at,
        completed_at=spans[0].ended_at,
        spans=spans,
    )
    return store, trace


def _span(
    span_id,
    span_type,
    sequence,
    *,
    parent="root",
    inputs=(),
    outputs=(),
    attributes=None,
):
    from datetime import datetime, timezone

    now = datetime(2026, 1, 2, tzinfo=timezone.utc)
    return TraceSpan(
        trace_id="trace-1",
        span_id=span_id,
        parent_span_id=parent,
        run_id="run-1",
        span_type=span_type,
        name=span_id,
        sequence=sequence,
        started_at=now,
        ended_at=now,
        status=TraceStatus.SUCCEEDED,
        input_references=list(inputs),
        output_references=list(outputs),
        attributes=attributes or {},
    )


def _request(mode=ReplayMode.OFFLINE):
    return ReplayRequest(
        replay_id="replay-1",
        source_trace_id="trace-1",
        source_run_id="run-1",
        mode=mode,
    )


def _sha(value) -> str:
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def _intelligence_evidence(
    *,
    context_hash: str = "3" * 64,
    snapshot_id: str = "snapshot_replay-captured",
    graph_hash: str = "4" * 64,
) -> RepositoryIntelligenceTraceEvidence:
    query = "update calculator"
    empty_hash = _sha([])
    return RepositoryIntelligenceTraceEvidence(
        search_query=query,
        search_query_sha256=_sha(query),
        context_hash=context_hash,
        snapshot=IndexSnapshotTraceEvidence(
            snapshot_id=snapshot_id,
            repository_id="repo_replay",
            workspace_id="workspace_replay",
            state="current",
            project_map_hash="5" * 64,
            graph_hash=graph_hash,
            source_fingerprint="6" * 64,
            parser_versions={"python": "1.0.0"},
        ),
        retrieval_scoring_sha256=empty_hash,
        dependency_result_hash=empty_hash,
        architecture_result_hash=empty_hash,
    )


def _with_intelligence_span(
    store: ContentAddressedStore,
    trace: Trace,
    evidence: RepositoryIntelligenceTraceEvidence,
) -> Trace:
    metadata = store.put_json(
        evidence.model_dump(mode="json"),
        producing_span_id="intelligence",
        media_type=REPOSITORY_INTELLIGENCE_EVIDENCE_MEDIA_TYPE,
    )
    output = store.reference_output(
        metadata,
        reference_id="intelligence-output",
        name="repository intelligence evidence",
    )
    sequence = max(item.sequence for item in trace.spans) + 1
    span = _span(
        "intelligence",
        TraceSpanType.CUSTOM,
        sequence,
        outputs=[output],
        attributes={"component": REPOSITORY_INTELLIGENCE_COMPONENT},
    )
    return Trace.model_validate(
        trace.model_copy(update={"spans": [*trace.spans, span]}).model_dump()
    )


def test_providerless_replay_runs_parsing_policy_and_verifier(tmp_path: Path) -> None:
    store, trace = _fixture(tmp_path)
    calls = {"policy": 0, "verifier": 0}

    def policy(span, inputs):
        calls["policy"] += 1
        return {"outcome": "allow"}

    def verifier(span, inputs):
        calls["verifier"] += 1
        return {"passed": True}

    result = ReplayEngine(
        store,
        schemas={"parse": ParsedOutput},
        policy_evaluator=policy,
        verifier=verifier,
    ).replay(trace, _request())

    assert result.session.status == ReplaySessionStatus.SUCCEEDED
    assert result.session.provider_calls == 0
    assert result.session.network_calls == 0
    assert calls == {"policy": 1, "verifier": 1}
    assert result.verifier_result == {"passed": True}
    assert "provider" in result.session.substitutions


def test_replay_reuses_captured_repository_intelligence_without_provider(
    tmp_path: Path,
) -> None:
    store, trace = _fixture(tmp_path)
    trace = _with_intelligence_span(store, trace, _intelligence_evidence())

    result = ReplayEngine(store).replay(trace, _request())

    assert result.session.status == ReplaySessionStatus.SUCCEEDED
    assert result.repository_intelligence.captured_snapshot_reused is True
    assert result.repository_intelligence.compared_current is False
    assert result.repository_intelligence.findings == ()
    assert result.session.intelligence_drift == []
    assert result.session.policy_drift == []
    assert result.session.provider_calls == 0
    assert result.session.network_calls == 0
    replayed = next(
        item
        for item in result.session.span_results
        if item.span_id == "intelligence"
    )
    assert replayed.action.value == "reused"


def test_replay_classifies_current_index_and_graph_drift_providerlessly(
    tmp_path: Path,
) -> None:
    store, trace = _fixture(tmp_path)
    captured = _intelligence_evidence()
    current = _intelligence_evidence(
        context_hash="7" * 64,
        snapshot_id="snapshot_replay-current",
        graph_hash="8" * 64,
    )
    trace = _with_intelligence_span(store, trace, captured)

    result = ReplayEngine(
        store,
        repository_intelligence_resolver=lambda evidence: current,
    ).replay(trace, _request())

    categories = {
        item.category for item in result.repository_intelligence.findings
    }
    assert result.session.status == ReplaySessionStatus.SUCCEEDED
    assert result.repository_intelligence.compared_current is True
    assert result.repository_intelligence.index_drift is True
    assert IntelligenceDriftCategory.INDEX_SNAPSHOT in categories
    assert IntelligenceDriftCategory.GRAPH in categories
    assert IntelligenceDriftCategory.INDEX_SNAPSHOT in (
        result.session.intelligence_drift
    )
    assert result.session.policy_drift == []
    assert result.session.provider_calls == 0
    assert result.session.network_calls == 0


def test_replay_uses_content_addressed_component_outputs(tmp_path: Path) -> None:
    store, trace = _fixture(tmp_path)
    policy_metadata = store.put_json(
        {"outcome": "allow"},
        producing_span_id="policy",
        media_type="application/vnd.agentbus.policy+json",
    )
    verifier_metadata = store.put_json(
        {"passed": True},
        producing_span_id="verifier",
        media_type="application/vnd.agentbus.verifier+json",
    )
    outputs = {
        "policy": store.reference_output(
            policy_metadata,
            reference_id="policy-output",
            name="policy decision",
        ),
        "verifier": store.reference_output(
            verifier_metadata,
            reference_id="verifier-output",
            name="verifier result",
        ),
    }
    trace = Trace.model_validate(
        trace.model_copy(
            update={
                "spans": [
                    span.model_copy(
                        update={
                            "attributes": {},
                            "output_references": [outputs[span.span_id]],
                        }
                    )
                    if span.span_id in outputs
                    else span
                    for span in trace.spans
                ]
            }
        ).model_dump()
    )

    result = ReplayEngine(store, schemas={"parse": ParsedOutput}).replay(
        trace,
        _request(),
    )

    assert result.session.status == ReplaySessionStatus.SUCCEEDED
    assert result.verifier_result == {"passed": True}
    assert result.session.provider_calls == 0
    assert result.session.network_calls == 0


def test_strict_replay_fails_closed_on_policy_drift(tmp_path: Path) -> None:
    store, trace = _fixture(tmp_path)
    result = ReplayEngine(
        store,
        schemas={"parse": ParsedOutput},
        policy_evaluator=lambda span, inputs: {"outcome": "deny"},
    ).replay(trace, _request(ReplayMode.STRICT))

    assert result.session.status == ReplaySessionStatus.INCOMPATIBLE
    assert "drifted" in result.session.failure_message


def test_offline_replay_rejects_uncaptured_external_side_effect(
    tmp_path: Path,
) -> None:
    store, trace = _fixture(tmp_path, include_external=True)

    result = ReplayEngine(store).replay(trace, _request())

    assert result.session.status == ReplaySessionStatus.INCOMPATIBLE
    assert result.session.provider_calls == 0
    assert result.session.network_calls == 0


def test_simulation_replays_external_state_without_calling_it(tmp_path: Path) -> None:
    store, trace = _fixture(tmp_path, include_external=True)
    request = _request(ReplayMode.SIMULATE)
    request.tool_strategies["external"] = ToolReplayStrategy.SIMULATE_MUTATION

    result = ReplayEngine(store).replay(trace, request)

    assert result.session.status == ReplaySessionStatus.SUCCEEDED
    external = next(
        item for item in result.session.span_results if item.span_id == "external"
    )
    assert external.action.value == "simulated"


def test_offline_replay_simulates_recorded_mutation_by_default(
    tmp_path: Path,
) -> None:
    store, trace = _fixture(tmp_path)
    trace = Trace.model_validate(
        trace.model_copy(
            update={
                "spans": [
                    span.model_copy(
                        update={
                            "attributes": {
                                "tool_effect": "filesystem_mutation",
                                "replay_strategy": "rerun_sandbox",
                            }
                        }
                    )
                    if span.span_id == "tool"
                    else span
                    for span in trace.spans
                ]
            }
        ).model_dump()
    )

    result = ReplayEngine(store).replay(trace, _request())

    assert result.session.status == ReplaySessionStatus.SUCCEEDED
    tool = next(
        item for item in result.session.span_results if item.span_id == "tool"
    )
    assert tool.action.value == "simulated"
    assert result.session.provider_calls == 0
    assert result.session.network_calls == 0


def test_replay_cancellation_is_cooperative_and_bounded(tmp_path: Path) -> None:
    store, trace = _fixture(tmp_path)

    result = ReplayEngine(store, cancelled=lambda: True).replay(trace, _request())

    assert result.session.status == ReplaySessionStatus.CANCELLED
    assert result.session.span_results == []


def test_partial_replay_allocates_isolated_state_and_validates_checkpoint(
    tmp_path: Path,
) -> None:
    store, base_trace = _fixture(tmp_path)
    recorder = TraceRecorder("run-1")
    recorder.start_trace()
    checkpoint_manager = CheckpointManager(store)
    checkpoint = checkpoint_manager.capture(
        recorder,
        kind=CheckpointKind.GRAPH_PERSISTED,
        label="graph persisted",
    )
    trace = base_trace.model_copy(
        update={"checkpoints": [checkpoint.model_copy(update={
            "trace_id": base_trace.trace_id,
            "run_id": base_trace.run_id,
            "span_id": "root",
            "sequence": 7,
        })]}
    )
    trace = Trace.model_validate(trace.model_dump())
    state = checkpoint_manager.load_state(checkpoint)
    rewritten_state = state.model_copy(
        update={
            "checkpoint_id": trace.checkpoints[0].checkpoint_id,
            "trace_id": trace.trace_id,
            "run_id": trace.run_id,
        }
    )
    reference = trace.checkpoints[0].state_references[0]
    metadata = store.put_json(
        rewritten_state.model_dump(mode="json"),
        producing_span_id="root",
        media_type=reference.media_type,
    )
    trace = Trace.model_validate(
        trace.model_copy(
            update={
                "checkpoints": [
                    trace.checkpoints[0].model_copy(
                        update={
                            "state_references": [
                                reference.model_copy(
                                    update={
                                        "sha256": metadata.sha256,
                                        "byte_length": metadata.byte_size,
                                    }
                                )
                            ]
                        }
                    )
                ]
            }
        ).model_dump()
    )
    source_state = StateStore(tmp_path / "source.db")
    source_state.create_run(
        RunRecord(
            run_id="run-1",
            original_task="Replay",
            model="deterministic",
            workspace="workspace",
        )
    )
    isolation = ReplayIsolationManager(tmp_path / "replays", source_state)
    request = _request()
    request.from_checkpoint_id = trace.checkpoints[0].checkpoint_id

    result = ReplayEngine(
        store,
        checkpoint_manager=checkpoint_manager,
        isolation_manager=isolation,
    ).replay(trace, request)

    assert result.session.status == ReplaySessionStatus.SUCCEEDED
    assert result.session.isolated_workspace == "[ISOLATED_REPLAY_WORKSPACE]"
    assert isolation.actual_database_path("replay-1").is_file()


@pytest.mark.parametrize(
    "corruption",
    (
        "absent_content",
        "altered_content",
        "mismatched_hash",
        "mismatched_metadata_identity",
    ),
)
def test_replay_preflights_all_references_before_callbacks(
    tmp_path: Path,
    corruption: str,
) -> None:
    store, trace = _fixture(tmp_path)
    marker = tmp_path / "source-workspace-marker.txt"
    marker.write_text("original\n", encoding="utf-8")
    late_metadata = store.put_json(
        {"passed": True},
        producing_span_id="verifier",
        media_type="application/vnd.agentbus.verifier+json",
    )
    late_reference = store.reference_output(
        late_metadata,
        reference_id="late-verifier-output",
        name="verifier result",
    )
    if corruption == "mismatched_hash":
        late_reference = late_reference.model_copy(update={"sha256": "f" * 64})
    elif corruption == "mismatched_metadata_identity":
        late_reference = late_reference.model_copy(
            update={"media_type": "application/json"}
        )
    trace = Trace.model_validate(
        trace.model_copy(
            update={
                "spans": [
                    span.model_copy(update={"output_references": [late_reference]})
                    if span.span_id == "verifier"
                    else span
                    for span in trace.spans
                ]
            }
        ).model_dump()
    )
    blob_path, _ = store._object_paths(late_metadata.sha256)
    if corruption == "absent_content":
        blob_path.unlink()
    elif corruption == "altered_content":
        blob_path.write_bytes(b"altered")

    calls = {"policy": 0, "verifier": 0}

    def policy(span, inputs):
        calls["policy"] += 1
        return {"outcome": "allow"}

    def verifier(span, inputs):
        calls["verifier"] += 1
        return {"passed": True}

    result = ReplayEngine(
        store,
        schemas={"parse": ParsedOutput},
        policy_evaluator=policy,
        verifier=verifier,
    ).replay(trace, _request())

    assert result.session.status == ReplaySessionStatus.AWAITING_INPUT
    assert result.session.failure_category == "ReplayInputUnavailableError"
    assert result.session.span_results == []
    assert result.session.provider_calls == 0
    assert result.session.network_calls == 0
    assert calls == {"policy": 0, "verifier": 0}
    assert marker.read_text(encoding="utf-8") == "original\n"
