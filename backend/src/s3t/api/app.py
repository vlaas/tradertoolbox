"""The web application: the API behind its guard, and the built frontend.

:func:`create_api` makes the FastAPI application with the routes and the WebSocket. The OpenAPI
schema is made from it offline by ``tools/generate.py`` (API-02, QA-05), and is not served: nothing
reads it at run time (decided 2026-11-27, WP-0.7).

:func:`create_app` is what the server runs. What is under ``/api/`` reaches the API only through the
guard, and everything else is a file of the frontend. So a route that is registered elsewhere is not
reachable at all, and one that is registered here is closed (SEC-02).
"""

from fastapi import FastAPI
from starlette.types import ASGIApp, Receive, Scope, Send

from s3t import __version__
from s3t.api import routes, ws
from s3t.api.channel import Connections
from s3t.api.frontend import Frontend
from s3t.api.guard import PREFIX, Guard, cookie_name
from s3t.system.backend import Backend


def create_api(backend: Backend | None = None) -> FastAPI:
    """Without a backend it serves no request and gives its schema."""
    api = FastAPI(
        title="Self-3-Trader",
        version=__version__,
        openapi_url=None,
        docs_url=None,
        redoc_url=None,
    )
    api.state.backend = backend
    api.state.connections = Connections()
    routes.install(api)
    ws.install(api)
    return api


class Site:
    """The API under its prefix, and the frontend everywhere else."""

    def __init__(self, api: ASGIApp, frontend: ASGIApp) -> None:
        self._api, self._frontend = api, frontend

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        path: str = scope.get("path", "")
        inside = path == PREFIX or path.startswith(f"{PREFIX}/")
        await (self._api if inside else self._frontend)(scope, receive, send)


def create_app(backend: Backend) -> ASGIApp:
    server = backend.config.server
    guarded = Guard(
        create_api(backend),
        login=backend.login,
        origins=server.origins,
        cookie=cookie_name(server.port),
    )
    return Site(guarded, Frontend(backend.config.paths.frontend_dir, origins=server.origins))
