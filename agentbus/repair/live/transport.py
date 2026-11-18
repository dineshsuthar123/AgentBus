from __future__ import annotations

import hashlib
import http.client
import ipaddress
import json
import os
import re
import socket
import ssl
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import math
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout
import threading
import time
from urllib.parse import urlsplit

from pydantic import ValidationError

from agentbus.repair.live.contracts import (
    AccessVerification, Capabilities, ConditionalOperation, ConnectionManifest, Receipt, TargetPlan,
)
from agentbus.repair.service import RepairError

_DNS_WORKERS = ThreadPoolExecutor(max_workers=2, thread_name_prefix="repair-dns")
_DNS_SLOTS = threading.BoundedSemaphore(2)


def resolve_addresses(host, port):
    started = time.monotonic()
    if not _DNS_SLOTS.acquire(timeout=3):
        raise ConnectorError("connector_destination")
    future = _DNS_WORKERS.submit(socket.getaddrinfo, host, port, type=socket.SOCK_STREAM)
    future.add_done_callback(lambda _future: _DNS_SLOTS.release())
    try:
        return future.result(timeout=max(0.001, 3 - (time.monotonic() - started)))
    except (FutureTimeout, OSError):
        # The OS resolver may finish later; its occupied slot is retained until
        # then, so timed-out requests cannot create an unbounded thread/queue.
        raise ConnectorError("connector_destination") from None


class ConnectorError(RepairError):
    def __init__(self, code="connector_unavailable", *, status=503, retry_after=None):
        messages = {
            "connector_auth": "The target denied this credential or operation scope. Rotate or check its permissions.",
            "connector_conflict": "The target rejected changed, expired or revoked approval/preconditions. Review a fresh plan.",
            "connector_not_found": "The target has no durable receipt yet. This does not prove an in-flight request cannot commit.",
            "connector_rate_limited": "The target rate-limited the request. Inspect Retry-After before continuing.",
            "connector_invalid": "The target returned an invalid, oversized or unsupported response.",
            "connector_destination": "The approved destination or resolved address is unsafe or changed.",
        }
        super().__init__(messages.get(code, "The target outcome could not be confirmed. Reconcile the existing operation before retrying."), code=code, status=status)
        self.retry_after = retry_after


def credential(reference: str) -> str:
    if re.fullmatch(r"env:SYNDRA_REPAIR_[A-Z0-9_]{1,80}", reference):
        value = os.environ.get(reference[4:])
    elif re.fullmatch(r"keyring:[A-Za-z0-9_.-]{1,80}", reference):
        try:
            import keyring
            backend = keyring.get_keyring()
            allowed = ("keyring.backends.Windows", "keyring.backends.macOS", "keyring.backends.SecretService", "keyring.backends.kwallet", "keyring.backends.libsecret")
            if not type(backend).__module__.startswith(allowed):
                raise ValueError("Only an OS credential backend is supported.")
            value = keyring.get_password("syndra.customer-repair", reference[8:])
        except Exception:
            value = None
    else:
        value = None
    if not value or len(value) < 24 or len(value) > 4096 or any(ord(c) < 33 or ord(c) > 126 for c in value):
        raise ConnectorError("connector_auth", status=403)
    return value


def credential_fingerprint(manifest: ConnectionManifest) -> str:
    values = [manifest.credential_ref, credential(manifest.credential_ref)]
    if manifest.stripe:
        values += [manifest.stripe.credential_ref, credential(manifest.stripe.credential_ref)]
    return hashlib.sha256(json.dumps(values).encode()).hexdigest()


def retry_after_seconds(value):
    try:
        if value.isdigit():
            return min(86400, max(1, int(value)))
        until = parsedate_to_datetime(value)
        return min(86400, max(1, math.ceil((until - datetime.now(timezone.utc)).total_seconds())))
    except (ValueError, TypeError, OverflowError):
        return 30


def validate_destination(manifest: ConnectionManifest):
    try:
        url = urlsplit(manifest.destination)
        host, port = url.hostname, url.port or (443 if url.scheme == "https" else 80)
        if (not host or url.username or url.password or url.query or url.fragment or
            url.path != "/syndra-correction/v1" or "%" in host or "\\" in manifest.destination or
            any(ord(c) < 33 for c in manifest.destination)):
            raise ValueError
        ips = {ipaddress.ip_address(value) for value in manifest.approved_addresses}
        networks = [ipaddress.ip_network(value, strict=True) for value in manifest.private_networks]
        if manifest.transport == "disposable_loopback":
            if url.scheme != "http" or manifest.identity.environment != "sandbox" or not all(ip.is_loopback for ip in ips):
                raise ValueError
        elif url.scheme != "https":
            raise ValueError
        for ip in ips:
            if ip.is_link_local or ip.is_multicast or ip.is_unspecified or ip.is_reserved or getattr(ip, "ipv4_mapped", None):
                raise ValueError
            if not ip.is_global and manifest.transport != "disposable_loopback":
                if not any(ip.version == network.version and ip in network for network in networks):
                    raise ValueError
                if ip.is_loopback:
                    raise ValueError
        resolved = {ipaddress.ip_address(item[4][0]) for item in resolve_addresses(host, port)}
        if not resolved or not resolved.issubset(ips):
            raise ValueError
        return url, sorted(str(ip) for ip in resolved)[0], port
    except (ValueError, OSError):
        raise ConnectorError("connector_destination", status=400) from None


class _PinnedConnection(http.client.HTTPConnection):
    def __init__(self, host, port, address, *, tls, timeout=10):
        super().__init__(host, port, timeout=timeout)
        self.address, self.tls = address, tls

    def connect(self):
        # Connect to the already approved numeric address, never resolve twice.
        deadline = time.monotonic() + self.timeout
        self.sock = socket.create_connection((self.address, self.port), self.timeout)
        self.sock.settimeout(max(0.001, deadline - time.monotonic()))
        if self.tls:
            self.sock = ssl.create_default_context().wrap_socket(self.sock, server_hostname=self.host)
        active_socket = self.sock
        def abort():
            try:
                active_socket.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            active_socket.close()
        self._deadline_timer = threading.Timer(max(0.001, deadline - time.monotonic()), abort)
        self._deadline_timer.daemon = True
        self._deadline_timer.start()

    def finish(self):
        # HTTPConnection may call close() as soon as headers announce EOF framing.
        # Keep the deadline alive until the response body is consumed as well.
        timer = getattr(self, "_deadline_timer", None)
        if timer:
            timer.cancel()
        super().close()


class ApplicationConnector:
    def __init__(self, manifest: ConnectionManifest):
        self.manifest = manifest
        validate_destination(manifest)

    def request(self, method, suffix, payload=None, *, timeout=10):
        if not re.fullmatch(r"/(?:capabilities|purchases(?:/[A-Za-z0-9_.:-]{1,80})?|plans(?:/[a-f0-9]{32}/revoke)?|operations(?:/[a-f0-9]{32}(?:/verify)?)?)", suffix):
            raise ConnectorError("connector_destination", status=400)
        url, address, port = validate_destination(self.manifest)
        connection = _PinnedConnection(url.hostname, port, address, tls=url.scheme == "https", timeout=timeout)
        body = None if payload is None else json.dumps(payload, separators=(",", ":")).encode()
        if body is not None and len(body) > 1_500_000:
            raise ConnectorError("connector_invalid", status=400)
        try:
            connection.request(method, url.path + suffix, body=body, headers={
                "Authorization": "Bearer " + credential(self.manifest.credential_ref),
                "Content-Type": "application/json", "Accept": "application/json",
            })
            response = connection.getresponse()
            data = response.read(1_500_001)
            if len(data) > 1_500_000:
                raise ConnectorError("connector_invalid")
            if response.status in {401, 403}:
                raise ConnectorError("connector_auth", status=403)
            if response.status == 404:
                raise ConnectorError("connector_not_found", status=404)
            if response.status in {409, 412, 422}:
                raise ConnectorError("connector_conflict", status=409)
            if response.status == 429:
                retry = response.getheader("Retry-After", "1")
                raise ConnectorError("connector_rate_limited", status=429, retry_after=retry_after_seconds(retry))
            if response.status < 200 or response.status >= 300:
                raise ConnectorError()
            if response.getheader("Content-Type", "").split(";")[0] != "application/json":
                raise ConnectorError("connector_invalid")
            return json.loads(data)
        except (OSError, http.client.HTTPException):
            raise ConnectorError() from None
        except (ValueError, RecursionError):
            raise ConnectorError("connector_invalid") from None
        finally:
            connection.finish()

    def read(self, suffix, model, *, timeout=10):
        try:
            value = model.model_validate(self.request("GET", suffix, timeout=timeout))
            if getattr(value, "identity", self.manifest.identity) != self.manifest.identity:
                raise ConnectorError("connector_conflict", status=409)
            return value
        except ValidationError:
            raise ConnectorError("connector_invalid") from None

    def capabilities(self):
        return self.read("/capabilities", Capabilities)

    def approve(self, plan: TargetPlan):
        result = self.request("POST", "/plans", plan.model_dump(mode="json"))
        if result != {"plan_id": plan.plan_id, "digest": plan.digest, "status": "active"}:
            raise ConnectorError("connector_invalid")

    def apply(self, operation: ConditionalOperation):
        try:
            return Receipt.model_validate(self.request("POST", "/operations", operation.model_dump(mode="json")))
        except ValidationError:
            raise ConnectorError("connector_invalid") from None

    def lookup(self, operation_id):
        return self.read(f"/operations/{operation_id}", Receipt)

    def verify(self, operation_id):
        return self.read(f"/operations/{operation_id}/verify", AccessVerification, timeout=2)

    def revoke(self, plan_id):
        return self.request("POST", f"/plans/{plan_id}/revoke", {})
