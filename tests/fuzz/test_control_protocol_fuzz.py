from __future__ import annotations

from datetime import datetime, timezone
from urllib.parse import quote

import pytest
from fastapi.testclient import TestClient
from hypothesis import given, settings, strategies as st

from agentbus.config import AgentBusConfig
from agentbus.control.app import ControlAppContext, create_app
from agentbus.control.services import ControlQueryService
from agentbus.execution.state_store import StateStore


TOKEN = "control-fuzz-token-that-is-at-least-thirty-two-bytes"
SECRET = "AZURE_OPENAI_API_KEY=control-fuzz-private-value"
PROTOCOL = "2025-11-25"
FUZZ_SETTINGS = settings(
    max_examples=100,
    deadline=None,
    derandomize=True,
    database=None,
)
_UNICODE_TEXT = st.text(
    alphabet=st.characters(blacklist_categories=("Cs",)),
    max_size=512,
)
_JSON_VALUE = st.recursive(
    st.none() | st.booleans() | st.integers() | _UNICODE_TEXT,
    lambda children: st.lists(children, max_size=8)
    | st.dictionaries(_UNICODE_TEXT, children, max_size=8),
    max_leaves=32,
)


class _NoDispatchSupervisor:
    def __init__(self) -> None:
        self.submissions: list[object] = []
        self.cancellations: list[object] = []
        self.resumptions: list[object] = []

    def submit(self, request):
        self.submissions.append(request)
        raise AssertionError("invalid fuzz requests must not reach run submission")

    def cancel(self, run_id, reason=None):
        self.cancellations.append((run_id, reason))
        raise AssertionError("invalid fuzz requests must not reach cancellation")

    def resume(self, run_id):
        self.resumptions.append(run_id)
        raise AssertionError("invalid fuzz requests must not reach resume")

    def shutdown(self, *, wait=True):
        return None


@pytest.fixture(scope="module")
def control_client(
    tmp_path_factory: pytest.TempPathFactory,
):
    root = tmp_path_factory.mktemp("control-protocol-fuzz")
    store = StateStore(root / "state.db")
    config = AgentBusConfig(
        workspace_dir=str(root),
        state_db=str(root / "state.db"),
    )
    supervisor = _NoDispatchSupervisor()
    app = create_app(
        token=TOKEN,
        query_service=ControlQueryService(config, store),
        supervisor=supervisor,
        context=ControlAppContext(
            daemon_id="control-fuzz-daemon",
            host="127.0.0.1",
            port=43123,
            started_at=datetime.now(timezone.utc),
            state_database=str(root / "state.db"),
        ),
        shutdown_supervisor=False,
    )
    try:
        with TestClient(app, raise_server_exceptions=False) as client:
            yield client, supervisor
    finally:
        app.state.replay_supervisor.shutdown(wait=True)


def _auth() -> dict[str, str]:
    return {"Authorization": f"Bearer {TOKEN}"}


def _assert_rest_error(
    response,
    code: str | set[str] = "validation_error",
) -> None:
    assert 400 <= response.status_code < 500
    expected_codes = {code} if isinstance(code, str) else code
    assert response.json()["error"]["code"] in expected_codes
    assert len(response.content) < 65_536
    assert "traceback" not in response.text.lower()
    assert SECRET not in response.text
    assert TOKEN not in response.text


def _assert_rpc_error(response) -> None:
    assert response.status_code == 200
    payload = response.json()
    assert payload["jsonrpc"] == "2.0"
    assert payload["error"]["code"] in {-32700, -32600, -32602, -32000}
    assert len(response.content) < 65_536
    assert "traceback" not in response.text.lower()
    assert SECRET not in response.text
    assert TOKEN not in response.text


@st.composite
def _invalid_run_payloads(draw: st.DrawFn) -> dict:
    case = draw(
        st.sampled_from(
            (
                "missing",
                "extra",
                "wrong_types",
                "wrong_enum",
                "massive",
                "recursive",
                "path",
                "unicode",
            )
        )
    )
    if case == "missing":
        return draw(
            st.sampled_from(
                (
                    {},
                    {"task": "missing workspace"},
                    {"workspace": "."},
                )
            )
        )
    if case == "extra":
        unknown = draw(
            st.text("abcdefghijklmnopqrstuvwxyz_", min_size=1, max_size=64).filter(
                lambda value: value not in {"task", "workspace", "provider"}
            )
        )
        return {
            "task": "extra field",
            "workspace": ".",
            unknown: draw(_JSON_VALUE),
        }
    if case == "wrong_types":
        return {
            "task": draw(st.none() | st.integers() | st.lists(_JSON_VALUE)),
            "workspace": draw(st.none() | st.integers() | st.dictionaries(
                _UNICODE_TEXT,
                _JSON_VALUE,
                max_size=4,
            )),
        }
    if case == "wrong_enum":
        return {
            "task": "invalid enum",
            "workspace": ".",
            "provider": draw(
                _UNICODE_TEXT.filter(
                    lambda value: value not in {
                        "ollama",
                        "azure",
                        "deterministic",
                    }
                )
            ),
        }
    if case == "massive":
        return {
            "task": "massive workspace",
            "workspace": "x" * draw(
                st.integers(min_value=4_097, max_value=20_000)
            ),
        }
    if case == "recursive":
        return {
            "task": "recursive metadata",
            "workspace": ".",
            "provider": "invalid",
            "metadata": draw(_JSON_VALUE),
        }
    if case == "path":
        path = draw(
            st.sampled_from(
                (
                    "../.env",
                    "C:relative",
                    "//server/share/private",
                    "\\\\?\\C:\\private",
                    "file:///etc/passwd",
                    "%2e%2e%2f.env",
                    "name:stream",
                )
            )
        )
        return {
            "task": "path-like input",
            "workspace": path,
            "provider": "invalid",
        }
    return {
        "task": draw(_UNICODE_TEXT),
        "workspace": ".",
        "workflow": "invalid",
    }


@FUZZ_SETTINGS
@given(raw=st.binary(max_size=2_048))
def test_malformed_json_returns_bounded_public_errors(
    control_client,
    raw: bytes,
) -> None:
    client, _ = control_client
    body = raw + SECRET.encode("ascii")

    rest = client.post(
        "/api/v1/runs",
        headers={**_auth(), "Content-Type": "application/json"},
        content=body,
    )
    rpc = client.post(
        "/mcp",
        headers={**_auth(), "Content-Type": "application/json"},
        content=body,
    )

    _assert_rest_error(rest, code={"invalid_request", "validation_error"})
    _assert_rpc_error(rpc)


@FUZZ_SETTINGS
@given(payload=_invalid_run_payloads())
def test_invalid_rest_payloads_never_dispatch_or_leak(
    control_client,
    payload: dict,
) -> None:
    client, supervisor = control_client

    response = client.post("/api/v1/runs", headers=_auth(), json=payload)

    _assert_rest_error(response)
    assert supervisor.submissions == []


@st.composite
def _invalid_rpc_payloads(draw: st.DrawFn):
    case = draw(
        st.sampled_from(
            (
                "wrong_version",
                "missing",
                "invalid_id",
                "invalid_method",
                "invalid_params",
                "massive",
                "recursive",
                "duplicate",
            )
        )
    )
    if case == "wrong_version":
        return {
            "jsonrpc": draw(_UNICODE_TEXT.filter(lambda value: value != "2.0")),
            "id": 1,
            "method": "ping",
        }
    if case == "missing":
        return draw(
            st.sampled_from(
                (
                    {},
                    {"jsonrpc": "2.0", "id": 1},
                    {"jsonrpc": "2.0", "method": 1},
                )
            )
        )
    if case == "invalid_id":
        return {
            "jsonrpc": "2.0",
            "id": draw(st.booleans() | st.lists(_JSON_VALUE) | st.dictionaries(
                _UNICODE_TEXT,
                _JSON_VALUE,
                max_size=4,
            )),
            "method": "ping",
        }
    if case == "invalid_method":
        return {
            "jsonrpc": "2.0",
            "id": 1,
            "method": draw(st.none() | st.integers() | st.lists(_JSON_VALUE)),
        }
    if case == "invalid_params":
        return {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": draw(st.none() | st.integers() | st.lists(_JSON_VALUE)),
        }
    if case == "massive":
        return {
            "jsonrpc": "2.0",
            "id": "x" * draw(st.integers(min_value=4_097, max_value=20_000)),
            "method": SECRET + "x" * 20_000,
            "params": {},
        }
    if case == "recursive":
        return {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "unknown",
            "params": {"nested": draw(_JSON_VALUE)},
        }
    request_id = draw(st.integers() | _UNICODE_TEXT)
    message = {
        "jsonrpc": "2.0",
        "id": request_id,
        "method": "ping",
        "params": {},
    }
    return [message, message]


@FUZZ_SETTINGS
@given(payload=_invalid_rpc_payloads())
def test_invalid_jsonrpc_payloads_fail_closed_and_stay_bounded(
    control_client,
    payload,
) -> None:
    client, supervisor = control_client

    response = client.post(
        "/mcp",
        headers={
            **_auth(),
            "Content-Type": "application/json",
            "MCP-Protocol-Version": PROTOCOL,
        },
        json=payload,
    )

    _assert_rpc_error(response)
    assert supervisor.submissions == []
    assert supervisor.cancellations == []


_INVALID_REVISION = (
    st.none()
    | st.integers(max_value=0)
    | _UNICODE_TEXT.filter(
        lambda value: not value.strip().lstrip("+-").isdigit()
    )
    | st.lists(st.integers(), max_size=4)
    | st.dictionaries(_UNICODE_TEXT, _JSON_VALUE, max_size=4)
)


@FUZZ_SETTINGS
@given(revision=_INVALID_REVISION)
def test_invalid_cancellation_revisions_never_reach_approval_service(
    control_client,
    revision,
) -> None:
    client, supervisor = control_client

    response = client.post(
        "/api/v1/runs/missing/approvals/missing/approve",
        headers=_auth(),
        json={"revision": revision},
    )

    _assert_rest_error(response)
    assert supervisor.cancellations == []


def test_recursive_and_oversized_bodies_are_rejected_with_bounded_errors(
    control_client,
) -> None:
    client, _ = control_client
    depth = 2_000
    nested = (
        '{"task":"x","workspace":".","unknown":'
        + "[" * depth
        + "0"
        + "]" * depth
        + "}"
    ).encode("utf-8")

    recursive = client.post(
        "/api/v1/runs",
        headers={**_auth(), "Content-Type": "application/json"},
        content=nested,
    )
    oversized = client.post(
        "/api/v1/runs",
        headers={**_auth(), "Content-Type": "application/json"},
        content=b"{" + b"x" * 1_000_001,
    )

    _assert_rest_error(recursive)
    _assert_rest_error(oversized, code="request_too_large")


def test_replay_and_pagination_validation_matrix_is_stable(
    control_client,
) -> None:
    client, _ = control_client
    replay_payloads = (
        {},
        {"mode": "unknown"},
        {"mode": "offline", "unknown": True},
        {"mode": "offline", "from_span_id": "x" * 129},
        {
            "mode": "offline",
            "from_span_id": "span",
            "from_checkpoint_id": "checkpoint",
        },
        {"mode": "offline", "changed_inputs": {"task_text": "changed"}},
    )
    for payload in replay_payloads:
        response = client.post(
            "/api/v1/runs/missing/replays",
            headers=_auth(),
            json=payload,
        )
        _assert_rest_error(response)

    invalid_queries = (
        ("/api/v1/runs", {"limit": "0"}),
        ("/api/v1/runs", {"limit": "1001"}),
        ("/api/v1/runs/missing/trace/spans", {"after": "-1"}),
        ("/api/v1/runs/missing/trace/spans", {"limit": "501"}),
        ("/api/v1/replays", {"status": "unknown"}),
        (
            "/api/v1/runs/missing/changes/file.py",
            {"revision": "middle"},
        ),
    )
    for path, params in invalid_queries:
        response = client.get(path, headers=_auth(), params=params)
        _assert_rest_error(response)


def test_invalid_replay_ids_and_unicode_paths_fail_safely(control_client) -> None:
    client, _ = control_client
    replay_ids = (
        "not-a-uuid",
        "00000000-0000-0000-0000-00000000000g",
        "{" + "x" * 127,
        chr(0x6771) + chr(0x4EAC) + "-replay",
    )
    for replay_id in replay_ids:
        response = client.get(
            "/api/v1/replays/" + quote(replay_id, safe=""),
            headers=_auth(),
        )
        _assert_rest_error(response, code="not_found")


def test_control_plane_remains_healthy_after_fuzzing(control_client) -> None:
    client, supervisor = control_client

    health = client.get("/health")
    info = client.get("/api/v1/info", headers=_auth())

    assert health.status_code == 200
    assert health.json()["status"] == "ok"
    assert info.status_code == 200
    assert len(info.content) < 65_536
    assert TOKEN not in info.text
    assert supervisor.submissions == []
    assert supervisor.cancellations == []
    assert supervisor.resumptions == []
