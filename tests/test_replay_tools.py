from datetime import datetime, timezone
from pathlib import Path

from agentbus.replay import (
    ReplayMode,
    ToolReplayPlanner,
    ToolReplayStrategy,
    capture_tool_envelope,
    load_tool_envelope,
)
from agentbus.tools.protocol import (
    CapabilityScope,
    ToolCapability,
    ToolCapabilityName,
    ToolDescriptor,
    ToolInvocation,
    ToolInvocationContext,
    ToolPolicyDecision,
    ToolPolicyOutcome,
    ToolSafetyClassification,
    ToolVersion,
    capability_fingerprint,
    sha256_json,
)
from agentbus.trace import ContentAddressedStore


NOW = datetime(2026, 1, 2, tzinfo=timezone.utc)


def _capability(name=ToolCapabilityName.FILESYSTEM_READ):
    return ToolCapability(
        name=name,
        scope=CapabilityScope(roots=("workspace",)),
    )


def _descriptor(*capabilities, version=ToolVersion(major=1)):
    return ToolDescriptor(
        name="filesystem.read",
        version=version,
        description="Read a file",
        capabilities=tuple(capabilities or [_capability()]),
        argument_schema={
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"],
            "additionalProperties": False,
        },
        output_schema={"type": "object"},
        safety=ToolSafetyClassification.SAFE,
        idempotent=True,
    )


def _invocation(descriptor):
    return ToolInvocation(
        invocation_id="tool-1",
        run_id="run-1",
        task_id="step-1",
        tool_name=descriptor.name,
        tool_version=descriptor.version,
        arguments={"path": "workspace/app.py"},
        requested_capabilities=descriptor.capabilities,
        context=ToolInvocationContext(
            workspace_identity="C:/Users/Alice/private/source",
            worktree_identity="C:/Users/Alice/private/source",
            caller_role="coder",
            workspace_trusted=True,
        ),
        requested_at=NOW,
    )


def _decision(invocation, outcome=ToolPolicyOutcome.ALLOW):
    return ToolPolicyDecision(
        outcome=outcome,
        rule_id="allow.safe",
        reason="safe",
        invocation_id=invocation.invocation_id,
        invocation_revision=invocation.invocation_revision,
        capability_fingerprint=capability_fingerprint(
            invocation.requested_capabilities
        ),
        arguments_sha256=sha256_json(invocation.arguments),
    )


def test_tool_envelope_is_sanitized_and_round_trips(tmp_path: Path) -> None:
    store = ContentAddressedStore(
        tmp_path / "objects",
        private_roots=["C:/Users/Alice/private/source"],
    )
    descriptor = _descriptor()
    invocation = _invocation(descriptor)
    reference = capture_tool_envelope(
        store,
        descriptor=descriptor,
        invocation=invocation,
        policy_decision=_decision(invocation),
        producing_span_id="tool-span",
        reference_id="tool-envelope",
    )

    payload = store.get(reference.sha256).data
    envelope = load_tool_envelope(store, reference.sha256)

    assert b"C:/Users/Alice/private/source" not in payload
    assert envelope.invocation.arguments == {"path": "workspace/app.py"}


def test_current_policy_is_evaluated_and_safe_read_can_rerun() -> None:
    descriptor = _descriptor()
    invocation = _invocation(descriptor)
    from agentbus.replay.tools import CapturedToolEnvelope

    envelope = CapturedToolEnvelope(
        descriptor=descriptor,
        invocation=invocation,
        policy_decision=_decision(invocation),
    )

    calls = []

    class AllowPolicy:
        def evaluate(self, replay_invocation, current_descriptor):
            calls.append(replay_invocation)
            return _decision(replay_invocation)

    assessment = ToolReplayPlanner(AllowPolicy()).assess(
        envelope,
        descriptor,
        mode=ReplayMode.OFFLINE,
    )

    assert len(calls) == 1
    assert calls[0].context.provider_consented is False
    assert assessment.current_outcome == ToolPolicyOutcome.ALLOW
    assert assessment.strategy == ToolReplayStrategy.RERUN_SANDBOX
    assert assessment.fresh_authorization_required is False


def test_expanded_capabilities_require_fresh_authorization() -> None:
    historical_descriptor = _descriptor()
    invocation = _invocation(historical_descriptor)
    from agentbus.replay.tools import CapturedToolEnvelope

    envelope = CapturedToolEnvelope(
        descriptor=historical_descriptor,
        invocation=invocation,
        policy_decision=_decision(invocation),
    )
    expanded = _descriptor(
        _capability(),
        _capability(ToolCapabilityName.FILESYSTEM_WRITE),
        version=ToolVersion(major=2),
    )

    assessment = ToolReplayPlanner().assess(
        envelope,
        expanded,
        mode=ReplayMode.OFFLINE,
    )

    assert assessment.capability_drift is True
    assert assessment.fresh_authorization_required is True
    assert assessment.strategy == ToolReplayStrategy.REJECT


def test_policy_drift_is_reported_and_new_denial_blocks_replay() -> None:
    descriptor = _descriptor()
    invocation = _invocation(descriptor)
    from agentbus.replay.tools import CapturedToolEnvelope

    envelope = CapturedToolEnvelope(
        descriptor=descriptor,
        invocation=invocation,
        policy_decision=_decision(invocation),
    )

    class DenyPolicy:
        def evaluate(self, replay_invocation, current_descriptor):
            return _decision(replay_invocation, ToolPolicyOutcome.DENY)

    assessment = ToolReplayPlanner(DenyPolicy()).assess(
        envelope,
        descriptor,
        mode=ReplayMode.OFFLINE,
    )

    assert assessment.policy_drift is True
    assert assessment.current_outcome == ToolPolicyOutcome.DENY
    assert assessment.strategy == ToolReplayStrategy.REJECT
