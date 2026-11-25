"""Secrets never leave the backend through the API (GEN-02, GEN-03, SEC-01, SEC-05; plan 3.6).

The broker runs on canary credentials and a canary account number, with every logger at DEBUG, the
web server's and the WebSocket library's among them. Every route and both topics are used, over real
sockets, and a drop and a refused token happen in between. Then nothing of a canary is in the log
output, in any answer, in any message, or in any file that was written.

The session's token is in one place: the ``Set-Cookie`` of the login. The password is in none.
"""

import asyncio
import io
import json
import logging
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import pytest
from google.protobuf import json_format
from pydantic import SecretStr
from websockets.asyncio.client import connect

from broker.conftest import ACCOUNT, CLIENT_SECRET, REFRESH_TOKEN
from s3t.api.app import create_app
from s3t.api.server import listen
from s3t.broker.fake.market import SyntheticMarket
from s3t.broker.fake.rest import FakeTastytrade
from s3t.broker.fake.stream import FakeDxLink
from s3t.broker.interface import DataSource
from s3t.broker.tastytrade.broker import Tastytrade
from s3t.config import load_config
from s3t.secrets import BrokerSecrets, install_redaction
from s3t.system.backend import open_backend
from s3t.system.login import set_password
from s3t.wire.v1.channel_pb2 import ClientMessage, ServerMessage
from system.conftest import CHEAP, PASSWORD

from .conftest import CONFIG
from .test_server import free_port, serving

pytestmark = pytest.mark.anyio

LOGGERS = ("", "s3t", "httpx", "httpcore", "uvicorn", "uvicorn.error", "uvicorn.access",
           "websockets", "websockets.server", "websockets.client", "asyncio")  # fmt: skip


@pytest.fixture
def captured() -> Iterator[io.StringIO]:
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(logging.Formatter("%(name)s %(levelname)s %(message)s"))
    install_redaction(handler)
    levels = {name: logging.getLogger(name).level for name in LOGGERS}
    root = logging.getLogger()
    root.addHandler(handler)
    for name in LOGGERS:
        logging.getLogger(name).setLevel(logging.DEBUG)
    try:
        yield stream
    finally:
        root.removeHandler(handler)
        for name, level in levels.items():
            logging.getLogger(name).setLevel(level)


@dataclass
class Canary:
    """The fake servers with the canary credentials and account, and the broker on them."""

    rest: FakeTastytrade
    dxlink: FakeDxLink
    broker: Tastytrade


def canary() -> Canary:
    now = lambda: datetime.now(UTC)  # noqa: E731
    market = SyntheticMarket(now(), seed=1)
    rest = FakeTastytrade(
        market, client_secret=CLIENT_SECRET, refresh_token=REFRESH_TOKEN, account=ACCOUNT
    )
    dxlink = FakeDxLink(market, now=now, rest=rest)
    broker = Tastytrade(
        BrokerSecrets(SecretStr(CLIENT_SECRET), SecretStr(REFRESH_TOKEN)),
        transport=rest.transport,
        base_url="https://api.fake.invalid",
        stream_connect=dxlink.connect,
        source=DataSource("fake"),
    )
    return Canary(rest, dxlink, broker)


async def until(condition: Callable[[], bool]) -> None:
    """Real time, and the machine may be loaded: wait for what is expected, and not for a while."""
    for _ in range(1500):
        if condition():
            return
        await asyncio.sleep(0.02)
    raise AssertionError("it did not come")


def written(directory: Path, but: Path) -> str:
    """Every file below a directory."""
    found = [f for f in directory.rglob("*") if f.is_file() and f != but]
    return b"".join(f.read_bytes() for f in found).decode("latin-1")


async def test_canaries_never_leave(tmp_path: Path, captured: io.StringIO) -> None:
    file = tmp_path / "s3t.toml"
    file.write_text(CONFIG.replace("port = 8740", f"port = {free_port()}") + 'source = "fake"\n')
    config = load_config(file)
    origin = f"http://127.0.0.1:{config.server.port}"
    answers: list[str] = []
    messages: list[str] = []
    cookies: list[str] = []
    fake = canary()

    async def use(http: httpx.AsyncClient, method: str, path: str, **options: object) -> None:
        answer = await http.request(method, path, **options)  # type: ignore[arg-type]
        cookies.extend(answer.headers.get_list("set-cookie"))
        headers = [(k, v) for k, v in answer.headers.items() if k != "set-cookie"]
        answers.append(f"{answer.status_code} {headers} {answer.text}")

    async with open_backend(config, broker=fake.broker, hasher=CHEAP) as backend:
        await set_password(backend.store, PASSWORD, hasher=CHEAP)
        async with (
            serving(create_app(backend), listen(config.server)),
            httpx.AsyncClient(base_url=origin, headers={"origin": origin}) as http,
        ):
            await use(http, "GET", "/")
            await use(http, "POST", "/api/login", json={"password": "wrong"})
            await use(http, "POST", "/api/login", json={"passwrd": PASSWORD})
            await use(http, "POST", "/api/login", json={"password": PASSWORD})
            name, token = cookies[-1].split(";")[0].split("=", 1)
            await use(http, "GET", f"/api/health?{name}={token}&password={PASSWORD}")
            for path in ("/api/session", "/api/health", "/api/features", "/api/nothing"):
                await use(http, "GET", path)

            async with connect(
                origin.replace("http", "ws") + "/api/ws",
                subprotocols=["s3t.v1.protobuf"],  # type: ignore[list-item]
                origin=origin,  # type: ignore[arg-type]
                additional_headers={"Cookie": f"{name}={token}"},
            ) as ws:

                async def read() -> None:
                    async for data in ws:
                        assert isinstance(data, bytes)
                        message = ServerMessage.FromString(data)
                        messages.append(json_format.MessageToJson(message))

                reader = asyncio.create_task(read())
                await ws.send(_client({"subscribe": {"id": 1, "status": {}}}))
                symbols = {"symbol": ["SPY", "NOSUCH"]}
                await ws.send(_client({"subscribe": {"id": 2, "quotes": symbols}}))
                await until(lambda: any('"bid": [\n' in m and "NOSUCH" not in m for m in messages))
                fake.dxlink.expire_tokens()
                fake.dxlink.drop()
                phase = "STREAM_PHASE_DISCONNECTED"
                await until(lambda: any(phase in m for m in messages))
                await until(lambda: backend.feed.status().stream.reconnects == 1)
                fake.rest.expire_tokens()
                fake.rest.refuse_renewal()
                symbols = {"symbol": ["AAPL"]}
                await ws.send(_client({"subscribe": {"id": 3, "quotes": symbols}}))
                await until(lambda: any("AUTH_STATE_REFUSED" in m for m in messages))
                await use(http, "GET", "/api/health")
                await use(http, "POST", "/api/logout")
                await asyncio.wait_for(reader, 5)

    # An error with a secret in it, through the same handler.
    logging.getLogger("uvicorn.error").error("cookie %s=%s", name, token)
    logging.getLogger("uvicorn.error").error("body %s", json.dumps({"password": PASSWORD}))
    text = captured.getvalue()
    files = written(tmp_path, but=file)
    broker_canaries = [CLIENT_SECRET, REFRESH_TOKEN, ACCOUNT]
    broker_canaries += [*fake.rest.issued_tokens, *fake.rest.quote_tokens]
    surfaces = {
        "log": text,
        "answers": "\n".join(answers),
        "messages": "\n".join(messages),
        "cookies": "\n".join(cookies).replace(token, "<the token itself>"),
        "files": files,
    }
    for where, surface in surfaces.items():
        # Of these two also a piece: the redaction knows whole values only.
        own = [PASSWORD[:14], token[:14], token[-14:]]
        leaked = [c for c in [*broker_canaries, *own] if c in surface]
        assert not leaked, f"{len(leaked)} canaries in {where}"

    # The run did what it should, and the log has seen it.
    assert len(fake.rest.quote_tokens) == 2 and len(messages) > 5
    assert [a[:3] for a in answers] == ["503", "401", "422", "200", "200", "200", "200", "200",
                                        "404", "200", "204"]  # fmt: skip
    assert '"auth":"refused"' in answers[-2]
    assert "***" in text and "accounts:<acct>:owner" in text
    assert "websockets" in text and "uvicorn.access" in text and "$argon2id$" in surfaces["files"]


def _client(doc: dict[str, Any]) -> bytes:
    return json_format.ParseDict(doc, ClientMessage()).SerializeToString()
