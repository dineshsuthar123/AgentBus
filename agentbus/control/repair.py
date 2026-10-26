from __future__ import annotations

from typing import Annotated, Literal
from datetime import datetime

from fastapi import APIRouter, Query
from pydantic import Field

from agentbus.control.models import ProtocolModel
from agentbus.control.version import API_PREFIX
from agentbus.repair.demo import demo_snapshot
from agentbus.repair.models import (
    Digest, Identifier, RepairBatch, RepairCase, RepairCaseSummary, RepairEvent,
    RepairPackage, RepairPreview, RepairSnapshot,
)

RepairId = Annotated[str, Field(pattern=r"^[a-f0-9]{32}$")]


class RepairCaseResponse(ProtocolModel):
    case: RepairCase


class RepairCaseListResponse(ProtocolModel):
    cases: list[RepairCaseSummary]


class RepairImportRequest(ProtocolModel):
    snapshot: RepairSnapshot


class RepairPreviewResponse(ProtocolModel):
    preview: RepairPreview | None


class RepairApplyRequest(ProtocolModel):
    preview_id: RepairId
    preview_fingerprint: Digest
    request_id: RepairId
    purchase_ids: list[Identifier] = Field(min_length=1, max_length=200)
    confirmation: Literal["APPLY_SELECTED_CORRECTIONS"]


class RepairBatchResponse(ProtocolModel):
    batch: RepairBatch


class RepairBatchListResponse(ProtocolModel):
    batches: list[RepairBatch]


class RepairEventListResponse(ProtocolModel):
    events: list[RepairEvent]


class RepairExportRequest(ProtocolModel):
    preview_id: RepairId


class RepairPackageResponse(ProtocolModel):
    package: RepairPackage


class RepairDemoChangeRequest(ProtocolModel):
    action: Literal["support_corrects_five", "refund_one"]


class RepairDemoChangeResponse(ProtocolModel):
    affected_count: int


class RepairAccountResponse(ProtocolModel):
    purchase_id: Identifier
    account_id: Identifier | None
    allowed: bool | None
    observed_at: datetime
    message: str


def repair_router(service_provider) -> APIRouter:
    router = APIRouter(prefix=f"{API_PREFIX}/customer-repair", tags=["Customer repair"])

    @router.get("/cases", response_model=RepairCaseListResponse)
    def cases(limit: int = Query(50, ge=1, le=50), offset: int = Query(0, ge=0, le=10_000)):
        return RepairCaseListResponse(cases=service_provider().list(limit=limit, offset=offset))

    @router.post("/demo", response_model=RepairCaseResponse)
    def create_demo():
        return RepairCaseResponse(case=service_provider().create(demo_snapshot(), demo=True))

    @router.get("/template", response_model=RepairImportRequest)
    def template():
        return RepairImportRequest(snapshot=demo_snapshot())

    @router.post("/cases", response_model=RepairCaseResponse)
    def import_case(request: RepairImportRequest):
        return RepairCaseResponse(case=service_provider().create(request.snapshot))

    @router.get("/cases/{case_id}", response_model=RepairCaseResponse)
    def case(case_id: RepairId):
        return RepairCaseResponse(case=service_provider().get(case_id))

    @router.get("/cases/{case_id}/preview", response_model=RepairPreviewResponse)
    def current_preview(case_id: RepairId):
        return RepairPreviewResponse(preview=service_provider().latest_preview(case_id))

    @router.post("/cases/{case_id}/preview", response_model=RepairPreviewResponse)
    def preview(case_id: RepairId):
        return RepairPreviewResponse(preview=service_provider().preview(case_id))

    @router.post("/cases/{case_id}/apply", response_model=RepairBatchResponse)
    def apply(case_id: RepairId, request: RepairApplyRequest):
        return RepairBatchResponse(batch=service_provider().apply(case_id, **request.model_dump(exclude={"confirmation"})))

    @router.get("/cases/{case_id}/batches", response_model=RepairBatchListResponse)
    def batches(case_id: RepairId):
        return RepairBatchListResponse(batches=service_provider().batches(case_id))

    @router.get("/cases/{case_id}/batches/{request_id}", response_model=RepairBatchResponse)
    def batch(case_id: RepairId, request_id: RepairId):
        return RepairBatchResponse(batch=service_provider().batch(case_id, request_id))

    @router.get("/cases/{case_id}/events", response_model=RepairEventListResponse)
    def events(case_id: RepairId):
        return RepairEventListResponse(events=service_provider().events(case_id))

    @router.post("/cases/{case_id}/export", response_model=RepairPackageResponse)
    def export(case_id: RepairId, request: RepairExportRequest):
        return RepairPackageResponse(package=service_provider().export(case_id, request.preview_id))

    @router.post("/cases/{case_id}/demo-change", response_model=RepairDemoChangeResponse)
    def demo_change(case_id: RepairId, request: RepairDemoChangeRequest):
        return RepairDemoChangeResponse(affected_count=service_provider().demo_change(case_id, request.action))

    @router.get("/cases/{case_id}/demo-account/{purchase_id}", response_model=RepairAccountResponse)
    def demo_account(case_id: RepairId, purchase_id: Identifier):
        return RepairAccountResponse(**service_provider().account_access(case_id, purchase_id))

    return router
