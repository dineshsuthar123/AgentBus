from __future__ import annotations

import string
import tempfile
from pathlib import Path

from hypothesis import given, settings, strategies as st

from agentbus.execution.models import RunRecord
from agentbus.execution.state_store import StateStore
from agentbus.replay import CheckpointKind, CheckpointManager, ReplayCheckpointState
from agentbus.trace import (
    ContentAddressedStore,
    RuntimeTrace,
    TraceArchiveExporter,
    TraceArchiveImporter,
    TraceRecorder,
    TraceSpanType,
    TraceStatus,
)
from agentbus.trace.sealing import seal_run_provenance


PROPERTY_SETTINGS = settings(
    max_examples=50,
    deadline=None,
    derandomize=True,
    database=None,
)
ARCHIVE_SETTINGS = settings(
    max_examples=10,
    deadline=None,
    derandomize=True,
    database=None,
)
_SAFE_TEXT = st.text(
    string.ascii_letters + string.digits + " .,_-",
    min_size=0,
    max_size=512,
)
_TASK_ID = st.text(
    string.ascii_lowercase + string.digits + "-",
    min_size=1,
    max_size=24,
).map(lambda value: "task-" + value.strip("-") or "task-safe")


def _temporary_root(prefix: str):
    base = (Path.cwd() / ".tmp").resolve()
    base.mkdir(exist_ok=True)
    return tempfile.TemporaryDirectory(prefix=prefix, dir=base)


@PROPERTY_SETTINGS
@given(payload=_SAFE_TEXT)
def test_content_addressed_put_is_hash_stable_and_idempotent(payload: str) -> None:
    with _temporary_root("property-trace-store-") as temporary:
        store = ContentAddressedStore(Path(temporary) / "objects")

        first = store.put_text(payload, producing_span_id="span-first")
        repeated = store.put_text(payload, producing_span_id="span-first")
        merged = store.put_text(payload, producing_span_id="span-second")

        assert repeated.sha256 == first.sha256 == merged.sha256
        assert store.get(first.sha256).data.decode("utf-8") == payload
        assert len(store.list_metadata()) == 1
        assert set(merged.producing_span_ids) == {"span-first", "span-second"}
        assert store.verify_all()[0].sha256 == first.sha256


def _sealed_trace(root: Path, payload: str):
    workspace = root / "workspace"
    workspace.mkdir()
    state_store = StateStore(root / "state.db")
    state_store.create_run(
        RunRecord(
            run_id="run-property",
            original_task="Property archive",
            model="deterministic",
            workspace=str(workspace),
            graph_data={"version": 1, "tasks": []},
        )
    )
    runtime = RuntimeTrace.open(
        state_store,
        "run-property",
        object_root=root / "source-objects",
        workspace=workspace,
    )
    span = runtime.start_span(
        TraceSpanType.PROVIDER_RESPONSE,
        "property response",
        attributes={
            "provider": "deterministic",
            "model": "fixture-v1",
            "role": "coder",
        },
    )
    metadata = runtime.object_store.put_json(
        {"result": payload},
        producing_span_id=span.span_id,
        media_type="application/json",
    )
    output = runtime.object_store.reference_output(
        metadata,
        reference_id="property-result",
        name="validation.result",
    )
    runtime.finish_span(span, output_references=[output])
    trace = runtime.finish(status=TraceStatus.SUCCEEDED)
    provenance = seal_run_provenance(
        trace,
        state_store=state_store,
        object_store=runtime.object_store,
        configuration={"provider": "deterministic"},
        task_graph={"version": 1, "tasks": []},
        final_repository_tree_sha256="a" * 64,
    )
    return runtime.object_store, trace, provenance, metadata


@ARCHIVE_SETTINGS
@given(payload=_SAFE_TEXT)
def test_trace_archive_round_trip_preserves_integrity_and_is_idempotent(
    payload: str,
) -> None:
    with _temporary_root("property-trace-archive-") as temporary:
        root = Path(temporary)
        source_store, trace, provenance, metadata = _sealed_trace(root, payload)
        first_path = root / "first.agentbus-trace"
        second_path = root / "second.agentbus-trace"
        exporter = TraceArchiveExporter(source_store)

        first_manifest = exporter.export(
            trace,
            provenance,
            first_path,
            assertions={"case": payload},
            include_source_content=True,
        )
        second_manifest = exporter.export(
            trace,
            provenance,
            second_path,
            assertions={"case": payload},
            include_source_content=True,
        )

        destination = ContentAddressedStore(root / "imported-objects")
        importer = TraceArchiveImporter(destination)
        first_import = importer.import_archive(
            first_path,
            allow_source_content=True,
        )
        second_import = importer.import_archive(
            first_path,
            allow_source_content=True,
        )

        assert first_path.read_bytes() == second_path.read_bytes()
        assert second_manifest == first_manifest
        assert first_import.manifest.archive_root == first_manifest.archive_root
        assert first_import.manifest.provenance_root == provenance.integrity_root
        assert first_import.trace == trace
        assert first_import.provenance == provenance
        assert first_import.assertions == {"case": payload}
        assert first_import.available_object_hashes == [metadata.sha256]
        assert second_import.available_object_hashes == [metadata.sha256]
        assert len(destination.list_metadata()) == 1


@st.composite
def _checkpoint_tasks(draw):
    completed = draw(st.lists(_TASK_ID, unique=True, max_size=16))
    required = draw(
        st.lists(
            st.sampled_from(completed),
            unique=True,
            max_size=len(completed),
        )
        if completed
        else st.just([])
    )
    return completed, required


@settings(
    max_examples=25,
    deadline=None,
    derandomize=True,
    database=None,
)
@given(tasks=_checkpoint_tasks())
def test_checkpoint_round_trip_preserves_valid_causal_ancestry(
    tasks: tuple[list[str], list[str]],
) -> None:
    completed, required = tasks
    with _temporary_root("property-checkpoint-") as temporary:
        store = ContentAddressedStore(Path(temporary) / "objects")
        manager = CheckpointManager(store)
        recorder = TraceRecorder("run-property")
        recorder.start_trace()
        parent = manager.capture(
            recorder,
            kind=CheckpointKind.GRAPH_PERSISTED,
            label="graph",
        )
        child = manager.capture(
            recorder,
            kind=CheckpointKind.TASK_COMPLETED,
            label="tasks",
            parent_checkpoint_id=parent.checkpoint_id,
            completed_task_ids=completed,
            required_task_ids=required,
        )

        ancestry = manager.validate_ancestry(
            recorder.snapshot(),
            child.checkpoint_id,
        )
        restored = ReplayCheckpointState.model_validate_json(
            ancestry[-1].model_dump_json()
        )

        assert [item.checkpoint_id for item in ancestry] == [
            parent.checkpoint_id,
            child.checkpoint_id,
        ]
        assert restored == ancestry[-1]
        assert restored.completed_task_ids == completed
        assert restored.required_task_ids == required
