from pathlib import Path

import pytest
from pydantic import BaseModel, ConfigDict

from agentbus.models.types import ModelResult, ModelRole
from agentbus.replay import (
    CapturedRoutedModel,
    ModelSubstitutionCatalog,
    ReplayIncompatibleError,
    ReplayInputUnavailableError,
    capture_model_envelope,
)
from agentbus.trace import (
    ContentAddressedStore,
    ReplayMode,
    Trace,
    TraceSpan,
    TraceSpanType,
    TraceStatus,
)


class ExpectedResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    answer: int


def _capture(tmp_path: Path, *, value=None, prompt="safe prompt"):
    store = ContentAddressedStore(tmp_path / "objects")
    result = ModelResult(
        value=value if value is not None else {"answer": 42},
        provider="deterministic",
        model="fixture-model",
        role=ModelRole.CODER,
    )
    output = capture_model_envelope(
        store,
        result,
        prompt=prompt,
        producing_span_id="provider-span",
        reference_id="request-1",
        schema_name="ExpectedResponse",
    )
    span = TraceSpan(
        trace_id="trace-1",
        span_id="root",
        run_id="run-1",
        span_type=TraceSpanType.RUN,
        name="run",
        sequence=1,
        status=TraceStatus.RUNNING,
        output_references=[output],
    )
    trace = Trace(
        trace_id="trace-1",
        run_id="run-1",
        root_span_id="root",
        spans=[span],
    )
    return store, trace


def test_captured_model_replays_without_provider_and_revalidates_schema(
    tmp_path: Path,
) -> None:
    store, trace = _capture(tmp_path)
    catalog = ModelSubstitutionCatalog.from_trace(trace, store)
    model = CapturedRoutedModel(catalog, ModelRole.CODER)

    value = model.generate_json("a different offline prompt", schema=ExpectedResponse)

    assert value == {"answer": 42}
    assert model.last_result.provider == "replay"
    assert model.last_result.provider_metadata["providerless"] is True
    assert catalog.remaining() == {}


def test_strict_replay_rejects_prompt_fingerprint_drift(tmp_path: Path) -> None:
    store, trace = _capture(tmp_path, prompt="original prompt")
    model = CapturedRoutedModel(
        ModelSubstitutionCatalog.from_trace(trace, store),
        ModelRole.CODER,
        mode=ReplayMode.STRICT,
    )

    with pytest.raises(ReplayIncompatibleError, match="fingerprint"):
        model.generate_json("changed prompt")


def test_capture_never_stores_raw_prompt_or_hidden_reasoning(
    tmp_path: Path,
) -> None:
    raw_prompt = "private task contents that must not be retained"
    store, trace = _capture(
        tmp_path,
        value={"answer": 42, "chain_of_thought": "hidden reasoning"},
        prompt=raw_prompt,
    )
    reference = trace.spans[0].output_references[0]
    payload = store.get(reference.sha256).data

    assert raw_prompt.encode() not in payload
    assert b"hidden reasoning" not in payload
    assert b"[HIDDEN_CONTENT]" in payload


def test_replay_rejects_schema_mismatch_and_capture_exhaustion(
    tmp_path: Path,
) -> None:
    store, trace = _capture(tmp_path, value={"unexpected": True})
    model = CapturedRoutedModel(
        ModelSubstitutionCatalog.from_trace(trace, store),
        ModelRole.CODER,
    )

    with pytest.raises(Exception):
        model.generate_json("prompt", schema=ExpectedResponse)

    with pytest.raises(ReplayInputUnavailableError, match="No captured"):
        model.generate_json("prompt")
