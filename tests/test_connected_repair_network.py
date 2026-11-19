from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import threading

import pytest

from agentbus.repair.live.contracts import ConnectionManifest, Identity
from agentbus.repair.live.transport import ApplicationConnector, ConnectorError, retry_after_seconds


@contextmanager
def response_target(monkeypatch, status, *, body=b'{"error":"private-provider-body-do-not-persist"}', headers=None):
    calls = []
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args): pass
        def do_GET(self):
            calls.append(self.path)
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            for key, value in (headers or {}).items(): self.send_header(key, value)
            self.end_headers()
            try: self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError): pass
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    monkeypatch.setenv("SYNDRA_REPAIR_NETWORK_FIXTURE", "owned-disposable-credential-" + "x" * 32)
    manifest = ConnectionManifest(connection_id="network-fixture", identity=Identity(application="test", tenant="test", environment="sandbox", configuration_revision="one"),
        destination=f"http://127.0.0.1:{server.server_port}/syndra-correction/v1", approved_addresses=["127.0.0.1"],
        credential_ref="env:SYNDRA_REPAIR_NETWORK_FIXTURE", revision=1, transport="disposable_loopback", permissions=["read"])
    try: yield ApplicationConnector(manifest), calls
    finally:
        server.shutdown()
        server.server_close()
        thread.join(5)


@pytest.mark.parametrize("status,code", [(401, "connector_auth"), (403, "connector_auth"), (404, "connector_not_found"),
    (409, "connector_conflict"), (412, "connector_conflict"), (422, "connector_conflict"), (500, "connector_unavailable"),
    (503, "connector_unavailable"), (302, "connector_unavailable")])
def test_real_http_errors_are_classified_without_raw_provider_bodies(monkeypatch, status, code):
    with response_target(monkeypatch, status, headers={"Location": "http://169.254.169.254/latest/meta-data/"}) as (client, calls):
        with pytest.raises(ConnectorError) as caught:
            client.request("GET", "/capabilities")
        assert caught.value.code == code
        assert "private-provider-body" not in str(caught.value)
        assert calls == ["/syndra-correction/v1/capabilities"]


def test_retry_after_is_not_shortened_to_an_unsafe_early_retry(monkeypatch):
    with response_target(monkeypatch, 429, headers={"Retry-After": "120"}) as (client, _calls):
        with pytest.raises(ConnectorError) as caught:
            client.request("GET", "/capabilities")
        assert caught.value.retry_after == 120
    assert retry_after_seconds("invalid") == 30


def test_real_oversized_and_malformed_success_responses_are_rejected(monkeypatch):
    for body in (b"x" * 1_500_001, b"invalid-json"):
        with response_target(monkeypatch, 200, body=body) as (client, _calls):
            with pytest.raises(ConnectorError, match="invalid, oversized"):
                client.request("GET", "/capabilities")


def test_real_tls_failure_does_not_downgrade_to_plain_http(monkeypatch):
    from agentbus.repair.live.transport import _PinnedConnection
    with response_target(monkeypatch, 200) as (client, calls):
        from urllib.parse import urlsplit
        url = urlsplit(client.manifest.destination)
        connection = _PinnedConnection("127.0.0.1", url.port, "127.0.0.1", tls=True, timeout=2)
        try:
            with pytest.raises(OSError):
                connection.request("GET", "/syndra-correction/v1/capabilities")
        finally: connection.finish()
        assert calls == []
