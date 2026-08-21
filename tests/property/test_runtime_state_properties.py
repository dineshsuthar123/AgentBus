from __future__ import annotations

import sqlite3
import string
import tempfile
from pathlib import Path

import pytest
from hypothesis import given, settings, strategies as st

from agentbus.configuration import resolve_configuration
from agentbus.execution.models import RunRecord, TaskSpec
from agentbus.execution.state_store import StateStore, ToolInvocationConflictError
from agentbus.intelligence.migrations import (
    apply_migrations,
    schema_version,
    verify_schema,
)
from agentbus.intelligence.schema import LATEST_SCHEMA_VERSION
from agentbus.policy import (
    ToolApprovalBindingError,
    ToolApprovalDisposition,
    ToolPolicyEngine,
    build_tool_approval_request,
    decide_tool_approval,
    validate_tool_approval,
)
from agentbus.tools.descriptors import descriptor_map
from agentbus.tools.protocol import (
    CapabilityScope,
    ToolCapability,
    ToolCapabilityName,
    ToolDescriptor,
    ToolInvocation,
    ToolInvocationContext,
    ToolPolicyOutcome,
    idempotency_key_sha256,
    sha256_json,
)


PROPERTY_SETTINGS = settings(
    max_examples=32,
    deadline=None,
    derandomize=True,
    database=None,
)
_SAFE_COMPONENT = st.text(
    string.ascii_lowercase + string.digits + "_-",
    min_size=1,
    max_size=32,
).filter(lambda value: value.strip("_-") != "")


def _temporary_root(prefix: str):
    base = (Path.cwd() / ".tmp").resolve()
    base.mkdir(exist_ok=True)
    return tempfile.TemporaryDirectory(prefix=prefix, dir=base)


@PROPERTY_SETTINGS
@given(mask=st.integers(min_value=0, max_value=31), base=st.integers(1, 40))
def test_configuration_layers_always_resolve_highest_precedence(
    mask: int,
    base: int,
) -> None:
    with _temporary_root("property-config-") as temporary:
        root = Path(temporary)
        workspace = root / "workspace"
        workspace.mkdir()
        user = root / "user.toml"
        workspace_file = workspace / ".agentbus" / "config.toml"
        explicit = root / "explicit.toml"
        layers = (
            ("user", user, base),
            ("workspace", workspace_file, base + 1),
            ("explicit", explicit, base + 2),
        )
        active: list[tuple[str, int]] = []
        for index, (name, path, value) in enumerate(layers):
            if mask & (1 << index):
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(
                    f"[agentbus]\nmax_steps = {value}\n",
                    encoding="utf-8",
                )
                active.append((f"{name}:{path.resolve()}", value))
        cli = {"max_steps": base + 3} if mask & 8 else {}
        if cli:
            active.append(("cli", base + 3))
        environ = (
            {"AGENTBUS_MAX_STEPS": str(base + 4)}
            if mask & 16
            else {}
        )
        if environ:
            active.append(("environment:AGENTBUS_MAX_STEPS", base + 4))

        resolved = resolve_configuration(
            workspace=workspace,
            user_config_file=user,
            config_file=explicit if explicit.exists() else None,
            cli_overrides=cli,
            environ=environ,
        )

        if active:
            expected_source, expected_value = active[-1]
            assert resolved.config.max_steps == expected_value
            assert resolved.sources["max_steps"] == expected_source
        else:
            assert resolved.sources["max_steps"] == "default"


def _delete_invocation(
    root: Path,
    *,
    path: str,
    idempotency_key: str,
) -> tuple[ToolInvocation, ToolDescriptor]:
    descriptor = descriptor_map(workspace=root, worktree=root)["filesystem.delete"]
    capabilities = tuple(
        ToolCapability(
            name=capability.name,
            scope=CapabilityScope(
                **capability.scope.model_copy(
                    update={"affected_paths": (path,)}
                ).model_dump()
            ),
        )
        for capability in descriptor.capabilities
    )
    invocation = ToolInvocation(
        invocation_id="invocation-property",
        run_id="run-property",
        task_id="task-property",
        tool_name=descriptor.name,
        tool_version=descriptor.version,
        arguments={"path": path, "expected_sha256": "0" * 64},
        requested_capabilities=capabilities,
        context=ToolInvocationContext(
            workspace_identity=str(root.resolve()),
            worktree_identity=str(root.resolve()),
            caller_role="coder",
            workspace_trusted=True,
            provider_consented=True,
        ),
        idempotency_key=idempotency_key,
    )
    return invocation, descriptor


@PROPERTY_SETTINGS
@given(
    component=_SAFE_COMPONENT,
    key=_SAFE_COMPONENT,
    mutation=st.sampled_from(
        (
            "arguments",
            "idempotency",
            "cancellation",
            "task",
            "revision",
            "workspace",
            "capability",
        )
    ),
)
def test_approval_binding_rejects_every_generated_invocation_mutation(
    component: str,
    key: str,
    mutation: str,
) -> None:
    with _temporary_root("property-approval-") as temporary:
        root = Path(temporary)
        path = f"src/{component}.py"
        invocation, descriptor = _delete_invocation(
            root,
            path=path,
            idempotency_key=key,
        )
        decision = ToolPolicyEngine().evaluate(invocation, descriptor)
        assert decision.outcome == ToolPolicyOutcome.REQUIRE_APPROVAL
        request = build_tool_approval_request(
            invocation,
            descriptor,
            decision,
            approval_id="approval-property",
        )
        grant = decide_tool_approval(
            request,
            invocation,
            disposition=ToolApprovalDisposition.APPROVED,
        )

        restored = ToolInvocation.model_validate_json(invocation.model_dump_json())
        validate_tool_approval(grant, restored, descriptor)
        assert request.arguments_sha256 == sha256_json(invocation.arguments)
        assert request.idempotency_key_sha256 == idempotency_key_sha256(key)
        assert "idempotency_key" not in request.model_dump(mode="json")
        assert request.idempotency_key_sha256 != key
        assert sha256_json({"left": component, "right": key}) == sha256_json(
            {"right": key, "left": component}
        )

        if mutation == "arguments":
            changed = invocation.model_copy(
                update={
                    "arguments": {
                        "path": f"src/{component}-changed.py",
                        "expected_sha256": "0" * 64,
                    }
                }
            )
        elif mutation == "idempotency":
            changed = invocation.model_copy(
                update={"idempotency_key": key + "-changed"}
            )
        elif mutation == "cancellation":
            changed = invocation.model_copy(
                update={"cancellation_revision": 1}
            )
        elif mutation == "task":
            changed = invocation.model_copy(update={"task_id": "task-other"})
        elif mutation == "revision":
            changed = invocation.model_copy(update={"invocation_revision": 2})
        elif mutation == "workspace":
            changed = invocation.model_copy(
                update={
                    "context": invocation.context.model_copy(
                        update={"workspace_identity": str(root / "other")}
                    )
                }
            )
        else:
            changed = invocation.model_copy(
                update={
                    "requested_capabilities": (
                        *invocation.requested_capabilities,
                        ToolCapability(name=ToolCapabilityName.PROCESS_NETWORK),
                    )
                }
            )

        with pytest.raises(ToolApprovalBindingError):
            validate_tool_approval(grant, changed, descriptor)


@settings(
    max_examples=20,
    deadline=None,
    derandomize=True,
    database=None,
)
@given(component=_SAFE_COMPONENT, key=_SAFE_COMPONENT)
def test_duplicate_idempotency_key_never_creates_duplicate_mutation(
    component: str,
    key: str,
) -> None:
    with _temporary_root("property-idempotency-") as temporary:
        root = Path(temporary)
        store = StateStore(root / "state.db")
        store.create_run_with_tasks(
            RunRecord(
                run_id="run-property",
                original_task="Idempotency property",
                model="deterministic",
                workspace=str(root),
                graph_data={"version": 1, "tasks": []},
            ),
            [
                TaskSpec(
                    task_id="task-property",
                    title="Property task",
                    description="Exercise duplicate invocation handling",
                )
            ],
        )
        invocation, _ = _delete_invocation(
            root,
            path=f"src/{component}.py",
            idempotency_key=key,
        )

        first = store.record_tool_invocation(invocation)
        duplicate = store.record_tool_invocation(
            invocation.model_copy(
                update={"invocation_id": "invocation-duplicate"}
            )
        )

        assert duplicate == first
        assert len(store.list_tool_invocations("run-property")) == 1
        requested_events = [
            event
            for event in store.list_events("run-property")
            if event["event_type"] == "tool_invocation_requested"
        ]
        assert len(requested_events) == 1
        with pytest.raises(ToolInvocationConflictError):
            store.record_tool_invocation(
                invocation.model_copy(
                    update={
                        "invocation_id": "invocation-conflict",
                        "arguments": {
                            "path": f"src/{component}-other.py",
                            "expected_sha256": "0" * 64,
                        },
                    }
                )
            )


@PROPERTY_SETTINGS
@given(target=st.integers(min_value=1, max_value=LATEST_SCHEMA_VERSION))
def test_index_migrations_are_incremental_and_idempotent(target: int) -> None:
    connection = sqlite3.connect(":memory:")
    try:
        assert apply_migrations(connection, target_version=target) == target
        assert schema_version(connection) == target
        assert apply_migrations(connection) == LATEST_SCHEMA_VERSION
        before = connection.execute(
            "SELECT version, name, applied_at "
            "FROM schema_migrations ORDER BY version"
        ).fetchall()

        assert apply_migrations(connection) == LATEST_SCHEMA_VERSION
        after = connection.execute(
            "SELECT version, name, applied_at "
            "FROM schema_migrations ORDER BY version"
        ).fetchall()

        assert after == before
        assert [row[0] for row in after] == list(
            range(1, LATEST_SCHEMA_VERSION + 1)
        )
        verify_schema(connection)
    finally:
        connection.close()
