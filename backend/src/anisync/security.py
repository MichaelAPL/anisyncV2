"""HTTP-level protections: CSRF (Origin check) and request IDs."""

import uuid
from http.cookies import CookieError, SimpleCookie
from urllib.parse import urlsplit

from starlette.datastructures import Headers, MutableHeaders
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from anisync import sessions

SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS", "TRACE"})


def origin_of(url: str | None) -> str | None:
    """'https://Example.com/a?b' -> 'https://example.com'; 'null' stays 'null'; anything unparsable -> None."""
    if not url:
        return None
    if url == "null":
        return "null"
    parts = urlsplit(url)
    return f"{parts.scheme}://{parts.netloc}".lower() if parts.scheme and parts.netloc else None


def _has_session_cookie(headers: Headers) -> bool:
    try:
        return sessions.COOKIE in SimpleCookie(headers.get("cookie", ""))
    except CookieError:
        return False


class CsrfMiddleware:
    """Reject state-changing requests that a browser sent from another site.

    Browsers attach `Origin` (or at least `Referer`) to every cross-site POST/PUT/PATCH/DELETE, so a request whose
    origin isn't ours is refused. A request with no origin information at all can't come from a cross-site page, but
    if it carries a session cookie we refuse it too (a browser always sends the header; a missing one is suspicious).
    The session cookie is also SameSite=Lax, which is a second layer.
    """

    def __init__(self, app: ASGIApp, allowed_origin: str):
        self.app = app
        self.allowed = origin_of(allowed_origin)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope["method"] in SAFE_METHODS:
            return await self.app(scope, receive, send)
        headers = Headers(scope=scope)
        source = origin_of(headers.get("origin")) if "origin" in headers else origin_of(headers.get("referer"))
        blocked = (source is not None and source != self.allowed) or (source is None and _has_session_cookie(headers))
        if blocked:
            response = JSONResponse({"detail": "Cross-site request blocked"}, status_code=403)
            return await response(scope, receive, send)
        await self.app(scope, receive, send)


class RequestIdMiddleware:
    """Give every request an ID (response header `X-Request-ID`, `scope["state"]["request_id"]`) to find it in logs."""

    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        request_id = uuid.uuid4().hex[:12]  # never taken from the client: it ends up in our logs
        scope.setdefault("state", {})["request_id"] = request_id

        async def send_with_id(message: Message) -> None:
            if message["type"] == "http.response.start":
                MutableHeaders(scope=message)["X-Request-ID"] = request_id
            await send(message)

        await self.app(scope, receive, send_with_id)
