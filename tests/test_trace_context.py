from concurrent.futures import ThreadPoolExecutor

import pytest

from agentbus.trace import (
    TraceContext,
    copy_trace_context,
    current_trace_context,
    trace_context,
)


def test_nested_trace_context_inherits_and_restores_identity() -> None:
    root = TraceContext(trace_id="trace-1", run_id="run-1", span_id="root")
    task = root.child("task-1", task_id="step-1", worker_id="worker-1")

    assert current_trace_context() is None
    with trace_context(root):
        assert current_trace_context(required=True) == root
        with trace_context(task):
            assert current_trace_context(required=True) == task
        assert current_trace_context(required=True) == root
    assert current_trace_context() is None


def test_trace_context_restores_after_failure() -> None:
    root = TraceContext(trace_id="trace-1", run_id="run-1", span_id="root")

    with pytest.raises(RuntimeError, match="boom"):
        with trace_context(root):
            raise RuntimeError("boom")

    with pytest.raises(RuntimeError, match="No AgentBus trace context"):
        current_trace_context(required=True)


def test_child_context_is_immutable_and_serializable() -> None:
    context = TraceContext(
        trace_id="trace-1",
        run_id="run-1",
        span_id="tool-1",
        task_id="step-1",
        invocation_id="invocation-1",
    )

    assert TraceContext.model_validate_json(context.model_dump_json()) == context
    with pytest.raises(Exception):
        context.span_id = "mutated"


def test_copied_context_propagates_to_worker_without_leaking() -> None:
    root = TraceContext(trace_id="trace-1", run_id="run-1", span_id="root")

    def read_context() -> TraceContext | None:
        return current_trace_context()

    with trace_context(root):
        inherited_read = copy_trace_context(read_context)
        with ThreadPoolExecutor(max_workers=1) as executor:
            inherited = executor.submit(inherited_read).result()
            plain = executor.submit(read_context).result()

    assert inherited == root
    assert plain is None
