from __future__ import annotations

import os
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

try:
    from fastapi import FastAPI, Query, Request
    from fastapi.exceptions import RequestValidationError
    from fastapi.responses import JSONResponse, StreamingResponse
except ImportError as exc:  # pragma: no cover - exercised by the CLI dependency test
    raise RuntimeError(
        'The control plane requires optional dependencies. Install "agentbus[ide]".'
    ) from exc

from agentbus import __version__
from agentbus.config import AgentBusConfig
from agentbus.control.authentication import (
    BearerAuthenticator,
    safe_error_message,
    validate_origin,
)
from agentbus.control.errors import ControlPlaneError
from agentbus.control.event_stream import (
    ControlEventReader,
    parse_event_cursor,
    stream_events,
)
from agentbus.control.models import (
    API_PREFIX,
    ApprovalDecisionRequest,
    ApprovalDecisionResponse,
    ApprovalListResponse,
    CancelResponse,
    ChangeListResponse,
    DiffResponse,
    DoctorResponse,
    ErrorBody,
    ErrorResponse,
    FileContentResponse,
    HealthResponse,
    InfoResponse,
    ProviderCheckRequest,
    ProviderListResponse,
    ProviderSummary,
    ResumeResponse,
    RunAcceptedResponse,
    RunActionRequest,
    RunCreateRequest,
    RunListResponse,
    RunReportResponse,
    RunSummary,
    SchedulerResponse,
    TaskListResponse,
    UsageResponse,
    WorkspaceValidationRequest,
    WorkspaceValidationResponse,
    WorktreeListResponse,
)
from agentbus.control.services import ControlQueryService
from agentbus.control.supervisor import BackgroundRunSupervisor
from agentbus.execution.models import ApprovalOutcome
from agentbus.execution.state_store import StateStoreError
from agentbus.git.repository import GitRepositoryError
from agentbus.security.redaction import sanitize_json

MAX_REQUEST_BYTES = 1_000_000


@dataclass(frozen=True)
class ControlAppContext:
    daemon_id: str
    host: str
    port: int
    started_at: datetime
    state_database: str


def create_app(
    *,
    token: str,
    query_service: ControlQueryService,
    supervisor: BackgroundRunSupervisor,
    context: ControlAppContext,
    shutdown_supervisor: bool = True,
):
    authenticator = BearerAuthenticator(token)
    event_reader = ControlEventReader(query_service.store)

    @asynccontextmanager
    async def lifespan(_app):
        yield
        if shutdown_supervisor:
            supervisor.shutdown(wait=True)

    app = FastAPI(
        title="AgentBus Control Protocol",
        version="1.0",
        docs_url=None,
        redoc_url=None,
        responses={
            400: {"model": ErrorResponse, "description": "Invalid request"},
            403: {"model": ErrorResponse, "description": "Forbidden"},
            404: {"model": ErrorResponse, "description": "Not found"},
            409: {"model": ErrorResponse, "description": "Conflict"},
            413: {"model": ErrorResponse, "description": "Request too large"},
            422: {"model": ErrorResponse, "description": "Validation error"},
            500: {"model": ErrorResponse, "description": "Internal error"},
        },
        lifespan=lifespan,
    )
    app.state.control_context = context
    app.state.query_service = query_service
    app.state.supervisor = supervisor

    @app.middleware("http")
    async def secure_local_request(request: Request, call_next):
        try:
            validate_origin(request.headers.get("origin"))
            length = request.headers.get("content-length")
            if length is not None and int(length) > MAX_REQUEST_BYTES:
                return _error_json(
                    JSONResponse,
                    code="request_too_large",
                    message="The request body exceeds the control-plane limit.",
                    status_code=413,
                )
            if request.url.path != "/health":
                authenticator.authenticate(request.headers)
        except (ControlPlaneError, ValueError) as exc:
            status_code = getattr(exc, "status_code", 400)
            return _error_json(
                JSONResponse,
                code=getattr(exc, "code", "invalid_request"),
                message=safe_error_message(exc),
                retryable=bool(getattr(exc, "retryable", False)),
                status_code=status_code,
            )
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        return response

    @app.exception_handler(ControlPlaneError)
    async def control_error_handler(_request: Request, exc: ControlPlaneError):
        return _error_json(
            JSONResponse,
            code=exc.code,
            message=exc.safe_message,
            retryable=exc.retryable,
            details=sanitize_json(exc.details),
            status_code=exc.status_code,
        )

    @app.exception_handler(RequestValidationError)
    async def validation_error_handler(_request: Request, exc: RequestValidationError):
        details = [
            {
                "location": [str(part) for part in item.get("loc", ())],
                "message": item.get("msg", "Invalid value."),
                "type": item.get("type", "validation_error"),
            }
            for item in exc.errors()
        ]
        return _error_json(
            JSONResponse,
            code="validation_error",
            message="The request did not match the control protocol.",
            details={"issues": details[:50]},
            status_code=422,
        )

    @app.exception_handler(StateStoreError)
    @app.exception_handler(GitRepositoryError)
    async def service_error_handler(_request: Request, exc: Exception):
        return _error_json(
            JSONResponse,
            code="control_service_error",
            message=safe_error_message(exc),
            status_code=409,
        )

    @app.exception_handler(Exception)
    async def unexpected_error_handler(_request: Request, exc: Exception):
        return _error_json(
            JSONResponse,
            code="internal_error",
            message="The control-plane request failed safely.",
            retryable=False,
            status_code=500,
        )

    @app.get("/health", response_model=HealthResponse)
    async def health() -> HealthResponse:
        return HealthResponse()

    @app.get(f"{API_PREFIX}/info", response_model=InfoResponse)
    async def info() -> InfoResponse:
        return InfoResponse(
            agentbus_version=__version__,
            daemon_id=context.daemon_id,
            pid=os.getpid(),
            host=context.host,
            port=context.port,
            started_at=context.started_at,
            state_database=context.state_database,
            capabilities=[
                "runs",
                "durable-resume",
                "cancellation",
                "sse-replay",
                "approvals",
                "repository-diffs",
            ],
        )

    @app.post(
        f"{API_PREFIX}/workspaces/validate",
        response_model=WorkspaceValidationResponse,
    )
    async def validate_workspace(
        request: WorkspaceValidationRequest,
    ) -> WorkspaceValidationResponse:
        return query_service.workspace_service.validate(request)

    @app.get(f"{API_PREFIX}/providers", response_model=ProviderListResponse)
    async def providers() -> ProviderListResponse:
        return query_service.providers()

    @app.post(f"{API_PREFIX}/providers/check", response_model=ProviderSummary)
    async def check_provider(request: ProviderCheckRequest) -> ProviderSummary:
        if request.live_consent:
            raise ControlPlaneError(
                "Live provider checks are not exposed by the local control API."
            )
        values = query_service.providers().providers
        return next(item for item in values if item.name == request.provider)

    @app.get(f"{API_PREFIX}/doctor", response_model=DoctorResponse)
    async def doctor(workspace: str | None = None) -> DoctorResponse:
        return query_service.doctor(workspace)

    @app.post(f"{API_PREFIX}/runs", response_model=RunAcceptedResponse, status_code=202)
    async def create_run(request: RunCreateRequest) -> RunAcceptedResponse:
        return supervisor.submit(request)

    @app.get(f"{API_PREFIX}/runs", response_model=RunListResponse)
    async def list_runs(limit: int = Query(default=100, ge=1, le=1000)):
        return query_service.list_runs(limit)

    @app.get(f"{API_PREFIX}/runs/{{run_id}}", response_model=RunSummary)
    async def get_run(run_id: str) -> RunSummary:
        return query_service.run_summary(query_service.get_run(run_id))

    @app.post(f"{API_PREFIX}/runs/{{run_id}}/resume", response_model=ResumeResponse)
    async def resume_run(run_id: str) -> ResumeResponse:
        return supervisor.resume(run_id)

    @app.post(f"{API_PREFIX}/runs/{{run_id}}/cancel", response_model=CancelResponse)
    async def cancel_run(
        run_id: str,
        request: RunActionRequest | None = None,
    ) -> CancelResponse:
        return supervisor.cancel(run_id, request.reason if request else None)

    @app.get(f"{API_PREFIX}/runs/{{run_id}}/tasks", response_model=TaskListResponse)
    async def tasks(run_id: str) -> TaskListResponse:
        return query_service.tasks(run_id)

    @app.get(
        f"{API_PREFIX}/runs/{{run_id}}/scheduler",
        response_model=SchedulerResponse,
    )
    async def scheduler(run_id: str) -> SchedulerResponse:
        return query_service.scheduler(run_id)

    @app.get(
        f"{API_PREFIX}/runs/{{run_id}}/worktrees",
        response_model=WorktreeListResponse,
    )
    async def worktrees(run_id: str) -> WorktreeListResponse:
        return query_service.worktrees(run_id)

    @app.get(f"{API_PREFIX}/runs/{{run_id}}/usage", response_model=UsageResponse)
    async def usage(run_id: str) -> UsageResponse:
        return query_service.usage(run_id)

    @app.get(
        f"{API_PREFIX}/runs/{{run_id}}/report",
        response_model=RunReportResponse,
    )
    async def report(run_id: str) -> RunReportResponse:
        return query_service.report(run_id)

    def event_response(request: Request, run_id: str | None = None):
        header_cursor = request.headers.get("last-event-id")
        query_cursor = request.query_params.get("after")
        cursor = parse_event_cursor(query_cursor or header_cursor)
        return StreamingResponse(
            stream_events(
                event_reader,
                after_sequence=cursor,
                run_id=run_id,
                disconnected=request.is_disconnected,
            ),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache, no-store",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    @app.get(f"{API_PREFIX}/events")
    async def events(request: Request):
        return event_response(request)

    @app.get(f"{API_PREFIX}/runs/{{run_id}}/events")
    async def run_events(run_id: str, request: Request):
        query_service.get_run(run_id)
        return event_response(request, run_id)

    @app.get(
        f"{API_PREFIX}/runs/{{run_id}}/approvals",
        response_model=ApprovalListResponse,
    )
    async def approvals(run_id: str) -> ApprovalListResponse:
        return query_service.approvals(run_id)

    @app.post(
        f"{API_PREFIX}/runs/{{run_id}}/approvals/{{approval_id}}/approve",
        response_model=ApprovalDecisionResponse,
    )
    async def approve(
        run_id: str,
        approval_id: str,
        request: ApprovalDecisionRequest,
    ) -> ApprovalDecisionResponse:
        return query_service.decide_approval(
            run_id,
            approval_id,
            request,
            ApprovalOutcome.APPROVED,
        )

    @app.post(
        f"{API_PREFIX}/runs/{{run_id}}/approvals/{{approval_id}}/reject",
        response_model=ApprovalDecisionResponse,
    )
    async def reject(
        run_id: str,
        approval_id: str,
        request: ApprovalDecisionRequest,
    ) -> ApprovalDecisionResponse:
        return query_service.decide_approval(
            run_id,
            approval_id,
            request,
            ApprovalOutcome.REJECTED,
        )

    @app.get(
        f"{API_PREFIX}/runs/{{run_id}}/changes",
        response_model=ChangeListResponse,
    )
    async def changes(run_id: str) -> ChangeListResponse:
        return query_service.repository.list_changes(query_service.get_run(run_id))

    @app.get(f"{API_PREFIX}/runs/{{run_id}}/diff", response_model=DiffResponse)
    async def diff(
        run_id: str,
        path: str | None = None,
        byte_limit: int = Query(default=30_000, ge=1, le=MAX_REQUEST_BYTES),
    ) -> DiffResponse:
        return query_service.repository.diff(
            query_service.get_run(run_id),
            path=path,
            byte_limit=byte_limit,
        )

    @app.get(
        f"{API_PREFIX}/runs/{{run_id}}/changes/{{path:path}}",
        response_model=FileContentResponse,
    )
    async def file_content(
        run_id: str,
        path: str,
        revision: str = Query(default="after", pattern="^(before|after)$"),
    ) -> FileContentResponse:
        return query_service.repository.file_content(
            query_service.get_run(run_id),
            path,
            revision=revision,
        )

    return app


def default_app(
    *,
    token: str,
    config: AgentBusConfig,
    supervisor: BackgroundRunSupervisor,
    daemon_id: str,
    host: str,
    port: int,
):
    query = ControlQueryService(config)
    return create_app(
        token=token,
        query_service=query,
        supervisor=supervisor,
        context=ControlAppContext(
            daemon_id=daemon_id,
            host=host,
            port=port,
            started_at=datetime.now(timezone.utc),
            state_database=str(config.state_database_path.resolve()),
        ),
    )


def _error_json(
    response_type,
    *,
    code: str,
    message: str,
    status_code: int,
    retryable: bool = False,
    details: dict[str, Any] | None = None,
):
    payload = ErrorResponse(
        error=ErrorBody(
            code=code,
            message=message,
            retryable=retryable,
            details=details or {},
        )
    )
    return response_type(
        status_code=status_code,
        content=payload.model_dump(mode="json"),
    )
