from __future__ import annotations

from datetime import datetime, timezone
import re
from typing import Annotated, Literal

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, StrictBool, field_validator, model_validator

_CREDENTIAL_SHAPE = re.compile(
    r"\b(?:sk-[A-Za-z0-9_-]{20,}|[sr]k_(?:live|test)_[A-Za-z0-9]{16,}|"
    r"gh[pousr]_[A-Za-z0-9]{30,}|(?:AKIA|ASIA)[A-Z0-9]{16}|"
    r"xox[baprs]-[A-Za-z0-9-]{20,})\b"
)


def safe_identifier(value: str) -> str:
    from agentbus.security.redaction import redact_text

    if redact_text(value) != value or _CREDENTIAL_SHAPE.search(value):
        raise ValueError("Use a pseudonymous identifier without credential material.")
    return value


Identifier = Annotated[str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,79}$"), AfterValidator(safe_identifier)]
Digest = Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]
RepairDisposition = Literal["repairable", "already_correct", "ineligible", "unresolved", "out_of_scope"]


class RepairModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class FixReference(RepairModel):
    before_revision: str = Field(pattern=r"^[a-f0-9]{7,64}$")
    after_revision: str = Field(pattern=r"^[a-f0-9]{7,64}$")
    review_confirmed: Literal[True]

    @field_validator("review_confirmed", mode="before")
    @classmethod
    def exact_review(cls, value):
        if value is not True:
            raise ValueError("Confirm review of the supplied fix.")
        return value

    @model_validator(mode="after")
    def distinct_revisions(self):
        if self.before_revision == self.after_revision:
            raise ValueError("The fix must identify distinct before and after revisions.")
        return self


class RepairPolicy(RepairModel):
    contract: Literal["purchase-access/v1"] = "purchase-access/v1"
    refunded_access: Literal["do_not_restore"] = "do_not_restore"
    cancelled_access: Literal["do_not_restore"] = "do_not_restore"
    operation: Literal["restore_missing_access"] = "restore_missing_access"


class PurchaseEvidence(RepairModel):
    purchase_id: Identifier
    payment_id: Identifier
    tenant_id: Identifier
    account_id: Identifier | None
    entitlement: Identifier
    paid_at: datetime
    payment_state: Literal["paid", "refunded", "cancelled", "unknown"]
    access_present: StrictBool | None
    mapping_verified: StrictBool
    history_complete: StrictBool
    evidence_ids: list[Identifier] = Field(min_length=1, max_length=20)
    revision: int = Field(strict=True, ge=1, le=2_147_483_647)

    @field_validator("paid_at")
    @classmethod
    def aware_purchase_time(cls, value):
        return aware_time(value)

    @field_validator("evidence_ids")
    @classmethod
    def normalize_events(cls, values):
        return sorted(set(values))


class RepairSnapshot(RepairModel):
    format: Literal["syndra.purchase-access/v1"] = "syndra.purchase-access/v1"
    title: str = Field(min_length=1, max_length=160)
    incident_start: datetime
    incident_end: datetime
    observed_at: datetime
    coverage_complete: StrictBool
    fix: FixReference
    policy: RepairPolicy = Field(default_factory=RepairPolicy)
    purchases: list[PurchaseEvidence] = Field(min_length=1, max_length=500)

    @field_validator("incident_start", "incident_end", "observed_at")
    @classmethod
    def aware_snapshot_time(cls, value):
        return aware_time(value)

    @field_validator("title")
    @classmethod
    def safe_title(cls, value):
        from agentbus.security.redaction import redact_text

        value = value.strip()
        if not value or any(ord(char) < 32 for char in value) or redact_text(value) != value or _CREDENTIAL_SHAPE.search(value):
            raise ValueError("Use a short title without credentials or control characters.")
        return value

    @model_validator(mode="after")
    def consistent_snapshot(self):
        if self.incident_start >= self.incident_end:
            raise ValueError("The incident end must follow its start.")
        if self.observed_at < self.incident_end:
            raise ValueError("The snapshot must be observed after the incident ends.")
        ids = [purchase.purchase_id for purchase in self.purchases]
        if len(ids) != len(set(ids)):
            raise ValueError("Purchase identifiers must be unique; combine duplicate event evidence.")
        if any(purchase.paid_at > self.observed_at for purchase in self.purchases):
            raise ValueError("Purchase evidence cannot be newer than the snapshot.")
        return self


class RepairObservation(RepairModel):
    purchase_id: Identifier
    tenant_id: Identifier
    account_id: Identifier | None
    entitlement: Identifier
    disposition: RepairDisposition
    reason: str = Field(max_length=500)
    evidence_ids: list[Identifier]
    observed_revision: int
    before_access: bool | None
    proposed_access: bool | None


class RepairCounts(RepairModel):
    total: int = 0
    repairable: int = 0
    already_correct: int = 0
    ineligible: int = 0
    unresolved: int = 0
    out_of_scope: int = 0


class DemoProof(RepairModel):
    fixture: Literal["purchase-access/v1"] = "purchase-access/v1"
    broken_handler_granted_access: Literal[False] = False
    fixed_handler_granted_access: Literal[True] = True
    historical_missing_before_fix: int
    historical_missing_after_fix: int
    before_code: str
    after_code: str
    provider_calls: Literal[0] = 0


class RepairCaseSummary(RepairModel):
    case_id: str
    title: str
    mode: Literal["local_demo", "imported_snapshot"]
    created_at: datetime
    coverage_complete: bool
    record_count: int
    batch_count: int = 0


class RepairCase(RepairModel):
    summary: RepairCaseSummary
    snapshot: RepairSnapshot
    demo_proof: DemoProof | None = None


class RepairPreview(RepairModel):
    preview_id: str
    case_id: str
    fingerprint: Digest
    snapshot_fingerprint: Digest
    observed_at: datetime
    created_at: datetime
    expires_at: datetime
    policy: RepairPolicy
    coverage_complete: bool
    counts: RepairCounts
    observations: list[RepairObservation] = Field(max_length=500)


class RepairResult(RepairModel):
    purchase_id: Identifier
    outcome: Literal["restored", "already_correct", "ineligible", "unresolved", "conflict"]
    reason: str = Field(max_length=500)
    verified_access: bool | None
    operation_id: str | None = None


class RepairBatch(RepairModel):
    request_id: str
    case_id: str
    preview_id: str
    created_at: datetime
    status: Literal["completed", "needs_attention"]
    selected_count: int
    restored_count: int
    skipped_count: int
    unresolved_count: int
    results: list[RepairResult] = Field(min_length=1, max_length=200)


class RepairEvent(RepairModel):
    sequence: int
    kind: Literal["created", "previewed", "support_corrected", "refunded", "applied"]
    created_at: datetime
    affected_count: int


class RepairPackage(RepairModel):
    format: Literal["syndra.repair-package/v1"] = "syndra.repair-package/v1"
    case_id: str
    mode: Literal["local_demo", "imported_snapshot"]
    snapshot: RepairSnapshot
    preview: RepairPreview
    batches: list[RepairBatch] = Field(max_length=100)
    checksum: Digest


def aware_time(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("Supply a timestamp with a timezone.")
    return value.astimezone(timezone.utc)
