import pytest
import requests

from agentbus.control.acceptance import _resume_run


class StubResponse:
    def __init__(self, status_code, payload):
        self.status_code = status_code
        self.payload = payload

    def json(self):
        return self.payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"HTTP {self.status_code}")


def test_resume_waits_for_transient_active_owner_release(monkeypatch):
    responses = iter(
        [
            StubResponse(
                409,
                {
                    "error": {
                        "message": "The run already has an active owner.",
                    }
                },
            ),
            StubResponse(
                200,
                {
                    "run_id": "run-1",
                    "status": "running",
                    "resumed": True,
                },
            ),
        ]
    )
    sleeps = []
    monkeypatch.setattr(
        "agentbus.control.acceptance.requests.post",
        lambda *args, **kwargs: next(responses),
    )

    result = _resume_run(
        "http://127.0.0.1:43123",
        {"Authorization": "Bearer test"},
        "run-1",
        clock=lambda: 0,
        sleeper=sleeps.append,
    )

    assert result["resumed"] is True
    assert sleeps == [0.02]


def test_resume_does_not_retry_an_unrelated_conflict(monkeypatch):
    response = StubResponse(
        409,
        {
            "error": {
                "message": "The run cannot be resumed from its current state.",
            }
        },
    )
    monkeypatch.setattr(
        "agentbus.control.acceptance.requests.post",
        lambda *args, **kwargs: response,
    )

    with pytest.raises(requests.HTTPError, match="HTTP 409"):
        _resume_run(
            "http://127.0.0.1:43123",
            {"Authorization": "Bearer test"},
            "run-1",
            clock=lambda: 0,
            sleeper=lambda _: pytest.fail("unrelated conflicts must not be retried"),
        )
