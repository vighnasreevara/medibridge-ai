"""Request boundary (pure ASGI, so long-lived SSE streams are not buffered):
request ids, Host allow-list, CSRF defence, body-size limits and security headers.

CSRF: every state-changing request must carry ``X-MediBridge-Request: 1``. Browsers cannot add a custom
header cross-origin without a CORS preflight, and this API grants no CORS. Origin, when present, must be
allow-listed. Session cookies are additionally SameSite=Strict.
"""
from __future__ import annotations

import json
import secrets
from urllib.parse import urlsplit

from starlette.types import ASGIApp, Message, Receive, Scope, Send

from ..config import get_settings

CSRF_HEADER = b"x-medibridge-request"
UNSAFE = {"POST", "PUT", "PATCH", "DELETE"}
DEFAULT_MAX_BODY = 1 * 1024 * 1024
UPLOAD_MAX_BODY = 50 * 1024 * 1024
UPLOAD_SUFFIXES = ("/intake/audio",)
UPLOAD_PREFIXES = ("/api/documents",)

CSP = ("default-src 'self'; img-src 'self' data: blob:; media-src 'self' blob:; style-src 'self' 'unsafe-inline'; "
       "script-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'; "
       "object-src 'self'")

SECURITY_HEADERS = [
    (b"content-security-policy", CSP.encode()),
    (b"x-content-type-options", b"nosniff"),
    (b"x-frame-options", b"DENY"),
    (b"referrer-policy", b"no-referrer"),
    (b"cross-origin-opener-policy", b"same-origin"),
    (b"permissions-policy", b"microphone=(self), camera=(), geolocation=()"),
]


def _allowed_hosts() -> set[str]:
    s = get_settings()
    hosts = {f"127.0.0.1:{s.medibridge_api_port}", f"localhost:{s.medibridge_api_port}", "testserver"}
    web = urlsplit(s.medibridge_web_origin)
    if web.netloc:
        hosts |= {web.netloc, web.netloc.replace("127.0.0.1", "localhost")}
    return hosts


def _allowed_origins() -> set[str]:
    s = get_settings()
    return {s.medibridge_web_origin, s.medibridge_web_origin.replace("127.0.0.1", "localhost"),
            f"http://127.0.0.1:{s.medibridge_api_port}", f"http://localhost:{s.medibridge_api_port}",
            "http://testserver"}


async def _reject(send: Send, status: int, code: str, message: str) -> None:
    body = json.dumps({"detail": {"code": code, "message": message}}).encode()
    await send({"type": "http.response.start", "status": status,
                "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode()),
                            *SECURITY_HEADERS]})
    await send({"type": "http.response.body", "body": body})


class BoundaryMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers = {k.lower(): v for k, v in scope["headers"]}
        path, method = scope["path"], scope["method"]
        request_id = secrets.token_hex(8)
        scope.setdefault("state", {})["request_id"] = request_id

        if headers.get(b"host", b"").decode("latin-1") not in _allowed_hosts():
            await _reject(send, 421, "misdirected", "Unknown host.")
            return
        if method in UNSAFE and path.startswith("/api/"):
            if headers.get(CSRF_HEADER) != b"1":
                await _reject(send, 403, "csrf", "Missing request header.")
                return
            origin = headers.get(b"origin")
            if origin and origin.decode("latin-1") not in _allowed_origins():
                await _reject(send, 403, "csrf", "Origin not allowed.")
                return
            if headers.get(b"transfer-encoding", b"").lower() == b"chunked":
                await _reject(send, 411, "length_required", "Chunked request bodies are not accepted.")
                return
            upload = path.startswith(UPLOAD_PREFIXES) or path.endswith(UPLOAD_SUFFIXES)
            limit = UPLOAD_MAX_BODY if upload else DEFAULT_MAX_BODY
            length = headers.get(b"content-length")
            if length is not None and (not length.isdigit() or int(length) > limit):
                await _reject(send, 413, "too_large", "Request body too large.")
                return

        extra = [(b"x-request-id", request_id.encode()), *SECURITY_HEADERS]
        if path.startswith("/api/"):
            extra.append((b"cache-control", b"no-store"))

        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                existing = {k.lower() for k, _ in message.get("headers", [])}
                message["headers"] = [*message.get("headers", []), *[(k, v) for k, v in extra if k not in existing]]
            await send(message)

        await self.app(scope, receive, send_with_headers)
