from __future__ import annotations

import hashlib
import locale
import os
import platform
import threading
import time
from collections.abc import Iterable, Mapping
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, Generic, TypeVar
from uuid import UUID

from pydantic import Field, field_validator, model_validator

from agentbus.security.redaction import redact_text
from agentbus.trace.models import (
    Sha256Digest,
    Trace,
    TraceIdentifier,
    TraceModel,
    TraceSpan,
    TraceSpanType,
)
from agentbus.trace.redaction import canonical_json_bytes, sanitize_document

NONDETERMINISM_SCHEMA_VERSION = 1
_Value = TypeVar("_Value")


class NondeterminismSource(str, Enum):
    WALL_CLOCK = "wall_clock"
    RANDOM_UUID = "random_uuid"
    UNSEEDED_RANDOMNESS = "unseeded_randomness"
    MAPPING_ORDER = "mapping_order"
    FILESYSTEM_ORDER = "filesystem_order"
    PROCESS_SCHEDULING = "process_scheduling"
    ENVIRONMENT = "environment"
    TEMPORARY_PATH = "temporary_path"
    GIT_CONFIGURATION = "git_configuration"
    LOCALE = "locale"
    LINE_ENDINGS = "line_endings"
    PROVIDER_VARIATION = "provider_variation"
    MCP_VARIATION = "mcp_variation"
    TOOL_OUTPUT_ORDER = "tool_output_order"


class NondeterminismDisposition(str, Enum):
    CONTROLLED = "controlled"
    CAPTURED = "captured"
    SUBSTITUTED = "substituted"
    OBSERVED_ONLY = "observed_only"
    UNRESOLVED = "unresolved"


class NondeterminismFinding(TraceModel):
    source: NondeterminismSource
    disposition: NondeterminismDisposition
    reason: str = Field(min_length=1, max_length=1_000)
    span_ids: list[TraceIdentifier] = Field(default_factory=list, max_length=1_024)
    evidence: dict[str, Any] = Field(default_factory=dict)

    @field_validator("reason")
    @classmethod
    def reason_is_safe(cls, value: str) -> str:
        return redact_text(value, max_chars=1_000) or "unspecified"

    @field_validator("span_ids")
    @classmethod
    def span_ids_are_unique(cls, value: list[str]) -> list[str]:
        return list(dict.fromkeys(value))

    @field_validator("evidence")
    @classmethod
    def evidence_is_sanitized(cls, value: dict[str, Any]) -> dict[str, Any]:
        sanitized = sanitize_document(value, private_roots=(Path.home(),)).value
        if not isinstance(sanitized, dict):
            raise ValueError("nondeterminism evidence must be an object")
        return sanitized

    def as_span_attribute(self) -> dict[str, Any]:
        return {
            "source": self.source.value,
            "disposition": self.disposition.value,
            "reason": self.reason,
        }


class EnvironmentFingerprint(TraceModel):
    schema_version: int = NONDETERMINISM_SCHEMA_VERSION
    operating_system_sha256: Sha256Digest
    python_sha256: Sha256Digest
    locale_sha256: Sha256Digest
    timezone_sha256: Sha256Digest
    line_endings_sha256: Sha256Digest
    environment_sha256: Sha256Digest
    git_configuration_sha256: Sha256Digest
    combined_sha256: Sha256Digest

    @model_validator(mode="after")
    def combined_hash_is_valid(self) -> "EnvironmentFingerprint":
        expected = _combined_fingerprint(
            operating_system_sha256=self.operating_system_sha256,
            python_sha256=self.python_sha256,
            locale_sha256=self.locale_sha256,
            timezone_sha256=self.timezone_sha256,
            line_endings_sha256=self.line_endings_sha256,
            environment_sha256=self.environment_sha256,
            git_configuration_sha256=self.git_configuration_sha256,
        )
        if self.combined_sha256 != expected:
            raise ValueError("environment fingerprint component hash mismatch")
        return self


class NondeterminismReport(TraceModel):
    schema_version: int = NONDETERMINISM_SCHEMA_VERSION
    trace_id: TraceIdentifier
    run_id: TraceIdentifier
    findings: list[NondeterminismFinding] = Field(
        default_factory=list,
        max_length=10_000,
    )
    environment: EnvironmentFingerprint | None = None

    @property
    def unresolved_sources(self) -> list[NondeterminismSource]:
        return sorted(
            {
                finding.source
                for finding in self.findings
                if finding.disposition == NondeterminismDisposition.UNRESOLVED
            },
            key=lambda source: source.value,
        )


class RecordedValueUnavailableError(ValueError):
    """Raised when deterministic replay exhausts a captured value stream."""


class RecordedValueStream(Generic[_Value]):
    """Inject captured values without changing process-global time or randomness."""

    def __init__(self, values: Iterable[_Value], *, source: str) -> None:
        self._values = tuple(values)
        self._source = source
        self._position = 0
        self._lock = threading.Lock()

    @property
    def remaining(self) -> int:
        with self._lock:
            return len(self._values) - self._position

    def take(self) -> _Value:
        with self._lock:
            if self._position >= len(self._values):
                raise RecordedValueUnavailableError(
                    f"captured {self._source} values are exhausted"
                )
            value = self._values[self._position]
            self._position += 1
            return value


class DeterministicInputProvider:
    """Explicit replay dependencies for code that normally reads host state."""

    def __init__(
        self,
        *,
        wall_clock_values: Iterable[datetime] = (),
        uuid_values: Iterable[UUID] = (),
        random_values: Iterable[float] = (),
    ) -> None:
        self._wall_clock = RecordedValueStream(
            wall_clock_values,
            source=NondeterminismSource.WALL_CLOCK.value,
        )
        self._uuids = RecordedValueStream(
            uuid_values,
            source=NondeterminismSource.RANDOM_UUID.value,
        )
        self._random = RecordedValueStream(
            random_values,
            source=NondeterminismSource.UNSEEDED_RANDOMNESS.value,
        )

    def now(self) -> datetime:
        value = self._wall_clock.take()
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("captured wall-clock values must be timezone-aware")
        return value

    def uuid4(self) -> UUID:
        return self._uuids.take()

    def random(self) -> float:
        value = self._random.take()
        if not 0.0 <= value < 1.0:
            raise ValueError("captured random values must be in the interval [0, 1)")
        return value


class NondeterminismDetector:
    """Detect and explain replay inputs that may vary between executions."""

    def detect_trace(
        self,
        trace: Trace,
        *,
        environment: EnvironmentFingerprint | None = None,
    ) -> NondeterminismReport:
        findings: list[NondeterminismFinding] = []
        for span in sorted(trace.spans, key=lambda item: item.sequence):
            findings.extend(self.detect_span(span))

        worker_spans = [span for span in trace.spans if span.worker_id is not None]
        workers = {span.worker_id for span in worker_spans}
        if len(workers) > 1:
            findings.append(
                NondeterminismFinding(
                    source=NondeterminismSource.PROCESS_SCHEDULING,
                    disposition=NondeterminismDisposition.OBSERVED_ONLY,
                    reason=(
                        "Concurrent worker ordering is recorded, but host scheduling "
                        "cannot be reproduced exactly."
                    ),
                    span_ids=[span.span_id for span in worker_spans],
                    evidence={"worker_count": len(workers)},
                )
            )
        if environment is not None:
            findings.append(
                NondeterminismFinding(
                    source=NondeterminismSource.ENVIRONMENT,
                    disposition=NondeterminismDisposition.CAPTURED,
                    reason=(
                        "Host-dependent values are represented by sanitized hashes "
                        "for drift detection."
                    ),
                    span_ids=[trace.root_span_id],
                    evidence={"fingerprint": environment.combined_sha256},
                )
            )
        return NondeterminismReport(
            trace_id=trace.trace_id,
            run_id=trace.run_id,
            findings=_deduplicate_findings(findings),
            environment=environment,
        )

    def detect_span(self, span: TraceSpan) -> list[NondeterminismFinding]:
        findings = _declared_findings(span)
        findings.extend(_provider_findings(span))
        findings.extend(_attribute_findings(span))
        return _deduplicate_findings(findings)


def environment_fingerprint(
    *,
    environment: Mapping[str, str] | None = None,
    git_configuration: Mapping[str, str] | None = None,
    operating_system: Mapping[str, str] | None = None,
    python: Mapping[str, Any] | None = None,
    locale_name: str | None = None,
    timezone_names: Iterable[str] | None = None,
    line_ending: str | None = None,
) -> EnvironmentFingerprint:
    """Hash bounded host dimensions without retaining their raw values."""

    os_values = operating_system or {
        "system": platform.system(),
        "release": platform.release(),
        "machine": platform.machine(),
    }
    python_values = python or {
        "implementation": platform.python_implementation(),
        "version": platform.python_version(),
    }
    current_locale = locale_name
    if current_locale is None:
        locale_parts = locale.getlocale()
        current_locale = ".".join(item for item in locale_parts if item) or "unknown"
    current_timezones = tuple(timezone_names or time.tzname)
    safe_environment = sanitize_document(
        dict(environment or {}),
        private_roots=(Path.home(),),
    ).value
    safe_git_configuration = sanitize_document(
        dict(git_configuration or {}),
        private_roots=(Path.home(),),
    ).value
    components = {
        "operating_system_sha256": _hash_json(os_values),
        "python_sha256": _hash_json(python_values),
        "locale_sha256": _hash_json(current_locale),
        "timezone_sha256": _hash_json(current_timezones),
        "line_endings_sha256": _hash_json(line_ending or os.linesep),
        "environment_sha256": _hash_json(safe_environment),
        "git_configuration_sha256": _hash_json(safe_git_configuration),
    }
    return EnvironmentFingerprint(
        **components,
        combined_sha256=_combined_fingerprint(**components),
    )


def environment_drift(
    left: EnvironmentFingerprint,
    right: EnvironmentFingerprint,
) -> list[NondeterminismSource]:
    sources: list[NondeterminismSource] = []
    comparisons = (
        (
            NondeterminismSource.ENVIRONMENT,
            left.environment_sha256,
            right.environment_sha256,
        ),
        (
            NondeterminismSource.GIT_CONFIGURATION,
            left.git_configuration_sha256,
            right.git_configuration_sha256,
        ),
        (NondeterminismSource.LOCALE, left.locale_sha256, right.locale_sha256),
        (
            NondeterminismSource.LINE_ENDINGS,
            left.line_endings_sha256,
            right.line_endings_sha256,
        ),
    )
    for source, left_hash, right_hash in comparisons:
        if left_hash != right_hash:
            sources.append(source)
    if (
        left.operating_system_sha256 != right.operating_system_sha256
        or left.python_sha256 != right.python_sha256
        or left.timezone_sha256 != right.timezone_sha256
    ):
        sources.append(NondeterminismSource.ENVIRONMENT)
    return list(dict.fromkeys(sources))


def annotate_span_nondeterminism(
    span: TraceSpan,
    findings: Iterable[NondeterminismFinding],
) -> TraceSpan:
    attributes = dict(span.attributes)
    existing = attributes.get("nondeterminism", [])
    if not isinstance(existing, list):
        existing = []
    additions = [
        finding.as_span_attribute()
        for finding in findings
        if not finding.span_ids or span.span_id in finding.span_ids
    ]
    attributes["nondeterminism"] = [*existing, *additions]
    copied = span.model_copy(deep=True)
    copied.attributes = attributes
    return copied


def _declared_findings(span: TraceSpan) -> list[NondeterminismFinding]:
    declared = span.attributes.get("nondeterminism")
    if not isinstance(declared, list):
        return []
    findings = []
    for item in declared:
        if not isinstance(item, dict):
            continue
        try:
            source = NondeterminismSource(str(item["source"]))
            disposition = NondeterminismDisposition(str(item["disposition"]))
        except (KeyError, ValueError):
            continue
        findings.append(
            NondeterminismFinding(
                source=source,
                disposition=disposition,
                reason=str(
                    item.get(
                        "reason",
                        f"{source.value} was declared by the recorded span.",
                    )
                ),
                span_ids=[span.span_id],
            )
        )
    return findings


def _provider_findings(span: TraceSpan) -> list[NondeterminismFinding]:
    if span.span_type != TraceSpanType.PROVIDER_RESPONSE:
        return []
    provider = str(span.attributes.get("provider", "unknown")).lower()
    if provider == "deterministic":
        disposition = NondeterminismDisposition.CONTROLLED
        reason = "The provider route is deterministic."
    elif span.output_references:
        disposition = NondeterminismDisposition.CAPTURED
        reason = "The bounded provider response can substitute for provider variation."
    else:
        disposition = NondeterminismDisposition.UNRESOLVED
        reason = "Provider variation is unresolved because no response was captured."
    return [
        NondeterminismFinding(
            source=NondeterminismSource.PROVIDER_VARIATION,
            disposition=disposition,
            reason=reason,
            span_ids=[span.span_id],
            evidence={"provider": provider},
        )
    ]


def _attribute_findings(span: TraceSpan) -> list[NondeterminismFinding]:
    specifications = (
        ("uses_wall_clock", NondeterminismSource.WALL_CLOCK),
        ("uses_random_uuid", NondeterminismSource.RANDOM_UUID),
        ("uses_unseeded_randomness", NondeterminismSource.UNSEEDED_RANDOMNESS),
        ("mapping_order_unstable", NondeterminismSource.MAPPING_ORDER),
        ("filesystem_order_unstable", NondeterminismSource.FILESYSTEM_ORDER),
        ("temporary_path_dependent", NondeterminismSource.TEMPORARY_PATH),
        ("git_configuration_dependent", NondeterminismSource.GIT_CONFIGURATION),
        ("locale_dependent", NondeterminismSource.LOCALE),
        ("line_ending_dependent", NondeterminismSource.LINE_ENDINGS),
        ("mcp_external", NondeterminismSource.MCP_VARIATION),
        ("tool_output_order_unstable", NondeterminismSource.TOOL_OUTPUT_ORDER),
    )
    findings = []
    for flag, source in specifications:
        if span.attributes.get(flag) is not True:
            continue
        disposition_value = span.attributes.get(
            f"{source.value}_disposition",
            NondeterminismDisposition.UNRESOLVED.value,
        )
        try:
            disposition = NondeterminismDisposition(str(disposition_value))
        except ValueError:
            disposition = NondeterminismDisposition.UNRESOLVED
        findings.append(
            NondeterminismFinding(
                source=source,
                disposition=disposition,
                reason=(
                    f"The span depends on {source.value.replace('_', ' ')}; "
                    f"its recorded disposition is {disposition.value}."
                ),
                span_ids=[span.span_id],
            )
        )
    return findings


def _deduplicate_findings(
    findings: Iterable[NondeterminismFinding],
) -> list[NondeterminismFinding]:
    deduplicated: dict[tuple[str, str, tuple[str, ...]], NondeterminismFinding] = {}
    for finding in findings:
        key = (
            finding.source.value,
            finding.disposition.value,
            tuple(finding.span_ids),
        )
        deduplicated.setdefault(key, finding)
    return list(deduplicated.values())


def _hash_json(value: Any) -> str:
    sanitized = sanitize_document(value, private_roots=(Path.home(),))
    return hashlib.sha256(sanitized.canonical_bytes).hexdigest()


def _combined_fingerprint(
    *,
    operating_system_sha256: str,
    python_sha256: str,
    locale_sha256: str,
    timezone_sha256: str,
    line_endings_sha256: str,
    environment_sha256: str,
    git_configuration_sha256: str,
) -> str:
    return hashlib.sha256(
        canonical_json_bytes(
            {
                "environment": environment_sha256,
                "git_configuration": git_configuration_sha256,
                "line_endings": line_endings_sha256,
                "locale": locale_sha256,
                "operating_system": operating_system_sha256,
                "python": python_sha256,
                "timezone": timezone_sha256,
            }
        )
    ).hexdigest()


__all__ = [
    "DeterministicInputProvider",
    "EnvironmentFingerprint",
    "NONDETERMINISM_SCHEMA_VERSION",
    "NondeterminismDetector",
    "NondeterminismDisposition",
    "NondeterminismFinding",
    "NondeterminismReport",
    "NondeterminismSource",
    "RecordedValueStream",
    "RecordedValueUnavailableError",
    "annotate_span_nondeterminism",
    "environment_drift",
    "environment_fingerprint",
]
