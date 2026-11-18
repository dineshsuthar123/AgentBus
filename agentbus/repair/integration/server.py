from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from dataclasses import dataclass
import secrets
from typing import Literal

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import Field, SecretStr

from agentbus.repair.live.contracts import (
    AccessVerification, Capabilities, ConditionalOperation, Digest, Identifier, OperationId,
    PurchasePage, PurchaseState, Receipt, RepairModel, TargetPlan,
)
from agentbus.repair.service import RepairError


@dataclass(frozen=True)
class Principal:
    name: str
    token: SecretStr
    permissions: frozenset[str]

    def __post_init__(self):
        if len(self.token.get_secret_value()) < 32:
            raise ValueError("Use at least 32 random bytes for a scoped bearer credential.")


class PurchaseRead(RepairModel):
    purchase_ids: list[Identifier] = Field(min_length=1, max_length=100)
    cursor: Identifier | None = None


class PlanAcknowledgement(RepairModel):
    plan_id: OperationId
    digest: Digest
    status: Literal["active"]


class PlanRevocation(RepairModel):
    plan_id: OperationId
    status: Literal["revoked"]


class NormalAccess(RepairModel):
    allowed: bool
    propagated: bool
    revision: int


def create_correction_app(application, principals: list[Principal], *, process_outbox=True) -> FastAPI:
    """Mount in an application that implements the documented domain contract.

    Bearer credentials are application-issued service credentials, not browser
    sessions. Put this API behind the application's verified HTTPS ingress.
    """
    @asynccontextmanager
    async def lifespan(_app):
        stopped = asyncio.Event()
        async def consume():
            while not stopped.is_set():
                try:
                    await run_in_threadpool(application.consume_outbox)
                except RepairError:
                    pass
                try:
                    await asyncio.wait_for(stopped.wait(), timeout=0.2)
                except TimeoutError:
                    pass
        task = asyncio.create_task(consume()) if process_outbox else None
        yield
        stopped.set()
        if task:
            await task

    app = FastAPI(title="Syndra application correction API", version="1.0", lifespan=lifespan,
                  docs_url=None, redoc_url=None, openapi_url=None)
    bearer = HTTPBearer(auto_error=False)

    def require(permission):
        def authenticated(supplied: HTTPAuthorizationCredentials | None = Depends(bearer)):
            if supplied is None or supplied.scheme.casefold() != "bearer":
                raise HTTPException(401, "Bearer authentication is required.")
            for principal in principals:
                if secrets.compare_digest(supplied.credentials, principal.token.get_secret_value()):
                    if permission not in principal.permissions:
                        raise HTTPException(403, "This operation is outside the credential's scope.")
                    return principal
            raise HTTPException(401, "Credential is invalid or revoked.")
        return authenticated

    @app.middleware("http")
    async def bounded_body(request: Request, call_next):
        # Reject both declared and streamed overflows before validation echoes data.
        size = 0
        receive = request._receive
        async def limited_receive():
            nonlocal size
            message = await receive()
            size += len(message.get("body", b""))
            if size > 1_500_000:
                raise RepairError("Request exceeds the correction contract bound.", status=413)
            return message
        request._receive = limited_receive
        try:
            if int(request.headers.get("content-length", "0")) > 1_500_000:
                return JSONResponse({"error": "request_too_large"}, status_code=413)
            return await call_next(request)
        except (ValueError, RepairError):
            return JSONResponse({"error": "invalid_request"}, status_code=413)

    @app.exception_handler(RequestValidationError)
    async def invalid(_request, _error):
        return JSONResponse({"error": "invalid_contract"}, status_code=422)

    @app.exception_handler(RepairError)
    async def domain_error(_request, error):
        return JSONResponse({"error": error.code}, status_code=error.status)

    @app.exception_handler(Exception)
    async def unexpected(_request, _error):
        return JSONResponse({"error": "outcome_unconfirmed"}, status_code=503)

    prefix = "/syndra-correction/v1"

    @app.get(prefix + "/capabilities", response_model=Capabilities)
    def capabilities(principal=Depends(require("read"))):
        return application.capabilities(principal.name, sorted(principal.permissions))

    @app.post(prefix + "/purchases", response_model=PurchasePage)
    def purchases(request: PurchaseRead, _principal=Depends(require("read"))):
        return application.read_page(request.purchase_ids, request.cursor)

    @app.get(prefix + "/purchases/{purchase_id}", response_model=PurchaseState)
    def purchase(purchase_id: Identifier, _principal=Depends(require("read"))):
        return application.purchase(purchase_id)

    @app.post(prefix + "/plans", response_model=PlanAcknowledgement)
    def approve(plan: TargetPlan, principal=Depends(require("approve"))):
        return application.register_plan(plan, principal.name)

    @app.post(prefix + "/plans/{plan_id}/revoke", response_model=PlanRevocation)
    def revoke(plan_id: OperationId, principal=Depends(require("revoke"))):
        return application.revoke(plan_id, principal.name)

    @app.post(prefix + "/operations", response_model=Receipt)
    def apply(operation: ConditionalOperation, principal=Depends(require("repair"))):
        return application.apply(operation, principal.name)

    @app.get(prefix + "/operations/{operation_id}", response_model=Receipt)
    def receipt(operation_id: OperationId, _principal=Depends(require("read"))):
        return application.lookup(operation_id)

    @app.get(prefix + "/operations/{operation_id}/verify", response_model=AccessVerification)
    def verify(operation_id: OperationId, _principal=Depends(require("verify"))):
        return application.verify(operation_id)

    @app.get("/application/access/{purchase_id}", response_model=NormalAccess)
    def normal_application_access(purchase_id: Identifier, _principal=Depends(require("verify"))):
        # Scoped service capability check, not customer impersonation or admin bypass.
        return application.access(purchase_id)

    return app
