"""Who may reach the API (SEC-01, SEC-02; plan 3.6). Every route is enumerated from the application,
so one that is added without being named here fails the suite."""

import pytest
from fastapi.routing import APIWebSocketRoute, iter_route_contexts

from s3t.api.app import Site, create_api, create_app
from s3t.api.guard import LARGEST_BODY, PUBLIC, Guard

from .conftest import ASOF, COOKIE, ORIGIN, ApiRig, Refused

pytestmark = pytest.mark.anyio

ROUTES = {
    ("POST", "/api/login"): "public: it is how a session is opened",
    ("POST", "/api/logout"): "needs a session",
    ("GET", "/api/session"): "needs a session",
    ("GET", "/api/health"): "needs a session",
    ("GET", "/api/features"): "needs a session",
    ("WEBSOCKET", "/api/ws"): "needs a session",
}
CLOSED = sorted(route for route, why in ROUTES.items() if why == "needs a session")
CLOSED_HTTP = [route for route in CLOSED if route[0] != "WEBSOCKET"]


def routes() -> set[tuple[str, str]]:
    """Every route of the application, those of included routers too."""
    found: set[tuple[str, str]] = set()
    for context in iter_route_contexts(create_api().routes):
        websocket = isinstance(context.original_route, APIWebSocketRoute)
        for method in ["WEBSOCKET"] if websocket else sorted(context.methods or ["?"]):
            found.add((method, context.path or "?"))
    return found


def test_every_route_is_named_here() -> None:
    assert routes() == set(ROUTES)
    assert all(path.startswith("/api/") for _, path in routes())
    assert {route for route, why in ROUTES.items() if why.startswith("public")} == PUBLIC


@pytest.mark.parametrize(("method", "path"), CLOSED_HTTP)
async def test_a_request_without_a_session_is_refused(rig: ApiRig, method: str, path: str) -> None:
    async def status() -> int:
        answer = await rig.http.request(method, path)
        assert answer.headers["cache-control"] == "no-store"
        assert answer.status_code < 300 or answer.json().keys() == {"code", "message"}
        return answer.status_code

    assert await status() == 401
    for token in ("", "x", "x" * 43, "0" * 64):
        rig.http.cookies.set(COOKIE, token, path="/api")
        assert await status() == 401
    rig.http.cookies.clear()
    token = await rig.log_in()
    rig.http.cookies.clear()
    rig.http.cookies.set("session-token", token, path="/api")  # under another name
    assert await status() == 401
    rig.http.cookies.set(COOKIE, token, path="/api")
    assert await status() in (200, 204)


@pytest.mark.parametrize(("method", "path"), CLOSED_HTTP)
async def test_a_session_that_has_expired_is_refused(rig: ApiRig, method: str, path: str) -> None:
    await rig.log_in()
    rig.clock.now += 30 * 86400 - 1
    assert (await rig.http.get("/api/session")).status_code == 200
    rig.clock.now += 1
    assert (await rig.http.request(method, path)).status_code == 401


@pytest.mark.parametrize("origin", [None, "null", "http://evil.example", "http://127.0.0.1:9999",
                                    "https://127.0.0.1:8740", "http://127.0.0.1", ""])  # fmt: skip
@pytest.mark.parametrize("path", ["/api/login", "/api/logout"])
async def test_what_could_change_something_comes_from_a_page_of_this_backend(
    rig: ApiRig, origin: str | None, path: str
) -> None:
    """Also with a session: another port of the host is the same *site* for the cookie."""
    await rig.log_in()
    del rig.http.headers["origin"]
    headers = {} if origin is None else {"origin": origin}
    answer = await rig.http.post(path, headers=headers, json={"password": "x"})
    assert (answer.status_code, answer.json()["code"]) == (403, "bad_origin")
    assert (await rig.http.get("/api/session")).status_code == 200  # reading is harmless


async def test_the_host_name_is_an_origin_too(rig: ApiRig) -> None:
    headers = {"origin": "http://trader.example.internal:8740"}
    answer = await rig.http.post("/api/login", headers=headers, json={"password": "x"})
    assert answer.status_code == 401  # a wrong password, so it got that far


@pytest.mark.parametrize(
    "path",
    ["//api/health", "/API/health", "/api/../api/health", "/api//health", "/api/health/"]
    + ["/api/%68ealth", "/%61pi/health", "/api", "/api/", "/apihealth", "/api/nothing"]
    + ["/openapi.json", "/api/openapi.json", "/docs", "/redoc"],
)
async def test_no_other_way_to_write_a_path_gets_in(rig: ApiRig, path: str) -> None:
    answer = await rig.http.get(path)
    assert answer.status_code in (401, 404, 503)  # 503: the frontend is not built here
    assert "data_time" not in answer.text


async def test_a_route_that_is_added_is_closed(rig: ApiRig) -> None:
    """Without doing anything for it. And one outside the prefix is not reachable at all."""
    api = create_api(rig.backend)
    guard = Guard(api, login=rig.backend.login, origins=[ORIGIN], cookie=COOKIE)
    seen: list[str] = []

    @api.get("/api/new")
    async def new() -> None:
        seen.append("new")

    @api.get("/outside")
    async def outside() -> None:
        seen.append("outside")

    async def nothing(scope: object, receive: object, send: object) -> None:
        await send({"type": "http.response.start", "status": 404, "headers": []})  # type: ignore[operator]
        await send({"type": "http.response.body", "body": b""})  # type: ignore[operator]

    rig.http._transport.app = Site(guard, nothing)  # type: ignore[attr-defined]
    assert (await rig.http.get("/api/new")).status_code == 401
    assert (await rig.http.get("/outside")).status_code == 404
    await rig.log_in()
    assert (await rig.http.get("/api/new")).status_code == 200
    assert (await rig.http.get("/outside")).status_code == 404
    assert seen == ["new"]


async def test_a_large_body_is_refused_before_it_is_read(rig: ApiRig) -> None:
    body = b'{"password": "' + b"x" * LARGEST_BODY + b'"}'
    assert (await rig.http.post("/api/login", content=body)).status_code == 413

    async def chunks():  # type: ignore[no-untyped-def]
        yield b'{"password": "x"}'

    answer = await rig.http.post("/api/login", content=chunks())
    assert (answer.status_code, answer.json()["code"]) == (411, "length_required")


# ---------------------------------------------------------------------- the WebSocket


@pytest.mark.parametrize(
    "origin",
    [None, "null", "", "http://evil.example", "http://127.0.0.1:9999"]
    + ["https://127.0.0.1:8740", "ws://127.0.0.1:8740", "http://127.0.0.1:8740.evil.example"],
)
async def test_the_websocket_refuses_another_origin(rig: ApiRig, origin: str | None) -> None:
    await rig.log_in()
    with pytest.raises(Refused):
        await rig.socket(origin=origin)
    assert (await rig.socket()).accepted is not None
    assert (await rig.socket(origin="http://trader.example.internal:8740")).accepted is not None


@pytest.mark.parametrize("token", ["", "x", "x" * 43])
async def test_the_websocket_refuses_without_a_session(rig: ApiRig, token: str) -> None:
    with pytest.raises(Refused):
        await rig.socket(token=token)


async def test_the_websocket_refuses_a_session_that_has_ended(rig: ApiRig) -> None:
    token = await rig.log_in()
    await rig.http.post("/api/logout")
    with pytest.raises(Refused):
        await rig.socket(token=token)
    token = await rig.log_in()
    rig.clock.now += 30 * 86400
    with pytest.raises(Refused):
        await rig.socket(token=token)


@pytest.mark.parametrize("path", ["/", "/ws", "/api", "/api/", "/api/health", "/apiws"])
async def test_there_is_one_websocket(rig: ApiRig, path: str) -> None:
    await rig.log_in()
    with pytest.raises(Refused):
        await rig.socket(path=path)


@pytest.mark.parametrize("subprotocol", ["", "s3t.v2.protobuf", "graphql-ws"])
async def test_a_page_that_speaks_something_else_is_refused(rig: ApiRig, subprotocol: str) -> None:
    await rig.log_in()
    with pytest.raises(Refused):
        await rig.socket(subprotocol=subprotocol)


async def test_the_application_is_the_guarded_one(rig: ApiRig) -> None:
    """What the server runs is what these tests test."""
    assert isinstance(create_app(rig.backend), Site) and rig.clock.wall() >= ASOF
