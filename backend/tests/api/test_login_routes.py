"""The login over HTTP (SEC-01): the cookie, the refusals, and the end of a session."""

import asyncio
import logging
from http.cookies import SimpleCookie
from pathlib import Path

import pytest

from s3t.api.ws import SESSION_LOOK_S
from s3t.broker.fake.clock import VirtualClock
from s3t.system.login import FREE_TRIES, set_password
from s3t.wire.v1.channel_pb2 import BYE_KIND_SESSION_ENDED
from system.conftest import CHEAP, PASSWORD

from .conftest import COOKIE, ApiRig, make_config, running

pytestmark = pytest.mark.anyio


async def test_the_cookie(rig: ApiRig) -> None:
    answer = await rig.http.post("/api/login", json={"password": PASSWORD})
    assert answer.status_code == 200
    headers = answer.headers.get_list("set-cookie")
    assert len(headers) == 1
    cookie = SimpleCookie(headers[0])
    assert list(cookie) == [COOKIE]
    morsel = cookie[COOKIE]
    assert (morsel["httponly"], morsel["samesite"], morsel["path"]) == (True, "strict", "/api")
    assert morsel["max-age"] == str(30 * 86400) and morsel["domain"] == ""
    assert morsel["secure"] == ""  # no TLS before the phone step
    assert answer.json() == {
        "created_at": "2026-11-20T15:00:00Z",
        "expires_at": "2026-12-20T15:00:00Z",
    }
    assert morsel.value not in answer.text
    assert (await rig.http.get("/api/session")).json() == answer.json()


async def test_a_wrong_password(rig: ApiRig) -> None:
    for _ in range(FREE_TRIES):
        answer = await rig.http.post("/api/login", json={"password": "wrong"})
        assert (answer.status_code, answer.json()["code"]) == (401, "wrong_password")
        assert "set-cookie" not in answer.headers
    answer = await rig.http.post("/api/login", json={"password": PASSWORD})
    assert (answer.status_code, answer.json()["code"]) == (429, "held")
    assert answer.headers["retry-after"] == "1"
    rig.clock.now += 1
    assert (await rig.http.post("/api/login", json={"password": PASSWORD})).status_code == 200


async def test_before_a_password_is_set(
    tmp_path: Path, clock: VirtualClock, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.WARNING, "s3t"):
        async with running(make_config(tmp_path), clock, password=None) as rig:
            answer = await rig.http.post("/api/login", json={"password": PASSWORD})
    assert (answer.status_code, answer.json()["code"]) == (409, "no_password")
    assert "no password is set" in caplog.text


@pytest.mark.parametrize(
    "body",
    [
        {"passwrd": PASSWORD},
        {"password": [PASSWORD]},
        {"password": PASSWORD, "user": "x"},
        {"password": {"a": PASSWORD}},
        [PASSWORD],
        PASSWORD,
    ],
)
async def test_an_answer_never_repeats_what_was_sent(rig: ApiRig, body: object) -> None:
    """FastAPI's own answer to what it cannot read does."""
    answer = await rig.http.post("/api/login", json=body)
    assert (answer.status_code, answer.json()["code"]) == (422, "invalid")
    assert PASSWORD not in answer.text


async def test_a_logout_ends_the_session_and_its_websockets(rig: ApiRig) -> None:
    other = await rig.log_in()
    rig.http.cookies.clear()
    await rig.log_in()
    mine, theirs = await rig.socket(), await rig.socket(token=other)
    answer = await rig.http.post("/api/logout")
    assert answer.status_code == 204 and COOKIE not in rig.http.cookies
    assert (await rig.http.get("/api/session")).status_code == 401
    await rig.clock.settle()
    assert mine.received[-1].bye.kind == BYE_KIND_SESSION_ENDED and mine.closed == 1000
    assert not theirs.received and theirs.closed is None  # another browser stays


async def test_a_new_password_ends_the_open_websockets(rig: ApiRig) -> None:
    """It is set by another process, so it is seen at the next look."""
    await rig.log_in()
    socket = await rig.socket()
    await rig.pass_time(SESSION_LOOK_S + 1)
    assert socket.closed is None
    await set_password(rig.backend.store, "another-password", hasher=CHEAP)
    await rig.pass_time(SESSION_LOOK_S + 1)
    for _ in range(100):  # the look is in the store's thread
        if socket.closed is not None:
            break
        await asyncio.sleep(0.01)
    assert socket.closed == 1000 and socket.received[-1].bye.kind == BYE_KIND_SESSION_ENDED


async def test_an_error_inside_is_an_answer_without_its_text(
    rig: ApiRig, monkeypatch: pytest.MonkeyPatch
) -> None:
    await rig.log_in()

    async def broken() -> None:
        raise RuntimeError("a detail of the inside")

    monkeypatch.setattr(rig.backend.features, "report", broken)
    answer = await rig.http.get("/api/features")
    assert (answer.status_code, answer.json()["code"]) == (500, "internal")
    assert "detail" not in answer.text


async def test_sessions_survive_a_restart(tmp_path: Path, clock: VirtualClock) -> None:
    config = make_config(tmp_path)
    async with running(config, clock) as rig:
        token = await rig.log_in()
    async with running(config, clock, password=None) as rig:
        rig.http.cookies.set(COOKIE, token, path="/api")
        assert (await rig.http.get("/api/session")).status_code == 200
