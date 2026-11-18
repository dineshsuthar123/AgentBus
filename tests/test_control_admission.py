from __future__ import annotations

import asyncio

import pytest

from agentbus.control.admission import ControlAdmission, ControlAdmissionMiddleware


def test_rate_limit_refills_with_elapsed_time_and_has_bounded_burst():
    now = [0.0]
    limit = ControlAdmission(requests_per_second=2, burst=2, clock=lambda: now[0])
    for _ in range(2):
        assert limit.acquire(stream=False) is None
        limit.release(stream=False)
    assert limit.acquire(stream=False) == 429
    now[0] = 0.5
    assert limit.acquire(stream=False) is None
    limit.release(stream=False)
    assert limit.acquire(stream=False) == 429
    now[0] = 1000
    for _ in range(2):
        assert limit.acquire(stream=False) is None
        limit.release(stream=False)
    assert limit.acquire(stream=False) == 429


def test_stream_capacity_reserves_rest_capacity_and_recovers_after_release():
    limit = ControlAdmission(max_requests=1, max_streams=1)
    assert limit.acquire(stream=True) is None
    assert limit.acquire(stream=True) == 503
    assert limit.acquire(stream=False) is None
    assert limit.acquire(stream=False) == 503
    limit.release(stream=True)
    assert limit.acquire(stream=True) is None
    limit.release(stream=True)
    limit.release(stream=False)


@pytest.mark.parametrize("failure", [RuntimeError, asyncio.CancelledError])
def test_middleware_holds_stream_slot_through_disconnect_and_failure(failure):
    async def scenario():
        limit = ControlAdmission(max_streams=1)
        started, finish = asyncio.Event(), asyncio.Event()
        messages = []

        async def app(_scope, _receive, _send):
            started.set()
            await finish.wait()
            raise failure()

        async def send(message):
            messages.append(message)

        async def receive():
            return {"type": "http.disconnect"}

        middleware = ControlAdmissionMiddleware(app, admission=limit)
        scope = {"type": "http", "path": "/api/v1/events", "method": "GET", "headers": []}
        first = asyncio.create_task(middleware(scope, receive, send))
        await started.wait()
        await middleware(scope, receive, send)
        assert messages[0]["status"] == 503
        finish.set()
        with pytest.raises(failure):
            await first
        assert limit.acquire(stream=True) is None
        limit.release(stream=True)

    asyncio.run(scenario())
