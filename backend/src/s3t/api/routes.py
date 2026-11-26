"""The REST routes: thin wrappers over the services (API-01).

The answer of every route is a typed record, from which the TypeScript types are generated
(API-02). An error is an :class:`ApiError`. The text of an error never repeats what was sent: what
was sent to the login is a password.

A route that serves a feature says so with :func:`needs` (GEN-04); the others belong to the process
itself.
"""

import math
from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, FastAPI, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, SecretStr
from starlette.exceptions import HTTPException

from s3t.api.channel import Connections
from s3t.api.guard import PREFIX, cookie_name, session_of
from s3t.system.backend import Backend
from s3t.system.features import Feature, FeaturesReport
from s3t.system.health import Health, health
from s3t.system.login import Held, LoginRefused, NoPassword, Session


class ApiError(BaseModel):
    """The answer of a route that refuses or fails."""

    code: str
    message: str


class Failure(Exception):
    """Ends a request with an :class:`ApiError`."""

    def __init__(
        self, status: int, code: str, message: str, headers: dict[str, str] | None = None
    ) -> None:
        super().__init__(message)
        self.status, self.code, self.message, self.headers = status, code, message, headers


class LoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    password: SecretStr


class SessionReport(BaseModel):
    """The session of this browser."""

    created_at: datetime
    expires_at: datetime


def backend_of(request: Request) -> Backend:
    backend = request.app.state.backend
    assert isinstance(backend, Backend)
    return backend


def connections_of(request: Request) -> Connections:
    connections = request.app.state.connections
    assert isinstance(connections, Connections)
    return connections


BackendDep = Annotated[Backend, Depends(backend_of)]
ConnectionsDep = Annotated[Connections, Depends(connections_of)]
SessionDep = Annotated[Session, Depends(session_of)]


class FeatureGate:
    """Refuses a request when the data its feature needs is absent."""

    def __init__(self, feature: Feature) -> None:
        self.feature = feature

    async def __call__(self, backend: BackendDep) -> None:
        reason = await backend.features.refusal(self.feature)
        if reason is not None:
            raise Failure(503, "unavailable", f"{self.feature.title}: {reason}")


def needs(feature: Feature) -> Any:
    """A dependency: the route serves this feature (GEN-04)."""
    return Depends(FeatureGate(feature))


def _error(description: str) -> dict[str, Any]:
    return {"model": ApiError, "description": description}


UNAUTHENTICATED: dict[int | str, dict[str, Any]] = {401: _error("There is no session.")}
BAD_ORIGIN: dict[int | str, dict[str, Any]] = {403: _error("The page is not of this backend.")}

router = APIRouter(prefix=PREFIX)

COOKIE_MAX_AGE_S = 400 * 86400
"""As long as a browser keeps a cookie. The backend decides when a session ends: its end moves with
each use, and a cookie that a browser has kept longer opens nothing."""


@router.post(
    "/login",
    operation_id="login",
    responses=BAD_ORIGIN
    | {
        401: _error("The password is wrong."),
        409: _error("No password has been set."),
        422: _error("The request cannot be read."),
        429: _error("There were too many wrong passwords. Retry-After says how long to wait."),
    },
)
async def login(body: LoginRequest, response: Response, backend: BackendDep) -> SessionReport:
    """Open a session. The answer sets its cookie."""
    try:
        opened = await backend.login.log_in(body.password.get_secret_value())
    except Held as e:
        seconds = math.ceil(e.seconds)
        headers = {"Retry-After": str(seconds)}
        raise Failure(429, "held", f"wait {seconds} s", headers) from None
    except NoPassword:
        raise Failure(409, "no_password", "no password has been set on the backend") from None
    except LoginRefused:
        raise Failure(401, "wrong_password", "the password is wrong") from None
    session = opened.session
    response.set_cookie(
        cookie_name(backend.config.server.port),
        opened.token.get_secret_value(),
        max_age=COOKIE_MAX_AGE_S,
        path=PREFIX,
        httponly=True,
        samesite="strict",
    )
    return SessionReport(created_at=session.created_at, expires_at=session.expires_at)


@router.post(
    "/logout", operation_id="logout", status_code=204, responses=UNAUTHENTICATED | BAD_ORIGIN
)
async def logout(
    response: Response, session: SessionDep, backend: BackendDep, connections: ConnectionsDep
) -> None:
    """End the session, and the WebSockets that were opened with it."""
    await backend.login.log_out(session.id)
    connections.end(session.id)
    response.delete_cookie(
        cookie_name(backend.config.server.port), path=PREFIX, httponly=True, samesite="strict"
    )


@router.get("/session", operation_id="session", responses=UNAUTHENTICATED)
async def session(session: SessionDep) -> SessionReport:
    """The session of this browser."""
    return SessionReport(created_at=session.created_at, expires_at=session.expires_at)


@router.get("/health", operation_id="health", responses=UNAUTHENTICATED)
async def get_health(backend: BackendDep) -> Health:
    """The state of everything."""
    return await health(backend)


@router.get("/features", operation_id="features", responses=UNAUTHENTICATED)
async def get_features(backend: BackendDep) -> FeaturesReport:
    """The data tiers that are there, and the features that need them."""
    return await backend.features.report()


# ---------------------------------------------------------------------- errors


def answer(status: int, code: str, message: str, headers: dict[str, str] | None = None) -> Response:
    body = ApiError(code=code, message=message).model_dump()
    return JSONResponse(body, status_code=status, headers=headers)


async def _failure(_: Request, error: Exception) -> Response:
    assert isinstance(error, Failure)
    return answer(error.status, error.code, error.message, error.headers)


async def _invalid(_: Request, error: Exception) -> Response:
    assert isinstance(error, RequestValidationError)
    # Where and why. FastAPI's own answer repeats the input.
    parts = [f"{'.'.join(str(x) for x in e['loc'])}: {e['msg']}" for e in error.errors()]
    return answer(422, "invalid", "; ".join(parts))


async def _http(_: Request, error: Exception) -> Response:
    assert isinstance(error, HTTPException)
    headers = dict(error.headers) if error.headers else None
    return answer(error.status_code, "http", str(error.detail), headers)


async def _internal(_: Request, error: Exception) -> Response:
    # The server logs the error with its trace, through the redaction.
    return answer(500, "internal", "an unexpected error; the log of the backend has it")


type Handler = Callable[[Request, Exception], Awaitable[Response]]

HANDLERS: tuple[tuple[type[Exception], Handler], ...] = (
    (Failure, _failure),
    (RequestValidationError, _invalid),
    (HTTPException, _http),
    (Exception, _internal),
)


def install(app: FastAPI) -> None:
    app.include_router(router)
    for kind, handler in HANDLERS:
        app.add_exception_handler(kind, handler)
