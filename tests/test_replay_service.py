import sys
from pathlib import Path

import pytest

from agentbus.config import AgentBusConfig
from agentbus.execution.models import RunRecord, TaskSpec
from agentbus.execution.state_store import StateStore
from agentbus.policy import ToolPolicyEngine
from agentbus.replay import (
    ForkRequest,
    ReplayIncompatibleError,
    ReplayRequest,
    ReplaySessionStatus,
    ReplaySpanAction,
    ToolReplayPlanner,
    load_tool_envelope,
)
from agentbus.replay.service import (
    TraceReplayService,
    _CachedToolReplayPlanner,
)
from agentbus.runtime.intelligence import PlannerIntelligenceContext
from agentbus.sandbox.platform import ExecutableCatalog
from agentbus.tools import builtin_tool_registry
from agentbus.tools.capabilities import derive_required_capabilities
from agentbus.tools.dispatcher import ToolDispatcher
from agentbus.tools.protocol import (
    ToolCapabilityName,
    ToolInvocation,
    ToolInvocationContext,
    ToolPolicyOutcome,
)
from agentbus.trace import (
    IntelligenceDriftCategory,
    REPOSITORY_INTELLIGENCE_COMPONENT,
    ReplayMode,
    RuntimeTrace,
    TraceSpanType,
    TraceStatus,
    build_repository_intelligence_trace_evidence,
)
from agentbus.trace.errors import TraceIntegrityError
from agentbus.trace.protocols import provenance_protocol_documents
from agentbus.trace.sealing import seal_run_provenance


def _service(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    config = AgentBusConfig(
        workspace_dir=str(workspace),
        state_db=str(tmp_path / "state.db"),
    )
    store = StateStore(config.state_database_path)
    return config, store, TraceReplayService(config, state_store=store)


def _record_run(
    config: AgentBusConfig,
    store: StateStore,
    run_id: str,
    *,
    intelligence_context: PlannerIntelligenceContext | None = None,
):
    store.create_run(
        RunRecord(
            run_id=run_id,
            original_task=f"Replay {run_id}",
            model="deterministic",
            workspace=str(config.workspace_path),
            graph_data={"version": 1, "tasks": []},
        )
    )
    runtime = RuntimeTrace.open(
        store,
        run_id,
        object_root=config.trace_store_path,
        workspace=config.workspace_path,
    )
    with runtime.scope(runtime.root_context):
        runtime.call(
            TraceSpanType.VERIFIER,
            "final verifier",
            lambda: {"passed": True, "exit_code": 0},
            capture="json",
        )
        if intelligence_context is not None:
            evidence = build_repository_intelligence_trace_evidence(
                f"Replay {run_id}",
                intelligence_context,
                private_roots=(config.workspace_path,),
            )
            span = runtime.start_span(
                TraceSpanType.CUSTOM,
                "repository intelligence",
                attributes={"component": REPOSITORY_INTELLIGENCE_COMPONENT},
            )
            query_input = runtime.capture_json_input(
                span,
                "repository.intelligence.query",
                {"search_query": evidence.search_query},
            )
            evidence_output = runtime.capture_json_output(
                span,
                "repository.intelligence.evidence",
                evidence.model_dump(mode="json"),
            )
            runtime.finish_span(
                span,
                input_references=[query_input],
                output_references=[evidence_output],
            )
        runtime.call(
            TraceSpanType.REVIEWER,
            "final reviewer",
            lambda: {"approved": True, "issues": []},
            capture="json",
        )
    trace = runtime.finish(status=TraceStatus.SUCCEEDED)
    assert trace is not None
    manifest = seal_run_provenance(
        trace,
        state_store=store,
        object_store=runtime.object_store,
        configuration={"provider": "deterministic"},
        task_graph={"version": 1, "tasks": []},
        final_repository_tree_sha256="c" * 64,
    )
    return trace, manifest


def _record_tool_run(
    config: AgentBusConfig,
    store: StateStore,
    run_id: str,
):
    task_id = "task-tool"
    store.create_run_with_tasks(
        RunRecord(
            run_id=run_id,
            original_task="Replay one managed mutation",
            model="deterministic",
            workspace=str(config.workspace_path),
            graph_data={"version": 1, "tasks": []},
        ),
        [
            TaskSpec(
                task_id=task_id,
                title="Create a replay fixture",
                description="Exercise traced tool policy replay.",
            )
        ],
    )
    runtime = RuntimeTrace.open(
        store,
        run_id,
        object_root=config.trace_store_path,
        workspace=config.workspace_path,
    )
    registry = builtin_tool_registry(
        workspace=config.workspace_path,
        executable_catalog=ExecutableCatalog({"python": sys.executable}),
    )
    dispatcher = ToolDispatcher(
        registry,
        store,
        runtime_trace=runtime,
    )
    descriptor = registry.descriptor("filesystem.create")
    provisional = ToolInvocation(
        invocation_id="tool-service-1",
        run_id=run_id,
        task_id=task_id,
        tool_name=descriptor.name,
        tool_version=descriptor.version,
        arguments={
            "path": "replay-created.txt",
            "content": "captured once\n",
        },
        requested_capabilities=descriptor.capabilities,
        context=ToolInvocationContext(
            workspace_identity=str(config.workspace_path),
            worktree_identity=str(config.workspace_path),
            caller_role="coder",
            workspace_trusted=True,
            provider_consented=True,
        ),
    )
    invocation = ToolInvocation.model_validate(
        provisional.model_dump(mode="python")
        | {
            "requested_capabilities": derive_required_capabilities(
                provisional,
                descriptor,
            )
        }
    )
    with runtime.scope(runtime.root_context):
        response = dispatcher.dispatch(invocation)
    assert response.result is not None
    trace = runtime.finish(status=TraceStatus.SUCCEEDED)
    assert trace is not None
    seal_run_provenance(
        trace,
        state_store=store,
        object_store=runtime.object_store,
        configuration={"provider": "deterministic"},
        task_graph={"version": 1, "tasks": []},
        final_repository_tree_sha256="d" * 64,
    )
    return trace, config.workspace_path / "replay-created.txt"


def test_service_verifies_replays_and_persists_terminal_session(
    tmp_path,
) -> None:
    config, store, service = _service(tmp_path)
    trace, manifest = _record_run(config, store, "run-service")
    request = ReplayRequest(
        replay_id="replay-service",
        source_trace_id=trace.trace_id,
        source_run_id=trace.run_id,
        mode=ReplayMode.OFFLINE,
    )

    verification = service.verify(trace.run_id)
    replayability = service.replayability(trace.trace_id)
    prepared, pending = service.queue_replay(trace.run_id, request)
    result = service.replay(trace.run_id, prepared)

    assert verification.valid is True
    assert verification.provenance_root == manifest.integrity_root
    assert verification.object_count == 2
    assert verification.protocol_drift == []
    assert replayability.replayable_offline is True
    assert result.session.status == ReplaySessionStatus.SUCCEEDED
    assert result.session.created_at == pending.created_at
    assert result.session.provider_calls == 0
    assert result.session.network_calls == 0
    assert store.get_replay_result(request.replay_id) == result


def test_service_reuses_capture_when_current_local_index_is_unavailable(
    tmp_path,
) -> None:
    config, store, _ = _service(tmp_path)
    captured_context = PlannerIntelligenceContext()
    trace, _ = _record_run(
        config,
        store,
        "run-intelligence-replay",
        intelligence_context=captured_context,
    )

    class UnavailableLocalIntelligence:
        def __init__(self):
            self.queries = []

        def planner_context(self, user_task):
            self.queries.append(user_task)
            return None

    source = UnavailableLocalIntelligence()
    service = TraceReplayService(
        config,
        state_store=store,
        intelligence_source=source,
    )
    result = service.replay(
        trace.run_id,
        ReplayRequest(
            replay_id="replay-intelligence-service",
            source_trace_id=trace.trace_id,
            source_run_id=trace.run_id,
            mode=ReplayMode.OFFLINE,
        ),
    )

    assert result.session.status == ReplaySessionStatus.SUCCEEDED
    assert source.queries == ["Replay run-intelligence-replay"]
    assert result.repository_intelligence.captured_evidence_reused is True
    assert result.repository_intelligence.captured_snapshot_reused is False
    assert result.repository_intelligence.compared_current is True
    assert result.repository_intelligence.findings[0].category == (
        IntelligenceDriftCategory.CURRENT_INDEX_UNAVAILABLE
    )
    assert result.session.provider_calls == 0
    assert result.session.network_calls == 0


def test_service_replays_managed_tool_through_current_policy_once(
    tmp_path,
) -> None:
    config, store, _ = _service(tmp_path)
    trace, created = _record_tool_run(config, store, "run-tool-policy")
    created.unlink()
    policy_calls = []

    class CountingPolicy(ToolPolicyEngine):
        def evaluate(self, invocation, descriptor, *, approval=None):
            policy_calls.append((invocation, descriptor))
            return super().evaluate(
                invocation,
                descriptor,
                approval=approval,
            )

    service = TraceReplayService(
        config,
        state_store=store,
        tool_replay_planner=ToolReplayPlanner(CountingPolicy()),
    )
    result = service.replay(
        trace.run_id,
        ReplayRequest(
            replay_id="replay-tool-policy",
            source_trace_id=trace.trace_id,
            source_run_id=trace.run_id,
            mode=ReplayMode.OFFLINE,
        ),
    )

    assert result.session.status == ReplaySessionStatus.SUCCEEDED
    assert len(policy_calls) == 1
    assert policy_calls[0][0].context.provider_consented is True
    assert created.exists() is False
    source_by_id = {span.span_id: span for span in trace.spans}
    replay_by_id = {
        span_result.span_id: span_result
        for span_result in result.session.span_results
    }
    tool_span = next(
        span
        for span in trace.spans
        if span.span_type == TraceSpanType.TOOL_INVOCATION
    )
    policy_span = next(
        span
        for span in trace.spans
        if span.span_type == TraceSpanType.TOOL_POLICY
    )
    assert replay_by_id[tool_span.span_id].action == ReplaySpanAction.SIMULATED
    assert replay_by_id[policy_span.span_id].action == ReplaySpanAction.REPLAYED
    assert replay_by_id[policy_span.span_id].output_sha256 in {
        reference.sha256
        for reference in source_by_id[policy_span.span_id].output_references
    }
    assert result.session.policy_drift == []


def test_service_policy_cache_distinguishes_changed_envelopes(
    tmp_path,
) -> None:
    config, store, service = _service(tmp_path)
    trace, _ = _record_tool_run(config, store, "run-cache-envelope")
    tool_span = next(
        span
        for span in trace.spans
        if span.span_type == TraceSpanType.TOOL_INVOCATION
    )
    envelope = load_tool_envelope(
        service.object_store,
        tool_span.output_references[0].sha256,
    )
    calls = []

    class CountingPlanner:
        def assess(self, *args, **kwargs):
            calls.append(args[0])
            return ToolReplayPlanner().assess(*args, **kwargs)

    planner = _CachedToolReplayPlanner(CountingPlanner())
    descriptor = service.tool_descriptors[envelope.descriptor.name]

    planner.assess(
        envelope,
        descriptor,
        mode=ReplayMode.OFFLINE,
    )
    planner.assess(
        envelope.model_copy(update={"result": None}),
        descriptor,
        mode=ReplayMode.OFFLINE,
    )

    assert len(calls) == 2


def test_service_partial_replay_uses_owned_isolation_and_persists_it(
    tmp_path,
) -> None:
    config, store, service = _service(tmp_path)
    trace, _ = _record_run(config, store, "run-partial")
    verifier = next(
        span
        for span in trace.spans
        if span.span_type == TraceSpanType.VERIFIER
    )
    request = ReplayRequest(
        replay_id="replay-partial",
        source_trace_id=trace.trace_id,
        source_run_id=trace.run_id,
        mode=ReplayMode.OFFLINE,
        from_span_id=verifier.span_id,
    )

    prepared, pending = service.queue_replay(trace.run_id, request)
    result = service.replay(trace.run_id, prepared)

    assert result.session.status == ReplaySessionStatus.SUCCEEDED
    assert result.session.created_at == pending.created_at
    assert request.isolated_workspace is None
    assert prepared.isolated_workspace is not None
    assert (
        result.session.isolated_workspace
        == "[ISOLATED_REPLAY_WORKSPACE]"
    )
    persisted = store.get_replay_session(request.replay_id)
    assert persisted == result.session
    replay_root = (
        config.workspace_path.parent
        / ".agentbus-replays"
        / config.workspace_path.name
        / request.replay_id
    )
    assert (replay_root / "state.db").is_file()
    assert config.workspace_path not in replay_root.parents


def test_service_persists_unexpected_replay_failure(
    tmp_path,
    monkeypatch,
) -> None:
    config, store, service = _service(tmp_path)
    trace, _ = _record_run(config, store, "run-unexpected")
    request = ReplayRequest(
        replay_id="replay-unexpected",
        source_trace_id=trace.trace_id,
        source_run_id=trace.run_id,
        mode=ReplayMode.OFFLINE,
    )

    class BrokenEngine:
        def replay(self, *_args, **_kwargs):
            raise RuntimeError("unexpected private failure")

    monkeypatch.setattr(service, "_engine", lambda *_args: BrokenEngine())

    with pytest.raises(RuntimeError, match="unexpected private failure"):
        service.replay(trace.run_id, request)

    persisted = store.get_replay_session(request.replay_id)
    assert persisted.status == ReplaySessionStatus.FAILED
    assert persisted.failure_category == "RuntimeError"
    assert persisted.completed_at is not None


def test_service_exports_imports_and_replays_archive_without_execution_on_import(
    tmp_path,
) -> None:
    config, store, service = _service(tmp_path)
    trace, _ = _record_run(config, store, "run-archive")
    archive = tmp_path / "run.agentbus-trace"

    exported = service.export_trace(trace.run_id, archive)
    imported = service.import_archive(archive)

    assert exported.trace_id == trace.trace_id
    assert imported.trace == trace
    assert imported.objects_imported is True
    assert store.list_replay_sessions() == []

    replayed = service.replay_archive(archive)
    assert replayed.replay.session.status == ReplaySessionStatus.SUCCEEDED
    assert replayed.replay.session.provider_calls == 0
    assert replayed.replay.session.network_calls == 0

    isolated_root = tmp_path / "isolated"
    isolated_workspace = isolated_root / "workspace"
    isolated_workspace.mkdir(parents=True)
    isolated_config = AgentBusConfig(
        workspace_dir=str(isolated_workspace),
        state_db=str(isolated_root / "state.db"),
    )
    isolated_store = StateStore(isolated_config.state_database_path)
    isolated_service = TraceReplayService(
        isolated_config,
        state_store=isolated_store,
    )

    isolated_import = isolated_service.import_archive(archive)
    imported_run = isolated_store.get_run(isolated_import.trace.run_id)
    persisted_trace = isolated_store.get_run_trace(isolated_import.trace.run_id)
    persisted_provenance = isolated_store.get_run_provenance_manifest(
        isolated_import.trace.run_id
    )

    assert imported_run.workspace == "[IMPORTED_TRACE_WORKSPACE]"
    assert imported_run.metadata["imported_trace"] is True
    assert persisted_trace == isolated_import.trace
    assert persisted_provenance == isolated_import.provenance
    assert isolated_store.list_replay_sessions() == []

    imported_request = ReplayRequest(
        replay_id="replay-imported",
        source_trace_id=isolated_import.trace.trace_id,
        source_run_id=isolated_import.trace.run_id,
        mode=ReplayMode.OFFLINE,
    )
    imported_replay = isolated_service.replay(
        isolated_import.trace.run_id,
        imported_request,
    )
    assert imported_replay.session.status == ReplaySessionStatus.SUCCEEDED
    assert imported_replay.session.provider_calls == 0
    assert imported_replay.session.network_calls == 0


def test_service_rejects_current_protocol_drift_before_archive_import(
    tmp_path,
    monkeypatch,
) -> None:
    config, store, service = _service(tmp_path)
    trace, _ = _record_run(config, store, "run-stale-protocol")
    archive = tmp_path / "stale-protocol.agentbus-trace"
    service.export_trace(trace.run_id, archive)

    target_root = tmp_path / "target"
    target_workspace = target_root / "workspace"
    target_workspace.mkdir(parents=True)
    marker = target_workspace / "marker.txt"
    marker.write_text("original\n", encoding="utf-8")
    target_config = AgentBusConfig(
        workspace_dir=str(target_workspace),
        state_db=str(target_root / "state.db"),
    )
    target_store = StateStore(target_config.state_database_path)
    target_service = TraceReplayService(
        target_config,
        state_store=target_store,
    )
    current_protocols = provenance_protocol_documents()
    changed_protocols = dict(current_protocols)
    changed_protocols[sorted(changed_protocols)[0]] = {"schema_version": 999}
    monkeypatch.setattr(
        "agentbus.replay.service.provenance_protocol_documents",
        lambda: changed_protocols,
    )

    with pytest.raises(ReplayIncompatibleError, match="protocol"):
        target_service.replay_archive(archive)

    assert target_service.object_store.list_metadata() == []
    assert target_store.list_replay_sessions() == []
    assert marker.read_text(encoding="utf-8") == "original\n"


def test_service_rejects_archive_replacement_before_replay(
    tmp_path,
    monkeypatch,
) -> None:
    config, store, service = _service(tmp_path)
    first, _ = _record_run(config, store, "run-archive-first")
    second, _ = _record_run(config, store, "run-archive-second")
    first_archive = tmp_path / "first.agentbus-trace"
    second_archive = tmp_path / "second.agentbus-trace"
    service.export_trace(first.run_id, first_archive)
    service.export_trace(second.run_id, second_archive)

    target_root = tmp_path / "replacement-target"
    target_workspace = target_root / "workspace"
    target_workspace.mkdir(parents=True)
    marker = target_workspace / "marker.txt"
    marker.write_text("original\n", encoding="utf-8")
    target_config = AgentBusConfig(
        workspace_dir=str(target_workspace),
        state_db=str(target_root / "state.db"),
    )
    target_store = StateStore(target_config.state_database_path)
    target_service = TraceReplayService(
        target_config,
        state_store=target_store,
    )
    import_archive = target_service.import_archive
    monkeypatch.setattr(
        target_service,
        "import_archive",
        lambda _source, **kwargs: import_archive(second_archive, **kwargs),
    )

    def unexpected_engine(*_args, **_kwargs):
        raise AssertionError("replacement archive reached replay execution")

    monkeypatch.setattr(target_service, "_engine", unexpected_engine)

    with pytest.raises(TraceIntegrityError, match="changed"):
        target_service.replay_archive(first_archive)

    assert target_store.list_replay_sessions() == []
    assert marker.read_text(encoding="utf-8") == "original\n"


def test_service_rejects_changed_policy_without_replaying_mutation(
    tmp_path,
) -> None:
    config, store, _ = _service(tmp_path)
    trace, created = _record_tool_run(config, store, "run-policy-drift")
    created.unlink()

    class DenyPolicy:
        def evaluate(self, invocation, descriptor):
            decision = ToolPolicyEngine().evaluate(invocation, descriptor)
            return decision.model_copy(
                update={
                    "outcome": ToolPolicyOutcome.DENY,
                    "rule_id": "deny.changed-policy",
                    "reason": "Current replay policy denies the invocation.",
                }
            )

    service = TraceReplayService(
        config,
        state_store=store,
        tool_replay_planner=ToolReplayPlanner(DenyPolicy()),
    )
    result = service.replay(
        trace.run_id,
        ReplayRequest(
            replay_id="replay-policy-drift",
            source_trace_id=trace.trace_id,
            source_run_id=trace.run_id,
            mode=ReplayMode.OFFLINE,
        ),
    )

    assert result.session.status == ReplaySessionStatus.INCOMPATIBLE
    assert result.session.provider_calls == 0
    assert result.session.network_calls == 0
    assert created.exists() is False


def test_service_rejects_expanded_capability_without_replaying_mutation(
    tmp_path,
) -> None:
    config, store, baseline = _service(tmp_path)
    trace, created = _record_tool_run(config, store, "run-capability-drift")
    created.unlink()
    descriptors = dict(baseline.tool_descriptors)
    current = descriptors["filesystem.create"]
    expanded_capability = current.capabilities[0].model_copy(
        update={"name": ToolCapabilityName.FILESYSTEM_DELETE}
    )
    descriptors[current.name] = current.model_copy(
        update={
            "version": current.version.model_copy(
                update={"major": current.version.major + 1}
            ),
            "capabilities": (*current.capabilities, expanded_capability),
        }
    )
    service = TraceReplayService(
        config,
        state_store=store,
        tool_descriptors=descriptors,
    )
    result = service.replay(
        trace.run_id,
        ReplayRequest(
            replay_id="replay-capability-drift",
            source_trace_id=trace.trace_id,
            source_run_id=trace.run_id,
            mode=ReplayMode.OFFLINE,
        ),
    )

    assert result.session.status == ReplaySessionStatus.INCOMPATIBLE
    assert result.session.provider_calls == 0
    assert result.session.network_calls == 0
    assert created.exists() is False


def test_service_comparison_is_idempotent_and_fork_stays_offline(
    tmp_path,
) -> None:
    config, store, service = _service(tmp_path)
    left, _ = _record_run(config, store, "run-left")
    right, _ = _record_run(config, store, "run-right")

    first = service.compare(left.run_id, right.trace_id)
    second = service.compare(left.trace_id, right.run_id)
    fork_request = ForkRequest(
        replay_id="replay-fork",
        source_trace_id=left.trace_id,
        source_run_id=left.run_id,
        changed_inputs={"task_text": "Changed offline task"},
    )
    pending = store.create_replay_session(
        ReplayRequest(
            replay_id=fork_request.replay_id,
            source_trace_id=fork_request.source_trace_id,
            source_run_id=fork_request.source_run_id,
            mode=fork_request.mode,
            fork=True,
            changed_inputs=fork_request.changed_inputs,
        )
    )
    fork = service.fork(left.run_id, fork_request)

    assert first == second
    assert store.get_trace_comparison(first.comparison_id) == first
    assert fork.replay.session.status == ReplaySessionStatus.SUCCEEDED
    assert fork.replay.session.created_at == pending.created_at
    assert fork.replay.session.provider_calls == 0
    assert fork.replay.session.network_calls == 0
    assert fork.fork_trace.trace_id != left.trace_id
    assert fork.replay.session.result_trace_id == fork.fork_trace.trace_id
    assert (
        fork.replay.session.comparison_id
        == fork.comparison.comparison_id
    )
    assert store.get_trace(fork.fork_trace.trace_id) == fork.fork_trace
    fork_run = store.get_run(fork.fork_trace.run_id)
    assert fork_run.metadata["forked_trace"] is True
    assert (
        store.get_provenance_manifest(fork.fork_trace.trace_id).integrity_root
        == fork.comparison.right_provenance_root
    )
    assert (
        store.get_trace_comparison(fork.comparison.comparison_id)
        == fork.comparison
    )
    assert store.get_replay_result("replay-fork") == fork.replay
