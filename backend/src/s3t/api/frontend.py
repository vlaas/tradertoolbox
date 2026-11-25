"""The built frontend, served by the backend (SEC-07).

Everything outside ``/api/`` is a file of the built frontend, and nothing else: there is no code
behind these addresses and no data in the files. They are public, because the login screen is one
of them. The addresses of the screens are hash routes (UI-01), so every screen is the one page.

The page itself is asked for again each time. Vite puts a hash into the name of every file below
``assets/``, so those are kept for good.

The content policy lets the page load code and data from this backend only.
"""

from collections.abc import Iterable
from pathlib import Path

from starlette.exceptions import HTTPException
from starlette.responses import PlainTextResponse, Response
from starlette.staticfiles import StaticFiles
from starlette.types import Receive, Scope, Send

DEFAULT_DIR = Path(__file__).resolve().parents[4] / "frontend" / "dist"
"""The build in the repository that the backend runs from."""
NOT_BUILT = "The frontend is not built. Run `npm run build` in frontend/, or `make e2e`.\n"


def content_policy(origins: Iterable[str]) -> str:
    # 'self' does not cover a WebSocket in every browser that is in use.
    sockets = " ".join(sorted("ws" + origin.removeprefix("http") for origin in origins))
    return "; ".join(
        (
            "default-src 'self'",
            f"connect-src 'self' {sockets}",
            "img-src 'self' data:",
            "font-src 'self' data:",
            "style-src 'self' 'unsafe-inline'",
            "object-src 'none'",
            "base-uri 'none'",
            "form-action 'self'",
            "frame-ancestors 'none'",
        )
    )


class Frontend:
    def __init__(self, directory: Path | None, *, origins: Iterable[str]) -> None:
        self._dir = DEFAULT_DIR if directory is None else directory
        self._files = StaticFiles(directory=self._dir, html=True, check_dir=False)
        self._headers = {
            "content-security-policy": content_policy(origins),
            "x-content-type-options": "nosniff",
            "referrer-policy": "no-referrer",
        }

    @property
    def built(self) -> bool:
        return (self._dir / "index.html").is_file()

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "websocket":
            await receive()  # websocket.connect
            await send({"type": "websocket.close", "code": 1008})
        elif scope["type"] == "http":
            response = await self._response(scope)
            for name, value in self._headers.items():
                response.headers[name] = value
            await response(scope, receive, send)

    async def _response(self, scope: Scope) -> Response:
        if not self.built:
            return PlainTextResponse(NOT_BUILT, status_code=503)
        path = self._files.get_path(scope)
        try:
            response = await self._files.get_response(path, scope)
        except HTTPException as e:
            return PlainTextResponse(f"{e.detail}\n", status_code=e.status_code, headers=e.headers)
        kept = path.startswith("assets/") and response.status_code == 200
        response.headers["cache-control"] = (
            "public, max-age=31536000, immutable" if kept else "no-cache"
        )
        return response
