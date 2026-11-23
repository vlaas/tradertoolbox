"""The WebSocket adapter of the session against a real socket on the loopback address.

Every other stream test uses the in-memory connection. This one puts the fake server behind
``websockets.serve`` and runs the session over the adapter, on the real clock."""

import asyncio

import pytest
from websockets.asyncio.server import serve

from s3t.broker.fake.market import SyntheticMarket
from s3t.broker.fake.stream import FakeDxLink
from s3t.broker.interface import EventBatch, EventType, QuoteToken, StreamItem, StreamState, Sub
from s3t.broker.tastytrade.dxlink import DxLinkSession
from s3t.broker.tastytrade.ws import ConnectionLost, open_websocket

from .conftest import ASOF

pytestmark = pytest.mark.anyio


async def test_the_session_over_a_real_websocket(market: SyntheticMarket) -> None:
    from datetime import timedelta

    from pydantic import SecretStr

    fake = FakeDxLink(market, now=lambda: ASOF, tokens=("loopback-quote-token",))
    async with serve(fake.serve, "127.0.0.1", 0) as server:
        port = server.sockets[0].getsockname()[1]
        url = f"ws://127.0.0.1:{port}/realtime"
        fake.url = url

        async def token() -> QuoteToken:
            secret = SecretStr("loopback-quote-token")
            return QuoteToken(url, secret, "api", ASOF, ASOF + timedelta(hours=24))

        items: list[StreamItem] = []
        session = DxLinkSession(token, items.append, now=lambda: ASOF)
        session.start()
        for _ in range(500):
            if session.status().phase == "connected":
                break
            await asyncio.sleep(0.01)
        await session.subscribe([Sub("SPY", EventType.QUOTE)])
        for _ in range(100):
            if any(isinstance(i, EventBatch) for i in items):
                break
            await asyncio.sleep(0.01)
        batch = next(i for i in items if isinstance(i, EventBatch))
        assert next(batch.rows()).symbol == "SPY"
        headers = {m["type"] for m in fake.received}
        assert {"SETUP", "AUTH", "CHANNEL_REQUEST", "FEED_SETUP", "FEED_SUBSCRIPTION"} <= headers

        fake.drop()  # the server closes the socket: the adapter reports it, the session ends
        for _ in range(200):
            if any(isinstance(i, StreamState) and i.phase == "disconnected" for i in items):
                break
            await asyncio.sleep(0.01)
        lost = next(i for i in items if isinstance(i, StreamState) and i.phase == "disconnected")
        assert lost.reason and "closed" in lost.reason
        await session.aclose()

    with pytest.raises(ConnectionLost, match="cannot connect"):
        await open_websocket(url)  # nothing listens there any more
