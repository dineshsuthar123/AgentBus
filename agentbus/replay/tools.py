from __future__ import annotations

from datetime import datetime
from enum import Enum
from pathlib import Path
import re
from typing import Any

from pydantic import Field, field_validator, model_validator

from agentbus.policy import (
    ToolApprovalDisposition,
    ToolApprovalGrant,
    ToolPolicyEngine,
    approval_binding_sha256,
    build_tool_approval_request,
    validate_tool_approval,
)
from agentbus.replay.errors import ReplayIncompatibleError
from agentbus.replay.session import ToolReplayStrategy
from agentbus.trace.models import (
    ReplayMode,
    Trace,
    TraceModel,
    TraceOutput,
    TraceSpanType,
)
from agentbus.trace.redaction import sanitize_document
from agentbus.trace.storage import ContentAddressedStore
from agentbus.tools.capabilities import derive_replay_required_capabilities
from agentbus.tools.records import (
    invocation_identity_sha256,
    policy_decision_sha256,
)
from agentbus.tools.protocol import (
    ToolCapability,
    ToolCapabilityName,
    ToolApprovalRequest,
    ToolDescriptor,
    ToolInvocation,
    ToolInvocationContext,
    ToolPolicyDecision,
    ToolPolicyOutcome,
    ToolResult,
    ToolSafetyClassification,
    capability_fingerprint,
    idempotency_key_sha256,
    safe_protocol_dict,
    sha256_json,
)

TOOL_ENVELOPE_MEDIA_TYPE = "application/vnd.agentbus.tool-envelope+json"
TOOL_ENVELOPE_VERSION = 2
HISTORICAL_EXECUTION_ENVELOPE_VERSION = 1
_LEGACY_TOOL_ENVELOPE_VERSION = 1
_SUPPORTED_TOOL_ENVELOPE_VERSIONS = frozenset(
    {_LEGACY_TOOL_ENVELOPE_VERSION, TOOL_ENVELOPE_VERSION}
)
_SHA256_PATTERN = re.compile(r"^[a-f0-9]{64}$")
_MUTATING_CAPABILITIES = {
    ToolCapabilityName.FILESYSTEM_WRITE,
    ToolCapabilityName.FILESYSTEM_CREATE,
    ToolCapabilityName.FILESYSTEM_DELETE,
    ToolCapabilityName.FILESYSTEM_RENAME,
    ToolCapabilityName.GIT_WRITE,
    ToolCapabilityName.GIT_COMMIT,
    ToolCapabilityName.GIT_BRANCH,
    ToolCapabilityName.GIT_WORKTREE,
    ToolCapabilityName.PACKAGE_INSTALL,
}
_EXTERNAL_CAPABILITIES = {
    ToolCapabilityName.PROCESS_NETWORK,
    ToolCapabilityName.MCP_CONNECT,
    ToolCapabilityName.MCP_INVOKE,
}
_PROCESS_CAPABILITIES = {
    ToolCapabilityName.PROCESS_EXECUTE,
    ToolCapabilityName.TEST_EXECUTE,
}


class HistoricalExecutionClass(str, Enum):
    MANAGED_TOOL = "managed_tool"
    MANAGED_PROCESS = "managed_process"


class HistoricalExecutablePathClass(str, Enum):
    ALIAS = "alias"
    ABSOLUTE_ALLOWLISTED = "absolute_allowlisted"
    WORKSPACE_RELATIVE = "workspace_relative"


class HistoricalExecutionEnvelope(TraceModel):
    """Integrity-bound evidence emitted only for a completed managed dispatch."""

    schema_version: int = HISTORICAL_EXECUTION_ENVELOPE_VERSION
    execution_class: HistoricalExecutionClass
    descriptor_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    descriptor_contract_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    invocation_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    arguments_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    capability_fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")
    policy_decision_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    policy_context_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    resource_budget_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    result_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    result_status: str = Field(min_length=1, max_length=64)
    approval_id: str | None = Field(default=None, max_length=128)
    approval_sha256: str | None = Field(
        default=None,
        pattern=r"^[a-f0-9]{64}$",
    )
    executable: str | None = Field(default=None, max_length=1_024)
    executable_path_class: HistoricalExecutablePathClass | None = None
    executable_provenance_sha256: str | None = Field(
        default=None,
        pattern=r"^[a-f0-9]{64}$",
    )
    dependency_identity_sha256: str | None = Field(
        default=None,
        pattern=r"^[a-f0-9]{64}$",
    )
    working_directory: str | None = Field(default=None, max_length=2_048)
    source_identity_sha256: str | None = Field(
        default=None,
        pattern=r"^[a-f0-9]{64}$",
    )
    candidate_identity_sha256: str | None = Field(
        default=None,
        pattern=r"^[a-f0-9]{64}$",
    )
    historical_process_dispatched: bool = False
    evidence_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")

    @field_validator("working_directory")
    @classmethod
    def working_directory_is_relative(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.replace("\\", "/")
        if (
            not normalized
            or normalized.startswith("/")
            or re.match(r"^[A-Za-z]:/", normalized)
            or any(part == ".." for part in normalized.split("/"))
        ):
            raise ValueError(
                "historical execution working directory must be workspace-relative"
            )
        return normalized

    @model_validator(mode="after")
    def schema_and_fingerprint_are_valid(self) -> "HistoricalExecutionEnvelope":
        if self.schema_version != HISTORICAL_EXECUTION_ENVELOPE_VERSION:
            raise ValueError(
                "unsupported historical execution envelope version: "
                f"{self.schema_version}"
            )
        if self.execution_class == HistoricalExecutionClass.MANAGED_PROCESS:
            if (
                self.executable is None
                or self.executable_path_class is None
                or self.executable_provenance_sha256 is None
                or self.working_directory is None
                or not self.historical_process_dispatched
            ):
                raise ValueError(
                    "historical process evidence is incomplete or was not dispatched"
                )
        elif any(
            value is not None
            for value in (
                self.executable,
                self.executable_path_class,
                self.executable_provenance_sha256,
                self.working_directory,
            )
        ) or self.historical_process_dispatched:
            raise ValueError(
                "non-process historical evidence cannot claim process identity"
            )
        if (self.approval_id is None) != (self.approval_sha256 is None):
            raise ValueError("historical approval identity is incomplete")
        if self.evidence_sha256 != _historical_evidence_sha256(self):
            raise ValueError("historical execution evidence fingerprint is invalid")
        return self


class CapturedToolEnvelope(TraceModel):
    envelope_version: int = TOOL_ENVELOPE_VERSION
    descriptor: ToolDescriptor
    invocation: ToolInvocation
    policy_decision: ToolPolicyDecision
    result: ToolResult | None = None
    approval: ToolApprovalGrant | None = None
    historical_execution: HistoricalExecutionEnvelope | None = None

    @model_validator(mode="after")
    def bindings_match(self) -> "CapturedToolEnvelope":
        if self.envelope_version not in _SUPPORTED_TOOL_ENVELOPE_VERSIONS:
            raise ValueError(
                f"unsupported tool envelope version: {self.envelope_version}"
            )
        if self.envelope_version == 1 and self.historical_execution is not None:
            raise ValueError(
                "legacy tool envelopes cannot contain historical execution evidence"
            )
        if self.descriptor.name != self.invocation.tool_name:
            raise ValueError("tool envelope descriptor does not match invocation")
        if (
            self.descriptor.version != self.invocation.tool_version
            or self.descriptor.protocol_version
            != self.invocation.protocol_version
        ):
            raise ValueError(
                "tool envelope descriptor version does not match invocation"
            )
        if (
            self.policy_decision.invocation_id != self.invocation.invocation_id
            or self.policy_decision.invocation_revision
            != self.invocation.invocation_revision
            or self.policy_decision.capability_fingerprint
            != capability_fingerprint(self.invocation.requested_capabilities)
        ):
            raise ValueError("tool envelope policy binding does not match invocation")
        if self.result is not None and (
            self.result.invocation_id != self.invocation.invocation_id
            or self.result.invocation_revision
            != self.invocation.invocation_revision
        ):
            raise ValueError("tool envelope result does not match invocation")
        if self.result is not None and (
            policy_decision_sha256(self.result.policy_decision)
            != policy_decision_sha256(self.policy_decision)
        ):
            raise ValueError("tool envelope result policy does not match invocation")
        if self.historical_execution is not None:
            _validate_historical_execution_bindings(self)
        return self


class ToolReplayAssessment(TraceModel):
    invocation_id: str = Field(min_length=1, max_length=128)
    strategy: ToolReplayStrategy
    historical_outcome: ToolPolicyOutcome
    current_outcome: ToolPolicyOutcome | None = None
    current_decision: ToolPolicyDecision | None = None
    descriptor_drift: bool = False
    capability_drift: bool = False
    policy_drift: bool = False
    approval_compatible_for_substitution: bool = False
    fresh_authorization_required: bool = False
    historical_authorization_validated: bool = False
    historical_executable: str | None = Field(default=None, max_length=1_024)
    captured_result_reused: bool = False
    process_dispatched: bool = False
    reasons: list[str] = Field(min_length=1, max_length=128)


def capture_tool_envelope(
    store: ContentAddressedStore,
    *,
    descriptor: ToolDescriptor,
    invocation: ToolInvocation,
    policy_decision: ToolPolicyDecision,
    producing_span_id: str,
    reference_id: str,
    result: ToolResult | None = None,
    approval: ToolApprovalGrant | None = None,
) -> TraceOutput:
    private_roots = _invocation_private_roots(invocation)
    safe_descriptor = ToolDescriptor.model_validate(
        _sanitize_protocol_model(
            store,
            descriptor,
            private_roots=private_roots,
        )
    )
    safe_invocation = ToolInvocation.model_validate(
        _sanitize_protocol_model(
            store,
            invocation,
            private_roots=private_roots,
        )
    )
    safe_decision = _sanitize_policy_decision(
        store,
        safe_invocation,
        policy_decision,
        private_roots=private_roots,
    )
    safe_result = None
    if result is not None:
        result_payload = _sanitize_protocol_model(
            store,
            result,
            private_roots=private_roots,
        )
        result_payload["policy_decision"] = safe_decision.model_dump(mode="json")
        safe_result = ToolResult.model_validate(result_payload)
    safe_approval = (
        _sanitize_approval(
            store,
            approval,
            invocation=safe_invocation,
            private_roots=private_roots,
        )
        if approval is not None
        else None
    )
    historical_execution = _build_historical_execution_envelope(
        descriptor=safe_descriptor,
        invocation=safe_invocation,
        policy_decision=safe_decision,
        result=safe_result,
        approval=safe_approval,
    )
    envelope = CapturedToolEnvelope(
        descriptor=safe_descriptor,
        invocation=safe_invocation,
        policy_decision=safe_decision,
        result=safe_result,
        approval=safe_approval,
        historical_execution=historical_execution,
    )
    metadata = store.put_json(
        envelope.model_dump(mode="json"),
        producing_span_id=producing_span_id,
        media_type=TOOL_ENVELOPE_MEDIA_TYPE,
    )
    return store.reference_output(
        metadata,
        reference_id=reference_id,
        name=f"tool.invocation.{invocation.tool_name}",
        replayable=True,
    )


def sanitize_replay_policy_decision(
    store: ContentAddressedStore,
    *,
    invocation: ToolInvocation,
    policy_decision: ToolPolicyDecision,
) -> ToolPolicyDecision:
    private_roots = _invocation_private_roots(invocation)
    safe_invocation = ToolInvocation.model_validate(
        _sanitize_protocol_model(
            store,
            invocation,
            private_roots=private_roots,
        )
    )
    return _sanitize_policy_decision(
        store,
        safe_invocation,
        policy_decision,
        private_roots=private_roots,
    )


def _sanitize_policy_decision(
    store: ContentAddressedStore,
    safe_invocation: ToolInvocation,
    policy_decision: ToolPolicyDecision,
    *,
    private_roots: tuple[str, ...],
) -> ToolPolicyDecision:
    decision_payload = _sanitize_protocol_model(
        store,
        policy_decision,
        private_roots=private_roots,
    )
    decision_payload.update(
        {
            "capability_fingerprint": capability_fingerprint(
                safe_invocation.requested_capabilities
            ),
            "arguments_sha256": sha256_json(safe_invocation.arguments),
        }
    )
    return ToolPolicyDecision.model_validate(decision_payload)


def _sanitize_protocol_model(
    store: ContentAddressedStore,
    value: Any,
    *,
    private_roots: tuple[str, ...] = (),
) -> dict[str, Any]:
    sanitized = sanitize_document(
        safe_protocol_dict(value),
        private_roots=(*store.private_roots, *private_roots),
    ).value
    if not isinstance(sanitized, dict):
        raise ValueError("sanitized tool protocol value must remain an object")
    return sanitized


def _sanitize_approval(
    store: ContentAddressedStore,
    approval: ToolApprovalGrant,
    *,
    invocation: ToolInvocation,
    private_roots: tuple[str, ...],
) -> ToolApprovalGrant:
    request_payload = _sanitize_protocol_model(
        store,
        approval.request,
        private_roots=private_roots,
    )
    request_payload.update(
        {
            "requested_capabilities": [
                item.model_dump(mode="json")
                for item in invocation.requested_capabilities
            ],
            "capability_fingerprint": capability_fingerprint(
                invocation.requested_capabilities
            ),
            "arguments_sha256": sha256_json(invocation.arguments),
            "workspace_identity": invocation.context.workspace_identity,
            "worktree_identity": invocation.context.worktree_identity,
            "idempotency_key_sha256": idempotency_key_sha256(
                invocation.idempotency_key
            ),
        }
    )
    request = ToolApprovalRequest.model_validate(request_payload)
    approval_payload = _sanitize_protocol_model(
        store,
        approval,
        private_roots=private_roots,
    )
    approval_payload.update(
        {
            "request": request.model_dump(mode="json"),
            "binding_sha256": approval_binding_sha256(request, invocation),
        }
    )
    return ToolApprovalGrant.model_validate(approval_payload)


def _invocation_private_roots(
    invocation: ToolInvocation,
) -> tuple[str, ...]:
    return (
        invocation.context.workspace_identity,
        invocation.context.worktree_identity,
    )


def _build_historical_execution_envelope(
    *,
    descriptor: ToolDescriptor,
    invocation: ToolInvocation,
    policy_decision: ToolPolicyDecision,
    result: ToolResult | None,
    approval: ToolApprovalGrant | None,
) -> HistoricalExecutionEnvelope | None:
    payload = _historical_execution_payload(
        descriptor=descriptor,
        invocation=invocation,
        policy_decision=policy_decision,
        result=result,
        approval=approval,
    )
    if payload is None:
        return None
    payload["evidence_sha256"] = sha256_json(payload)
    return HistoricalExecutionEnvelope.model_validate(payload)


def _historical_execution_payload(
    *,
    descriptor: ToolDescriptor,
    invocation: ToolInvocation,
    policy_decision: ToolPolicyDecision,
    result: ToolResult | None,
    approval: ToolApprovalGrant | None,
) -> dict[str, Any] | None:
    if result is None or policy_decision.outcome not in {
        ToolPolicyOutcome.ALLOW,
        ToolPolicyOutcome.ALLOW_WITH_CONSTRAINTS,
    }:
        return None
    decision_approval_id = policy_decision.safe_metadata.get("approval_id")
    approval_claimed = (
        result.approval_id is not None or decision_approval_id is not None
    )
    if approval_claimed and (
        not isinstance(decision_approval_id, str)
        or approval is None
        or result.approval_id != approval.approval_id
        or decision_approval_id != approval.approval_id
    ):
        return None
    if approval is not None and (
        not approval_claimed
        or approval.disposition != ToolApprovalDisposition.APPROVED
    ):
        return None

    capability_names = {
        capability.name for capability in invocation.requested_capabilities
    }
    process_execution = bool(capability_names & _PROCESS_CAPABILITIES)
    executable = None
    executable_path_class = None
    executable_provenance_sha256 = None
    dependency_identity_sha256 = None
    working_directory = None
    historical_process_dispatched = False
    if process_execution:
        executable = invocation.arguments.get("executable")
        diagnostics = result.safe_diagnostic_metadata
        executable_metadata = diagnostics.get("executable")
        structured_executable = result.structured_output.get("executable")
        diagnostic_pid = diagnostics.get("pid")
        structured_pid = result.structured_output.get("pid")
        if (
            not isinstance(executable, str)
            or not executable
            or not isinstance(executable_metadata, dict)
            or executable_metadata.get("alias") != executable
            or structured_executable != executable
            or type(diagnostic_pid) is not int
            or diagnostic_pid <= 0
            or structured_pid != diagnostic_pid
            or diagnostics.get("shell") is not False
            or diagnostics.get("process_started") is False
        ):
            return None
        working_directory = _workspace_relative_working_directory(invocation)
        if working_directory is None:
            return None
        executable_path_class = _executable_path_class(executable)
        executable_provenance_sha256 = sha256_json(executable_metadata)
        dependencies = diagnostics.get("dependencies")
        if isinstance(dependencies, (dict, list)):
            dependency_identity_sha256 = sha256_json(dependencies)
        historical_process_dispatched = True

    policy_context = invocation.context.policy_context
    source_identity = _optional_digest(
        policy_context.get("source_identity_sha256")
    )
    candidate_identity = _optional_digest(
        policy_context.get("candidate_identity_sha256")
    )
    return {
        "schema_version": HISTORICAL_EXECUTION_ENVELOPE_VERSION,
        "execution_class": (
            HistoricalExecutionClass.MANAGED_PROCESS.value
            if process_execution
            else HistoricalExecutionClass.MANAGED_TOOL.value
        ),
        "descriptor_sha256": _descriptor_sha256(descriptor),
        "descriptor_contract_sha256": _descriptor_contract_sha256(
            descriptor
        ),
        "invocation_sha256": invocation_identity_sha256(invocation),
        "arguments_sha256": sha256_json(invocation.arguments),
        "capability_fingerprint": capability_fingerprint(
            invocation.requested_capabilities
        ),
        "policy_decision_sha256": policy_decision_sha256(policy_decision),
        "policy_context_sha256": sha256_json(policy_context),
        "resource_budget_sha256": sha256_json(
            invocation.resource_budget.model_dump(mode="json")
        ),
        "result_sha256": sha256_json(result.model_dump(mode="json")),
        "result_status": result.status.value,
        "approval_id": approval.approval_id if approval is not None else None,
        "approval_sha256": (
            sha256_json(approval.model_dump(mode="json"))
            if approval is not None
            else None
        ),
        "executable": executable,
        "executable_path_class": (
            executable_path_class.value
            if executable_path_class is not None
            else None
        ),
        "executable_provenance_sha256": executable_provenance_sha256,
        "dependency_identity_sha256": dependency_identity_sha256,
        "working_directory": working_directory,
        "source_identity_sha256": source_identity,
        "candidate_identity_sha256": candidate_identity,
        "historical_process_dispatched": historical_process_dispatched,
    }


def _validate_historical_execution_bindings(
    envelope: CapturedToolEnvelope,
) -> None:
    evidence = envelope.historical_execution
    assert evidence is not None
    expected = _build_historical_execution_envelope(
        descriptor=envelope.descriptor,
        invocation=envelope.invocation,
        policy_decision=envelope.policy_decision,
        result=envelope.result,
        approval=envelope.approval,
    )
    if expected is None or expected != evidence:
        raise ValueError(
            "historical execution evidence does not match the captured tool scope"
        )
    if envelope.approval is not None:
        validate_tool_approval(
            envelope.approval,
            envelope.invocation,
            envelope.descriptor,
            now=envelope.approval.decided_at,
        )
        if (
            envelope.policy_decision.outcome
            != ToolPolicyOutcome.ALLOW_WITH_CONSTRAINTS
            or envelope.policy_decision.safe_metadata.get("approval_id")
            != envelope.approval.approval_id
            or envelope.policy_decision.safe_metadata.get(
                "original_policy_rule"
            )
            != envelope.approval.request.policy_rule
        ):
            raise ValueError(
                "historical execution policy does not match its exact approval"
            )


def _historical_evidence_sha256(
    evidence: HistoricalExecutionEnvelope,
) -> str:
    payload = evidence.model_dump(mode="json", exclude={"evidence_sha256"})
    return sha256_json(payload)


def _descriptor_sha256(descriptor: ToolDescriptor) -> str:
    return sha256_json(descriptor.model_dump(mode="json"))


def _descriptor_contract_sha256(descriptor: ToolDescriptor) -> str:
    capabilities = []
    for capability in descriptor.capabilities:
        scope = capability.scope.model_dump(mode="json")
        scope["roots"] = ["[SCOPED_ROOT]"] if scope["roots"] else []
        scope["working_directories"] = (
            ["[SCOPED_WORKING_DIRECTORY]"]
            if scope["working_directories"]
            else []
        )
        if capability.name in _PROCESS_CAPABILITIES:
            scope["executables"] = (
                ["[DYNAMIC_EXECUTABLE_CLOSURE]"]
                if scope["executables"]
                else []
            )
        capabilities.append(
            {"name": capability.name.value, "scope": scope}
        )
    return sha256_json(
        {
            "name": descriptor.name,
            "version": descriptor.version.model_dump(mode="json"),
            "protocol_version": descriptor.protocol_version,
            "capabilities": capabilities,
            "argument_schema": descriptor.argument_schema,
            "output_schema": descriptor.output_schema,
            "safety": descriptor.safety.value,
            "idempotent": descriptor.idempotent,
            "supports_cancellation": descriptor.supports_cancellation,
            "maximum_timeout_seconds": descriptor.maximum_timeout_seconds,
        }
    )


def _workspace_relative_working_directory(
    invocation: ToolInvocation,
) -> str | None:
    requested = invocation.arguments.get("working_directory")
    if requested is None:
        return "."
    if not isinstance(requested, str) or not requested.strip():
        return None
    raw = requested.strip().replace("\\", "/")
    private_prefix = "[PRIVATE_PATH]"
    if raw == private_prefix:
        return "."
    if raw.startswith(private_prefix + "/"):
        raw = raw[len(private_prefix) + 1 :]
    is_absolute = raw.startswith("/") or bool(
        re.match(r"^[A-Za-z]:/", raw)
    )
    if is_absolute:
        worktree = invocation.context.worktree_identity.replace("\\", "/")
        if raw == worktree:
            return "."
        prefix = worktree.rstrip("/") + "/"
        if not raw.startswith(prefix):
            return None
        raw = raw[len(prefix) :]
    parts = [part for part in raw.split("/") if part not in {"", "."}]
    if any(part == ".." for part in parts):
        return None
    return "/".join(parts) or "."


def _executable_path_class(
    executable: str,
) -> HistoricalExecutablePathClass:
    normalized = executable.replace("\\", "/")
    if normalized.startswith("/") or re.match(r"^[A-Za-z]:/", normalized):
        return HistoricalExecutablePathClass.ABSOLUTE_ALLOWLISTED
    if "/" in normalized:
        return HistoricalExecutablePathClass.WORKSPACE_RELATIVE
    return HistoricalExecutablePathClass.ALIAS


def _optional_digest(value: Any) -> str | None:
    return (
        value
        if isinstance(value, str) and _SHA256_PATTERN.fullmatch(value)
        else None
    )


def load_tool_envelope(
    store: ContentAddressedStore,
    sha256: str,
) -> CapturedToolEnvelope:
    metadata = store.get_metadata(sha256)
    if metadata.media_type != TOOL_ENVELOPE_MEDIA_TYPE:
        raise ReplayIncompatibleError(
            "Captured tool reference has an incompatible media type."
        )
    try:
        return CapturedToolEnvelope.model_validate(store.get_json(sha256))
    except Exception as exc:
        raise ReplayIncompatibleError(
            "Captured tool envelope is invalid or incompatible."
        ) from exc


def historical_execution_catalog(
    trace: Trace,
    store: ContentAddressedStore,
) -> dict[str, CapturedToolEnvelope]:
    """Load one unambiguous authenticated process result per invocation."""
    catalog: dict[str, CapturedToolEnvelope] = {}
    for span in trace.spans:
        if span.span_type != TraceSpanType.TOOL_INVOCATION:
            continue
        references = [
            reference
            for reference in span.output_references
            if reference.media_type == TOOL_ENVELOPE_MEDIA_TYPE
        ]
        if len(references) > 1:
            raise ReplayIncompatibleError(
                "Managed tool span contains ambiguous captured envelopes."
            )
        if not references:
            continue
        envelope = load_tool_envelope(store, references[0].sha256)
        evidence = envelope.historical_execution
        if (
            evidence is None
            or evidence.execution_class
            != HistoricalExecutionClass.MANAGED_PROCESS
        ):
            continue
        if (
            span.invocation_id is not None
            and span.invocation_id != envelope.invocation.invocation_id
        ):
            raise ReplayIncompatibleError(
                "Historical execution evidence does not match its trace span."
            )
        existing = catalog.get(envelope.invocation.invocation_id)
        if existing is not None and (
            existing.historical_execution is None
            or existing.historical_execution.evidence_sha256
            != evidence.evidence_sha256
        ):
            raise ReplayIncompatibleError(
                "Historical execution evidence is ambiguous for one invocation."
            )
        catalog[envelope.invocation.invocation_id] = envelope
    return catalog


class ToolReplayPlanner:
    """Reevaluate historical tool behavior under the current descriptor/policy."""

    def __init__(self, policy_engine: ToolPolicyEngine | Any | None = None):
        self.policy_engine = policy_engine or ToolPolicyEngine()

    def assess(
        self,
        envelope: CapturedToolEnvelope,
        current_descriptor: ToolDescriptor,
        *,
        mode: ReplayMode,
        isolated_workspace: str | Path = "[ISOLATED_REPLAY_WORKSPACE]",
        historical_authorization: CapturedToolEnvelope | None = None,
    ) -> ToolReplayAssessment:
        authenticated = _authenticated_process_envelope(
            envelope,
            historical_authorization,
        )
        if authenticated is not None:
            return self._assess_authenticated_process(
                envelope,
                authenticated,
                current_descriptor,
                mode=mode,
                isolated_workspace=isolated_workspace,
            )
        if requires_authenticated_process_replay(envelope):
            return _historical_process_rejection(
                envelope,
                descriptor_drift=(
                    _descriptor_sha256(envelope.descriptor)
                    != _descriptor_sha256(current_descriptor)
                ),
                capability_drift=False,
                policy_drift=True,
                reason=(
                    "Authenticated historical process execution evidence is missing."
                ),
            )
        historical = envelope.policy_decision
        descriptor_drift = (
            envelope.descriptor.version != current_descriptor.version
            or envelope.descriptor.protocol_version
            != current_descriptor.protocol_version
            or envelope.descriptor.argument_schema
            != current_descriptor.argument_schema
            or envelope.descriptor.output_schema
            != current_descriptor.output_schema
        )
        historical_descriptor_names = {
            capability.name for capability in envelope.descriptor.capabilities
        }
        current_descriptor_names = {
            capability.name for capability in current_descriptor.capabilities
        }
        descriptor_capability_expansion = bool(
            current_descriptor_names - historical_descriptor_names
        )
        descriptor_drift = descriptor_drift or (
            historical_descriptor_names != current_descriptor_names
        )
        private_roots = _descriptor_private_roots(current_descriptor)
        policy_workspace = _policy_workspace_identity(
            current_descriptor,
            fallback=isolated_workspace,
        )
        replay_invocation = envelope.invocation.model_copy(
            update={
                "tool_version": current_descriptor.version,
                "protocol_version": current_descriptor.protocol_version,
                "context": ToolInvocationContext(
                    workspace_identity=policy_workspace,
                    worktree_identity=policy_workspace,
                    caller_role=envelope.invocation.context.caller_role,
                    workspace_trusted=True,
                    provider_consented=(
                        envelope.invocation.context.provider_consented
                    ),
                    policy_context=envelope.invocation.context.policy_context,
                ),
            }
        )
        historical_capabilities = {
            item.model_dump_json()
            for item in envelope.invocation.requested_capabilities
        }
        capability_drift = descriptor_capability_expansion
        expanded_capabilities = descriptor_capability_expansion
        try:
            current_required = derive_replay_required_capabilities(
                replay_invocation,
                current_descriptor,
                captured_descriptor=envelope.descriptor,
                captured_capabilities=(
                    envelope.invocation.requested_capabilities
                ),
            )
            safe_current_required = _sanitize_capabilities(
                current_required,
                private_roots=private_roots,
            )
            current_capabilities = {
                item.model_dump_json() for item in safe_current_required
            }
            capability_drift = capability_drift or (
                historical_capabilities != current_capabilities
            )
            expanded_capabilities = expanded_capabilities or bool(
                current_capabilities - historical_capabilities
            )
            replay_invocation = replay_invocation.model_copy(
                update={"requested_capabilities": current_required}
            )
            current = self.policy_engine.evaluate(
                replay_invocation,
                current_descriptor,
            )
            current = _sanitize_current_policy_decision(
                current,
                replay_invocation,
                safe_current_required,
                private_roots=private_roots,
                evaluated_at=historical.evaluated_at,
            )
        except Exception as exc:
            return ToolReplayAssessment(
                invocation_id=envelope.invocation.invocation_id,
                strategy=ToolReplayStrategy.REJECT,
                historical_outcome=historical.outcome,
                descriptor_drift=True,
                capability_drift=capability_drift,
                policy_drift=True,
                fresh_authorization_required=expanded_capabilities,
                reasons=[
                    "Current descriptor or policy rejected historical invocation validation.",
                    f"Safe error category: {type(exc).__name__}.",
                ],
            )
        policy_drift = (
            current.outcome != historical.outcome
            or current.rule_id != historical.rule_id
            or capability_fingerprint(current.constraints)
            != capability_fingerprint(historical.constraints)
        )
        approval_compatible = _approval_compatible(envelope)
        strategy, fresh_authorization, reasons = _strategy(
            envelope,
            current_descriptor,
            current,
            mode=mode,
            descriptor_drift=descriptor_drift,
            capability_drift=capability_drift,
            expanded_capabilities=expanded_capabilities,
            policy_drift=policy_drift,
            approval_compatible=approval_compatible,
        )
        return ToolReplayAssessment(
            invocation_id=envelope.invocation.invocation_id,
            strategy=strategy,
            historical_outcome=historical.outcome,
            current_outcome=current.outcome,
            current_decision=current,
            descriptor_drift=descriptor_drift,
            capability_drift=capability_drift,
            policy_drift=policy_drift,
            approval_compatible_for_substitution=approval_compatible,
            fresh_authorization_required=fresh_authorization,
            reasons=reasons,
        )

    def _assess_authenticated_process(
        self,
        envelope: CapturedToolEnvelope,
        authenticated: CapturedToolEnvelope,
        current_descriptor: ToolDescriptor,
        *,
        mode: ReplayMode,
        isolated_workspace: str | Path,
    ) -> ToolReplayAssessment:
        del mode  # Historical process replay is always captured-result only.
        historical = envelope.policy_decision
        evidence = authenticated.historical_execution
        assert evidence is not None
        descriptor_drift = (
            _descriptor_sha256(authenticated.descriptor)
            != _descriptor_sha256(current_descriptor)
        )
        if (
            evidence.descriptor_contract_sha256
            != _descriptor_contract_sha256(current_descriptor)
        ):
            return _historical_process_rejection(
                envelope,
                descriptor_drift=True,
                capability_drift=True,
                policy_drift=True,
                reason=(
                    "Current tool contract is incompatible with the authenticated "
                    "historical descriptor."
                ),
            )
        if not _same_historical_invocation(envelope, authenticated):
            return _historical_process_rejection(
                envelope,
                descriptor_drift=descriptor_drift,
                capability_drift=True,
                policy_drift=True,
                reason=(
                    "Tool span does not match the authenticated historical invocation."
                ),
            )

        replay_root = str(isolated_workspace)
        replay_descriptor = _relocate_process_descriptor(
            authenticated.descriptor,
            replay_root,
        )
        replay_invocation = _relocate_process_invocation(
            envelope.invocation,
            replay_root,
        )
        private_roots = (replay_root,)
        try:
            current_required = derive_replay_required_capabilities(
                replay_invocation,
                replay_descriptor,
                captured_descriptor=replay_descriptor,
                captured_capabilities=(
                    replay_invocation.requested_capabilities
                ),
            )
            safe_current_required = _sanitize_capabilities(
                current_required,
                private_roots=private_roots,
            )
            if safe_current_required != envelope.invocation.requested_capabilities:
                raise ValueError(
                    "Historical process capabilities no longer derive exactly."
                )
            replay_invocation = replay_invocation.model_copy(
                update={"requested_capabilities": current_required}
            )
            base_decision = self.policy_engine.evaluate(
                replay_invocation,
                replay_descriptor,
            )
            safe_base = _sanitize_current_policy_decision(
                base_decision,
                replay_invocation,
                safe_current_required,
                private_roots=private_roots,
                evaluated_at=historical.evaluated_at,
            )
            approval_compatible = _approval_compatible(authenticated)
            current = safe_base
            if base_decision.outcome == ToolPolicyOutcome.REQUIRE_APPROVAL:
                if not approval_compatible or authenticated.approval is None:
                    return _historical_process_rejection(
                        envelope,
                        descriptor_drift=descriptor_drift,
                        capability_drift=False,
                        policy_drift=True,
                        fresh_authorization_required=True,
                        reason=(
                            "Authenticated historical process evidence omits the "
                            "exact approval required by current policy."
                        ),
                    )
                if not _approval_request_matches_policy(
                    authenticated.approval,
                    safe_base,
                ):
                    return _historical_process_rejection(
                        envelope,
                        descriptor_drift=descriptor_drift,
                        capability_drift=False,
                        policy_drift=True,
                        fresh_authorization_required=True,
                        reason=(
                            "Historical approval policy scope differs from current policy."
                        ),
                    )
                replay_approval = _relocate_replay_approval(
                    authenticated.approval,
                    replay_invocation,
                    replay_descriptor,
                    base_decision,
                )
                approved_decision = self.policy_engine.evaluate(
                    replay_invocation,
                    replay_descriptor,
                    approval=replay_approval,
                )
                current = _sanitize_current_policy_decision(
                    approved_decision,
                    replay_invocation,
                    safe_current_required,
                    private_roots=private_roots,
                    evaluated_at=historical.evaluated_at,
                )
            if current.outcome == ToolPolicyOutcome.DENY:
                return _historical_process_rejection(
                    envelope,
                    descriptor_drift=descriptor_drift,
                    capability_drift=False,
                    policy_drift=True,
                    reason="Current policy denies the historical process scope.",
                )
        except Exception as exc:
            return _historical_process_rejection(
                envelope,
                descriptor_drift=descriptor_drift,
                capability_drift=True,
                policy_drift=True,
                reason=(
                    "Authenticated historical process validation failed with safe "
                    f"category {type(exc).__name__}."
                ),
            )

        pending_approval = envelope.result is None and (
            historical.outcome == ToolPolicyOutcome.REQUIRE_APPROVAL
        )
        current_for_span = safe_base if pending_approval else current
        policy_drift = (
            current_for_span.outcome != historical.outcome
            or current_for_span.rule_id != historical.rule_id
            or capability_fingerprint(current_for_span.constraints)
            != capability_fingerprint(historical.constraints)
        )
        reasons = [
            "Authenticated historical execution envelope validated against the current tool contract."
        ]
        if pending_approval:
            reasons.append(
                "Historical approval transition substituted from the exact completed invocation."
            )
        else:
            reasons.append(
                "Captured process result reused; historical executable was not dispatched."
            )
        return ToolReplayAssessment(
            invocation_id=envelope.invocation.invocation_id,
            strategy=ToolReplayStrategy.REUSE_CAPTURED,
            historical_outcome=historical.outcome,
            current_outcome=current_for_span.outcome,
            current_decision=current_for_span,
            descriptor_drift=descriptor_drift,
            capability_drift=False,
            policy_drift=policy_drift,
            approval_compatible_for_substitution=approval_compatible,
            fresh_authorization_required=False,
            historical_authorization_validated=True,
            historical_executable=evidence.executable,
            captured_result_reused=envelope.result is not None,
            process_dispatched=False,
            reasons=reasons,
        )


def _authenticated_process_envelope(
    envelope: CapturedToolEnvelope,
    historical_authorization: CapturedToolEnvelope | None,
) -> CapturedToolEnvelope | None:
    candidates = [envelope]
    if historical_authorization is not None:
        candidates.append(historical_authorization)
    authenticated = [
        candidate
        for candidate in candidates
        if candidate.historical_execution is not None
        and candidate.historical_execution.execution_class
        == HistoricalExecutionClass.MANAGED_PROCESS
    ]
    if not authenticated:
        return None
    fingerprints = {
        candidate.historical_execution.evidence_sha256
        for candidate in authenticated
        if candidate.historical_execution is not None
    }
    if len(fingerprints) != 1:
        return None
    return authenticated[0]


def is_managed_process_invocation(invocation: ToolInvocation) -> bool:
    return any(
        capability.name in _PROCESS_CAPABILITIES
        for capability in invocation.requested_capabilities
    )


def requires_authenticated_process_replay(
    envelope: CapturedToolEnvelope,
) -> bool:
    return bool(
        envelope.envelope_version > _LEGACY_TOOL_ENVELOPE_VERSION
        and is_managed_process_invocation(envelope.invocation)
    )


def _same_historical_invocation(
    envelope: CapturedToolEnvelope,
    authenticated: CapturedToolEnvelope,
) -> bool:
    evidence = authenticated.historical_execution
    if evidence is None:
        return False
    return bool(
        envelope.invocation.invocation_id
        == authenticated.invocation.invocation_id
        and envelope.invocation.invocation_revision
        == authenticated.invocation.invocation_revision
        and invocation_identity_sha256(envelope.invocation)
        == evidence.invocation_sha256
        and _descriptor_sha256(envelope.descriptor)
        == evidence.descriptor_sha256
    )


def _relocate_process_descriptor(
    descriptor: ToolDescriptor,
    workspace: str,
) -> ToolDescriptor:
    capabilities = []
    for capability in descriptor.capabilities:
        scope = capability.scope
        relocated_scope = scope.model_copy(
            update={
                "roots": (workspace,) if scope.roots else (),
                "working_directories": (
                    (workspace,) if scope.working_directories else ()
                ),
            }
        )
        capabilities.append(
            capability.model_copy(update={"scope": relocated_scope})
        )
    return descriptor.model_copy(update={"capabilities": tuple(capabilities)})


def _relocate_process_invocation(
    invocation: ToolInvocation,
    workspace: str,
) -> ToolInvocation:
    capabilities = []
    for capability in invocation.requested_capabilities:
        scope = capability.scope
        relocated_scope = scope.model_copy(
            update={
                "roots": (workspace,) if scope.roots else (),
                "working_directories": (
                    (workspace,) if scope.working_directories else ()
                ),
            }
        )
        capabilities.append(
            capability.model_copy(update={"scope": relocated_scope})
        )
    return invocation.model_copy(
        update={
            "requested_capabilities": tuple(capabilities),
            "context": ToolInvocationContext(
                workspace_identity=workspace,
                worktree_identity=workspace,
                caller_role=invocation.context.caller_role,
                workspace_trusted=True,
                provider_consented=invocation.context.provider_consented,
                policy_context=invocation.context.policy_context,
            ),
        }
    )


def _approval_request_matches_policy(
    approval: ToolApprovalGrant,
    decision: ToolPolicyDecision,
) -> bool:
    request = approval.request
    return bool(
        request.policy_rule == decision.rule_id
        and request.reason == decision.reason
        and request.proposed_constraints == decision.constraints
    )


def _relocate_replay_approval(
    approval: ToolApprovalGrant,
    invocation: ToolInvocation,
    descriptor: ToolDescriptor,
    decision: ToolPolicyDecision,
) -> ToolApprovalGrant:
    request = build_tool_approval_request(
        invocation,
        descriptor,
        decision,
        approval_id=approval.approval_id,
        # Expiry was validated at the historical decision time. Replay never
        # dispatches, so wall-clock passage must not create a new approval.
        expires_at=None,
    ).model_copy(update={"created_at": approval.request.created_at})
    payload = approval.model_dump(mode="json")
    payload.update(
        {
            "request": request.model_dump(mode="json"),
            "binding_sha256": approval_binding_sha256(request, invocation),
        }
    )
    return ToolApprovalGrant.model_validate(payload)


def _historical_process_rejection(
    envelope: CapturedToolEnvelope,
    *,
    descriptor_drift: bool,
    capability_drift: bool,
    policy_drift: bool,
    reason: str,
    fresh_authorization_required: bool = False,
) -> ToolReplayAssessment:
    return ToolReplayAssessment(
        invocation_id=envelope.invocation.invocation_id,
        strategy=ToolReplayStrategy.REJECT,
        historical_outcome=envelope.policy_decision.outcome,
        descriptor_drift=descriptor_drift,
        capability_drift=capability_drift,
        policy_drift=policy_drift,
        fresh_authorization_required=fresh_authorization_required,
        reasons=[reason],
    )


def _descriptor_private_roots(
    descriptor: ToolDescriptor,
) -> tuple[str, ...]:
    candidates = {
        value
        for capability in descriptor.capabilities
        for value in (
            *capability.scope.roots,
            *capability.scope.working_directories,
        )
    }
    return tuple(
        sorted(
            (
                value
                for value in candidates
                if Path(value).expanduser().is_absolute()
            ),
            key=len,
            reverse=True,
        )
    )


def _policy_workspace_identity(
    descriptor: ToolDescriptor,
    *,
    fallback: str | Path,
) -> str:
    for capability in descriptor.capabilities:
        for value in (
            *capability.scope.working_directories,
            *capability.scope.roots,
        ):
            if Path(value).expanduser().is_absolute():
                return value
    return str(fallback)


def _sanitize_capabilities(
    capabilities: tuple[ToolCapability, ...],
    *,
    private_roots: tuple[str, ...],
) -> tuple[ToolCapability, ...]:
    return tuple(
        ToolCapability.model_validate(
            sanitize_document(
                capability.model_dump(mode="json"),
                private_roots=private_roots,
            ).value
        )
        for capability in capabilities
    )


def _sanitize_current_policy_decision(
    decision: ToolPolicyDecision,
    invocation: ToolInvocation,
    safe_capabilities: tuple[ToolCapability, ...],
    *,
    private_roots: tuple[str, ...],
    evaluated_at: datetime,
) -> ToolPolicyDecision:
    payload = sanitize_document(
        safe_protocol_dict(decision),
        private_roots=private_roots,
    ).value
    safe_arguments = sanitize_document(
        invocation.arguments,
        private_roots=private_roots,
    ).value
    payload.update(
        {
            "capability_fingerprint": capability_fingerprint(
                safe_capabilities
            ),
            "arguments_sha256": sha256_json(safe_arguments),
            "evaluated_at": evaluated_at,
        }
    )
    return ToolPolicyDecision.model_validate(payload)


def _strategy(
    envelope: CapturedToolEnvelope,
    descriptor: ToolDescriptor,
    current: ToolPolicyDecision,
    *,
    mode: ReplayMode,
    descriptor_drift: bool,
    capability_drift: bool,
    expanded_capabilities: bool,
    policy_drift: bool,
    approval_compatible: bool,
) -> tuple[ToolReplayStrategy, bool, list[str]]:
    if current.outcome == ToolPolicyOutcome.DENY:
        return (
            ToolReplayStrategy.REJECT,
            False,
            ["Current policy denies the historical tool behavior."],
        )
    if expanded_capabilities:
        return (
            ToolReplayStrategy.REJECT,
            True,
            ["Current descriptor expands capabilities and requires fresh authorization."],
        )
    capabilities = {
        capability.name for capability in envelope.invocation.requested_capabilities
    }
    external = bool(capabilities & _EXTERNAL_CAPABILITIES)
    mutating = bool(capabilities & _MUTATING_CAPABILITIES)
    if mode in {ReplayMode.OFFLINE, ReplayMode.SIMULATE} and mutating:
        return (
            ToolReplayStrategy.SIMULATE_MUTATION,
            False,
            ["Mutation is simulated in providerless replay mode."],
        )
    if policy_drift and current.outcome == ToolPolicyOutcome.REQUIRE_APPROVAL:
        return (
            ToolReplayStrategy.REJECT,
            True,
            ["Current policy newly requires approval for this behavior."],
        )
    if external:
        if envelope.result is not None:
            return (
                ToolReplayStrategy.REUSE_CAPTURED,
                False,
                ["Captured external tool envelope is reused without a network call."],
            )
        return (
            ToolReplayStrategy.REJECT,
            False,
            ["External tool behavior has no captured result."],
        )
    if mutating:
        if (
            descriptor.safety == ToolSafetyClassification.SAFE
            and descriptor.idempotent
            and not descriptor_drift
            and not capability_drift
        ):
            return (
                ToolReplayStrategy.RERUN_SANDBOX,
                current.outcome == ToolPolicyOutcome.REQUIRE_APPROVAL,
                ["Deterministic mutation may rerun only in an isolated sandbox."],
            )
        return (
            ToolReplayStrategy.SIMULATE_MUTATION,
            False,
            ["Mutation is simulated because its current contract is not exactly stable."],
        )
    if envelope.result is not None:
        return (
            ToolReplayStrategy.REUSE_CAPTURED,
            False,
            ["Captured pure-read result may be reused deterministically."],
        )
    if current.outcome == ToolPolicyOutcome.REQUIRE_APPROVAL:
        return (
            ToolReplayStrategy.REJECT,
            True,
            [
                "Rerunning this historical invocation requires fresh scoped authorization."
            ],
        )
    return (
        ToolReplayStrategy.RERUN_SANDBOX,
        False,
        ["Safe deterministic tool may rerun in an isolated sandbox."],
    )


def _approval_compatible(envelope: CapturedToolEnvelope) -> bool:
    approval = envelope.approval
    if approval is None:
        return False
    request = approval.request
    try:
        validate_tool_approval(
            approval,
            envelope.invocation,
            envelope.descriptor,
            now=approval.decided_at,
        )
    except Exception:
        return False
    return bool(
        approval.disposition == ToolApprovalDisposition.APPROVED
        and request.invocation_id == envelope.invocation.invocation_id
        and request.invocation_revision == envelope.invocation.invocation_revision
        and request.tool_name == envelope.invocation.tool_name
        and request.tool_version == envelope.descriptor.version
        and request.protocol_version == envelope.descriptor.protocol_version
        and request.capability_fingerprint
        == capability_fingerprint(envelope.invocation.requested_capabilities)
        and request.arguments_sha256 == envelope.policy_decision.arguments_sha256
        and (
            envelope.result is None
            or envelope.result.approval_id == approval.approval_id
        )
    )


__all__ = [
    "TOOL_ENVELOPE_MEDIA_TYPE",
    "TOOL_ENVELOPE_VERSION",
    "HISTORICAL_EXECUTION_ENVELOPE_VERSION",
    "CapturedToolEnvelope",
    "HistoricalExecutionEnvelope",
    "HistoricalExecutionClass",
    "HistoricalExecutablePathClass",
    "ToolReplayAssessment",
    "ToolReplayPlanner",
    "capture_tool_envelope",
    "historical_execution_catalog",
    "is_managed_process_invocation",
    "load_tool_envelope",
    "requires_authenticated_process_replay",
    "sanitize_replay_policy_decision",
]
