from __future__ import annotations

import hashlib
import html
import secrets
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from importlib.resources import files
from pathlib import Path
from urllib.parse import urlsplit

from agentbus.control.errors import ControlPlaneForbiddenError
from agentbus.control.authentication import validate_loopback_host


def loopback_origin(host: str, port: int) -> str:
    host = validate_loopback_host(host)
    return f"http://{'[' + host + ']' if ':' in host else host}:{port}"


def studio_assets() -> Path:
    root = Path(str(files("syndra").joinpath("studio_assets"))).resolve()
    if not (root / "index.html").is_file():
        raise RuntimeError("Studio assets are missing. Install a built Syndra distribution or run scripts/prepare_studio.py after the Studio build.")
    return root


class BrowserSessions:
    """Process-local exchanges and sessions; credentials never enter discovery."""

    def __init__(self, origin: str):
        parsed = urlsplit(origin)
        if parsed.scheme != "http" or parsed.username or parsed.password or parsed.path or parsed.query or parsed.fragment:
            raise ValueError("Studio sessions require an HTTP loopback origin.")
        if parsed.port is None or not 0 <= parsed.port <= 65535:
            raise ValueError("Studio sessions require a numeric loopback port.")
        validate_loopback_host(parsed.hostname or "")
        self.origin = origin
        self._lock = threading.Lock()
        self._tickets: dict[str, tuple[float, str, str]] = {}
        self._sessions: dict[str, float] = {}

    def _prune(self) -> None:
        now = time.monotonic()
        self._tickets = {key: value for key, value in self._tickets.items() if value[0] > now}
        self._sessions = {key: value for key, value in self._sessions.items() if value > now}

    def ticket(self, bootstrap_origin: str, run_id: str | None) -> str:
        if urlsplit(self.origin).port == 0:
            raise ValueError("Studio launch requires a bound daemon port.")
        with self._lock:
            self._prune()
            if len(self._tickets) >= 16:
                raise ControlPlaneForbiddenError("Too many pending Studio launches; wait for expiry.")
            value = secrets.token_urlsafe(32)
            destination = "/studio/"
            if run_id:
                destination += "#/runs/" + run_id
            self._tickets[self._digest(value)] = (time.monotonic() + 30, bootstrap_origin, destination)
            return value

    def redeem(self, code: str, origin: str | None) -> tuple[str, str]:
        with self._lock:
            self._prune()
            key = self._digest(code)
            ticket = self._tickets.get(key)
            if ticket is None or origin != ticket[1]:
                raise ControlPlaneForbiddenError("Studio launch expired, reused, or has the wrong origin. Launch Studio again.")
            if len(self._sessions) >= 64:
                raise ControlPlaneForbiddenError("Too many Studio sessions.")
            del self._tickets[key]
            session = secrets.token_urlsafe(32)
            self._sessions[self._digest(session)] = time.monotonic() + 7200
            return session, ticket[2]

    def authenticate(self, session: str | None, *, origin: str | None, fetch_site: str | None, mutation: bool) -> None:
        if fetch_site != "same-origin" or (mutation and origin != self.origin):
            raise ControlPlaneForbiddenError("Studio requires a same-origin browser request.")
        with self._lock:
            self._prune()
            if not session or self._digest(session) not in self._sessions:
                raise ControlPlaneForbiddenError("Studio session expired. Launch Studio again.")

    def logout(self, session: str | None) -> None:
        with self._lock:
            if session:
                self._sessions.pop(self._digest(session), None)

    @staticmethod
    def _digest(value: str) -> str:
        return hashlib.sha256(value.encode()).hexdigest()


def launch_browser_exchange(sessions: BrowserSessions, run_id: str | None = None) -> str:
    """Serve one transient bootstrap document; the launch URL contains no secret."""
    studio_assets()

    class Bootstrap(BaseHTTPRequestHandler):
        def setup(self):
            super().setup()
            self.connection.settimeout(2)

        def log_message(self, format, *args):
            pass

        def do_GET(self):
            if self.path != "/" or self.headers.get("Host") != urlsplit(origin).netloc or self.headers.get("Origin") or self.headers.get("Sec-Fetch-Dest") in {"iframe", "image", "script"}:
                self.send_error(403)
                return
            nonce = secrets.token_urlsafe(24)
            payload = (
                '<!doctype html><html lang="en"><meta charset="utf-8"><title>Open Syndra Studio</title>'
                '<meta name="referrer" content="strict-origin"><body><p>Opening your local Syndra session.</p>'
                f'<form method="post" action="{html.escape(sessions.origin)}/studio/session/exchange">'
                f'<input type="hidden" name="code" value="{html.escape(code)}">'
                '<button type="submit">Open Syndra Studio</button></form>'
                f'<script nonce="{nonce}">document.forms[0].submit()</script></body></html>'
            ).encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(payload)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Frame-Options", "DENY")
            # Cross-origin form posts need their source Origin for ticket binding.
            self.send_header("Referrer-Policy", "strict-origin")
            self.send_header("Content-Security-Policy", f"default-src 'none'; script-src 'nonce-{nonce}'; form-action {sessions.origin}; frame-ancestors 'none'")
            self.end_headers()
            self.wfile.write(payload)
            delivered.set()

    server = HTTPServer(("127.0.0.1", 0), Bootstrap)
    origin = f"http://127.0.0.1:{server.server_port}"
    try:
        code = sessions.ticket(origin, run_id)
    except Exception:
        server.server_close()
        raise
    delivered = threading.Event()
    server.timeout = 1

    def serve() -> None:
        deadline = time.monotonic() + 30
        try:
            while not delivered.is_set() and time.monotonic() < deadline:
                server.handle_request()
        finally:
            server.server_close()

    threading.Thread(target=serve, name="syndra-studio-bootstrap", daemon=True).start()
    return origin + "/"
