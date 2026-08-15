from __future__ import annotations

import base64
import csv
import hashlib
import io
import json
import re
import stat
import subprocess
import tarfile
import tempfile
import zipfile
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path, PurePosixPath
from typing import Any
from zipfile import ZIP_STORED, ZipFile

from pydantic import ValidationError

from agentbus import __version__
from agentbus.execution.models import RunRecord
from agentbus.execution.state_store import StateStore
from agentbus.git.repository import (
    GitRepository,
    GitRepositoryError,
    WorkspaceRepositoryMismatch,
    safe_git_environment,
)
from agentbus.mcp import McpClient, McpServerConfig, mcp_server_capabilities
from agentbus.mcp.errors import McpProtocolError
from agentbus.policy import (
    ToolApprovalBindingError,
    ToolApprovalDisposition,
    ToolPolicyEngine,
    build_tool_approval_request,
    decide_tool_approval,
    validate_tool_approval,
)
from agentbus.product.logging import ProductLogWriter
from agentbus.release_packaging import audit_distributions
from agentbus.tools.capabilities import derive_required_capabilities
from agentbus.tools.descriptors import descriptor_map
from agentbus.tools.filesystem_security import (
    ContainedPathResolver,
    FileSystemContainmentError,
    FileSystemSecurityError,
)
from agentbus.tools.protocol import (
    ToolCapability,
    ToolCapabilityName,
    ToolInvocation,
    ToolInvocationContext,
    ToolPolicyOutcome,
    deserialize_protocol_model,
)
from agentbus.trace import (
    ContentAddressedStore,
    RuntimeTrace,
    TraceArchiveExporter,
    TraceArchiveImporter,
    TraceSpanType,
    TraceStatus,
)
from agentbus.trace.errors import TraceIntegrityError
from agentbus.trace.sealing import seal_run_provenance


DEFENSIVE_SECURITY_DISCLAIMER = (
    "This scorecard is controlled local defensive validation, not formal "
    "penetration-test certification."
)


class DefensiveSecurityStatus(str, Enum):
    PASS = "pass"
    PASS_WITH_WARNINGS = "pass_with_warnings"
    FAIL = "fail"


@dataclass(frozen=True)
class DefensiveSecurityEvidence:
    boundary_id: str
    title: str
    status: DefensiveSecurityStatus
    tested_boundaries: tuple[str, ...]
    observation: str
    limitations: tuple[str, ...] = ()

    @property
    def passed(self) -> bool:
        return self.status != DefensiveSecurityStatus.FAIL

    def to_dict(self) -> dict[str, Any]:
        return {
            "boundary_id": self.boundary_id,
            "title": self.title,
            "status": self.status.value,
            "passed": self.passed,
            "tested_boundaries": list(self.tested_boundaries),
            "observation": self.observation,
            "limitations": list(self.limitations),
        }


@dataclass(frozen=True)
class DefensiveSecurityScorecard:
    generated_at: datetime
    classification: DefensiveSecurityStatus
    evidence: tuple[DefensiveSecurityEvidence, ...]
    offline: bool = True
    network_used: bool = False
    provider_calls: int = 0
    external_targets_contacted: int = 0
    formal_penetration_test_certification: bool = False
    disclaimer: str = DEFENSIVE_SECURITY_DISCLAIMER

    @property
    def ok(self) -> bool:
        return self.classification != DefensiveSecurityStatus.FAIL

    @property
    def tested_boundaries(self) -> tuple[str, ...]:
        return tuple(
            f"{item.boundary_id}: {boundary}"
            for item in self.evidence
            for boundary in item.tested_boundaries
        )

    @property
    def unresolved_limitations(self) -> tuple[str, ...]:
        return tuple(
            f"{item.title}: {limitation}"
            for item in self.evidence
            for limitation in item.limitations
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "classification": self.classification.value,
            "generated_at": self.generated_at.isoformat(),
            "offline": self.offline,
            "network_used": self.network_used,
            "provider_calls": self.provider_calls,
            "external_targets_contacted": self.external_targets_contacted,
            "formal_penetration_test_certification": (
                self.formal_penetration_test_certification
            ),
            "disclaimer": self.disclaimer,
            "evidence_count": len(self.evidence),
            "tested_boundary_count": len(self.tested_boundaries),
            "tested_boundaries": list(self.tested_boundaries),
            "unresolved_limitations": list(self.unresolved_limitations),
            "evidence": [item.to_dict() for item in self.evidence],
        }


@dataclass(frozen=True)
class _ProbeContext:
    scratch: Path
    repository: Path
    artifacts: tuple[Path, ...]


_Probe = Callable[[_ProbeContext], DefensiveSecurityEvidence]


def run_defensive_security_validation(
    root: str | Path,
    *,
    artifacts: Iterable[str | Path] = (),
) -> DefensiveSecurityScorecard:
    """Exercise AgentBus-owned security boundaries without external targets."""

    repository = Path(root).expanduser().resolve()
    if not repository.is_dir():
        raise ValueError("Defensive security validation root must be a directory.")
    selected_artifacts = tuple(
        Path(artifact).expanduser().resolve() for artifact in artifacts
    )
    specifications: tuple[tuple[str, str, _Probe], ...] = (
        (
            "filesystem_containment",
            "Filesystem containment",
            _probe_filesystem_containment,
        ),
        (
            "approval_capability_scope",
            "Approval and capability scope",
            _probe_approval_capability_scope,
        ),
        ("git_safety", "Git safety", _probe_git_safety),
        (
            "malformed_protocol_handling",
            "Malformed protocol handling",
            _probe_malformed_protocol,
        ),
        (
            "hostile_mcp_peer",
            "Synthetic hostile MCP peer",
            _probe_hostile_mcp_peer,
        ),
        (
            "trace_archive_integrity",
            "Trace and archive integrity",
            _probe_trace_archive_integrity,
        ),
        (
            "diagnostic_privacy",
            "Diagnostic privacy",
            _probe_diagnostic_privacy,
        ),
        ("package_contents", "Package contents", _probe_package_contents),
        ("vsix_contents", "VSIX contents", _probe_vsix_contents),
    )
    evidence: list[DefensiveSecurityEvidence] = []
    with tempfile.TemporaryDirectory(prefix="agentbus-security-validation-") as value:
        temporary = Path(value).resolve()
        for boundary_id, title, probe in specifications:
            scratch = temporary / boundary_id
            scratch.mkdir()
            context = _ProbeContext(
                scratch=scratch,
                repository=repository,
                artifacts=selected_artifacts,
            )
            try:
                result = probe(context)
                if result.boundary_id != boundary_id or result.title != title:
                    raise RuntimeError("Security probe identity mismatch.")
            except Exception as exc:
                result = DefensiveSecurityEvidence(
                    boundary_id=boundary_id,
                    title=title,
                    status=DefensiveSecurityStatus.FAIL,
                    tested_boundaries=(),
                    observation=(
                        "Controlled local validation did not complete "
                        f"({type(exc).__name__})."
                    ),
                )
            evidence.append(result)

    classification = (
        DefensiveSecurityStatus.FAIL
        if any(item.status == DefensiveSecurityStatus.FAIL for item in evidence)
        else DefensiveSecurityStatus.PASS_WITH_WARNINGS
        if any(
            item.status == DefensiveSecurityStatus.PASS_WITH_WARNINGS
            for item in evidence
        )
        else DefensiveSecurityStatus.PASS
    )
    return DefensiveSecurityScorecard(
        generated_at=datetime.now(UTC),
        classification=classification,
        evidence=tuple(evidence),
    )


def _probe_filesystem_containment(
    context: _ProbeContext,
) -> DefensiveSecurityEvidence:
    workspace = context.scratch / "workspace"
    workspace.mkdir()
    safe_file = workspace / "safe.txt"
    safe_file.write_text("safe\n", encoding="utf-8")
    outside = context.scratch / "outside.txt"
    outside.write_text("outside-marker\n", encoding="utf-8")
    resolver = ContainedPathResolver(workspace)
    resolved = resolver.resolve("safe.txt")
    if resolved.path != safe_file.resolve():
        raise AssertionError("Contained paths did not resolve inside the workspace.")

    for candidate in ("../outside.txt", str(outside.resolve()), ".env"):
        try:
            resolver.resolve(candidate)
        except FileSystemSecurityError:
            continue
        raise AssertionError("A hostile filesystem path was accepted.")

    tested = [
        "Relative paths resolve beneath the assigned canonical root.",
        "Traversal, absolute paths, and protected credential paths are rejected.",
    ]
    limitations: list[str] = []
    link = workspace / "outside-link"
    try:
        link.symlink_to(outside)
    except (NotImplementedError, OSError):
        limitations.append(
            "The operating system did not permit creation of the optional symlink "
            "escape fixture."
        )
    else:
        try:
            resolver.resolve("outside-link")
        except FileSystemContainmentError:
            tested.append("A symlink resolving outside the assigned root is rejected.")
        else:
            raise AssertionError("A symlink escape was accepted.")

    if outside.read_text(encoding="utf-8") != "outside-marker\n":
        raise AssertionError("The containment probe modified an outside file.")
    return _evidence(
        "filesystem_containment",
        "Filesystem containment",
        tested,
        "Contained reads succeeded and hostile path forms were blocked without "
        "modifying outside data.",
        limitations,
    )


def _probe_approval_capability_scope(
    context: _ProbeContext,
) -> DefensiveSecurityEvidence:
    workspace = context.scratch / "workspace"
    workspace.mkdir()
    descriptor = descriptor_map(workspace=workspace)["filesystem.delete"]
    base = ToolInvocation(
        invocation_id="security-invocation",
        run_id="security-run",
        task_id="security-task",
        tool_name=descriptor.name,
        tool_version=descriptor.version,
        arguments={"path": "obsolete.txt", "expected_sha256": "0" * 64},
        requested_capabilities=descriptor.capabilities,
        context=ToolInvocationContext(
            workspace_identity=str(workspace.resolve()),
            worktree_identity=str(workspace.resolve()),
            caller_role="coder",
            workspace_trusted=True,
            provider_consented=True,
        ),
    )
    invocation = base.model_copy(
        update={
            "requested_capabilities": derive_required_capabilities(base, descriptor)
        }
    )
    engine = ToolPolicyEngine()
    required = engine.evaluate(invocation, descriptor)
    if required.outcome != ToolPolicyOutcome.REQUIRE_APPROVAL:
        raise AssertionError("A destructive operation did not require approval.")
    request = build_tool_approval_request(
        invocation,
        descriptor,
        required,
        approval_id="security-approval",
    )
    grant = decide_tool_approval(
        request,
        invocation,
        disposition=ToolApprovalDisposition.APPROVED,
    )
    allowed = engine.evaluate(invocation, descriptor, approval=grant)
    if allowed.outcome != ToolPolicyOutcome.ALLOW_WITH_CONSTRAINTS:
        raise AssertionError("An exact approved invocation was not authorized.")

    changed_base = invocation.model_copy(
        update={
            "arguments": {
                "path": "different.txt",
                "expected_sha256": "0" * 64,
            }
        }
    )
    changed = changed_base.model_copy(
        update={
            "requested_capabilities": derive_required_capabilities(
                changed_base,
                descriptor,
            )
        }
    )
    changed_decision = engine.evaluate(changed, descriptor, approval=grant)
    if changed_decision.outcome != ToolPolicyOutcome.DENY:
        raise AssertionError("An approval was reused for changed arguments.")

    expanded = invocation.model_copy(
        update={
            "requested_capabilities": invocation.requested_capabilities
            + (ToolCapability(name=ToolCapabilityName.PROCESS_NETWORK),)
        }
    )
    try:
        validate_tool_approval(grant, expanded, descriptor)
    except ToolApprovalBindingError:
        pass
    else:
        raise AssertionError("An approval authorized expanded capabilities.")
    return _evidence(
        "approval_capability_scope",
        "Approval and capability scope",
        (
            "Destructive filesystem capability scope is derived from arguments.",
            "An exact invocation-bound approval authorizes only the original request.",
            "Argument mutation and capability expansion invalidate authorization.",
        ),
        "Least-scope derivation and exact approval binding rejected both replay "
        "mutations.",
    )


def _probe_git_safety(context: _ProbeContext) -> DefensiveSecurityEvidence:
    parent = context.scratch / "parent"
    parent.mkdir()
    _initialize_git(parent)
    nested = parent / "nested"
    nested.mkdir()
    try:
        GitRepository(str(nested)).validate_workspace()
    except WorkspaceRepositoryMismatch:
        pass
    else:
        raise AssertionError("Git was allowed to walk into a parent repository.")

    isolated = context.scratch / "isolated"
    isolated.mkdir()
    _initialize_git(isolated)
    literal_name = "literal;metacharacter.txt"
    (isolated / literal_name).write_text("literal\n", encoding="utf-8")
    marker = context.scratch / "outside-marker.txt"
    marker.write_text("unchanged\n", encoding="utf-8")
    repository = GitRepository(str(isolated))
    repository.validate_workspace()
    if repository.changed_files() != [literal_name]:
        raise AssertionError("Git did not preserve a literal metacharacter path.")
    try:
        repository.stage(("-option-like.txt",))
    except GitRepositoryError:
        pass
    else:
        raise AssertionError("An option-like Git path was accepted.")
    if marker.read_text(encoding="utf-8") != "unchanged\n":
        raise AssertionError("A literal Git path triggered an outside side effect.")
    return _evidence(
        "git_safety",
        "Git safety",
        (
            "A nested directory that resolves to a parent Git root is rejected.",
            "Repository-relative metacharacter paths remain literal argument data.",
            "Option-like paths are rejected before a managed Git mutation.",
        ),
        "Only isolated fixture repositories were inspected; no remote or user "
        "repository mutation occurred.",
    )


def _probe_malformed_protocol(
    context: _ProbeContext,
) -> DefensiveSecurityEvidence:
    del context
    malformed = b'{"invocation_id":'
    hostile_secret = "sk-proj-" + "Q7mN2xP9vR4tY8kL3cD6sF1hJ5wB0zA"
    invalid = json.dumps(
        {
            "invocation_id": "invalid identifier",
            "run_id": "security-run",
            "task_id": "security-task",
            "tool_name": "filesystem.read",
            "tool_version": {"major": 1},
            "arguments": {"api_key": hostile_secret},
            "requested_capabilities": [],
            "context": {
                "workspace_identity": "workspace",
                "worktree_identity": "workspace",
                "caller_role": "coder",
            },
            "unexpected": hostile_secret,
        },
        sort_keys=True,
    )
    for payload in (malformed, invalid):
        try:
            deserialize_protocol_model(payload, ToolInvocation)
        except ValidationError:
            continue
        raise AssertionError("A malformed tool protocol document was accepted.")
    return _evidence(
        "malformed_protocol_handling",
        "Malformed protocol handling",
        (
            "Invalid JSON is rejected before tool invocation construction.",
            "Forbidden fields, invalid identifiers, and empty capability sets are "
            "rejected by the typed protocol boundary.",
        ),
        "Malformed synthetic protocol documents were rejected without executing a "
        "tool or reproducing hostile values in the scorecard.",
    )


class _HostileMcpTransport:
    def __init__(self) -> None:
        self.started = False
        self.closed = False
        self.notified = False
        self.protocol_version: str | None = None

    def start(self) -> None:
        self.started = True

    def request(
        self,
        message: dict[str, Any],
        *,
        timeout_seconds: float,
        cancellation: Any = None,
    ) -> dict[str, Any]:
        del timeout_seconds, cancellation
        return {
            "jsonrpc": "2.0",
            "id": int(message["id"]) + 1,
            "result": {
                "protocolVersion": "2025-11-25",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "hostile-fixture", "version": "1"},
            },
        }

    def notify(
        self,
        message: dict[str, Any],
        *,
        timeout_seconds: float,
        cancellation: Any = None,
    ) -> None:
        del message, timeout_seconds, cancellation
        self.notified = True

    def set_protocol_version(self, protocol_version: str) -> None:
        self.protocol_version = protocol_version

    def close(self) -> None:
        self.closed = True


def _probe_hostile_mcp_peer(
    context: _ProbeContext,
) -> DefensiveSecurityEvidence:
    del context
    config = McpServerConfig(
        server_id="hostile",
        transport="stdio",
        executable_alias="synthetic-hostile",
        capability_map={"echo": mcp_server_capabilities("hostile")},
    )
    transport = _HostileMcpTransport()
    client = McpClient(config, transport)
    try:
        client.connect()
    except McpProtocolError:
        pass
    else:
        raise AssertionError("A hostile MCP response identity was accepted.")
    if (
        not transport.started
        or not transport.closed
        or transport.notified
        or client.is_connected
    ):
        raise AssertionError("The rejected MCP connection was not safely closed.")
    return _evidence(
        "hostile_mcp_peer",
        "Synthetic hostile MCP peer",
        (
            "An in-memory peer returning a mismatched JSON-RPC response ID is "
            "rejected.",
            "Protocol failure closes the transport before initialization completes.",
        ),
        "The synthetic peer was in-process and contacted no process, socket, or "
        "third-party MCP server.",
    )


def _probe_trace_archive_integrity(
    context: _ProbeContext,
) -> DefensiveSecurityEvidence:
    workspace = context.scratch / "workspace"
    workspace.mkdir()
    state_store = StateStore(context.scratch / "state.db")
    state_store.create_run(
        RunRecord(
            run_id="security-trace-run",
            original_task="Validate local trace integrity",
            model="deterministic",
            workspace=str(workspace),
            graph_data={"version": 1, "tasks": []},
        )
    )
    runtime = RuntimeTrace.open(
        state_store,
        "security-trace-run",
        object_root=context.scratch / "source-objects",
        workspace=workspace,
    )
    span = runtime.start_span(
        TraceSpanType.PROVIDER_RESPONSE,
        "synthetic captured response",
        attributes={
            "provider": "deterministic",
            "model": "security-fixture-v1",
            "role": "coder",
        },
    )
    metadata = runtime.object_store.put_json(
        {"code": "result = 42"},
        producing_span_id=span.span_id,
        media_type="application/vnd.agentbus.model-envelope+json",
    )
    output = runtime.object_store.reference_output(
        metadata,
        reference_id="security-output",
        name="model.response",
    )
    runtime.finish_span(span, output_references=[output])
    trace = runtime.finish(status=TraceStatus.SUCCEEDED)
    provenance = seal_run_provenance(
        trace,
        state_store=state_store,
        object_store=runtime.object_store,
        configuration={"provider": "deterministic"},
        task_graph={"version": 1, "tasks": []},
        final_repository_tree_sha256="e" * 64,
    )
    archive = context.scratch / "trace.agentbus-trace"
    TraceArchiveExporter(runtime.object_store).export(
        trace,
        provenance,
        archive,
        include_source_content=True,
    )

    inspected_store = ContentAddressedStore(context.scratch / "inspected-objects")
    inspected = TraceArchiveImporter(inspected_store).inspect(archive)
    if inspected_store.list_metadata() or metadata.sha256 not in (
        inspected.available_object_hashes
    ):
        raise AssertionError("Valid trace inspection changed destination state.")

    tampered = context.scratch / "tampered.agentbus-trace"
    _tamper_trace_object(archive, tampered, metadata.sha256)
    destination = ContentAddressedStore(context.scratch / "imported-objects")
    try:
        TraceArchiveImporter(destination).import_archive(
            tampered,
            allow_source_content=True,
        )
    except TraceIntegrityError:
        pass
    else:
        raise AssertionError("A tampered trace object was imported.")
    if destination.list_metadata():
        raise AssertionError("Tampered trace data was written before rejection.")
    return _evidence(
        "trace_archive_integrity",
        "Trace and archive integrity",
        (
            "A sealed local trace archive validates without importing source objects.",
            "A content-addressed object mutation is rejected before destination "
            "writes.",
        ),
        "Valid provenance and tampered object bytes produced distinct bounded "
        "outcomes.",
    )


def _probe_diagnostic_privacy(
    context: _ProbeContext,
) -> DefensiveSecurityEvidence:
    log_path = context.scratch / "logs" / "product.log"
    secret = "sk-proj-" + "R7mN2xP9vT4kL8cD3sF6hJ1wB5zA0qE"
    private_path = str(Path.home().resolve() / "private-agentbus-fixture")
    ProductLogWriter(log_path).write(
        level="warning",
        component="security-validation",
        message=f"provider failed at {private_path} with token {secret}",
        fields={"api_key": secret, "workspace": private_path},
    )
    content = log_path.read_text(encoding="utf-8")
    encoded_private_path = private_path.replace("\\", "\\\\")
    if secret in content or private_path in content or encoded_private_path in content:
        raise AssertionError("A diagnostic log persisted private material.")
    if "[REDACTED]" not in content:
        raise AssertionError("Diagnostic redaction evidence was not persisted.")
    return _evidence(
        "diagnostic_privacy",
        "Diagnostic privacy",
        (
            "Credential-shaped values are removed from persisted product logs.",
            "Current-user absolute paths are removed from message and field data.",
        ),
        "The persisted diagnostic retained safe structure and redaction markers "
        "without private values.",
    )


def _probe_package_contents(
    context: _ProbeContext,
) -> DefensiveSecurityEvidence:
    safe = _write_distribution_pair(context.scratch / "safe")
    safe_report = audit_distributions(safe, root=context.scratch / "safe")
    if not safe_report.ok:
        raise AssertionError("The known-safe package fixture failed its audit.")
    unsafe = _write_distribution_pair(
        context.scratch / "unsafe",
        include_runtime=True,
    )
    unsafe_report = audit_distributions(unsafe, root=context.scratch / "unsafe")
    if unsafe_report.ok or not any(
        finding.code.endswith("RUNTIME_OR_KEY_FILE")
        for finding in unsafe_report.findings
    ):
        raise AssertionError("A runtime database was accepted in package contents.")

    tested = [
        "A metadata-complete synthetic wheel and source distribution pair is "
        "accepted.",
        "A package pair containing a runtime database is rejected.",
    ]
    limitations: list[str] = []
    actual = tuple(
        path
        for path in context.artifacts
        if path.name.lower().endswith((".whl", ".tar.gz", ".tgz"))
    )
    if actual:
        actual_report = audit_distributions(actual, root=context.repository)
        tested.append("Explicitly selected local Python release artifacts were audited.")
        if not actual_report.ok:
            return _failed_artifact_evidence(
                "package_contents",
                "Package contents",
                tested,
                len(actual_report.findings),
                "Python package",
            )
    else:
        limitations.append(
            "No current-version wheel and source distribution were selected, so "
            "real package bytes were not evaluated."
        )
    return _evidence(
        "package_contents",
        "Package contents",
        tested,
        "Synthetic package acceptance and contamination rejection completed; "
        f"selected real artifacts={len(actual)}.",
        limitations,
    )


def _probe_vsix_contents(context: _ProbeContext) -> DefensiveSecurityEvidence:
    safe = context.scratch / "safe.vsix"
    _write_vsix(safe)
    safe_findings = _audit_vsix(safe)
    if safe_findings:
        raise AssertionError("The known-safe VSIX fixture failed its audit.")
    unsafe = context.scratch / "unsafe.vsix"
    _write_vsix(unsafe, include_source=True)
    unsafe_findings = _audit_vsix(unsafe)
    if not any("forbidden" in finding for finding in unsafe_findings):
        raise AssertionError("Source files were accepted in VSIX contents.")

    tested = [
        "A minimal allowlisted synthetic VSIX with compatibility metadata is "
        "accepted.",
        "A VSIX containing extension source is rejected.",
    ]
    limitations: list[str] = []
    actual = tuple(
        path for path in context.artifacts if path.name.lower().endswith(".vsix")
    )
    actual_findings = [finding for path in actual for finding in _audit_vsix(path)]
    if actual:
        tested.append("Explicitly selected local VSIX artifacts were audited.")
    else:
        limitations.append(
            "No real VSIX was selected, so only controlled synthetic archive bytes "
            "were evaluated."
        )
    if actual_findings:
        return _failed_artifact_evidence(
            "vsix_contents",
            "VSIX contents",
            tested,
            len(actual_findings),
            "VSIX",
        )
    return _evidence(
        "vsix_contents",
        "VSIX contents",
        tested,
        "Synthetic VSIX acceptance and hostile-content rejection completed; "
        f"selected real artifacts={len(actual)}.",
        limitations,
    )


def _evidence(
    boundary_id: str,
    title: str,
    tested_boundaries: Iterable[str],
    observation: str,
    limitations: Iterable[str] = (),
) -> DefensiveSecurityEvidence:
    tested = tuple(tested_boundaries)
    unresolved = tuple(limitations)
    return DefensiveSecurityEvidence(
        boundary_id=boundary_id,
        title=title,
        status=(
            DefensiveSecurityStatus.PASS_WITH_WARNINGS
            if unresolved
            else DefensiveSecurityStatus.PASS
        ),
        tested_boundaries=tested,
        observation=observation,
        limitations=unresolved,
    )


def _failed_artifact_evidence(
    boundary_id: str,
    title: str,
    tested_boundaries: Iterable[str],
    finding_count: int,
    artifact_kind: str,
) -> DefensiveSecurityEvidence:
    return DefensiveSecurityEvidence(
        boundary_id=boundary_id,
        title=title,
        status=DefensiveSecurityStatus.FAIL,
        tested_boundaries=tuple(tested_boundaries),
        observation=(
            f"Selected local {artifact_kind} artifacts failed {finding_count} "
            "bounded content check(s)."
        ),
    )


def _initialize_git(root: Path) -> None:
    result = subprocess.run(
        ["git", "init", "--quiet"],
        cwd=root,
        capture_output=True,
        text=True,
        timeout=20,
        shell=False,
        env=safe_git_environment(),
    )
    if result.returncode != 0:
        raise RuntimeError("Local Git fixture initialization failed.")


def _tamper_trace_object(source: Path, destination: Path, digest: str) -> None:
    target = f"objects/{digest}.blob"
    with ZipFile(source) as archive:
        entries = [
            (info.filename, b"tampered" if info.filename == target else archive.read(info))
            for info in archive.infolist()
        ]
    if not any(name == target for name, _ in entries):
        raise AssertionError("Trace fixture did not contain its referenced object.")
    with ZipFile(destination, mode="w", compression=ZIP_STORED) as archive:
        for name, payload in entries:
            archive.writestr(name, payload)


def _write_distribution_pair(
    root: Path,
    *,
    include_runtime: bool = False,
) -> tuple[Path, Path]:
    root.mkdir(parents=True)
    wheel = root / f"agentbus-{__version__}-py3-none-any.whl"
    sdist = root / f"agentbus-{__version__}.tar.gz"
    dist_info = f"agentbus-{__version__}.dist-info"
    metadata = _package_metadata()
    entries = {
        "agentbus/__init__.py": f'__version__ = "{__version__}"\n'.encode(),
        "agentbus/py.typed": b"",
        f"{dist_info}/METADATA": metadata,
        f"{dist_info}/WHEEL": b"Wheel-Version: 1.0\nTag: py3-none-any\n",
        f"{dist_info}/licenses/LICENSE": b"MIT\n",
        f"{dist_info}/entry_points.txt": (
            b"[console_scripts]\n"
            b"agentbus = agentbus.cli:main\n"
            b"agentbus-eval = agentbus.eval:main\n"
        ),
    }
    if include_runtime:
        entries["agentbus/runtime.db"] = b"SQLite format 3\x00"
    record_name = f"{dist_info}/RECORD"
    entries[record_name] = _wheel_record(entries, record_name)
    with ZipFile(wheel, mode="w") as archive:
        for name, content in entries.items():
            archive.writestr(name, content)

    source_root = f"agentbus-{__version__}"
    source_entries = {
        f"{source_root}/LICENSE": b"MIT\n",
        f"{source_root}/MANIFEST.in": b"include LICENSE\n",
        f"{source_root}/PKG-INFO": metadata,
        f"{source_root}/README.md": b"# AgentBus\n",
        f"{source_root}/agentbus/__init__.py": entries["agentbus/__init__.py"],
        f"{source_root}/agentbus/py.typed": b"",
        f"{source_root}/pyproject.toml": b"[project]\nname='agentbus'\n",
    }
    if include_runtime:
        source_entries[f"{source_root}/agentbus/runtime.db"] = entries[
            "agentbus/runtime.db"
        ]
    with tarfile.open(sdist, mode="w:gz") as archive:
        for name, content in source_entries.items():
            info = tarfile.TarInfo(name)
            info.size = len(content)
            info.mtime = 0
            archive.addfile(info, io.BytesIO(content))
    return wheel, sdist


def _package_metadata() -> bytes:
    lines = [
        "Metadata-Version: 2.4",
        "Name: agentbus",
        f"Version: {__version__}",
        "Requires-Python: >=3.11",
        *(
            f"Provides-Extra: {extra}"
            for extra in ("all", "azure", "dev", "entra", "ide", "mcp")
        ),
        'Requires-Dist: pytest>=8; extra == "dev"',
        "",
        "",
    ]
    return "\n".join(lines).encode("utf-8")


def _wheel_record(entries: dict[str, bytes], record_name: str) -> bytes:
    output = io.StringIO(newline="")
    writer = csv.writer(output, lineterminator="\n")
    for name, content in entries.items():
        digest = base64.urlsafe_b64encode(hashlib.sha256(content).digest())
        encoded = digest.rstrip(b"=").decode("ascii")
        writer.writerow((name, f"sha256={encoded}", len(content)))
    writer.writerow((record_name, "", ""))
    return output.getvalue().encode("utf-8")


_VSIX_REQUIRED = frozenset(
    {
        "[Content_Types].xml",
        "extension.vsixmanifest",
        "extension/LICENSE.txt",
        "extension/readme.md",
        "extension/media/agentbus.svg",
        "extension/out/extension.js",
        "extension/package.json",
    }
)
_VSIX_ALLOWED_FILES = _VSIX_REQUIRED - {
    "[Content_Types].xml",
    "extension.vsixmanifest",
}
_VSIX_ALLOWED_DIRECTORIES = frozenset(
    {"extension/", "extension/media/", "extension/out/"}
)
_VSIX_FORBIDDEN = tuple(
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"(^|/)node_modules/",
        r"(^|/)\.env(?:\.|$)",
        r"\.(?:db|log|pem|pfx|p12|sqlite|sqlite3|pyc|pyo|ts|map)$",
        r"(^|/)\.vscode-test/",
        r"(^|/)(?:\.agentbus|\.venv|build|dist|runs|traces|worktrees|evaluation-output)/",
        r"(^|/)__pycache__/",
        r"(^|/)src/",
        r"(^|/)tests?/",
        r"(^|/).*profile.*/",
        r"(^|/)agentbus-support-.*\.zip$",
        r"(^|/)agentbus-vscode\.vsix$",
    )
)
_VSIX_SECRET_PATTERNS = (
    re.compile(r"\bsk-(?:proj-)?[A-Za-z0-9_-]{20,}\b"),
    re.compile(r"\bgh(?:p|o|u|s|r)_[A-Za-z0-9]{30,}\b"),
    re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b"),
    re.compile(r"AZURE_OPENAI_API_KEY\s*=\s*[^\s\"']{20,}", re.IGNORECASE),
    re.compile(r"Bearer\s+[A-Za-z0-9._~+/=-]{32,}", re.IGNORECASE),
)


def _write_vsix(path: Path, *, include_source: bool = False) -> None:
    package = {
        "name": "agentbus-vscode",
        "version": "0.6.0-beta.1",
        "private": True,
        "main": "./out/extension.js",
        "agentbusCompatibility": {
            "python": ">=0.6.0b1,<0.7.0",
            "controlProtocol": "1.0",
            "stateSchema": 6,
        },
    }
    entries: dict[str, str] = {
        "[Content_Types].xml": "<Types />\n",
        "extension.vsixmanifest": '<PackageManifest Version="0.6.0-beta.1" />\n',
        "extension/LICENSE.txt": "MIT\n",
        "extension/readme.md": "# AgentBus\n",
        "extension/media/agentbus.svg": "<svg />\n",
        "extension/out/extension.js": "exports.activate = () => {};\n",
        "extension/package.json": json.dumps(package, sort_keys=True),
    }
    if include_source:
        entries["extension/src/extension.ts"] = "export const unsafe = true;\n"
    with ZipFile(path, mode="w") as archive:
        for name, content in entries.items():
            archive.writestr(name, content)


def _audit_vsix(path: Path) -> tuple[str, ...]:
    findings: list[str] = []
    try:
        with ZipFile(path) as archive:
            infos = archive.infolist()
            names = [info.filename for info in infos]
            if len(names) != len(set(names)):
                findings.append("duplicate archive entry")
            if len(names) > 2_000:
                findings.append("archive entry limit exceeded")
            expanded = 0
            for info in infos:
                normalized = info.filename.replace("\\", "/")
                parts = PurePosixPath(normalized).parts
                if (
                    normalized.startswith("/")
                    or "\x00" in normalized
                    or ".." in parts
                ):
                    findings.append("unsafe archive path")
                if any(pattern.search(normalized) for pattern in _VSIX_FORBIDDEN):
                    findings.append("forbidden archive content")
                if not _vsix_name_allowed(normalized, info.is_dir()):
                    findings.append("unexpected VSIX content")
                if stat.S_ISLNK(info.external_attr >> 16):
                    findings.append("archive link")
                expanded += info.file_size
                if info.file_size > 5_000_000:
                    findings.append("entry size limit exceeded")
            if expanded > 25_000_000:
                findings.append("expanded archive size limit exceeded")
            missing = sorted(_VSIX_REQUIRED - set(names))
            if missing:
                findings.append("required VSIX content missing")

            for info in infos:
                if info.is_dir() or info.file_size > 5_000_000:
                    continue
                text = archive.read(info).decode("utf-8", errors="replace")
                if any(pattern.search(text) for pattern in _VSIX_SECRET_PATTERNS):
                    findings.append("secret-like VSIX content")
                if _contains_private_key(text):
                    findings.append("private key in VSIX content")
                home = str(Path.home().resolve())
                if len(home) > 3 and home.casefold() in text.casefold():
                    findings.append("personal absolute path in VSIX content")

            package_bytes = archive.read("extension/package.json") if not missing else None
            manifest = (
                archive.read("extension.vsixmanifest").decode(
                    "utf-8",
                    errors="replace",
                )
                if "extension.vsixmanifest" in names
                else ""
            )
    except (OSError, KeyError, zipfile.BadZipFile):
        return ("invalid VSIX archive",)

    if package_bytes is not None:
        try:
            package = json.loads(package_bytes)
        except (TypeError, json.JSONDecodeError, UnicodeDecodeError):
            findings.append("invalid VSIX package metadata")
        else:
            if not isinstance(package, dict):
                findings.append("invalid VSIX package metadata")
            else:
                if package.get("name") != "agentbus-vscode":
                    findings.append("unexpected VSIX package name")
                if package.get("main") != "./out/extension.js":
                    findings.append("unsafe VSIX entry point")
                if package.get("private") is not True:
                    findings.append("VSIX publication guard missing")
                compatibility = package.get("agentbusCompatibility")
                if compatibility != {
                    "python": ">=0.6.0b1,<0.7.0",
                    "controlProtocol": "1.0",
                    "stateSchema": 6,
                }:
                    findings.append("VSIX compatibility metadata mismatch")
                version = package.get("version")
                if not isinstance(version, str) or f'Version="{version}"' not in manifest:
                    findings.append("VSIX manifest version mismatch")
    return tuple(sorted(set(findings)))


def _vsix_name_allowed(name: str, directory: bool) -> bool:
    if directory:
        return name in _VSIX_ALLOWED_DIRECTORIES
    if name in _VSIX_ALLOWED_FILES or name in {
        "[Content_Types].xml",
        "extension.vsixmanifest",
    }:
        return True
    if not name.startswith("extension/out/") or not name.endswith(".js"):
        return False
    relative = name.removeprefix("extension/out/")
    return bool(relative) and all(
        part not in {"", ".", ".."}
        and re.fullmatch(r"[A-Za-z0-9._-]+", part) is not None
        for part in relative.split("/")
    )


def _contains_private_key(content: str) -> bool:
    for prefix in ("", "RSA ", "EC ", "OPENSSH "):
        if (
            f"-----BEGIN {prefix}PRIVATE KEY-----" in content
            and f"-----END {prefix}PRIVATE KEY-----" in content
        ):
            return True
    return False


__all__ = [
    "DEFENSIVE_SECURITY_DISCLAIMER",
    "DefensiveSecurityEvidence",
    "DefensiveSecurityScorecard",
    "DefensiveSecurityStatus",
    "run_defensive_security_validation",
]
