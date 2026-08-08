from __future__ import annotations

import threading
from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING, Iterator, Literal

from agentbus.config import AgentBusConfig
from agentbus.control.errors import (
    ControlPlaneConflictError,
    ControlPlaneError,
    ControlPlaneForbiddenError,
    ControlPlaneNotFoundError,
)
from agentbus.control.models import (
    WorkspaceContextPlanRequest,
    WorkspaceContextPlanResponse,
    WorkspaceGraphResponse,
    WorkspaceImpactRequest,
    WorkspaceImpactResponse,
    WorkspaceIndexCancellationResponse,
    WorkspaceIndexAttachRequest,
    WorkspaceIndexCreateRequest,
    WorkspaceIndexMutationResponse,
    WorkspaceIndexStatusResponse,
    WorkspaceIndexVerificationResponse,
    WorkspaceSearchRequest,
    WorkspaceSearchResponse,
    WorkspaceSymbolResponse,
    WorkspaceTestsResponse,
)
from agentbus.git.repository import GitRepositoryError
from agentbus.intelligence.errors import (
    IndexBusyError,
    IndexUnavailableError,
    RepositoryIntelligenceError,
    RepositoryQueryError,
)
from agentbus.intelligence.models import ImpactResult, IndexState, TestImpactResult
from agentbus.intelligence.service import (
    ContextCandidateSummary,
    ContextPlanSummary,
    RepositoryIntelligenceService,
    RepositorySearchReport,
    SearchMatch,
    SymbolSummary,
)

if TYPE_CHECKING:
    from agentbus.control.services import WorkspaceService


_INDEX_DATABASE_NAME = "repository-index.sqlite3"


class ControlIntelligenceService:
    """Manage contained workspace indexes behind the authenticated daemon."""

    def __init__(
        self,
        config: AgentBusConfig,
        workspace_service: WorkspaceService,
    ) -> None:
        self.config = config
        self.workspace_service = workspace_service
        self.database_path = (
            config.state_database_path.parent / _INDEX_DATABASE_NAME
        )
        self._services: dict[str, RepositoryIntelligenceService] = {}
        self._lock = threading.RLock()

    def build(
        self,
        request: WorkspaceIndexCreateRequest,
    ) -> WorkspaceIndexMutationResponse:
        self._require_trust(request.workspace_trusted)
        service = self._register(request.workspace)
        with _control_errors():
            result = service.build()
        return WorkspaceIndexMutationResponse(
            workspace_id=service.workspace_identity.workspace_id,
            repository_id=service.repository.repository_id,
            result=result,
        )

    def attach(
        self,
        request: WorkspaceIndexAttachRequest,
    ) -> WorkspaceIndexStatusResponse:
        service = self._register(request.workspace)
        return self.status(service.workspace_identity.workspace_id)

    def status(self, workspace_id: str) -> WorkspaceIndexStatusResponse:
        service = self._get(workspace_id)
        with _control_errors():
            status = service.status()
            overview = (
                service.overview()
                if status.snapshot_id is not None
                and status.state
                not in {IndexState.CORRUPTED, IndexState.INCOMPATIBLE}
                else None
            )
        return WorkspaceIndexStatusResponse(
            workspace_id=workspace_id,
            repository_id=service.repository.repository_id,
            status=status,
            overview=overview,
        )

    def update(
        self,
        workspace_id: str,
        *,
        workspace_trusted: bool,
    ) -> WorkspaceIndexMutationResponse:
        self._require_trust(workspace_trusted)
        service = self._get(workspace_id)
        with _control_errors():
            result = service.update()
        return WorkspaceIndexMutationResponse(
            workspace_id=workspace_id,
            repository_id=service.repository.repository_id,
            result=result,
        )

    def verify(
        self,
        workspace_id: str,
    ) -> WorkspaceIndexVerificationResponse:
        service = self._get(workspace_id)
        with _control_errors():
            result = service.verify()
        return WorkspaceIndexVerificationResponse(
            workspace_id=workspace_id,
            repository_id=service.repository.repository_id,
            result=result,
        )

    def repair(
        self,
        workspace_id: str,
        *,
        workspace_trusted: bool,
    ) -> WorkspaceIndexMutationResponse:
        self._require_trust(workspace_trusted)
        service = self._get(workspace_id)
        with _control_errors():
            result = service.repair()
        return WorkspaceIndexMutationResponse(
            workspace_id=workspace_id,
            repository_id=service.repository.repository_id,
            result=result,
        )

    def cancel(
        self,
        workspace_id: str,
    ) -> WorkspaceIndexCancellationResponse:
        service = self._get(workspace_id)
        with _control_errors():
            requested = service.store.request_index_cancellation(
                service.repository.repository_id
            )
            operation = service.store.get_index_operation(
                service.repository.repository_id
            )
        return WorkspaceIndexCancellationResponse(
            workspace_id=workspace_id,
            repository_id=service.repository.repository_id,
            cancellation_requested=requested,
            operation_id=(
                operation.operation_id if operation is not None else None
            ),
            operation_state=(
                operation.state.value if operation is not None else None
            ),
        )

    def search(
        self,
        workspace_id: str,
        request: WorkspaceSearchRequest,
    ) -> WorkspaceSearchResponse:
        service = self._get(workspace_id)
        with _control_errors():
            report = service.search(
                request.query,
                projects=request.projects,
                languages=request.languages,
                symbol_kinds=request.symbol_kinds,
                path_prefixes=request.path_prefixes,
                test_only=request.test_only,
                offset=request.offset,
                limit=request.limit,
            )
        return WorkspaceSearchResponse(
            workspace_id=workspace_id,
            report=_search_evidence(report, request.include_evidence),
        )

    def symbol(
        self,
        workspace_id: str,
        symbol_id: str,
        *,
        include_evidence: bool,
    ) -> WorkspaceSymbolResponse:
        service = self._get(workspace_id)
        with _control_errors():
            report = service.symbols(symbol_id, limit=2)
        symbol = next(
            (item for item in report.symbols if item.symbol_id == symbol_id),
            None,
        )
        if symbol is None:
            raise ControlPlaneNotFoundError(
                "The requested repository symbol was not found."
            )
        return WorkspaceSymbolResponse(
            workspace_id=workspace_id,
            snapshot_id=report.snapshot_id,
            index_state=report.index_state.value,
            symbol=_symbol_evidence(symbol, include_evidence),
        )

    def graph(
        self,
        workspace_id: str,
        symbol_id: str,
        *,
        direction: Literal["dependencies", "dependents"],
        depth: int,
        offset: int,
        limit: int,
        include_unresolved: bool,
        include_evidence: bool,
    ) -> WorkspaceGraphResponse:
        service = self._get(workspace_id)
        with _control_errors():
            report = service.dependencies(
                symbol_id,
                direction=direction,
                max_depth=depth,
                include_unresolved=include_unresolved,
            )
        page = report.edges[offset : offset + limit]
        node_ids = {report.subject.symbol_id}
        for edge in page:
            node_ids.update((edge.source_id, edge.target_id))
        nodes = [item for item in report.nodes if item.node_id in node_ids]
        next_offset = offset + len(page)
        if next_offset >= len(report.edges):
            next_offset = None
        return WorkspaceGraphResponse(
            workspace_id=workspace_id,
            snapshot_id=report.snapshot_id,
            index_state=report.index_state.value,
            direction=direction,
            subject=_symbol_evidence(report.subject, include_evidence),
            nodes=nodes,
            edges=list(page),
            offset=offset,
            limit=limit,
            total_edges=len(report.edges),
            next_offset=next_offset,
            maximum_depth_reached=report.maximum_depth_reached,
            truncated=report.truncated or next_offset is not None,
        )

    def impact(
        self,
        workspace_id: str,
        request: WorkspaceImpactRequest,
    ) -> WorkspaceImpactResponse:
        service = self._get(workspace_id)
        with _control_errors():
            result = service.impact(
                request.subjects,
                max_depth=request.max_depth,
                max_nodes=request.max_nodes,
                projects=request.projects,
                languages=request.languages,
            )
        return WorkspaceImpactResponse(
            workspace_id=workspace_id,
            result=_impact_evidence(result, request.include_evidence),
        )

    def tests(
        self,
        workspace_id: str,
        request: WorkspaceImpactRequest,
    ) -> WorkspaceTestsResponse:
        service = self._get(workspace_id)
        with _control_errors():
            result = service.tests_for(
                request.subjects,
                max_depth=request.max_depth,
                max_nodes=request.max_nodes,
                projects=request.projects,
                languages=request.languages,
            )
        return WorkspaceTestsResponse(
            workspace_id=workspace_id,
            result=_test_evidence(result, request.include_evidence),
        )

    def context_plan(
        self,
        workspace_id: str,
        request: WorkspaceContextPlanRequest,
    ) -> WorkspaceContextPlanResponse:
        service = self._get(workspace_id)
        with _control_errors():
            result = service.context_plan(
                request.task,
                role=request.role,
                byte_budget=request.byte_budget,
                token_budget=request.token_budget,
                projects=request.projects,
                changed_paths=request.changed_paths,
            )
        return WorkspaceContextPlanResponse(
            workspace_id=workspace_id,
            result=_context_evidence(result, request.include_evidence),
        )

    def _register(self, workspace: str) -> RepositoryIntelligenceService:
        try:
            repository = self.workspace_service.require_repository(workspace)
        except GitRepositoryError as exc:
            raise ControlPlaneForbiddenError(
                "Repository indexing requires an isolated workspace repository."
            ) from exc
        canonical = Path(repository.workspace).resolve()
        candidate = RepositoryIntelligenceService(
            canonical,
            self.database_path,
        )
        workspace_id = candidate.workspace_identity.workspace_id
        with self._lock:
            existing = self._services.get(workspace_id)
            if existing is not None:
                if existing.workspace != canonical:
                    raise ControlPlaneConflictError(
                        "Workspace identity is already bound to another local root."
                    )
                return existing
            self._services[workspace_id] = candidate
        return candidate

    def _get(self, workspace_id: str) -> RepositoryIntelligenceService:
        if not workspace_id or len(workspace_id) > 256:
            raise ControlPlaneNotFoundError(
                "The requested repository workspace was not found."
            )
        with self._lock:
            service = self._services.get(workspace_id)
        if service is None:
            raise ControlPlaneNotFoundError(
                "The requested repository workspace was not found."
            )
        return service

    @staticmethod
    def _require_trust(workspace_trusted: bool) -> None:
        if not workspace_trusted:
            raise ControlPlaneForbiddenError(
                "Repository index mutation requires a trusted workspace."
            )


@contextmanager
def _control_errors() -> Iterator[None]:
    try:
        yield
    except IndexBusyError as exc:
        raise ControlPlaneConflictError(
            "A repository index operation is already active."
        ) from exc
    except IndexUnavailableError as exc:
        raise ControlPlaneConflictError(
            "The repository index is unavailable or requires repair."
        ) from exc
    except RepositoryQueryError as exc:
        raise ControlPlaneError(str(exc)) from exc
    except RepositoryIntelligenceError as exc:
        raise ControlPlaneConflictError(
            "The repository intelligence operation failed safely."
        ) from exc
    except ValueError as exc:
        raise ControlPlaneError(
            "The repository intelligence request is invalid."
        ) from exc


def _symbol_evidence(
    symbol: SymbolSummary,
    include_evidence: bool,
) -> SymbolSummary:
    if include_evidence:
        return symbol
    return symbol.model_copy(update={"signature": None})


def _search_evidence(
    report: RepositorySearchReport,
    include_evidence: bool,
) -> RepositorySearchReport:
    if include_evidence:
        return report
    results = tuple(
        SearchMatch(
            **{
                **item.model_dump(mode="python"),
                "symbol": (
                    _symbol_evidence(item.symbol, False)
                    if item.symbol is not None
                    else None
                ),
                "dependency_path": (),
                "matched_terms": (),
                "score_components": {},
            }
        )
        for item in report.results
    )
    return report.model_copy(update={"results": results})


def _impact_evidence(
    result: ImpactResult,
    include_evidence: bool,
) -> ImpactResult:
    if include_evidence:
        return result
    return result.model_copy(
        update={
            "evidence": (),
            "tests": _test_evidence(result.tests, False),
        }
    )


def _test_evidence(
    result: TestImpactResult,
    include_evidence: bool,
) -> TestImpactResult:
    if include_evidence:
        return result
    return result.model_copy(update={"evidence": ()})


def _context_evidence(
    result: ContextPlanSummary,
    include_evidence: bool,
) -> ContextPlanSummary:
    if include_evidence:
        return result
    candidates = tuple(
        ContextCandidateSummary(
            **{
                **item.model_dump(mode="python"),
                "reasons": (),
                "exclusion_reason": None,
            }
        )
        for item in result.candidates
    )
    return result.model_copy(update={"candidates": candidates})


__all__ = ["ControlIntelligenceService"]
