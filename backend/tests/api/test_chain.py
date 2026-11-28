"""The ``chain`` topic and the bulk route of an expiration (API-05, API-06, SUB-04, SUB-05; WP-1.2):
the way from the chain service to the message and to Arrow. What the service computes is tested in
``tests/analysis/test_chain_service.py``."""

import asyncio
import math

import pyarrow as pa
import pytest

from s3t.api.chain_rows import ARROW_TYPE
from s3t.broker.interface import AuthError
from s3t.wire.v1.chain_pb2 import GREEKS_SOURCE_OWN, ChainRows, ChainSide
from s3t.wire.v1.channel_pb2 import (
    NOTE_KIND_UNKNOWN,
    NOTE_KIND_WINDOW,
    REFUSAL_KIND_INVALID,
    REFUSAL_KIND_UNAVAILABLE,
)

from .conftest import ApiRig, Socket

pytestmark = pytest.mark.anyio

KEY = "SPY:2026-12-18"  # a third Friday that the fake lists on ASOF
SIDE = [f.name for f in ChainSide.DESCRIPTOR.fields]


def check_lengths(rows: ChainRows) -> int:
    n = len(rows.strike)
    assert len(rows.expiration) == len(rows.iv) == n
    for side in (rows.call, rows.put):
        assert {len(getattr(side, name)) for name in SIDE} == {n}
    return n


def held(socket: Socket, number: int) -> dict[float, tuple[float, float, int]]:
    """What a page holds after every message so far: per strike the call's bid, its delta and
    the source of its Greeks."""
    found: dict[float, tuple[float, float, int]] = {}
    for message in socket.of(number):
        if message.WhichOneof("body") != "chain":
            continue
        if message.snapshot:
            found = {}
        rows = message.chain.rows
        for i in range(check_lengths(rows)):
            found[rows.strike[i]] = (rows.call.bid[i], rows.call.delta[i], rows.call.source[i])
    return found


async def test_a_snapshot_then_what_changes(rig: ApiRig) -> None:
    await rig.log_in()
    socket = await rig.socket()
    number = socket.subscribe(chain={"underlying": "SPY", "window": [{"expiration": KEY}]})
    await rig.pass_time(2)
    first = socket.of(number)[0]
    assert first.snapshot and first.WhichOneof("body") == "chain"
    chain = first.chain
    assert chain.head.underlying == "SPY" and chain.head.kind == "etf" and chain.head.spot > 0
    assert chain.head.stale_after_s == 120.0
    keys = [e.key for e in chain.expiration]
    assert KEY in keys and [e.index for e in chain.expiration] == list(range(len(keys)))
    december = chain.expiration[keys.index(KEY)]
    assert december.settlement == "pm" and december.standard and december.strikes > 100
    assert december.expected_move_method.startswith("straddle")
    assert check_lengths(chain.rows) == 40 and set(chain.rows.expiration) == {december.index}
    assert chain.notes.note == []
    await rig.pass_time(10)
    page = held(socket, number)
    assert len(page) == 40
    own = [v for v in page.values() if v[2] == GREEKS_SOURCE_OWN]
    assert len(own) >= 30 and all(0 < delta < 1 for _, delta, _ in own)
    later = socket.of(number)[1:]
    assert later and not any(m.snapshot for m in later)
    # The expirations come when they changed (all of them with the first fit), not with every row.
    assert any(m.chain.HasField("rows") and not m.chain.expiration for m in later)
    await socket.close()


async def test_a_window_too_wide_or_too_many_is_refused(rig: ApiRig) -> None:
    await rig.log_in()
    socket = await rig.socket()
    wide = socket.subscribe(
        chain={
            "underlying": "SPY",
            "window": [{"expiration": KEY, "low": 400.0, "high": 800.0}],
        }
    )
    many = socket.subscribe(chain={"underlying": "SPY", "window": [{"expiration": KEY}] * 9})
    half = socket.subscribe(
        chain={"underlying": "SPY", "window": [{"expiration": KEY, "low": 1.0}]}
    )
    empty = socket.subscribe(chain={"underlying": "", "window": []})
    await rig.pass_time(3)
    for n, text in (
        (wide, "holds 120 at most"),
        (many, "a view holds 8"),
        (half, "both ends"),
        (empty, "not a symbol"),
    ):
        (message,) = socket.of(n)
        assert message.refused.kind == REFUSAL_KIND_INVALID and text in message.refused.reason
    await socket.close()


async def test_notes_for_an_unknown_symbol_and_expiration(rig: ApiRig) -> None:
    await rig.log_in()
    socket = await rig.socket()
    nope = socket.subscribe(
        chain={"underlying": "NOPE", "window": [{"expiration": "NOPE:2026-12-18"}]}
    )
    old = socket.subscribe(
        chain={"underlying": "SPY", "window": [{"expiration": "SPY:2020-01-17"}]}
    )
    await rig.pass_time(3)
    notes = socket.of(nope)[0].chain.notes.note
    assert [(n.symbol, n.kind) for n in notes] == [("NOPE", NOTE_KIND_UNKNOWN)]
    notes = socket.of(old)[0].chain.notes.note
    assert [(n.symbol, n.kind) for n in notes] == [("SPY:2020-01-17", NOTE_KIND_WINDOW)]
    await socket.close()


async def test_a_closed_subscription_is_held_for_the_grace_period(rig: ApiRig) -> None:
    """SUB-04: the default 60 s, at the lowest priority."""
    await rig.log_in()
    socket = await rig.socket()
    number = socket.subscribe(chain={"underlying": "SPY", "window": [{"expiration": KEY}]})
    await rig.pass_time(5)
    socket.unsubscribe(number)
    await rig.pass_time(1)
    consumers = {c.name: c for c in rig.backend.feed.status().consumers}
    assert consumers["chain-held"].wanted == 4 * 80 and consumers["chain"].wanted == 2
    await rig.pass_time(61)
    names = {c.name for c in rig.backend.feed.status().consumers}
    assert not names & {"chain", "chain-held", "surface"}
    await socket.close()


async def test_the_topic_is_unavailable_without_its_data(rig: ApiRig) -> None:
    """GEN-04: without T1 the topic is refused and says why."""
    await rig.log_in()
    rig.fake.rest.expire_tokens()
    rig.fake.rest.refuse_renewal()
    with pytest.raises(AuthError):
        await rig.backend.broker.equity("SPY")
    socket = await rig.socket()
    number = socket.subscribe(chain={"underlying": "SPY", "window": [{"expiration": KEY}]})
    await rig.clock.settle()
    (message,) = socket.of(number)
    assert message.refused.kind == REFUSAL_KIND_UNAVAILABLE
    assert message.refused.reason.startswith("the broker refuses its data: ")
    answer = await rig.http.get(f"/api/chain/SPY/{KEY}")
    assert answer.status_code == 503 and answer.json()["code"] == "unavailable"
    await socket.close()


# --- the bulk route (API-06) ----------------------------------------------------------------------


async def get(rig: ApiRig, path: str):
    """A route that reads from the broker waits for its limiter, which sleeps on the clock."""
    asked = asyncio.ensure_future(rig.http.get(path))
    while not asked.done():
        await rig.pass_time(0.5)
    return asked.result()


async def test_a_whole_expiration_as_arrow(rig: ApiRig) -> None:
    await rig.log_in()
    answer = await get(rig, f"/api/chain/SPY/{KEY}")
    assert answer.status_code == 200 and answer.headers["content-type"] == ARROW_TYPE
    table = pa.ipc.open_stream(answer.content).read_all()
    meta = {k.decode(): v.decode() for k, v in (table.schema.metadata or {}).items()}
    assert meta["underlying"] == "SPY" and meta["key"] == KEY and meta["settlement"] == "pm"
    assert int(meta.get("strikes", table.num_rows)) == table.num_rows > 100
    strikes = table.column("strike").to_pylist()
    assert strikes == sorted(strikes)
    names = set(table.column_names)
    for side in ("call", "put"):
        assert {f"{side}_{n}" for n in ("symbol", "bid", "iv", "delta", "source", "stale")} <= names
    # Nothing holds this expiration: no quotes, and NaN, never null, for every missing number.
    bids = table.column("call_bid")
    assert bids.null_count == 0 and all(math.isnan(x) for x in bids.to_pylist())

    socket = await rig.socket()
    socket.subscribe(chain={"underlying": "SPY", "window": [{"expiration": KEY}]})
    await rig.pass_time(10)
    table = pa.ipc.open_stream((await get(rig, f"/api/chain/SPY/{KEY}")).content).read_all()
    sources = table.column("call_source").to_pylist()
    assert sources.count("own") >= 30
    await socket.close()


@pytest.mark.parametrize(
    ("path", "status", "code"),
    [
        ("/api/chain/NOPE/NOPE:2026-12-18", 404, "chain"),
        ("/api/chain/SPY/SPY:2020-01-17", 404, "chain"),
    ],
)
async def test_what_the_bulk_route_refuses(rig: ApiRig, path: str, status: int, code: str) -> None:
    await rig.log_in()
    answer = await get(rig, path)
    assert answer.status_code == status and answer.json()["code"] == code


async def test_the_health_route_has_the_chain(rig: ApiRig) -> None:
    await rig.log_in()
    socket = await rig.socket()
    socket.subscribe(chain={"underlying": "SPY", "window": [{"expiration": KEY}]})
    await rig.pass_time(10)
    chain = (await rig.http.get("/api/health")).json()["chain"]
    assert chain["grace_s"] == 60.0 and chain["flushes"] > 0 and chain["largest"] > 0
    assert [u["underlying"] for u in chain["underlyings"]] == ["SPY"]
    await socket.close()
