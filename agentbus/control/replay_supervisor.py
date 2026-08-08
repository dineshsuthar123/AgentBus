from __future__ import annotations

import threading
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from typing import Callable

from agentbus.control.errors import (
    ControlPlaneConflictError,
    ControlPlaneUnavailableError,
)
from agentbus.control.models import (
    ReplayAcceptedResponse,
    ReplayCancelResponse,
    ReplayCreateRequest,
    ReplaySessionResponse,
)
from agentbus.control.services import ControlQueryService
from agentbus.replay.forks import ForkRequest
from agentbus.replay.service import TraceReplayService
from agentbus.runtime.intelligence import load_repository_intelligence_source
from agentbus.replay.session import (
    ReplayRequest,
    ReplaySessionStatus,
    ToolReplayStrategy,
)
from agentbus.trace.models import ReplayMode

ReplayServiceFactory = Callable[
    [Callable[[], bool]],
    TraceReplayService,
]
_TERMINAL_REPLAY_STATUSES = {
    ReplaySessionStatus.SUCCEEDED.value,
    ReplaySessionStatus.FAILED.value,
    ReplaySessionStatus.CANCELLED.value,
    ReplaySessionStatus.INCOMPATIBLE.value,
    ReplaySessionStatus.AWAITING_INPUT.value,
}


@dataclass
class ActiveReplay:
    replay_id: str
    cancelled: threading.Event
    future: Future[object]


class BackgroundReplaySupervisor:
    """Own bounded providerless replay workers for the local control daemon."""

    def __init__(
        self,
        query_service: ControlQueryService,
        *,
        max_background_replays: int = 4,
        service_factory: ReplayServiceFactory | None = None,
    ) -> None:
        if max_background_replays < 1 or max_background_replays > 32:
            raise ValueError(
                "max_background_replays must be between 1 and 32"
            )
        self.query_service = query_service
        self.max_background_replays = max_background_replays
        self._service_factory = service_factory
        self._executor = ThreadPoolExecutor(
            max_workers=max_background_replays,
            thread_name_prefix="agentbus-replay",
        )
        self._active: dict[str, ActiveReplay] = {}
        self._lock = threading.RLock()
        self._closed = False

    def submit(
        self,
        run_id: str,
        request: ReplayCreateRequest,
    ) -> ReplayAcceptedResponse:
        with self._lock:
            self._ensure_open()
            if len(self._active) >= self.max_background_replays:
                raise ControlPlaneUnavailableError(
                    "The local replay worker pool is at capacity."
                )
            trace = self.query_service.trace(run_id)
            cancelled = threading.Event()
            service = (
                self._service_factory(cancelled.is_set)
                if self._service_factory is not None
                else self._default_service(cancelled.is_set, run_id)
            )
            replay_request, fork_request = self._domain_requests(
                trace,
                request,
            )
            prepared, pending = service.queue_replay(
                trace.trace_id,
                replay_request,
            )
            try:
                future = self._executor.submit(
                    self._execute,
                    service,
                    trace.trace_id,
                    prepared,
                    fork_request,
                )
            except Exception:
                # The durable pending row remains available for explicit recovery.
                raise
            active = ActiveReplay(
                replay_id=prepared.replay_id,
                cancelled=cancelled,
                future=future,
            )
            self._active[prepared.replay_id] = active
            future.add_done_callback(
                lambda _future, item=active: self._completed(item)
            )
        response = self.query_service.replay_session_response(pending)
        return ReplayAcceptedResponse.model_validate(response.model_dump())

    def cancel(self, replay_id: str) -> ReplayCancelResponse:
        session = self.query_service.replay(replay_id)
        if session.status in _TERMINAL_REPLAY_STATUSES:
            return ReplayCancelResponse(
                replay_id=replay_id,
                status=session.status,
                cancellation_requested=False,
            )
        with self._lock:
            active = self._active.get(replay_id)
            if active is None:
                latest = self.query_service.replay(replay_id)
                if latest.status in _TERMINAL_REPLAY_STATUSES:
                    return ReplayCancelResponse(
                        replay_id=replay_id,
                        status=latest.status,
                        cancellation_requested=False,
                    )
                raise ControlPlaneConflictError(
                    "The replay is not owned by this daemon and was not changed."
                )
            active.cancelled.set()
        return ReplayCancelResponse(
            replay_id=replay_id,
            status=session.status,
            cancellation_requested=True,
        )

    def wait(
        self,
        replay_id: str,
        *,
        timeout: float | None = None,
    ) -> ReplaySessionResponse:
        with self._lock:
            active = self._active.get(replay_id)
        if active is not None:
            try:
                active.future.result(timeout=timeout)
            except Exception:
                # The worker persists a bounded terminal failure for API inspection.
                pass
        return self.query_service.replay(replay_id)

    def has_active_replays(self) -> bool:
        with self._lock:
            return bool(self._active)

    def shutdown(self, *, wait: bool = True) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            active = list(self._active.values())
        for item in active:
            item.cancelled.set()
        self._executor.shutdown(wait=wait, cancel_futures=False)

    def _default_service(
        self,
        cancelled: Callable[[], bool],
        run_id: str,
    ) -> TraceReplayService:
        run = self.query_service.get_run(run_id)
        config = self.query_service.config.with_overrides(
            workspace_dir=run.workspace
        )
        return TraceReplayService(
            config,
            state_store=self.query_service.store,
            cancelled=cancelled,
            intelligence_source=load_repository_intelligence_source(
                config.workspace_path,
                config.state_database_path.parent / "repository-index.sqlite3",
            ),
        )

    @staticmethod
    def _execute(
        service: TraceReplayService,
        trace_id: str,
        request: ReplayRequest,
        fork_request: ForkRequest | None,
    ) -> object:
        if fork_request is not None:
            return service.fork(trace_id, fork_request)
        return service.replay(trace_id, request)

    @staticmethod
    def _domain_requests(
        trace,
        request: ReplayCreateRequest,
    ) -> tuple[ReplayRequest, ForkRequest | None]:
        mode = ReplayMode(request.mode)
        replay = ReplayRequest(
            source_trace_id=trace.trace_id,
            source_run_id=trace.run_id,
            mode=mode,
            from_span_id=request.from_span_id,
            from_checkpoint_id=request.from_checkpoint_id,
            fork=request.fork,
            changed_inputs=request.changed_inputs,
            tool_strategies={
                name: ToolReplayStrategy(strategy)
                for name, strategy in request.tool_strategies.items()
            },
            live_provider_consent=request.live_provider_consent,
        )
        fork = None
        if request.fork:
            fork = ForkRequest(
                replay_id=replay.replay_id,
                source_trace_id=trace.trace_id,
                source_run_id=trace.run_id,
                mode=mode,
                changed_inputs=request.changed_inputs,
                live_provider_consent=request.live_provider_consent,
            )
        return replay, fork

    def _completed(self, active: ActiveReplay) -> None:
        with self._lock:
            if self._active.get(active.replay_id) is active:
                self._active.pop(active.replay_id, None)

    def _ensure_open(self) -> None:
        if self._closed:
            raise ControlPlaneUnavailableError(
                "The control-plane replay supervisor is shutting down."
            )


__all__ = ["ActiveReplay", "BackgroundReplaySupervisor"]
