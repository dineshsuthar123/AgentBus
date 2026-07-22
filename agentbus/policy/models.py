from __future__ import annotations

from datetime import datetime
from enum import Enum

from pydantic import ConfigDict, Field

from agentbus.tools.protocol import (
    ToolApprovalRequest,
    ToolCapability,
    ToolDescriptor,
    ToolInvocation,
    ToolPolicyDecision,
    ToolProtocolModel,
)


class PolicyModel(ToolProtocolModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)


class ToolApprovalDisposition(str, Enum):
    APPROVED = "approved"
    REJECTED = "rejected"


class PolicyRuleMatch(PolicyModel):
    rule_id: str = Field(min_length=1, max_length=128)
    reason: str = Field(min_length=1, max_length=2_048)
    safe_metadata: dict[str, object] = Field(default_factory=dict)
    constraints: tuple[ToolCapability, ...] = ()


class ToolApprovalGrant(PolicyModel):
    approval_id: str = Field(min_length=1, max_length=128)
    request: ToolApprovalRequest
    disposition: ToolApprovalDisposition
    binding_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    reason: str | None = Field(default=None, max_length=2_048)
    decided_at: datetime


class PolicyEvaluationRecord(PolicyModel):
    invocation_id: str = Field(min_length=1, max_length=128)
    invocation_revision: int = Field(ge=1)
    descriptor: ToolDescriptor
    invocation: ToolInvocation
    decision: ToolPolicyDecision
    matched_rules: tuple[str, ...]
