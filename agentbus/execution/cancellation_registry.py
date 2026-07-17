from __future__ import annotations

import threading

from agentbus.execution.cancellation import CancellationState, CancellationToken
from agentbus.execution.state_store import RunNotFoundError, StateStore


class CancellationRegistry:
    """Own one shared cancellation token per run and persist its snapshots."""

    def __init__(self, state_store: StateStore):
        self._store = state_store
        self._lock = threading.RLock()
        self._tokens: dict[str, CancellationToken] = {}
        self._pending: dict[str, CancellationState] = {}

    def prepare(self, run_id: str) -> CancellationToken:
        """Create a token before a run row exists.

        Control-plane submission registers work before its planner persists the
        durable run. Updates during that narrow window remain in memory and are
        flushed by ``synchronize`` immediately after run creation.
        """
        with self._lock:
            token = self._tokens.get(run_id)
            if token is not None:
                return token
            token = self._attach(run_id, CancellationState())
            self._tokens[run_id] = token
            return token

    def get(self, run_id: str) -> CancellationToken:
        with self._lock:
            token = self._tokens.get(run_id)
            if token is not None:
                return token
            state = self._store.get_cancellation_state(run_id)
            token = self._attach(run_id, state)
            self._tokens[run_id] = token
            return token

    def register(
        self,
        run_id: str,
        token: CancellationToken,
        *,
        persist_current: bool = False,
    ) -> CancellationToken:
        with self._lock:
            existing = self._tokens.get(run_id)
            if existing is not None:
                if existing is not token:
                    raise ValueError(
                        f"Run '{run_id}' already has a different cancellation token."
                    )
                return existing
            token.add_listener(
                lambda updated, target=run_id: self._persist(target, updated)
            )
            self._tokens[run_id] = token
        if persist_current:
            self.synchronize(run_id)
        return token

    def synchronize(self, run_id: str) -> CancellationState:
        token = self.get(run_id)
        persisted = self._store.persist_cancellation_state(
            run_id,
            token.snapshot(),
        )
        with self._lock:
            self._pending.pop(run_id, None)
        return persisted

    def recover(self, run_id: str) -> CancellationToken:
        token = self.get(run_id)
        token.abandon_active_operations("cancellation-recovery")
        return token

    def request(self, run_id: str, reason: str | None = None) -> bool:
        return self.get(run_id).request(reason)

    def discard(self, run_id: str) -> None:
        with self._lock:
            self._tokens.pop(run_id, None)
            self._pending.pop(run_id, None)

    def shutdown(self) -> None:
        with self._lock:
            self._tokens.clear()
            self._pending.clear()

    @property
    def run_ids(self) -> tuple[str, ...]:
        with self._lock:
            return tuple(sorted(self._tokens))

    def _attach(
        self,
        run_id: str,
        state: CancellationState,
    ) -> CancellationToken:
        token = CancellationToken(state)
        token.add_listener(
            lambda updated, target=run_id: self._persist(target, updated)
        )
        return token

    def _persist(self, run_id: str, state: CancellationState) -> None:
        try:
            self._store.persist_cancellation_state(run_id, state)
        except RunNotFoundError:
            with self._lock:
                pending = self._pending.get(run_id)
                if pending is None or state.revision > pending.revision:
                    self._pending[run_id] = state
