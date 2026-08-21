from __future__ import annotations

import hashlib
import sys
from pathlib import Path

import pytest
from hypothesis import given, settings, strategies as st

from agentbus.execution.models import RunRecord, TaskSpec
from agentbus.execution.state_store import StateStore
from agentbus.policy import (
    ToolApprovalBindingError,
    ToolApprovalDisposition,
    ToolPolicyEngine,
    build_tool_approval_request,
    decide_tool_approval,
    validate_tool_approval,
)
from agentbus.sandbox.platform import ExecutableCatalog
from agentbus.tools.capabilities import (
    derive_required_capabilities,
    require_expected_capabilities,
)
from agentbus.tools.descriptors import descriptor_map
from agentbus.tools.protocol import (
    CapabilityScope,
    StructuredToolCall,
    ToolCapability,
    ToolCapabilityEscalationError,
    ToolCapabilityName,
    ToolDescriptor,
    ToolInvocation,
    ToolInvocationContext,
    ToolInvocationStatus,
    ToolVersion,
    capability_set_contains,
    validate_invocation_against_descriptor,
)
from agentbus.tools.runtime import build_managed_tool_runtime


FUZZ_SETTINGS = settings(
    max_examples=100,
    deadline=None,
    derandomize=True,
    database=None,
)
_ROOT = (Path.cwd() / ".agentbus-policy-fuzz-workspace").resolve()
_TOKEN = st.text(
    alphabet="abcdefghijklmnopqrstuvwxyz0123456789",
    min_size=1,
    max_size=24,
)


class _RejectIfEvaluated:
    def __init__(self) -> None:
        self.calls = 0

    def evaluate(self, _facts):
        self.calls += 1
        raise AssertionError("capability escalation reached policy evaluation")


@FUZZ_SETTINGS
@given(
    case=st.sampled_from(
        (
            "outside_root",
            "extra_pattern",
            "extra_executable",
            "network_enable",
            "extra_destination",
            "undeclared_capability",
        )
    ),
    token=_TOKEN,
)
def test_capability_escalations_fail_before_policy_evaluation(
    case: str,
    token: str,
) -> None:
    descriptor, invocation = _escalation_case(case, token)
    sentinel = _RejectIfEvaluated()
    engine = ToolPolicyEngine()
    engine.evaluator = sentinel

    assert capability_set_contains(
        descriptor.capabilities,
        invocation.requested_capabilities,
    ) is False
    with pytest.raises(ToolCapabilityEscalationError):
        validate_invocation_against_descriptor(invocation, descriptor)
    with pytest.raises(ToolCapabilityEscalationError):
        engine.evaluate(invocation, descriptor)
    assert sentinel.calls == 0


def _escalation_case(
    case: str,
    token: str,
) -> tuple[ToolDescriptor, ToolInvocation]:
    root = (_ROOT / f"allowed-{token}").resolve()
    name = ToolCapabilityName.FILESYSTEM_READ
    allowed = CapabilityScope(roots=(str(root),))
    requested = allowed

    if case == "outside_root":
        requested = CapabilityScope(
            roots=(str(root.parent / f"outside-{token}"),)
        )
    elif case == "extra_pattern":
        allowed = CapabilityScope(
            roots=(str(root),),
            patterns=(f"src/{token}/**",),
        )
        requested = CapabilityScope(
            roots=allowed.roots,
            patterns=allowed.patterns + (f"tests/{token}/**",),
        )
    elif case == "extra_executable":
        name = ToolCapabilityName.PROCESS_EXECUTE
        allowed = CapabilityScope(executables=("python",))
        requested = CapabilityScope(
            executables=("python", f"runner-{token}")
        )
    elif case == "network_enable":
        name = ToolCapabilityName.PROCESS_NETWORK
        allowed = CapabilityScope(network_allowed=False)
        requested = CapabilityScope(network_allowed=True)
    elif case == "extra_destination":
        name = ToolCapabilityName.PROCESS_NETWORK
        allowed = CapabilityScope(
            network_allowed=True,
            network_destinations=("api.example.invalid",),
        )
        requested = CapabilityScope(
            network_allowed=True,
            network_destinations=(
                "api.example.invalid",
                f"{token}.example.invalid",
            ),
        )

    declared = ToolCapability(name=name, scope=allowed)
    requested_capability = ToolCapability(name=name, scope=requested)
    if case == "undeclared_capability":
        requested_capability = ToolCapability(
            name=ToolCapabilityName.FILESYSTEM_WRITE,
            scope=allowed,
        )
    descriptor = ToolDescriptor(
        name="fuzz.policy",
        version=ToolVersion(major=1),
        description="Validate adversarial capability requests.",
        capabilities=(declared,),
        argument_schema={"type": "object"},
        output_schema={"type": "object"},
    )
    invocation = ToolInvocation(
        invocation_id=f"inv-{token}",
        run_id="run-fuzz",
        task_id="task-fuzz",
        tool_name=descriptor.name,
        tool_version=descriptor.version,
        arguments={},
        requested_capabilities=(requested_capability,),
        context=ToolInvocationContext(
            workspace_identity=str(root),
            worktree_identity=str(root),
            caller_role="coder",
            workspace_trusted=True,
            provider_consented=False,
        ),
    )
    return descriptor, invocation


@FUZZ_SETTINGS
@given(token=_TOKEN)
def test_runtime_capability_derivation_cannot_be_widened_by_model_claims(
    token: str,
) -> None:
    descriptor = descriptor_map(workspace=_ROOT)["filesystem.write"]
    relative_path = f"src/{token}.py"
    provisional = _tool_invocation(
        descriptor,
        arguments={"path": relative_path, "content": "bounded\n"},
        requested_capabilities=descriptor.capabilities,
        invocation_id=f"derive-{token}",
    )

    required = derive_required_capabilities(provisional, descriptor)
    first = required[0]
    widened_scope = first.scope.model_copy(
        update={
            "affected_paths": first.scope.affected_paths
            + (f"src/{token}-extra.py",)
        }
    )
    widened = (
        ToolCapability(name=first.name, scope=widened_scope),
        *required[1:],
    )

    assert all(
        capability.scope.affected_paths == (relative_path,)
        for capability in required
    )
    with pytest.raises(
        ToolCapabilityEscalationError,
        match="exactly match",
    ):
        require_expected_capabilities(widened, required)


@FUZZ_SETTINGS
@given(
    mutation=st.sampled_from(
        (
            "scope",
            "budget",
            "arguments",
            "revision",
            "workspace",
            "worktree",
            "idempotency",
            "cancellation",
        )
    ),
    token=_TOKEN,
)
def test_approval_binding_rejects_every_post_authorization_mutation(
    mutation: str,
    token: str,
) -> None:
    invocation, descriptor, grant = _approved_delete(token)
    changed = _mutate_approved_invocation(invocation, mutation, token)

    validate_tool_approval(grant, invocation, descriptor)
    with pytest.raises(ToolApprovalBindingError, match="changed"):
        validate_tool_approval(grant, changed, descriptor)


def _approved_delete(token: str):
    descriptor = descriptor_map(workspace=_ROOT)["filesystem.delete"]
    provisional = _tool_invocation(
        descriptor,
        arguments={
            "path": f"src/{token}.py",
            "expected_sha256": "0" * 64,
        },
        requested_capabilities=descriptor.capabilities,
        invocation_id=f"approval-{token}",
        idempotency_key=f"delete-{token}",
    )
    required = derive_required_capabilities(provisional, descriptor)
    invocation = provisional.model_copy(
        update={"requested_capabilities": required}
    )
    decision = ToolPolicyEngine().evaluate(invocation, descriptor)
    request = build_tool_approval_request(
        invocation,
        descriptor,
        decision,
        approval_id=f"grant-{token}",
    )
    grant = decide_tool_approval(
        request,
        invocation,
        disposition=ToolApprovalDisposition.APPROVED,
    )
    return invocation, descriptor, grant


def _mutate_approved_invocation(
    invocation: ToolInvocation,
    mutation: str,
    token: str,
) -> ToolInvocation:
    if mutation == "scope":
        original = invocation.requested_capabilities[0]
        changed_scope = original.scope.model_copy(
            update={
                "affected_paths": original.scope.affected_paths
                + (f"src/{token}-other.py",)
            }
        )
        return invocation.model_copy(
            update={
                "requested_capabilities": (
                    ToolCapability(
                        name=original.name,
                        scope=changed_scope,
                    ),
                )
            }
        )
    if mutation == "budget":
        budget = invocation.resource_budget.model_copy(
            update={
                "wall_clock_seconds": (
                    invocation.resource_budget.wall_clock_seconds + 1
                )
            }
        )
        return invocation.model_copy(update={"resource_budget": budget})
    if mutation == "arguments":
        return invocation.model_copy(
            update={
                "arguments": invocation.arguments
                | {"expected_sha256": "1" * 64}
            }
        )
    if mutation == "revision":
        return invocation.model_copy(
            update={"invocation_revision": invocation.invocation_revision + 1}
        )
    if mutation in {"workspace", "worktree"}:
        context = invocation.context.model_copy(
            update={
                f"{mutation}_identity": str(
                    _ROOT / f"changed-{mutation}-{token}"
                )
            }
        )
        return invocation.model_copy(update={"context": context})
    if mutation == "idempotency":
        return invocation.model_copy(
            update={"idempotency_key": f"changed-{token}"}
        )
    return invocation.model_copy(
        update={
            "cancellation_revision": invocation.cancellation_revision + 1
        }
    )


def _tool_invocation(
    descriptor: ToolDescriptor,
    *,
    arguments: dict,
    requested_capabilities: tuple[ToolCapability, ...],
    invocation_id: str,
    idempotency_key: str | None = None,
) -> ToolInvocation:
    return ToolInvocation(
        invocation_id=invocation_id,
        run_id="run-fuzz",
        task_id="task-fuzz",
        tool_name=descriptor.name,
        tool_version=descriptor.version,
        arguments=arguments,
        requested_capabilities=requested_capabilities,
        context=ToolInvocationContext(
            workspace_identity=str(_ROOT),
            worktree_identity=str(_ROOT),
            caller_role="coder",
            workspace_trusted=True,
            provider_consented=False,
        ),
        idempotency_key=idempotency_key,
    )


def test_delete_has_no_filesystem_effect_before_approval(
    tmp_path: Path,
) -> None:
    target = tmp_path / "keep.py"
    original = b"keep = True\n"
    target.write_bytes(original)
    runtime, _ = _managed_runtime(tmp_path)
    call = _runtime_call(
        runtime,
        "filesystem.delete",
        {
            "path": "keep.py",
            "expected_sha256": hashlib.sha256(original).hexdigest(),
        },
    )

    try:
        pending = runtime.invoke(
            call,
            run_id="run-fuzz",
            task_id="task-fuzz",
            caller_role="coder",
            workspace_trusted=True,
            provider_consented=False,
            invocation_id="inv-delete-pending",
        )

        assert pending.awaiting_approval is True
        assert pending.record.status == ToolInvocationStatus.AWAITING_APPROVAL
        assert target.read_bytes() == original
    finally:
        runtime.close()


def test_sensitive_create_has_no_filesystem_effect_before_approval(
    tmp_path: Path,
) -> None:
    target = tmp_path / ".github" / "workflows" / "adversarial.yml"
    runtime, _ = _managed_runtime(tmp_path)
    call = _runtime_call(
        runtime,
        "filesystem.create",
        {
            "path": ".github/workflows/adversarial.yml",
            "content": "name: must-not-exist-before-approval\n",
        },
    )

    try:
        pending = runtime.invoke(
            call,
            run_id="run-fuzz",
            task_id="task-fuzz",
            caller_role="coder",
            workspace_trusted=True,
            provider_consented=False,
            invocation_id="inv-create-pending",
        )

        assert pending.awaiting_approval is True
        assert pending.record.status == ToolInvocationStatus.AWAITING_APPROVAL
        assert target.exists() is False
    finally:
        runtime.close()


def _managed_runtime(root: Path):
    store = StateStore(root / "state.db")
    store.create_run_with_tasks(
        RunRecord(
            run_id="run-fuzz",
            original_task="Fuzz policy escalation",
            model="fake",
            workspace=str(root.resolve()),
            planner_output={"goal": "test", "steps": []},
            graph_data={"version": 1, "tasks": []},
        ),
        [
            TaskSpec(
                task_id="task-fuzz",
                title="Reject capability escalation",
                description="Prove authorization precedes side effects.",
            )
        ],
    )
    runtime = build_managed_tool_runtime(
        workspace=root,
        state_store=store,
        executable_catalog=ExecutableCatalog({"python": sys.executable}),
    )
    return runtime, store


def _runtime_call(
    runtime,
    tool_name: str,
    arguments: dict,
) -> StructuredToolCall:
    descriptor = runtime.registry.descriptor(tool_name)
    broad = StructuredToolCall(
        tool_name=tool_name,
        arguments=arguments,
        expected_capabilities=descriptor.capabilities,
    )
    provisional = runtime.invocation_from_call(
        broad,
        run_id="run-fuzz",
        task_id="task-fuzz",
        caller_role="coder",
        workspace_trusted=True,
        provider_consented=False,
        invocation_id="inv-provisional",
    )
    required = derive_required_capabilities(provisional, descriptor)
    return broad.model_copy(update={"expected_capabilities": required})
