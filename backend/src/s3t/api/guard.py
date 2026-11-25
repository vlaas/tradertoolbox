"""Who may reach the API (SEC-01, SEC-02). Proposed 2026-11-25 (WP-0.7).

:class:`Guard` is around everything under ``/api/``, so a route that is added is closed without
doing anything for it. It is plain ASGI and it is default deny:

- Every request and the WebSocket need a session, except the ones in :data:`PUBLIC`.
- The WebSocket, and every request with a method that could change something, must come from a
  page of this backend: their ``Origin`` is one of the configured ones. The session cookie is
  SameSite=Strict, but a *site* takes no account of the port, and another program on the same host
  is the same site.
- A request body has a stated length, and a small one. The login takes a body from anybody.

The cookie's name has the port in it. Cookies are shared between all ports of a host, so two
backends on one host would overwrite each other's. The end of the name is one that the log
redaction knows (``s3t.secrets``).

The cookie has no ``Secure`` mark: the VPN has no TLS (architecture note, section 6). The phone step
brings both.
"""

import json
import logging
from collections.abc import Iterable

from starlette.requests import HTTPConnection
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from s3t.system.login import Login, Session

PREFIX = "/api"
PUBLIC = frozenset({("POST", f"{PREFIX}/login")})
"""What may be reached without a session."""
SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
LARGEST_BODY = 16 * 1024
NO_STORE = (b"cache-control", b"no-store")
NOSNIFF = (b"x-content-type-options", b"nosniff")

LOG = logging.getLogger(__name__)


def cookie_name(port: int) -> str:
    return f"s3t-{port}-session-token"


class Guard:
    def __init__(self, app: ASGIApp, *, login: Login, origins: Iterable[str], cookie: str) -> None:
        self._app, self._login, self._cookie = app, login, cookie
        self._origins = frozenset(origins)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] not in ("http", "websocket"):
            return
        refusal = await self._refusal(scope)
        if refusal is None:
            await self._app(scope, receive, _no_store(send))
            return
        status, code, message = refusal
        if scope["type"] == "websocket":
            # The browser is not told more than that it failed, whatever the status is. uvicorn
            # makes it a 403, and logs an error for a denial with a response of its own.
            LOG.info("refused: the WebSocket, %s", code)
            await receive()  # websocket.connect
            await send({"type": "websocket.close", "code": 1008})
        else:
            await _answer(send, status, code, message)

    async def _refusal(self, scope: Scope) -> tuple[int, str, str] | None:
        connection = HTTPConnection(scope)
        websocket = scope["type"] == "websocket"
        method = "GET" if websocket else str(scope["method"])
        if websocket or method not in SAFE_METHODS:
            if connection.headers.get("origin") not in self._origins:
                LOG.warning("refused: %r from another origin", scope["path"])
                return 403, "bad_origin", "the request does not come from a page of this backend"
        if not websocket:
            refusal = _body_refusal(connection)
            if refusal is not None:
                return refusal
            if (method, scope["path"]) in PUBLIC:
                return None
        session = await self._session(connection)
        if session is None:
            return 401, "unauthenticated", "log in first"
        scope.setdefault("state", {})["session"] = session
        return None

    async def _session(self, connection: HTTPConnection) -> Session | None:
        token = connection.cookies.get(self._cookie)
        return None if token is None else await self._login.session(token)


def session_of(connection: HTTPConnection) -> Session:
    """The session that the guard found for this request or WebSocket."""
    session = connection.scope["state"]["session"]
    assert isinstance(session, Session)
    return session


def _body_refusal(connection: HTTPConnection) -> tuple[int, str, str] | None:
    headers = connection.headers
    if "transfer-encoding" in headers:  # its length is not known before it was read
        return 411, "length_required", "a body states its length"
    length = headers.get("content-length", "0")
    if not length.isdecimal() or int(length) > LARGEST_BODY:
        return 413, "too_large", f"a body has at most {LARGEST_BODY} bytes"
    return None


def _no_store(send: Send) -> Send:
    """Nothing that the API answers is kept by a browser."""

    async def wrapped(message: Message) -> None:
        if message["type"] == "http.response.start":
            message["headers"] = [*message.get("headers", ()), NO_STORE, NOSNIFF]
        await send(message)

    return wrapped


async def _answer(send: Send, status: int, code: str, message: str) -> None:
    body = json.dumps({"code": code, "message": message}).encode()
    headers = [
        (b"content-type", b"application/json"),
        (b"content-length", str(len(body)).encode()),
        NO_STORE,
        NOSNIFF,
    ]
    await send({"type": "http.response.start", "status": status, "headers": headers})
    await send({"type": "http.response.body", "body": body})
