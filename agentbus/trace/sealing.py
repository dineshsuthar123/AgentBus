from __future__ import annotations

import hashlib
import re
from collections.abc import Iterable, Mapping
from typing import TYPE_CHECKING, Any

from agentbus.control.models import CONTROL_PROTOCOL_VERSION
from agentbus.policy.defaults import DEFAULT_TOOL_POLICY
from agentbus.replay.classification import ReplayabilityClassifier
from agentbus.tools.protocol import TOOL_PROTOCOL_NAME, TOOL_PROTOCOL_VERSION
from agentbus.trace.errors import TraceIntegrityError
from agentbus.trace.models import (
    MAX_TRACE_ITEMS,
    Trace,
    TraceSpanType,
)
from agentbus.trace.provenance import (
    ProvenanceBuilder,
    ProvenanceManifest,
    ProviderRouteProvenance,
    ToolDescriptorProvenance,
    verify_provenance,
)
from agentbus.trace.redaction import canonical_json_bytes
from agentbus.trace.version import TRACE_SCHEMA_NAME, TRACE_SCHEMA_VERSION

if TYPE_CHECKING:
    from agentbus.execution.state_store import StateStore
    from agentbus.git.repository import GitRepository
    from agentbus.trace.storage import ContentAddressedStore


DEFAULT_POLICY_VERSION = "agentbus.tool-policy.v1"
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_MAX_EVIDENCE_RECORDS = MAX_TRACE_ITEMS // 4


def seal_run_provenance(
    trace: Trace,
    *,
    state_store: StateStore,
    object_store: ContentAddressedStore | None,
    configuration: Mapping[str, Any],
    task_graph: Mapping[str, Any],
    final_repository_tree_sha256: str | None,
    policy_version: str = DEFAULT_POLICY_VERSION,
    policy_document: Mapping[str, Any] | None = None,
) -> ProvenanceManifest:
    if trace.completed_at is None:
        raise TraceIntegrityError(
            "A running execution trace cannot be sealed as provenance."
        )
    referenced_hashes = _referenced_hashes(trace)
    payloads, available_hashes = _load_referenced_payloads(
        object_store,
        referenced_hashes,
    )
    classification = ReplayabilityClassifier().classify_trace(
        trace,
        available_object_hashes=available_hashes,
    )
    approvals, audits, artifacts = _durable_evidence(
        state_store,
        trace.run_id,
    )
    manifest = ProvenanceBuilder().build(
        trace,
        configuration=dict(configuration),
        provider_routes=_provider_routes(trace),
        tool_descriptors=_tool_descriptors(trace),
        policy_version=policy_version,
        policy_document=dict(
            policy_document
            if policy_document is not None
            else DEFAULT_TOOL_POLICY.model_dump(mode="json")
        ),
        protocol_hashes=_protocol_hashes(),
        task_graph=dict(task_graph),
        approvals=approvals,
        audit_entries=audits,
        artifacts=artifacts,
        final_repository_tree_sha256=final_repository_tree_sha256,
        replayability=classification.level,
        replayability_reasons=classification.reasons,
    )
    verify_provenance(
        manifest,
        trace,
        approvals=approvals,
        audit_entries=audits,
        artifacts=artifacts,
        blob_payloads=payloads,
    )
    return state_store.record_provenance_manifest(manifest)


def repository_state_sha256(
    repository: GitRepository,
    *,
    changed_files: Iterable[str] = (),
    commit_identifier: str | None = None,
) -> str:
    fingerprint = getattr(repository, "repository_state_sha256", None)
    if callable(fingerprint):
        digest = fingerprint()
        if not isinstance(digest, str) or not _SHA256.fullmatch(digest):
            raise TraceIntegrityError(
                "Repository state fingerprint is not a SHA-256 digest."
            )
        return digest
    head_commit = repository.head_commit(short=False)
    document = {
        "head_commit": head_commit,
        "commit_identifier": commit_identifier,
        "changed_path_hashes": sorted(
            hashlib.sha256(path.encode("utf-8")).hexdigest()
            for path in changed_files
        ),
    }
    return hashlib.sha256(canonical_json_bytes(document)).hexdigest()


def _load_referenced_payloads(
    object_store: ContentAddressedStore | None,
    referenced_hashes: set[str],
) -> tuple[dict[str, bytes], set[str]]:
    if not referenced_hashes:
        return {}, set()
    if object_store is None:
        raise TraceIntegrityError(
            "Trace objects are unavailable for provenance verification."
        )
    payloads = {
        digest: object_store.get(digest).data
        for digest in sorted(referenced_hashes)
    }
    return payloads, set(payloads)


def _referenced_hashes(trace: Trace) -> set[str]:
    return {
        reference.sha256
        for span in trace.spans
        for reference in (*span.input_references, *span.output_references)
    }


def _provider_routes(trace: Trace) -> list[ProviderRouteProvenance]:
    routes: dict[tuple[str, str, str, str | None], ProviderRouteProvenance] = {}
    for span in trace.spans:
        if span.span_type != TraceSpanType.PROVIDER_RESPONSE:
            continue
        role = _nonempty_text(span.attributes.get("role"))
        provider = _nonempty_text(span.attributes.get("provider"))
        model = _nonempty_text(span.attributes.get("model"))
        if role is None or provider is None or model is None:
            continue
        deployment = model if provider.lower() == "azure" else None
        route = ProviderRouteProvenance(
            role=role,
            provider=provider,
            model_identifier=model,
            deployment_identifier=deployment,
        )
        key = (role, provider, model, deployment)
        routes[key] = route
    return [routes[key] for key in sorted(routes)]


def _tool_descriptors(trace: Trace) -> list[ToolDescriptorProvenance]:
    descriptors: dict[
        tuple[str, str, str],
        ToolDescriptorProvenance,
    ] = {}
    for span in trace.spans:
        if span.span_type != TraceSpanType.TOOL_INVOCATION:
            continue
        name = _nonempty_text(
            span.attributes.get("tool_name")
        ) or _nonempty_text(span.name)
        version = _tool_version(span.attributes.get("tool_version"))
        protocol = _nonempty_text(
            span.attributes.get("descriptor_protocol_version")
        )
        if name is None or version is None or protocol is None:
            continue
        document = {
            "name": name,
            "version": version,
            "protocol_version": protocol,
        }
        descriptor = ToolDescriptorProvenance(
            **document,
            descriptor_sha256=hashlib.sha256(
                canonical_json_bytes(document)
            ).hexdigest(),
        )
        descriptors[(name, version, protocol)] = descriptor
    return [descriptors[key] for key in sorted(descriptors)]


def _tool_version(value: Any) -> str | None:
    if isinstance(value, Mapping):
        try:
            return ".".join(
                str(int(value.get(part, 0)))
                for part in ("major", "minor", "patch")
            )
        except (TypeError, ValueError):
            return None
    return _nonempty_text(value)


def _protocol_hashes() -> dict[str, str]:
    documents = {
        "control": {
            "name": "agentbus.control",
            "version": CONTROL_PROTOCOL_VERSION,
        },
        "replay_checkpoint": {
            "name": "agentbus.replay.checkpoint",
            "version": 1,
        },
        "tool": {
            "name": TOOL_PROTOCOL_NAME,
            "version": TOOL_PROTOCOL_VERSION,
        },
        "trace": {
            "name": TRACE_SCHEMA_NAME,
            "version": TRACE_SCHEMA_VERSION,
        },
    }
    return {
        name: hashlib.sha256(canonical_json_bytes(document)).hexdigest()
        for name, document in sorted(documents.items())
    }


def _durable_evidence(
    state_store: StateStore,
    run_id: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    snapshot = state_store.load_snapshot(run_id)
    approvals = [
        {
            "kind": "task",
            **approval.model_dump(mode="json"),
        }
        for approval in snapshot.approvals
    ]
    approvals.extend(
        {
            "kind": "tool",
            **approval.model_dump(mode="json"),
        }
        for approval in _all_tool_approvals(state_store, run_id)
    )
    audits = [
        audit.model_dump(mode="json")
        for audit in _all_tool_audits(state_store, run_id)
    ]
    artifacts = [
        artifact.model_dump(mode="json")
        for artifact in snapshot.artifacts
    ]
    if max(len(approvals), len(audits), len(artifacts)) > _MAX_EVIDENCE_RECORDS:
        raise TraceIntegrityError(
            "Durable provenance evidence exceeds the configured record bound."
        )
    return approvals, audits, artifacts


def _all_tool_approvals(state_store: StateStore, run_id: str) -> list[Any]:
    records: list[Any] = []
    cursor = 0
    while len(records) <= _MAX_EVIDENCE_RECORDS:
        page = state_store.list_tool_approvals(
            run_id,
            after_sequence=cursor,
            limit=1_000,
        )
        if not page:
            return records
        records.extend(page)
        cursor = page[-1].approval_sequence
    raise TraceIntegrityError(
        "Tool approval provenance exceeds the configured record bound."
    )


def _all_tool_audits(state_store: StateStore, run_id: str) -> list[Any]:
    records: list[Any] = []
    cursor = 0
    while len(records) <= _MAX_EVIDENCE_RECORDS:
        page = state_store.list_tool_audits(
            run_id,
            after_sequence=cursor,
            limit=1_000,
        )
        if not page:
            return records
        records.extend(page)
        cursor = page[-1].audit_sequence
    raise TraceIntegrityError(
        "Tool audit provenance exceeds the configured record bound."
    )


def _nonempty_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


__all__ = [
    "DEFAULT_POLICY_VERSION",
    "repository_state_sha256",
    "seal_run_provenance",
]
