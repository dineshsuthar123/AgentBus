from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Query
from pydantic import Field, StrictBool

from agentbus.control.models import ProtocolModel
from agentbus.control.repair import RepairId
from agentbus.control.version import API_PREFIX
from agentbus.repair.live.contracts import (
    AccessVerification, ConditionalOperation, ConnectionStatus, Decision, Digest, Identifier,
    Receipt, RepairPolicy, TargetPlan,
)


class RepairConnectionsResponse(ProtocolModel):
    connections: list[ConnectionStatus]


class RepairConnectionResponse(ProtocolModel):
    connection: ConnectionStatus


class ConnectedRefreshRequest(ProtocolModel):
    connection_id: Identifier
    policy: RepairPolicy
    candidate_ids: list[Identifier] | None = Field(default=None, min_length=1, max_length=10000)


class ConnectedPreview(ProtocolModel):
    scan_id: RepairId
    case_id: str
    connection_id: Identifier
    created_at: str
    policy: RepairPolicy
    counts: dict[str, int]
    examined: int
    offset: int
    decisions: list[Decision]


class ConnectedPreviewResponse(ProtocolModel):
    preview: ConnectedPreview | None


class ConnectedPlanRequest(ProtocolModel):
    scan_id: RepairId
    purchase_ids: list[Identifier] = Field(min_length=1, max_length=200)
    workspace_trusted: StrictBool


class ConnectedApprovalRequest(ProtocolModel):
    plan_digest: Digest


class ConnectedExecutionRequest(ProtocolModel):
    retry_unknown: StrictBool = False


class RemoteOutcome(ProtocolModel):
    operation_id: RepairId
    execution: Literal["queued", "dispatched", "not_applied", "committed", "outcome_unknown"]
    verification: Literal["not_started", "pending", "passed", "failed", "superseded"]
    attempts: int
    receipt: Receipt | None
    verification_evidence: AccessVerification | None
    verification_error: RemoteVerificationError | None = None


class RemoteVerificationError(ProtocolModel):
    code: str = Field(max_length=64, pattern=r"^[a-z_]+$")
    at: str = Field(max_length=64)


class ConnectedPlan(ProtocolModel):
    plan: TargetPlan
    case_id: str
    status: str
    stopped: bool
    approved: bool
    counts: dict[str, int]
    outcomes: list[RemoteOutcome]


class ConnectedPlanResponse(ProtocolModel):
    result: ConnectedPlan


class ConnectedPlansResponse(ProtocolModel):
    results: list[ConnectedPlan]


def connected_repair_router(provider):
    router = APIRouter(prefix=API_PREFIX + "/customer-repair", tags=["Connected customer repair"])

    @router.get("/connections", response_model=RepairConnectionsResponse)
    def connections():
        return {"connections": provider().connections()}

    @router.post("/connections/{connection_id}/check", response_model=RepairConnectionResponse)
    def check(connection_id: Identifier):
        return {"connection": provider().check(connection_id)}

    @router.get("/cases/{case_id}/connected-preview", response_model=ConnectedPreviewResponse)
    def preview(case_id: RepairId, offset: int = Query(0, ge=0, le=10000)):
        return {"preview": provider().preview(case_id, offset=offset)}

    @router.post("/cases/{case_id}/connected-preview", response_model=ConnectedPreviewResponse)
    def refresh(case_id: RepairId, request: ConnectedRefreshRequest):
        service = provider()
        return {"preview": service.refresh_ids(case_id, request.candidate_ids, request.connection_id, request.policy) if request.candidate_ids is not None else service.refresh(case_id, request.connection_id, request.policy)}

    @router.post("/cases/{case_id}/plans", response_model=ConnectedPlanResponse)
    def create_plan(case_id: RepairId, request: ConnectedPlanRequest):
        return {"result": provider().create_plan(case_id, request.scan_id, request.purchase_ids, trusted=request.workspace_trusted)}

    @router.get("/cases/{case_id}/plans", response_model=ConnectedPlansResponse)
    def plans(case_id: RepairId):
        return {"results": provider().plans(case_id)}

    @router.get("/plans/{plan_id}", response_model=ConnectedPlanResponse)
    def plan(plan_id: RepairId):
        return {"result": provider().plan(plan_id)}

    @router.post("/plans/{plan_id}/approve", response_model=ConnectedPlanResponse)
    def approve(plan_id: RepairId, request: ConnectedApprovalRequest):
        return {"result": provider().approve(plan_id, request.plan_digest)}

    @router.post("/plans/{plan_id}/execute", response_model=ConnectedPlanResponse)
    def execute(plan_id: RepairId, request: ConnectedExecutionRequest):
        return {"result": provider().execute(plan_id, retry_unknown=request.retry_unknown)}

    @router.post("/plans/{plan_id}/stop", response_model=ConnectedPlanResponse)
    def stop(plan_id: RepairId):
        return {"result": provider().stop(plan_id)}

    @router.post("/plans/{plan_id}/reconcile", response_model=ConnectedPlanResponse)
    def reconcile(plan_id: RepairId):
        return {"result": provider().reconcile(plan_id)}

    @router.post("/plans/{plan_id}/pause", response_model=ConnectedPlanResponse)
    def pause(plan_id: RepairId):
        return {"result": provider().pause(plan_id)}

    @router.post("/plans/{plan_id}/continue", response_model=ConnectedPlanResponse)
    def continue_plan(plan_id: RepairId):
        return {"result": provider().pause(plan_id, resume=True)}

    return router
