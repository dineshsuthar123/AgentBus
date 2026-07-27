from __future__ import annotations

import hashlib
import platform
from collections.abc import Iterable, Mapping
from datetime import datetime
from enum import Enum
from typing import Any, Callable

from pydantic import Field, field_validator

from agentbus import __version__ as agentbus_version
from agentbus.security.redaction import redact_text
from agentbus.trace.errors import TraceIntegrityError
from agentbus.trace.models import (
    MAX_SAFE_TEXT_CHARS,
    MAX_TRACE_ITEMS,
    Sha256Digest,
    Trace,
    TraceIdentifier,
    TraceModel,
    utc_now,
)
from agentbus.trace.redaction import (
    canonical_json_bytes,
    configuration_fingerprint,
    sanitize_document,
)
from agentbus.trace.version import TRACE_SCHEMA_VERSION

PROVENANCE_SCHEMA_VERSION = 1
PROVENANCE_INTEGRITY_ALGORITHM = "sha256-chain-v1"


class ReplayabilityLevel(str, Enum):
    EXACTLY_REPLAYABLE = "exactly_replayable"
    DETERMINISTICALLY_SUBSTITUTABLE = "deterministically_substitutable"
    PARTIALLY_REPLAYABLE = "partially_replayable"
    OBSERVATIONAL_ONLY = "observational_only"
    NON_REPLAYABLE = "non_replayable"


class ProviderRouteProvenance(TraceModel):
    role: str = Field(min_length=1, max_length=128)
    provider: str = Field(min_length=1, max_length=128)
    model_identifier: str = Field(min_length=1, max_length=256)
    deployment_identifier: str | None = Field(default=None, max_length=256)

    @field_validator(
        "role",
        "provider",
        "model_identifier",
        "deployment_identifier",
    )
    @classmethod
    def fields_are_safe(cls, value: str | None) -> str | None:
        return _safe_manifest_text(value)


class ToolDescriptorProvenance(TraceModel):
    name: str = Field(min_length=1, max_length=256)
    version: str = Field(min_length=1, max_length=128)
    protocol_version: str = Field(min_length=1, max_length=128)
    descriptor_sha256: Sha256Digest

    @field_validator("name", "version", "protocol_version")
    @classmethod
    def fields_are_safe(cls, value: str) -> str:
        return _safe_manifest_text(value) or ""


class EventStreamRange(TraceModel):
    first_sequence: int | None = Field(default=None, ge=1)
    last_sequence: int | None = Field(default=None, ge=1)
    event_count: int = Field(default=0, ge=0)


class ProvenanceIntegrityEntry(TraceModel):
    kind: str = Field(min_length=1, max_length=64)
    identifier: str = Field(min_length=1, max_length=256)
    sha256: Sha256Digest


class ProvenanceManifest(TraceModel):
    schema_version: int = PROVENANCE_SCHEMA_VERSION
    trace_id: TraceIdentifier
    run_id: TraceIdentifier
    generated_at: datetime
    agentbus_version: str = Field(min_length=1, max_length=128)
    trace_schema_version: int = TRACE_SCHEMA_VERSION
    operating_system: str = Field(min_length=1, max_length=256)
    python_version: str = Field(min_length=1, max_length=128)
    node_version: str | None = Field(default=None, max_length=128)
    vscode_version: str | None = Field(default=None, max_length=128)
    configuration_fingerprint: Sha256Digest
    provider_routes: list[ProviderRouteProvenance] = Field(
        default_factory=list,
        max_length=256,
    )
    tool_descriptors: list[ToolDescriptorProvenance] = Field(
        default_factory=list,
        max_length=4_096,
    )
    policy_version: str = Field(min_length=1, max_length=128)
    policy_sha256: Sha256Digest
    protocol_hashes: dict[str, Sha256Digest] = Field(default_factory=dict)
    input_object_hashes: list[Sha256Digest] = Field(default_factory=list)
    output_object_hashes: list[Sha256Digest] = Field(default_factory=list)
    task_graph_sha256: Sha256Digest
    event_stream: EventStreamRange
    final_repository_tree_sha256: Sha256Digest | None = None
    generated_artifact_hashes: list[Sha256Digest] = Field(default_factory=list)
    replayability: ReplayabilityLevel
    replayability_reasons: list[str] = Field(
        default_factory=list,
        max_length=1_024,
    )
    integrity_algorithm: str = PROVENANCE_INTEGRITY_ALGORITHM
    integrity_entries: list[ProvenanceIntegrityEntry] = Field(
        default_factory=list,
        max_length=MAX_TRACE_ITEMS,
    )
    integrity_root: Sha256Digest

    @field_validator("schema_version")
    @classmethod
    def schema_is_supported(cls, value: int) -> int:
        if value != PROVENANCE_SCHEMA_VERSION:
            raise ValueError(f"unsupported provenance schema version: {value}")
        return value

    @field_validator("trace_schema_version")
    @classmethod
    def trace_schema_is_supported(cls, value: int) -> int:
        if value != TRACE_SCHEMA_VERSION:
            raise ValueError(f"unsupported trace schema version: {value}")
        return value

    @field_validator(
        "input_object_hashes",
        "output_object_hashes",
        "generated_artifact_hashes",
    )
    @classmethod
    def hashes_are_sorted_and_unique(cls, value: list[str]) -> list[str]:
        if value != sorted(set(value)):
            raise ValueError("provenance object hashes must be sorted and unique")
        return value

    @field_validator(
        "agentbus_version",
        "operating_system",
        "python_version",
        "node_version",
        "vscode_version",
        "policy_version",
    )
    @classmethod
    def text_is_safe(cls, value: str | None) -> str | None:
        return _safe_manifest_text(value)


class ProvenanceBuilder:
    def __init__(
        self,
        *,
        clock: Callable[[], datetime] = utc_now,
        system_name: str | None = None,
        python_version: str | None = None,
    ):
        self.clock = clock
        self.system_name = system_name or (
            f"{platform.system()} {platform.release()}".strip()
        )
        self.python_version = python_version or platform.python_version()

    def build(
        self,
        trace: Trace,
        *,
        configuration: Mapping[str, Any],
        provider_routes: Iterable[ProviderRouteProvenance] = (),
        tool_descriptors: Iterable[ToolDescriptorProvenance] = (),
        policy_version: str,
        policy_document: Mapping[str, Any],
        protocol_hashes: Mapping[str, str] | None = None,
        task_graph: Mapping[str, Any],
        additional_blob_hashes: Iterable[str] = (),
        approvals: Iterable[Mapping[str, Any]] = (),
        audit_entries: Iterable[Mapping[str, Any]] = (),
        artifacts: Iterable[Mapping[str, Any]] = (),
        final_repository_tree_sha256: str | None = None,
        replayability: ReplayabilityLevel,
        replayability_reasons: Iterable[str] = (),
        node_version: str | None = None,
        vscode_version: str | None = None,
    ) -> ProvenanceManifest:
        input_hashes = _trace_reference_hashes(trace, inputs=True)
        output_hashes = _trace_reference_hashes(trace, inputs=False)
        all_blob_hashes = sorted(
            {
                *input_hashes,
                *output_hashes,
                *additional_blob_hashes,
            }
        )
        artifact_hashes = sorted(
            {
                reference.sha256
                for span in trace.spans
                for reference in span.artifact_references
                if reference.sha256 is not None
            }
        )
        event_sequences = [event.sequence for event in trace.events]
        manifest = ProvenanceManifest(
            trace_id=trace.trace_id,
            run_id=trace.run_id,
            generated_at=self.clock(),
            agentbus_version=agentbus_version,
            operating_system=self.system_name,
            python_version=self.python_version,
            node_version=node_version,
            vscode_version=vscode_version,
            configuration_fingerprint=configuration_fingerprint(
                dict(configuration)
            ),
            provider_routes=sorted(
                provider_routes,
                key=lambda item: (item.role, item.provider, item.model_identifier),
            ),
            tool_descriptors=sorted(
                tool_descriptors,
                key=lambda item: (item.name, item.version),
            ),
            policy_version=policy_version,
            policy_sha256=_document_sha256(dict(policy_document)),
            protocol_hashes=dict(sorted((protocol_hashes or {}).items())),
            input_object_hashes=input_hashes,
            output_object_hashes=output_hashes,
            task_graph_sha256=_document_sha256(dict(task_graph)),
            event_stream=EventStreamRange(
                first_sequence=min(event_sequences) if event_sequences else None,
                last_sequence=max(event_sequences) if event_sequences else None,
                event_count=len(event_sequences),
            ),
            final_repository_tree_sha256=final_repository_tree_sha256,
            generated_artifact_hashes=artifact_hashes,
            replayability=replayability,
            replayability_reasons=[
                _bounded_reason(reason) for reason in replayability_reasons
            ],
            integrity_entries=[],
            integrity_root="0" * 64,
        )
        entries = _integrity_entries(
            manifest,
            trace,
            blob_hashes=all_blob_hashes,
            approvals=approvals,
            audit_entries=audit_entries,
            artifacts=artifacts,
        )
        return ProvenanceManifest.model_validate(
            manifest.model_copy(
                update={
                    "integrity_entries": entries,
                    "integrity_root": _integrity_root(entries),
                }
            ).model_dump()
        )


def verify_provenance(
    manifest: ProvenanceManifest,
    trace: Trace,
    *,
    additional_blob_hashes: Iterable[str] = (),
    approvals: Iterable[Mapping[str, Any]] = (),
    audit_entries: Iterable[Mapping[str, Any]] = (),
    artifacts: Iterable[Mapping[str, Any]] = (),
    blob_payloads: Mapping[str, bytes] | None = None,
) -> None:
    if manifest.trace_id != trace.trace_id or manifest.run_id != trace.run_id:
        raise TraceIntegrityError(
            "Provenance manifest does not identify the supplied trace."
        )
    referenced = {
        *_trace_reference_hashes(trace, inputs=True),
        *_trace_reference_hashes(trace, inputs=False),
        *additional_blob_hashes,
    }
    if blob_payloads is not None:
        for digest in referenced:
            payload = blob_payloads.get(digest)
            if payload is None:
                raise TraceIntegrityError(
                    f"Provenance blob '{digest}' is unavailable."
                )
            if hashlib.sha256(payload).hexdigest() != digest:
                raise TraceIntegrityError(
                    f"Provenance blob '{digest}' failed hash validation."
                )
    entries = _integrity_entries(
        manifest,
        trace,
        blob_hashes=sorted(referenced),
        approvals=approvals,
        audit_entries=audit_entries,
        artifacts=artifacts,
    )
    if entries != manifest.integrity_entries:
        raise TraceIntegrityError("Provenance integrity entries do not match.")
    actual_root = _integrity_root(entries)
    if actual_root != manifest.integrity_root:
        raise TraceIntegrityError("Provenance integrity root does not match.")


def _integrity_entries(
    manifest: ProvenanceManifest,
    trace: Trace,
    *,
    blob_hashes: Iterable[str],
    approvals: Iterable[Mapping[str, Any]],
    audit_entries: Iterable[Mapping[str, Any]],
    artifacts: Iterable[Mapping[str, Any]],
) -> list[ProvenanceIntegrityEntry]:
    entries = [
        _entry(
            "manifest",
            manifest.trace_id,
            _manifest_core(manifest),
        )
    ]
    entries.extend(
        _entry("span", span.span_id, span.model_dump(mode="json"))
        for span in sorted(trace.spans, key=lambda item: item.sequence)
    )
    entries.extend(
        _entry("event", event.event_id, event.model_dump(mode="json"))
        for event in sorted(trace.events, key=lambda item: item.sequence)
    )
    entries.extend(
        _entry(
            "checkpoint",
            checkpoint.checkpoint_id,
            checkpoint.model_dump(mode="json"),
        )
        for checkpoint in sorted(
            trace.checkpoints,
            key=lambda item: item.sequence,
        )
    )
    entries.extend(
        ProvenanceIntegrityEntry(
            kind="blob",
            identifier=digest,
            sha256=digest,
        )
        for digest in sorted(set(blob_hashes))
    )
    entries.extend(_record_entries("approval", approvals))
    entries.extend(_record_entries("audit", audit_entries))
    entries.extend(_record_entries("artifact", artifacts))
    if len(entries) > MAX_TRACE_ITEMS:
        raise ValueError("Provenance integrity entry count exceeds its bound.")
    return entries


def _manifest_core(manifest: ProvenanceManifest) -> dict[str, Any]:
    return manifest.model_dump(
        mode="json",
        exclude={"integrity_entries", "integrity_root"},
    )


def _entry(
    kind: str,
    identifier: str,
    value: Any,
) -> ProvenanceIntegrityEntry:
    return ProvenanceIntegrityEntry(
        kind=kind,
        identifier=identifier,
        sha256=hashlib.sha256(canonical_json_bytes(value)).hexdigest(),
    )


def _record_entries(
    kind: str,
    records: Iterable[Mapping[str, Any]],
) -> list[ProvenanceIntegrityEntry]:
    sanitized = [
        sanitize_document(dict(record)).canonical_bytes for record in records
    ]
    sanitized.sort()
    return [
        ProvenanceIntegrityEntry(
            kind=kind,
            identifier=f"{kind}-{index:08d}",
            sha256=hashlib.sha256(payload).hexdigest(),
        )
        for index, payload in enumerate(sanitized, start=1)
    ]


def _integrity_root(entries: list[ProvenanceIntegrityEntry]) -> str:
    current = hashlib.sha256(b"agentbus-provenance:sha256-chain-v1").digest()
    for entry in entries:
        leaf = canonical_json_bytes(entry.model_dump(mode="json"))
        current = hashlib.sha256(current + hashlib.sha256(leaf).digest()).digest()
    return current.hex()


def _trace_reference_hashes(trace: Trace, *, inputs: bool) -> list[str]:
    if inputs:
        values = (
            reference.sha256
            for span in trace.spans
            for reference in span.input_references
        )
    else:
        values = (
            reference.sha256
            for span in trace.spans
            for reference in span.output_references
        )
    return sorted(set(values))


def _document_sha256(value: Mapping[str, Any]) -> str:
    document = sanitize_document(dict(value))
    return hashlib.sha256(document.canonical_bytes).hexdigest()


def _bounded_reason(value: str) -> str:
    text = sanitize_document(
        str(value),
        max_text_chars=MAX_SAFE_TEXT_CHARS,
    ).value
    return str(text)


def _safe_manifest_text(value: str | None) -> str | None:
    if value is None:
        return None
    return redact_text(value, max_chars=1_024)


__all__ = [
    "PROVENANCE_INTEGRITY_ALGORITHM",
    "PROVENANCE_SCHEMA_VERSION",
    "EventStreamRange",
    "ProvenanceBuilder",
    "ProvenanceIntegrityEntry",
    "ProvenanceManifest",
    "ProviderRouteProvenance",
    "ReplayabilityLevel",
    "ToolDescriptorProvenance",
    "verify_provenance",
]
