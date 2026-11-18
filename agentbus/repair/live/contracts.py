from __future__ import annotations

from datetime import datetime, timezone
from typing import Annotated, Literal

from pydantic import Field, StrictBool, field_validator, model_validator

from agentbus.repair.models import Digest, Identifier, RepairModel, aware_time
from agentbus.tools.protocol import sha256_json

PROTOCOL = "syndra.correction/v1"
Environment = Literal["sandbox", "staging", "production"]
OperationId = Annotated[str, Field(pattern=r"^[a-f0-9]{32}$")]


def now() -> datetime:
    return datetime.now(timezone.utc)


def digest(model, *, exclude=()) -> str:
    return sha256_json(model.model_dump(mode="json", exclude=set(exclude)) if hasattr(model, "model_dump") else model)


class Identity(RepairModel):
    application: Identifier
    tenant: Identifier
    environment: Environment
    configuration_revision: Identifier
    protocol: Literal["syndra.correction/v1"] = PROTOCOL


class ConnectionManifest(RepairModel):
    connection_id: Identifier
    identity: Identity
    destination: str = Field(max_length=400)
    approved_addresses: list[str] = Field(min_length=1, max_length=8)
    private_networks: list[str] = Field(default_factory=list, max_length=8)
    credential_ref: str = Field(pattern=r"^(env:SYNDRA_REPAIR_[A-Z0-9_]{1,80}|keyring:[A-Za-z0-9_.-]{1,80})$")
    revision: int = Field(strict=True, ge=1)
    transport: Literal["https", "disposable_loopback"] = "https"
    permissions: list[Literal["read", "approve", "repair", "verify", "revoke"]] = Field(min_length=1, max_length=5)
    stripe: StripeConfiguration | None = None


class StripeConfiguration(RepairModel):
    credential_ref: str = Field(pattern=r"^(env:SYNDRA_REPAIR_[A-Z0-9_]{1,80}|keyring:[A-Za-z0-9_.-]{1,80})$")
    platform_account: Identifier
    connected_account: Identifier | None = None
    livemode: StrictBool
    api_version: Literal["2026-08-26.dahlia"] = "2026-08-26.dahlia"
    use_entitlements: StrictBool = False


class Capabilities(RepairModel):
    identity: Identity
    principal: Identifier
    permissions: list[Literal["read", "approve", "repair", "verify", "revoke"]] = Field(max_length=5)
    workflow: Literal["paid-purchase-access/v1"] = "paid-purchase-access/v1"
    aggregate_serialization: StrictBool
    semantic_receipts: StrictBool
    normal_access_verification: StrictBool
    coordinated_external_payments: StrictBool = False
    receipt_retention: Literal["indefinite"] = "indefinite"
    compensation: Literal["unsupported"] = "unsupported"
    maximum_batch: int = Field(ge=1, le=200)


class ConnectedRepairPolicy(RepairModel):
    version: Literal["paid-purchase-access/v1"] = "paid-purchase-access/v1"
    payment_authority: Literal["application", "stripe"] = "application"
    access_authority: Literal["application"] = "application"
    history_authority: Literal["application"] = "application"
    maximum_age_seconds: int = Field(default=60, strict=True, ge=1, le=300)
    incident_start: datetime
    incident_end: datetime
    maximum_writes: int = Field(default=1, strict=True, ge=1, le=200)
    requests_per_second: int = Field(default=2, strict=True, ge=1, le=10)
    partial_refund: Literal["investigate"] = "investigate"
    dispute: Literal["do_not_restore"] = "do_not_restore"
    expired_period: Literal["investigate"] = "investigate"
    effect: Literal["grant_original_period_only"] = "grant_original_period_only"
    verification: Literal["normal_authorization_path"] = "normal_authorization_path"

    @field_validator("incident_start", "incident_end")
    @classmethod
    def aware(cls, value):
        return aware_time(value)

    @model_validator(mode="after")
    def interval(self):
        if self.incident_start >= self.incident_end:
            raise ValueError("Use a nonempty, half-open incident interval.")
        return self


RepairPolicy = ConnectedRepairPolicy


class PurchaseState(RepairModel):
    identity: Identity
    purchase_id: Identifier
    payment_id: Identifier
    account_id: Identifier | None
    feature: Identifier
    currency: str = Field(pattern=r"^[a-z]{3}$")
    amount_minor: int = Field(strict=True, gt=0, le=10**12)
    refunded_minor: int = Field(strict=True, ge=0, le=10**12)
    payment_state: Literal["paid", "authorized", "pending", "failed", "cancelled", "unknown"]
    purchased_at: datetime
    period_start: datetime
    period_end: datetime
    access_present: StrictBool | None
    mapping_verified: StrictBool
    history_complete: StrictBool
    disputed: StrictBool
    pending_payment_change: StrictBool
    cancellation: Literal["none", "period_end", "immediate"]
    restriction: Literal["none", "suspended", "deleted", "manual_override", "changed_product"]
    revision: int = Field(strict=True, ge=1)
    observed_at: datetime
    evidence_ids: list[Identifier] = Field(min_length=1, max_length=30)
    stripe_customer: Identifier | None = None
    stripe_invoice: Identifier | None = None
    stripe_subscription: Identifier | None = None
    stripe_product: Identifier | None = None
    stripe_feature: Identifier | None = None

    @field_validator("purchased_at", "period_start", "period_end", "observed_at")
    @classmethod
    def aware(cls, value):
        return aware_time(value)

    @model_validator(mode="after")
    def period(self):
        if self.period_start >= self.period_end or self.refunded_minor > self.amount_minor:
            raise ValueError("Invalid purchase period or refund amount.")
        return self


class PurchasePage(RepairModel):
    identity: Identity
    records: list[PurchaseState] = Field(max_length=100)
    next_cursor: Identifier | None = None
    complete: StrictBool


class Decision(RepairModel):
    purchase_id: Identifier
    disposition: Literal["eligible", "already_correct", "subsequent_history", "conflicted_stale", "investigation_required"]
    reason: str = Field(max_length=300)
    state: PurchaseState | None
    uncertainty: str | None = Field(default=None, max_length=300)


class Effect(RepairModel):
    account_id: Identifier
    feature: Identifier
    period_start: datetime
    period_end: datetime
    access_present: Literal[True] = True

    @field_validator("period_start", "period_end")
    @classmethod
    def aware(cls, value):
        return aware_time(value)


class ConditionalOperation(RepairModel):
    protocol: Literal["syndra.correction/v1"] = PROTOCOL
    operation_id: OperationId
    plan_id: OperationId
    workspace_digest: Digest
    identity: Identity
    connection_digest: Digest
    policy: RepairPolicy
    policy_digest: Digest
    purchase_id: Identifier
    payment_id: Identifier
    expected_revision: int = Field(strict=True, ge=1)
    effect: Effect
    logical_effect: Digest
    deadline: datetime
    payload_digest: Digest

    @field_validator("deadline")
    @classmethod
    def aware(cls, value):
        return aware_time(value)

    @model_validator(mode="after")
    def hashes(self):
        if self.policy_digest != digest(self.policy):
            raise ValueError("Policy digest mismatch.")
        expected = digest([self.identity.application, self.identity.tenant, self.identity.environment,
                           self.purchase_id, self.payment_id, self.effect.model_dump(mode="json")])
        if self.logical_effect != expected:
            raise ValueError("Logical effect mismatch.")
        if self.payload_digest != digest(self, exclude={"payload_digest"}):
            raise ValueError("Payload digest mismatch.")
        return self


class TargetPlan(RepairModel):
    plan_id: OperationId
    identity: Identity
    connection_digest: Digest
    workspace_digest: Digest
    policy_digest: Digest
    expires_at: datetime
    operations: list[ConditionalOperation] = Field(min_length=1, max_length=200)
    maximum_writes: int = Field(strict=True, ge=1, le=200)
    digest: Digest

    @field_validator("expires_at")
    @classmethod
    def aware(cls, value):
        return aware_time(value)

    @model_validator(mode="after")
    def binding(self):
        if len(self.operations) > self.maximum_writes or len({o.operation_id for o in self.operations}) != len(self.operations):
            raise ValueError("The selected set exceeds its bound or contains duplicates.")
        if len({o.logical_effect for o in self.operations}) != len(self.operations):
            raise ValueError("The selected set repeats a business effect.")
        for operation in self.operations:
            if (operation.plan_id != self.plan_id or operation.identity != self.identity or
                operation.connection_digest != self.connection_digest or operation.policy_digest != self.policy_digest or
                operation.workspace_digest != self.workspace_digest or operation.deadline != self.expires_at):
                raise ValueError("An operation does not belong to this exact plan.")
            if self.maximum_writes > operation.policy.maximum_writes:
                raise ValueError("The plan exceeds the reviewed policy's write limit.")
        if self.digest != digest(self, exclude={"digest"}):
            raise ValueError("Plan digest mismatch.")
        return self


class Receipt(RepairModel):
    operation_id: OperationId
    payload_digest: Digest
    logical_effect: Digest
    identity: Identity
    purchase_id: Identifier
    payment_id: Identifier
    effect: Effect
    outcome: Literal["committed", "not_applied"]
    reason: Literal["granted", "already_correct", "subsequent_history", "conflict", "logical_effect_exists"]
    revision: int = Field(strict=True, ge=1)
    committed_at: datetime
    outbox_id: OperationId | None

    @field_validator("committed_at")
    @classmethod
    def aware(cls, value):
        return aware_time(value)


class AccessVerification(RepairModel):
    operation_id: OperationId
    status: Literal["pending", "passed", "failed", "superseded"]
    method: Literal["normal_authorization_path"] = "normal_authorization_path"
    observed_at: datetime
    revision: int = Field(strict=True, ge=1)
    allowed: StrictBool
    reason: str = Field(max_length=300)

    @field_validator("observed_at")
    @classmethod
    def aware(cls, value):
        return aware_time(value)

    @model_validator(mode="after")
    def verified_access(self):
        if self.status == "passed" and not self.allowed:
            raise ValueError("Access cannot pass verification while authorization is denied.")
        return self


class ConnectionStatus(RepairModel):
    manifest: ConnectionManifest
    configured: bool
    checked_at: datetime | None = None
    capabilities: Capabilities | None = None
    write_ready: bool = False
    reason: str


ConnectionManifest.model_rebuild()
