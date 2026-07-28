from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar, Token, copy_context
from functools import wraps
from typing import Callable, Iterator, ParamSpec, TypeVar

from pydantic import ConfigDict

from agentbus.trace.models import TraceIdentifier, TraceModel


class TraceContext(TraceModel):
    """Serializable causal identity propagated through one execution path."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    trace_id: TraceIdentifier
    run_id: TraceIdentifier
    span_id: TraceIdentifier
    task_id: TraceIdentifier | None = None
    worker_id: TraceIdentifier | None = None
    invocation_id: TraceIdentifier | None = None

    def child(
        self,
        span_id: str,
        *,
        task_id: str | None = None,
        worker_id: str | None = None,
        invocation_id: str | None = None,
    ) -> "TraceContext":
        """Create a child identity, inheriting optional causal dimensions."""
        return TraceContext(
            trace_id=self.trace_id,
            run_id=self.run_id,
            span_id=span_id,
            task_id=self.task_id if task_id is None else task_id,
            worker_id=self.worker_id if worker_id is None else worker_id,
            invocation_id=(
                self.invocation_id if invocation_id is None else invocation_id
            ),
        )


_TRACE_CONTEXT: ContextVar[TraceContext | None] = ContextVar(
    "agentbus_trace_context",
    default=None,
)
_Result = TypeVar("_Result")
_Parameters = ParamSpec("_Parameters")


def current_trace_context(*, required: bool = False) -> TraceContext | None:
    context = _TRACE_CONTEXT.get()
    if required and context is None:
        raise RuntimeError("No AgentBus trace context is active.")
    return context


def set_trace_context(context: TraceContext) -> Token[TraceContext | None]:
    """Bind context until the returned token is reset."""
    return _TRACE_CONTEXT.set(context)


def reset_trace_context(token: Token[TraceContext | None]) -> None:
    _TRACE_CONTEXT.reset(token)


@contextmanager
def trace_context(context: TraceContext) -> Iterator[TraceContext]:
    token = set_trace_context(context)
    try:
        yield context
    finally:
        reset_trace_context(token)


def copy_trace_context(
    function: Callable[_Parameters, _Result],
) -> Callable[_Parameters, _Result]:
    """Capture the current context in a callable suitable for thread submission."""
    inherited = copy_context()

    @wraps(function)
    def wrapped(
        *args: _Parameters.args,
        **kwargs: _Parameters.kwargs,
    ) -> _Result:
        return inherited.copy().run(function, *args, **kwargs)

    return wrapped


__all__ = [
    "TraceContext",
    "copy_trace_context",
    "current_trace_context",
    "reset_trace_context",
    "set_trace_context",
    "trace_context",
]
