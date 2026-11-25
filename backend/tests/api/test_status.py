"""The ``status`` topic: the stream, the use of the subscriptions, the REST state and the mark of
the data (BRK-06, BRK-09, SUB-07, GEN-03, GEN-11; API-05)."""

from dataclasses import fields, replace
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any, get_args

import pytest

from s3t.api import status as wire
from s3t.api.topics import REFRESH_S
from s3t.broker.fake.clock import VirtualClock
from s3t.broker.interface import (
    AuthState,
    DataSource,
    EventType,
    Gap,
    RestStatus,
    StreamPhase,
    StreamStatus,
    Sub,
)
from s3t.secrets import REDACTOR
from s3t.subs.feed import ClassUse, ConsumerUse, FeedStatus, Priority
from s3t.wire.v1 import status_pb2 as pb
from s3t.wire.v1.channel_pb2 import ServerMessage

from .conftest import ASOF, ApiRig, Socket, make_config, running

pytestmark = pytest.mark.anyio

AT = datetime(2026, 11, 20, 15, 0, tzinfo=UTC)
US = AT.timestamp() * 1e6
GAP = Gap(9, 8, AT, AT + timedelta(seconds=3), "lost")
STREAM = StreamStatus(
    phase="stalled", since=AT, reason="silent", connected_at=AT + timedelta(seconds=1),
    last_data_at=AT + timedelta(seconds=2), reconnects=3, stalled=True, stalls=4, last_gap=GAP,
    subscribed=5,
    candles=6, limit=7, candle_limit=8, aggregation_period=0.5, parse_errors=9, sink_errors=10,
    ignored_events=11,
)  # fmt: skip
FEED = FeedStatus(
    stream=STREAM, resubscribing=True, granted=1, budget=2, candles=3, candle_budget=4, sent=5,
    pending=6, changes_last_minute=7, change_budget=8, rate_errors=9, lost=10,
    classes=(ClassUse(Priority.VIEWS, 11, 12, 13),),
    consumers=(ConsumerUse("a view", Priority.VIEWS, 14, 15),),
)  # fmt: skip
REST = RestStatus(
    auth="due", token_expires_at=AT, renews_from=AT - timedelta(seconds=60), last_renewal_at=AT,
    renewals=2, refusal="why", rate=2.5, burst=4, requests_last_minute=5,
    held_until=AT + timedelta(seconds=9), rate_limited=6,
)  # fmt: skip


def statuses(socket: Socket, number: int = 1) -> list[pb.Status]:
    return [m.status for m in socket.of(number)]


def names(message: Any) -> set[str]:
    return {f.name for f in message.DESCRIPTOR.fields}


# ---------------------------------------------------------------------- the message


def test_every_field_of_the_records_reaches_the_message() -> None:
    """A field that is added to a record is added here, or left out by name."""
    stream = {f.name for f in fields(StreamStatus)}
    left_out = {"subscribed", "candles", "limit", "candle_limit"}  # the manager's figures say it
    left_out |= {"parse_errors", "sink_errors", "ignored_events"}  # faults: the health route
    on_wire = {name.removesuffix("_us") for name in names(pb.StreamState)}
    assert stream - left_out == on_wire - {"resubscribing"}

    feed = {f.name for f in fields(FeedStatus)}
    assert feed - {"stream", "resubscribing", "consumers"} == names(pb.Subscriptions)
    use = {"demand_class" if f.name == "priority" else f.name for f in fields(ClassUse)}
    assert use == names(pb.ClassUse)

    rest = {f.name for f in fields(RestStatus)}
    assert rest == {name.removesuffix("_us") for name in names(pb.RestState)}


def test_the_values_of_the_records() -> None:
    stream = wire.stream(STREAM, resubscribing=True)
    assert stream.phase == pb.STREAM_PHASE_STALLED and stream.reason == "silent"
    assert (stream.since_us, stream.connected_at_us, stream.last_data_at_us) == (
        US,
        US + 1e6,
        US + 2e6,
    )
    assert (stream.reconnects, stream.stalls) == (3, 4)
    assert stream.resubscribing and stream.stalled
    gap = stream.last_gap
    assert (gap.since_us, gap.until_us, gap.reason) == (US, US + 3e6, "lost")
    assert stream.aggregation_period == 0.5

    use = wire.subscriptions(FEED)
    for name in names(pb.Subscriptions) - {"classes"}:
        assert getattr(use, name) == getattr(FEED, name), name
    (views,) = use.classes
    assert views.demand_class == pb.DEMAND_CLASS_VIEWS
    assert (views.wanted, views.granted, views.counted) == (11, 12, 13)

    rest = wire.rest(REST)
    assert rest.auth == pb.AUTH_STATE_DUE and rest.refusal == "why"
    assert (rest.token_expires_at_us, rest.renews_from_us) == (US, US - 60e6)
    assert (rest.last_renewal_at_us, rest.held_until_us) == (US, US + 9e6)
    assert (rest.renewals, rest.rate, rest.burst) == (2, 2.5, 4)
    assert (rest.requests_last_minute, rest.rate_limited) == (5, 6)


def test_what_is_missing_is_unset_and_not_zero() -> None:
    empty = replace(
        STREAM, since=None, reason=None, connected_at=None, last_data_at=None, last_gap=None,
        aggregation_period=None,
    )  # fmt: skip
    stream = wire.stream(empty, resubscribing=False)
    for name in (
        "since_us",
        "connected_at_us",
        "last_data_at_us",
        "last_gap",
        "aggregation_period",
    ):
        assert not stream.HasField(name), name
    none = replace(REST, token_expires_at=None, renews_from=None, last_renewal_at=None,
                   held_until=None, refusal=None)  # fmt: skip
    rest = wire.rest(none)
    for name in ("token_expires_at_us", "renews_from_us", "last_renewal_at_us", "held_until_us"):
        assert not rest.HasField(name), name
    assert rest.refusal == ""


@pytest.mark.parametrize(
    ("mapping", "known", "enum"),
    [
        (wire.PHASES, get_args(StreamPhase.__value__), pb.StreamPhase),
        (wire.AUTH, get_args(AuthState.__value__), pb.AuthState),
        (wire.CLASSES, tuple(Priority), pb.DemandClass),
        (wire.SOURCES, ("live", "fake", "replay"), pb.SourceKind),
    ],
)
def test_every_word_has_its_value_and_no_two_share_one(
    mapping: Any, known: tuple[Any, ...], enum: Any
) -> None:
    assert set(mapping) == set(known) and len(set(mapping.values())) == len(known)
    assert set(mapping.values()) == set(enum.values()) - {0}


def test_the_mark_of_the_data() -> None:
    """GEN-11."""
    assert wire.source(DataSource("live")) == pb.Source(kind=pb.SOURCE_KIND_LIVE)
    fake = wire.source(DataSource("fake"))
    assert (fake.kind, fake.mark) == (pb.SOURCE_KIND_FAKE, "SYNTHETIC DATA")
    replay = wire.source(DataSource("replay", date(2026, 11, 20)))
    assert (replay.kind, replay.mark) == (pb.SOURCE_KIND_REPLAY, "REPLAY 2026-11-20")
    assert replay.recorded_on == "2026-11-20"


def test_every_text_is_redacted_once_more() -> None:
    secret = "canary-status-text-77e1"
    REDACTOR.register(secret)
    stream = wire.stream(
        replace(STREAM, reason=f"lost {secret}", last_gap=replace(GAP, reason=secret)),
        resubscribing=False,
    )
    rest = wire.rest(replace(REST, refusal=f"grant {secret}"))
    for message in (stream, rest):
        assert secret.encode() not in message.SerializeToString()
    assert "***" in stream.reason and "***" in rest.refusal and "***" in stream.last_gap.reason


def test_what_trembles_is_not_a_change() -> None:
    """The broker client computes an instant from two clocks each time it is asked."""

    def status(**change: Any) -> pb.Status:
        return pb.Status(rest=wire.rest(replace(REST, **change)), data_time_us=1.0)

    us = timedelta(microseconds=1)
    assert wire.same(status(), status(token_expires_at=AT + 900 * us))
    assert wire.same(status(), status(requests_last_minute=99))
    assert not wire.same(status(), status(token_expires_at=AT + timedelta(minutes=15)))
    assert not wire.same(status(), status(held_until=None))
    assert not wire.same(status(), status(auth="renewing"))


# ---------------------------------------------------------------------- the topic


async def test_the_snapshot(rig: ApiRig) -> None:
    await rig.log_in()
    socket = await rig.socket()
    number = socket.subscribe(status={})
    await rig.clock.settle()
    (message,) = socket.of(number)
    status = message.status
    assert message.snapshot and status.source.mark == "SYNTHETIC DATA" and status.account == "main"
    assert status.data_time_us == rig.clock.wall().timestamp() * 1e6
    assert status.stream.phase == pb.STREAM_PHASE_CONNECTED
    assert status.stream.aggregation_period == 0.5
    assert (status.subscriptions.budget, status.subscriptions.granted) == (10_000, 0)
    assert status.rest.auth == pb.AUTH_STATE_VALID and status.rest.rate == 2.0
    assert status.HasField("live_since_us")


async def test_the_use_per_class_follows_the_demand(rig: ApiRig) -> None:
    """SUB-07."""
    await rig.log_in()
    socket = await rig.socket()
    socket.subscribe(status={})
    socket.subscribe(quotes={"symbol": ["SPY", "SPX"]})
    await rig.pass_time(1.5)
    use = statuses(socket)[-1].subscriptions
    assert (use.granted, use.sent, use.pending) == (6, 6, 0)
    by_class = {c.demand_class: (c.wanted, c.granted, c.counted) for c in use.classes}
    assert by_class[pb.DEMAND_CLASS_VIEWS] == (6, 6, 6)
    socket.unsubscribe(2)
    await rig.pass_time(1.5)
    assert statuses(socket)[-1].subscriptions.granted == 0


async def test_a_loss_is_published_and_the_way_back(rig: ApiRig) -> None:
    """BRK-06: disconnected, resubscribing, the gap, connected."""
    # Enough to be sent that the filling of a new session takes seconds.
    demand = rig.backend.feed.consumer("test", Priority.VIEWS)
    demand.set([Sub(f"NO{n}", EventType.QUOTE) for n in range(900)])
    await rig.log_in()
    socket = await rig.socket()
    socket.subscribe(status={})
    socket.subscribe(quotes={"symbol": ["SPY"]})
    await rig.pass_time(10)
    before = len(statuses(socket))
    rig.fake.dxlink.refuse_connects = 1
    rig.fake.dxlink.drop()
    await rig.clock.settle()
    lost = statuses(socket)[before]  # at once, not at the next look
    assert lost.stream.phase == pb.STREAM_PHASE_DISCONNECTED and lost.stream.reason
    assert not lost.HasField("live_since_us")  # nothing is live (UI-05)
    await rig.pass_time(20)
    after = statuses(socket)[before:]
    seen = [(s.stream.phase, s.stream.resubscribing) for s in after]
    assert (pb.STREAM_PHASE_CONNECTED, True) in seen
    last = after[-1]
    assert last.stream.phase == pb.STREAM_PHASE_CONNECTED and not last.stream.resubscribing
    assert last.stream.reconnects == 1 and last.stream.last_gap.reason
    assert last.live_since_us == last.stream.connected_at_us > lost.stream.since_us


async def test_a_stall_is_published(rig: ApiRig) -> None:
    """BRK-06. The phase lasts no time, so a mark says it until data comes again: also while the
    next session is open and as silent as the one before."""
    await rig.log_in()
    socket = await rig.socket()
    socket.subscribe(status={})
    socket.subscribe(quotes={"symbol": ["SPY"]})
    await rig.pass_time(2)
    assert not statuses(socket)[-1].stream.stalled
    rig.fake.dxlink.silent = True
    await rig.pass_time(75)  # 60 s without data, looked for every 5 s, and the way back
    marked = [s.stream for s in statuses(socket) if s.stream.stalled]
    assert {pb.STREAM_PHASE_DISCONNECTED, pb.STREAM_PHASE_CONNECTED} <= {s.phase for s in marked}
    assert "no data for" in marked[0].reason and marked[0].stalls == 1
    rig.fake.dxlink.silent = False
    await rig.pass_time(2)
    stream = statuses(socket)[-1].stream
    assert not stream.stalled and stream.phase == pb.STREAM_PHASE_CONNECTED


async def test_a_refused_grant_and_a_hold_are_published(rig: ApiRig) -> None:
    """BRK-09."""
    await rig.log_in()
    socket = await rig.socket()
    socket.subscribe(status={})
    await rig.clock.settle()
    rig.fake.rest.throttle(1, retry_after=20)
    socket.subscribe(quotes={"symbol": ["SPY"]})
    await rig.pass_time(1.5)
    rest = statuses(socket)[-1].rest
    assert rest.rate_limited == 1 and rest.held_until_us > rig.clock.wall().timestamp() * 1e6
    await rig.pass_time(25)
    assert not statuses(socket)[-1].rest.HasField("held_until_us")
    rig.fake.rest.expire_tokens()
    rig.fake.rest.refuse_renewal()
    socket.subscribe(quotes={"symbol": ["AAPL"]})
    await rig.pass_time(2)
    rest = statuses(socket)[-1].rest
    assert rest.auth == pb.AUTH_STATE_REFUSED and rest.refusal


async def test_nothing_is_sent_while_nothing_changes(tmp_path: Path) -> None:
    """On a Saturday, without a demand. The token is renewed by nobody."""
    clock = VirtualClock(ASOF + timedelta(days=1))
    async with running(make_config(tmp_path), clock) as rig:
        await rig.up()
        await rig.log_in()
        socket = await rig.socket()
        socket.subscribe(status={})
        await rig.pass_time(59)
        assert len(statuses(socket)) == 1


async def test_the_running_figures_come_every_few_seconds(rig: ApiRig) -> None:
    """The time of the last data moves with every batch. That alone is no reason for a message."""
    demand = rig.backend.feed.consumer("test", Priority.VIEWS)
    demand.set([Sub("SPY", EventType.QUOTE)])
    await rig.pass_time(3)
    await rig.log_in()
    socket = await rig.socket()
    socket.subscribe(status={})
    await rig.pass_time(20.5)
    times = [m.status.data_time_us / 1e6 for m in socket.of(1)]
    assert len(times) >= 4
    assert all(b - a >= REFRESH_S for a, b in zip(times, times[1:], strict=False))
    assert all(isinstance(m, ServerMessage) and m.snapshot for m in socket.of(1))
