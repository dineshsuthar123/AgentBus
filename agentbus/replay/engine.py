from __future__ import annotations

import hashlib
from collections.abc import Callable, Mapping
from datetime import datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from agentbus.models.base import validate_json_schema
from agentbus.replay.classification import ReplayabilityClassifier
from agentbus.replay.checkpoints import (
    CheckpointManager,
    ReplayIsolationManager,
)
from agentbus.replay.errors import (
    ReplayCancelledError,
    ReplayError,
    ReplayIncompatibleError,
    ReplayInputUnavailableError,
    ReplayIsolationError,
)
from agentbus.replay.inputs import ReplayInputCatalog
from agentbus.replay.session import (
    ReplayRequest,
    ReplayResult,
    ReplaySession,
    ReplaySessionStatus,
    ReplaySpanAction,
    ReplaySpanResult,
    ToolReplayStrategy,
)
from agentbus.replay.substitutions import (
    MODEL_ENVELOPE_MEDIA_TYPE,
    CapturedModelEnvelope,
)
from agentbus.replay.tools import (
    TOOL_ENVELOPE_MEDIA_TYPE,
    ToolReplayPlanner,
    load_tool_envelope,
)
from agentbus.trace.models import ReplayMode, Trace, TraceSpan, TraceSpanType, utc_now
from agentbus.trace.intelligence import (
    REPOSITORY_INTELLIGENCE_COMPONENT,
    RepositoryIntelligenceReplayReport,
    RepositoryIntelligenceTraceEvidence,
    compare_repository_intelligence,
    reuse_captured_repository_intelligence,
    unavailable_current_repository_intelligence,
)
from agentbus.trace.provenance import ReplayabilityLevel
from agentbus.trace.redaction import canonical_json_bytes, sanitize_document
from agentbus.trace.storage import ContentAddressedStore
from agentbus.trace.version import TRACE_SCHEMA_VERSION

Schema = type[BaseModel] | dict[str, Any]
PolicyEvaluator = Callable[[TraceSpan, list[Any]], dict[str, Any]]
Verifier = Callable[[TraceSpan, list[Any]], dict[str, Any]]
ToolExecutor = Callable[[TraceSpan, list[Any], Path], dict[str, Any]]
RepositoryIntelligenceResolver = Callable[
    [RepositoryIntelligenceTraceEvidence],
    RepositoryIntelligenceTraceEvidence | None,
]


class ReplayEngine:
    """Providerless trace replay with explicit substitution and side-effect modes."""

    def __init__(
        self,
        store: ContentAddressedStore,
        *,
        schemas: Mapping[str, Schema] | None = None,
        policy_evaluator: PolicyEvaluator | None = None,
        verifier: Verifier | None = None,
        reviewer: Verifier | None = None,
        tool_executor: ToolExecutor | None = None,
        tool_replay_planner: ToolReplayPlanner | None = None,
        tool_descriptors: Mapping[str, Any] | None = None,
        checkpoint_manager: CheckpointManager | None = None,
        isolation_manager: ReplayIsolationManager | None = None,
        source_workspace: str | Path | None = None,
        repository_intelligence_resolver: RepositoryIntelligenceResolver
        | None = None,
        cancelled: Callable[[], bool] | None = None,
        clock: Callable = utc_now,
    ):
        self.store = store
        self.schemas = dict(schemas or {})
        self.policy_evaluator = policy_evaluator
        self.verifier = verifier
        self.reviewer = reviewer
        self.tool_executor = tool_executor
        self.tool_replay_planner = tool_replay_planner
        self.tool_descriptors = dict(tool_descriptors or {})
        self.checkpoint_manager = checkpoint_manager
        self.isolation_manager = isolation_manager
        self.source_workspace = (
            Path(source_workspace).expanduser().resolve()
            if source_workspace is not None
            else None
        )
        self.repository_intelligence_resolver = repository_intelligence_resolver
        self.cancelled = cancelled or (lambda: False)
        self.clock = clock

    def replay(
        self,
        trace: Trace,
        request: ReplayRequest,
        *,
        session_created_at: datetime | None = None,
    ) -> ReplayResult:
        self._validate_request(trace, request)
        catalog = ReplayInputCatalog(trace, self.store)
        classification = ReplayabilityClassifier().classify_trace(
            trace,
            available_object_hashes=catalog.available_hashes,
        )
        created_at = session_created_at or self.clock()
        session = ReplaySession(
            replay_id=request.replay_id,
            source_trace_id=trace.trace_id,
            source_run_id=trace.run_id,
            mode=request.mode,
            status=ReplaySessionStatus.RUNNING,
            created_at=created_at,
            started_at=max(created_at, self.clock()),
            from_span_id=request.from_span_id,
            from_checkpoint_id=request.from_checkpoint_id,
            fork=request.fork,
            changed_input_names=sorted(request.changed_inputs),
            isolated_workspace=_safe_workspace_label(request.isolated_workspace),
            missing_inputs=classification.missing_input_hashes,
        )
        repository_intelligence = None
        try:
            catalog.validate_all()
            effective_request = self._prepare_partial_replay(
                trace,
                request,
                session,
            )
            self._validate_mode(classification.level, request, session)
            spans = self._selected_spans(trace, effective_request)
            verifier_result = None
            reviewer_result = None
            for span in spans:
                self._check_cancelled()
                result, component_result = self._replay_span(
                    span,
                    request=effective_request,
                    inputs=catalog,
                )
                session.span_results.append(result)
                if result.action == ReplaySpanAction.SUBSTITUTED:
                    session.substitutions.append(span.span_id)
                intelligence_span = (
                    span.span_type == TraceSpanType.CUSTOM
                    and span.attributes.get("component")
                    == REPOSITORY_INTELLIGENCE_COMPONENT
                )
                if not intelligence_span:
                    session.policy_drift.extend(result.drift)
                if intelligence_span and component_result is not None:
                    repository_intelligence = (
                        RepositoryIntelligenceReplayReport.model_validate(
                            component_result
                        )
                    )
                    session.intelligence_drift.extend(
                        item.category
                        for item in repository_intelligence.findings
                    )
                if span.span_type == TraceSpanType.VERIFIER:
                    verifier_result = component_result
                elif span.span_type == TraceSpanType.REVIEWER:
                    reviewer_result = component_result
            session.status = ReplaySessionStatus.SUCCEEDED
            session.completed_at = self.clock()
            replayed_status = trace.status
        except ReplayCancelledError as exc:
            _fail_session(session, ReplaySessionStatus.CANCELLED, exc, self.clock)
            replayed_status = trace.status
            verifier_result = None
            reviewer_result = None
        except ReplayInputUnavailableError as exc:
            _fail_session(
                session,
                ReplaySessionStatus.AWAITING_INPUT,
                exc,
                self.clock,
            )
            replayed_status = trace.status
            verifier_result = None
            reviewer_result = None
        except ReplayIncompatibleError as exc:
            _fail_session(
                session,
                ReplaySessionStatus.INCOMPATIBLE,
                exc,
                self.clock,
            )
            replayed_status = trace.status
            verifier_result = None
            reviewer_result = None
        except ReplayError as exc:
            _fail_session(session, ReplaySessionStatus.FAILED, exc, self.clock)
            replayed_status = trace.status
            verifier_result = None
            reviewer_result = None
        result_sha256 = hashlib.sha256(
            canonical_json_bytes(
                {
                    "source_trace_id": trace.trace_id,
                    "mode": request.mode.value,
                    "status": session.status.value,
                    "spans": [
                        result.model_dump(mode="json")
                        for result in session.span_results
                    ],
                    "verifier": verifier_result,
                    "reviewer": reviewer_result,
                    "repository_intelligence": (
                        repository_intelligence.model_dump(mode="json")
                        if repository_intelligence is not None
                        else None
                    ),
                }
            )
        ).hexdigest()
        return ReplayResult(
            session=session,
            source_status=trace.status,
            replayed_status=replayed_status,
            result_sha256=result_sha256,
            verifier_result=verifier_result,
            reviewer_result=reviewer_result,
            repository_intelligence=repository_intelligence,
        )

    def _prepare_partial_replay(
        self,
        trace: Trace,
        request: ReplayRequest,
        session: ReplaySession,
    ) -> ReplayRequest:
        if request.from_checkpoint_id is None and request.from_span_id is None:
            return request
        if self.isolation_manager is None:
            raise ReplayIsolationError(
                "Partial replay requires an isolated replay state manager."
            )
        base_commit = None
        if request.from_checkpoint_id is not None:
            if self.checkpoint_manager is None:
                raise ReplayIncompatibleError(
                    "Checkpoint replay requires a checkpoint state manager."
                )
            ancestry = self.checkpoint_manager.validate_ancestry(
                trace,
                request.from_checkpoint_id,
            )
            if not ancestry:
                raise ReplayIncompatibleError(
                    "Checkpoint replay has no valid ancestry."
                )
            base_commit = ancestry[-1].base_commit
        self.isolation_manager.reconstruct(
            request.replay_id,
            run_id=request.source_run_id,
            base_commit=base_commit,
        )
        actual_worktree = self.isolation_manager.actual_worktree_path(
            request.replay_id
        )
        isolated_path = (
            actual_worktree
            or self.isolation_manager.actual_database_path(request.replay_id).parent
        )
        session.isolated_workspace = "[ISOLATED_REPLAY_WORKSPACE]"
        return request.model_copy(
            update={"isolated_workspace": str(isolated_path)}
        )

    def _replay_span(
        self,
        span: TraceSpan,
        *,
        request: ReplayRequest,
        inputs: ReplayInputCatalog,
    ) -> tuple[ReplaySpanResult, dict[str, Any] | None]:
        loaded_inputs = [_load_reference(inputs, item) for item in span.input_references]
        loaded_outputs = [
            _load_reference(inputs, item) for item in span.output_references
        ]
        if (
            span.span_type == TraceSpanType.CUSTOM
            and span.attributes.get("component")
            == REPOSITORY_INTELLIGENCE_COMPONENT
        ):
            report = self._replay_repository_intelligence(loaded_outputs)
            drift = [item.category.value for item in report.findings]
            return (
                _span_result(
                    span,
                    ReplaySpanAction.REUSED,
                    (
                        "Captured repository intelligence snapshot reused; "
                        "current local evidence compared when available."
                    ),
                    payload=report.model_dump(mode="json"),
                    drift=drift,
                ),
                report.model_dump(mode="json"),
            )
        if span.span_type in {
            TraceSpanType.PROVIDER_REQUEST,
            TraceSpanType.PROVIDER_RESPONSE,
        }:
            envelopes = [
                CapturedModelEnvelope.model_validate_json(inputs.read(reference))
                for reference in span.output_references
                if reference.media_type == MODEL_ENVELOPE_MEDIA_TYPE
            ]
            if not envelopes and span.attributes.get("provider") != "deterministic":
                raise ReplayInputUnavailableError(
                    f"Provider span '{span.span_id}' has no captured envelope."
                )
            return (
                _span_result(
                    span,
                    ReplaySpanAction.SUBSTITUTED,
                    "Captured provider response substituted without a live call.",
                    payload=[item.model_dump(mode="json") for item in envelopes],
                ),
                None,
            )
        if span.span_type == TraceSpanType.MODEL_PARSE:
            parsed = _validate_registered_schema(
                span,
                loaded_inputs or loaded_outputs,
                self.schemas.get(span.span_id),
            )
            return (
                _span_result(
                    span,
                    ReplaySpanAction.REPLAYED,
                    "Captured model value passed normal structured validation.",
                    payload=parsed,
                ),
                parsed if isinstance(parsed, dict) else None,
            )
        if span.span_type == TraceSpanType.TOOL_POLICY:
            actual = (
                self.policy_evaluator(span, loaded_inputs)
                if self.policy_evaluator is not None
                else _captured_component_result(span, loaded_outputs)
            )
            drift = _component_drift(
                span,
                actual,
                "policy",
                loaded_outputs,
            )
            if drift and request.mode == ReplayMode.STRICT:
                raise ReplayIncompatibleError(drift[0])
            return (
                _span_result(
                    span,
                    ReplaySpanAction.REPLAYED,
                    "Policy decision reevaluated against replay inputs.",
                    payload=actual,
                    drift=drift,
                ),
                actual,
            )
        if span.span_type == TraceSpanType.TOOL_INVOCATION:
            return (
                self._replay_tool(span, request, loaded_inputs),
                None,
            )
        if span.span_type == TraceSpanType.VERIFIER:
            actual = (
                self.verifier(span, loaded_inputs)
                if self.verifier is not None
                else _captured_component_result(span, loaded_outputs)
            )
            return (
                _span_result(
                    span,
                    ReplaySpanAction.REPLAYED,
                    "Verifier reran against captured replay artifacts.",
                    payload=actual,
                    drift=_component_drift(
                        span,
                        actual,
                        "verifier",
                        loaded_outputs,
                    ),
                ),
                actual,
            )
        if span.span_type == TraceSpanType.REVIEWER:
            actual = (
                self.reviewer(span, loaded_inputs)
                if self.reviewer is not None
                else _captured_component_result(span, loaded_outputs)
            )
            return (
                _span_result(
                    span,
                    ReplaySpanAction.REPLAYED,
                    "Reviewer output passed captured structured replay.",
                    payload=actual,
                    drift=_component_drift(
                        span,
                        actual,
                        "reviewer",
                        loaded_outputs,
                    ),
                ),
                actual,
            )
        if span.span_type in {
            TraceSpanType.GIT_MUTATION,
            TraceSpanType.INTEGRATION,
        }:
            return (
                _span_result(
                    span,
                    ReplaySpanAction.SIMULATED,
                    "Repository mutation simulated; the source workspace was untouched.",
                ),
                None,
            )
        if span.span_type in {
            TraceSpanType.CANCELLATION,
            TraceSpanType.CLEANUP,
        }:
            return (
                _span_result(
                    span,
                    ReplaySpanAction.OBSERVED,
                    "Lifecycle ordering replayed without reproducing host timing.",
                ),
                None,
            )
        return (
            _span_result(
                span,
                ReplaySpanAction.REPLAYED,
                "Recorded orchestration state transition replayed.",
            ),
            None,
        )

    def _replay_repository_intelligence(
        self,
        loaded_outputs: list[Any],
    ) -> RepositoryIntelligenceReplayReport:
        if not loaded_outputs:
            raise ReplayInputUnavailableError(
                "Repository intelligence replay has no captured evidence."
            )
        try:
            captured = RepositoryIntelligenceTraceEvidence.model_validate(
                loaded_outputs[-1]
            )
        except ValueError as exc:
            raise ReplayInputUnavailableError(
                "Captured repository intelligence evidence is invalid."
            ) from exc
        if self.repository_intelligence_resolver is None:
            return reuse_captured_repository_intelligence(captured)
        try:
            current = self.repository_intelligence_resolver(captured)
        except Exception:
            current = None
        if current is None:
            return unavailable_current_repository_intelligence(captured)
        return compare_repository_intelligence(captured, current)

    def _replay_tool(
        self,
        span: TraceSpan,
        request: ReplayRequest,
        loaded_inputs: list[Any],
    ) -> ReplaySpanResult:
        assessment = None
        strategy = request.tool_strategies.get(span.span_id)
        if strategy is None and self.tool_replay_planner is not None:
            references = [
                reference
                for reference in span.output_references
                if reference.media_type == TOOL_ENVELOPE_MEDIA_TYPE
            ]
            if len(references) != 1:
                raise ReplayInputUnavailableError(
                    "Managed tool replay requires one captured tool envelope."
                )
            envelope = load_tool_envelope(
                self.store,
                references[0].sha256,
            )
            descriptor = self.tool_descriptors.get(envelope.descriptor.name)
            if descriptor is None:
                raise ReplayIncompatibleError(
                    "Current tool descriptor is unavailable for policy replay."
                )
            assessment = self.tool_replay_planner.assess(
                envelope,
                descriptor,
                mode=request.mode,
                isolated_workspace=(
                    request.isolated_workspace
                    or "[ISOLATED_REPLAY_WORKSPACE]"
                ),
            )
            strategy = assessment.strategy
        if strategy is None:
            strategy = _default_tool_strategy(span, request.mode)
        drift = (
            [
                reason
                for reason in assessment.reasons
                if assessment.policy_drift
                or assessment.capability_drift
                or assessment.descriptor_drift
            ]
            if assessment is not None
            else []
        )
        if strategy == ToolReplayStrategy.REUSE_CAPTURED:
            if not span.output_references:
                raise ReplayInputUnavailableError(
                    f"Tool span '{span.span_id}' has no captured result."
                )
            return _span_result(
                span,
                ReplaySpanAction.REUSED,
                "Captured bounded tool result reused.",
                drift=drift,
            )
        if strategy == ToolReplayStrategy.SIMULATE_MUTATION:
            return _span_result(
                span,
                ReplaySpanAction.SIMULATED,
                "Tool mutation simulated without filesystem side effects.",
                drift=drift,
            )
        if strategy == ToolReplayStrategy.REJECT:
            reason = (
                assessment.reasons[0]
                if assessment is not None
                else "No safe replay strategy is available."
            )
            raise ReplayIncompatibleError(
                f"Managed tool '{span.name}' is not safe to replay: {reason}"
            )
        workspace = self._isolated_workspace(request)
        if self.tool_executor is None:
            raise ReplayIncompatibleError(
                "Sandbox tool rerun was requested but no replay executor is configured."
            )
        payload = self.tool_executor(span, loaded_inputs, workspace)
        return _span_result(
            span,
            ReplaySpanAction.RERUN,
            "Tool reran in an isolated replay workspace.",
            payload=payload,
            drift=drift,
        )

    def _isolated_workspace(self, request: ReplayRequest) -> Path:
        if request.isolated_workspace is None:
            raise ReplayIsolationError(
                "Tool mutation replay requires an isolated workspace."
            )
        workspace = Path(request.isolated_workspace).expanduser().resolve()
        if not workspace.is_dir():
            raise ReplayIsolationError(
                "Configured replay workspace does not exist."
            )
        if self.source_workspace is not None and (
            workspace == self.source_workspace
            or workspace in self.source_workspace.parents
            or self.source_workspace in workspace.parents
        ):
            raise ReplayIsolationError(
                "Replay workspace must be isolated from the source repository."
            )
        return workspace

    def _selected_spans(
        self,
        trace: Trace,
        request: ReplayRequest,
    ) -> list[TraceSpan]:
        spans = sorted(trace.spans, key=lambda item: item.sequence)
        if request.from_span_id is None and request.from_checkpoint_id is None:
            return spans
        sequence = None
        if request.from_span_id is not None:
            selected = next(
                (item for item in spans if item.span_id == request.from_span_id),
                None,
            )
            if selected is None:
                raise ReplayIncompatibleError("Replay start span was not found.")
            sequence = selected.sequence
        if request.from_checkpoint_id is not None:
            checkpoint = next(
                (
                    item
                    for item in trace.checkpoints
                    if item.checkpoint_id == request.from_checkpoint_id
                ),
                None,
            )
            if checkpoint is None or not checkpoint.replayable:
                raise ReplayIncompatibleError(
                    "Replay checkpoint is unavailable or non-replayable."
                )
            sequence = checkpoint.sequence
        assert sequence is not None
        return [item for item in spans if item.sequence >= sequence]

    @staticmethod
    def _validate_request(trace: Trace, request: ReplayRequest) -> None:
        if trace.schema_version != TRACE_SCHEMA_VERSION:
            raise ReplayIncompatibleError("Trace schema version is unsupported.")
        if (
            request.source_trace_id != trace.trace_id
            or request.source_run_id != trace.run_id
        ):
            raise ReplayIncompatibleError(
                "Replay request does not identify the supplied source trace."
            )
        if request.live_provider_consent:
            raise ReplayIncompatibleError(
                "Providerless replay does not permit live-provider calls."
            )

    @staticmethod
    def _validate_mode(
        level: ReplayabilityLevel,
        request: ReplayRequest,
        session: ReplaySession,
    ) -> None:
        if session.missing_inputs:
            raise ReplayInputUnavailableError(
                f"Replay is missing {len(session.missing_inputs)} required input object(s)."
            )
        if (
            request.mode == ReplayMode.STRICT
            and level
            not in {
                ReplayabilityLevel.EXACTLY_REPLAYABLE,
                ReplayabilityLevel.DETERMINISTICALLY_SUBSTITUTABLE,
            }
        ):
            raise ReplayIncompatibleError(
                f"Strict replay cannot execute a {level.value} trace."
            )
        if (
            request.mode != ReplayMode.SIMULATE
            and level == ReplayabilityLevel.NON_REPLAYABLE
        ):
            raise ReplayIncompatibleError(
                "The trace contains behavior that is unavailable offline."
            )

    def _check_cancelled(self) -> None:
        if self.cancelled():
            raise ReplayCancelledError("Replay was cooperatively cancelled.")


def _load_reference(inputs: ReplayInputCatalog, reference) -> Any:
    payload = inputs.read(reference)
    if (
        reference.media_type == "application/json"
        or reference.media_type.endswith("+json")
    ):
        try:
            import json

            return json.loads(payload.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ReplayInputUnavailableError(
                f"Replay input '{reference.reference_id}' is invalid JSON."
            ) from exc
    return payload.decode("utf-8")


def _validate_registered_schema(
    span: TraceSpan,
    values: list[Any],
    schema: Schema | None,
) -> Any:
    if not values:
        raise ReplayInputUnavailableError(
            f"Model parse span '{span.span_id}' has no captured input."
        )
    value = values[0]
    if isinstance(value, dict) and "value" in value and "envelope_version" in value:
        envelope = CapturedModelEnvelope.model_validate(value)
        value = envelope.value
    if schema is None:
        if not isinstance(value, (dict, str)):
            raise ReplayIncompatibleError(
                "Captured model value is not a supported structured type."
            )
        return value
    if isinstance(schema, type) and issubclass(schema, BaseModel):
        return schema.model_validate(value).model_dump(mode="json")
    if not isinstance(value, dict):
        raise ReplayIncompatibleError("JSON Schema replay requires an object.")
    validate_json_schema(
        value,
        schema,
        provider="replay",
        model="captured",
        request_id=span.span_id,
    )
    return value


def _captured_component_result(
    span: TraceSpan,
    loaded_outputs: list[Any],
) -> dict[str, Any]:
    captured = _find_captured_component_result(span, loaded_outputs)
    if captured is not None:
        return captured
    raise ReplayInputUnavailableError(
        f"Span '{span.span_id}' has no captured structured result."
    )


def _find_captured_component_result(
    span: TraceSpan,
    loaded_outputs: list[Any],
) -> dict[str, Any] | None:
    value = span.attributes.get("result")
    if isinstance(value, dict):
        return value
    return next(
        (
            output
            for output in reversed(loaded_outputs)
            if isinstance(output, dict)
        ),
        None,
    )


def _component_drift(
    span: TraceSpan,
    actual: dict[str, Any],
    component: str,
    loaded_outputs: list[Any],
) -> list[str]:
    expected = _find_captured_component_result(span, loaded_outputs)
    if expected is not None and (
        sanitize_document(expected).value
        != sanitize_document(actual).value
    ):
        return [f"{component} result drifted from the captured decision."]
    return []


def _default_tool_strategy(
    span: TraceSpan,
    mode: ReplayMode,
) -> ToolReplayStrategy:
    if mode == ReplayMode.SIMULATE:
        return ToolReplayStrategy.SIMULATE_MUTATION
    effect = str(span.attributes.get("tool_effect", "unknown")).lower()
    if mode == ReplayMode.OFFLINE and effect in {
        "filesystem_mutation",
        "git_mutation",
        "process",
    }:
        return ToolReplayStrategy.SIMULATE_MUTATION
    configured = span.attributes.get("replay_strategy")
    if configured is not None:
        try:
            return ToolReplayStrategy(str(configured))
        except ValueError:
            return ToolReplayStrategy.REJECT
    if effect in {"read", "pure_read"}:
        return ToolReplayStrategy.REUSE_CAPTURED
    if effect in {"filesystem_mutation", "git_mutation", "process"}:
        return ToolReplayStrategy.SIMULATE_MUTATION
    return ToolReplayStrategy.REJECT


def _span_result(
    span: TraceSpan,
    action: ReplaySpanAction,
    summary: str,
    *,
    payload: Any = None,
    drift: list[str] | None = None,
) -> ReplaySpanResult:
    output_sha256 = (
        hashlib.sha256(canonical_json_bytes(sanitize_document(payload).value)).hexdigest()
        if payload is not None
        else None
    )
    return ReplaySpanResult(
        span_id=span.span_id,
        action=action,
        succeeded=True,
        summary=summary,
        output_sha256=output_sha256,
        drift=drift or [],
    )


def _fail_session(
    session: ReplaySession,
    status: ReplaySessionStatus,
    error: Exception,
    clock: Callable,
) -> None:
    session.status = status
    session.completed_at = clock()
    session.failure_category = type(error).__name__
    session.failure_message = str(error)


def _safe_workspace_label(value: str | None) -> str | None:
    if value is None:
        return None
    return "[ISOLATED_REPLAY_WORKSPACE]"


__all__ = ["ReplayEngine"]
