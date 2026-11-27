"""The services and routes against the replay broker, so that results are repeatable (QA-02).

The recording is made here, from the fake broker, through the application itself. The replay runs on
the virtual clock at the recorded pace."""

import asyncio
import json
import logging
from pathlib import Path
from typing import Any

import pytest
from google.protobuf import json_format

from s3t.broker.fake.broker import FakeBroker
from s3t.broker.fake.clock import VirtualClock
from s3t.broker.interface import EventType
from s3t.broker.recording import RecordingBroker
from s3t.config import Config
from s3t.wire.v1 import status_pb2 as pb
from s3t.wire.v1.channel_pb2 import NOTE_KIND_UNKNOWN

from .conftest import ASOF, ApiRig, Socket, make_config, running

pytestmark = pytest.mark.anyio

NAME = "session"
SYMBOLS = ["SPY", "SPX"]
REPLAY = f'source = "replay"\nrecording = "{NAME}"\n'

type Recorded = tuple[Config, dict[float, float]]
"""The configuration of a replay, and the bid of SPY by its receipt time."""


@pytest.fixture
async def recorded(tmp_path: Path) -> Recorded:
    config = make_config(tmp_path)
    clock = VirtualClock(ASOF)
    fake = FakeBroker(clock=clock.monotonic, sleep=clock.sleep, now=clock.wall, burst=20)
    broker = RecordingBroker(fake, config.paths.recordings_dir / NAME)
    ticker = asyncio.create_task(fake.dxlink.run(clock.sleep))
    bids: dict[float, float] = {}
    async with running(config, clock, broker=broker) as rig:
        await rig.log_in()
        socket = await rig.socket()
        number = socket.subscribe(quotes={"symbol": SYMBOLS})
        await rig.pass_time(10)
        fake.dxlink.drop()  # and a loss in the middle
        await rig.pass_time(20)
        for message in socket.of(number):
            for i, symbol in enumerate(message.quotes.rows.symbol):
                if symbol == "SPY":
                    bids[message.quotes.rows.quote_received_us[i]] = message.quotes.rows.bid[i]
    ticker.cancel()
    assert len(bids) > 20
    return make_config(tmp_path, REPLAY), bids


async def play(config: Config) -> tuple[ApiRig, Socket, dict[str, Any], dict[str, Any]]:
    """Subscribe at the start, play all of it, and take the answers of the routes at the end."""
    clock = VirtualClock(ASOF.replace(year=2027))  # the wall clock of another day
    async with running(config, clock) as rig:
        await rig.log_in()
        socket = await rig.socket()
        socket.subscribe(status={})
        socket.subscribe(quotes={"symbol": [*SYMBOLS, "AAPL"]})
        await rig.pass_time(40)
        health = (await rig.http.get("/api/health")).json()
        features = (await rig.http.get("/api/features")).json()
        return rig, socket, health, features


async def test_a_replay_shows_what_was_recorded(recorded: Recorded) -> None:
    config, bids = recorded
    rig, socket, health, features = await play(config)
    rows = [m.quotes.rows for m in socket.of(2) if m.quotes.HasField("rows")]
    seen = {
        table.quote_received_us[i]: table.bid[i]
        for table in rows
        for i, symbol in enumerate(table.symbol)
        if symbol == "SPY" and table.bid[i] == table.bid[i]
    }
    # To the last digit and microsecond. After the recorded loss the replay gives a pair its last
    # event once more, at the time of the replay.
    again = seen.items() - bids.items()
    assert len(seen) > 20 and len(again) <= 1
    for at, bid in again:
        assert bid == bids[max(t for t in bids if t < at)]
    # AAPL was never read while it was recorded.
    note = [m.quotes.notes.note for m in socket.of(2) if m.quotes.notes.note][0][0]
    assert (note.symbol, note.kind) == ("AAPL", NOTE_KIND_UNKNOWN)

    statuses = [m.status for m in socket.of(1)]
    assert {s.source.mark for s in statuses} == {"REPLAY 2026-11-20"}  # GEN-11
    phases = [s.stream.phase for s in statuses]
    assert pb.STREAM_PHASE_DISCONNECTED in phases  # the recorded loss, and the end
    assert statuses[-1].stream.reason == "end of the recording"
    assert not statuses[-1].HasField("live_since_us")
    assert statuses[-1].subscriptions.budget == 10_000

    # The time is the recording's, not the wall clock's, and so is the calendar's range.
    assert health["source"] == {
        "kind": "replay",
        "mark": "REPLAY 2026-11-20",
        "recorded_on": "2026-11-20",
    }
    assert health["data_time"].startswith("2026-11-20T15:00:")
    assert health["started_at"].startswith("2027-11-20")
    assert health["calendar"]["last"] == "2033-12-31"
    assert health["rates"]["downloads"] is False and health["rates"]["next_refresh_at"] is None
    assert health["stream"]["limit"] == 0 and health["rest"]["auth"] == "none"
    assert [f["name"] for f in features["features"] if f["available"]] == ["quotes"]


async def test_two_replays_are_equal(recorded: Recorded) -> None:
    """The messages, in their order, and the answers of the routes."""
    config, _ = recorded
    outputs: list[str] = []
    for _ in range(2):
        _, socket, health, features = await play(config)
        messages = [json_format.MessageToDict(m) for m in socket.received]
        assert len(messages) > 40
        del health["store"]["size"]  # the file holds the sessions of both
        del health["process"]  # the memory of the process, which the first run grew
        outputs.append(json.dumps([messages, health, features], sort_keys=True))
    assert outputs[0] == outputs[1]


async def test_the_rates_of_a_replay_are_those_of_its_day(recorded: Recorded) -> None:
    """REF-01: read from the files, and nothing is downloaded."""
    config, _ = recorded
    config.paths.rates_dir.mkdir(parents=True)
    for series in ("SOFR", "DGS1MO", "DGS3MO", "DGS6MO", "DGS1", "DTB3"):
        text = f"observation_date,{series}\n2026-11-18,4.00\n2026-11-19,4.10\n2026-11-20,9.99\n"
        (config.paths.rates_dir / f"{series}.csv").write_text(text)
    _, _, health, _ = await play(config)
    rates = health["rates"]
    assert rates["source"] == "curve"
    assert {knot["observed"] for knot in rates["knots"]} == {"2026-11-19"}


async def test_the_choice_is_logged(recorded: Recorded, caplog: pytest.LogCaptureFixture) -> None:
    """GEN-11: "logged at start"."""
    with caplog.at_level(logging.INFO, "s3t"):
        async with running(recorded[0], VirtualClock(ASOF)):
            pass
    assert "the broker is replay: REPLAY 2026-11-20" in caplog.text


async def test_the_market_state_of_a_replay(recorded: Recorded) -> None:
    async with running(recorded[0], VirtualClock(ASOF)) as rig:
        await rig.log_in()
        socket = await rig.socket()
        socket.subscribe(quotes={"symbol": ["SPY"]})
        await rig.pass_time(5)
        latest = rig.backend.market.latest(EventType.QUOTE, "SPY")
        assert latest is not None and latest.received_at <= rig.backend.broker.data_time()
        assert rig.backend.market.is_live(latest.received_at)
