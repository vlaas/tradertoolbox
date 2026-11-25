"""The state of the backend as a wire message (BRK-06, BRK-09, SUB-07, GEN-11; ``status.proto``)."""

from collections.abc import Mapping
from datetime import datetime
from typing import Literal

from s3t.api.rows import micros
from s3t.broker.interface import (
    AuthState,
    DataSource,
    RestStatus,
    StreamPhase,
    StreamStatus,
)
from s3t.subs.feed import FeedStatus, Priority
from s3t.system.backend import Backend
from s3t.system.health import redacted, stream_report
from s3t.wire.v1 import status_pb2 as pb

SOURCES: Mapping[Literal["live", "fake", "replay"], pb.SourceKind] = {
    "live": pb.SOURCE_KIND_LIVE,
    "fake": pb.SOURCE_KIND_FAKE,
    "replay": pb.SOURCE_KIND_REPLAY,
}
PHASES: Mapping[StreamPhase, pb.StreamPhase] = {
    "idle": pb.STREAM_PHASE_IDLE,
    "connecting": pb.STREAM_PHASE_CONNECTING,
    "connected": pb.STREAM_PHASE_CONNECTED,
    "stalled": pb.STREAM_PHASE_STALLED,
    "disconnected": pb.STREAM_PHASE_DISCONNECTED,
    "closed": pb.STREAM_PHASE_CLOSED,
}
AUTH: Mapping[AuthState, pb.AuthState] = {
    "none": pb.AUTH_STATE_NONE,
    "valid": pb.AUTH_STATE_VALID,
    "due": pb.AUTH_STATE_DUE,
    "lapsed": pb.AUTH_STATE_LAPSED,
    "renewing": pb.AUTH_STATE_RENEWING,
    "refused": pb.AUTH_STATE_REFUSED,
}
CLASSES: Mapping[Priority, pb.DemandClass] = {
    Priority.RECORDER: pb.DEMAND_CLASS_RECORDER,
    Priority.POSITIONS: pb.DEMAND_CLASS_POSITIONS,
    Priority.VIEWS: pb.DEMAND_CLASS_VIEWS,
    Priority.BACKGROUND: pb.DEMAND_CLASS_BACKGROUND,
}


def _us(instant: datetime | None) -> float | None:
    """For an optional field: unset when missing (the rule of ``market.proto``)."""
    return None if instant is None else micros(instant)


def source(source: DataSource) -> pb.Source:
    recorded_on = source.recorded_on
    return pb.Source(
        kind=SOURCES[source.kind],
        mark=source.mark or "",
        recorded_on="" if recorded_on is None else recorded_on.isoformat(),
    )


def stream(status: StreamStatus, *, resubscribing: bool) -> pb.StreamState:
    status = stream_report(status)
    gap = status.last_gap
    return pb.StreamState(
        phase=PHASES[status.phase],
        since_us=_us(status.since),
        reason=status.reason or "",
        connected_at_us=_us(status.connected_at),
        last_data_at_us=_us(status.last_data_at),
        reconnects=status.reconnects,
        stalls=status.stalls,
        stalled=status.stalled,
        resubscribing=resubscribing,
        last_gap=None
        if gap is None
        else pb.Gap(since_us=micros(gap.since), until_us=micros(gap.until), reason=gap.reason),
        aggregation_period=status.aggregation_period,
    )


def subscriptions(status: FeedStatus) -> pb.Subscriptions:
    return pb.Subscriptions(
        granted=status.granted,
        budget=status.budget,
        candles=status.candles,
        candle_budget=status.candle_budget,
        sent=status.sent,
        pending=status.pending,
        changes_last_minute=status.changes_last_minute,
        change_budget=status.change_budget,
        rate_errors=status.rate_errors,
        lost=status.lost,
        classes=[
            pb.ClassUse(
                demand_class=CLASSES[use.priority],
                wanted=use.wanted,
                granted=use.granted,
                counted=use.counted,
            )
            for use in status.classes
        ],
    )


def rest(status: RestStatus) -> pb.RestState:
    status = redacted(status)
    return pb.RestState(
        auth=AUTH[status.auth],
        token_expires_at_us=_us(status.token_expires_at),
        renews_from_us=_us(status.renews_from),
        last_renewal_at_us=_us(status.last_renewal_at),
        renewals=status.renewals,
        refusal=status.refusal or "",
        rate=status.rate,
        burst=status.burst,
        requests_last_minute=status.requests_last_minute,
        held_until_us=_us(status.held_until),
        rate_limited=status.rate_limited,
    )


def status(backend: Backend) -> pb.Status:
    feed = backend.feed.status()
    return pb.Status(
        source=source(backend.broker.source()),
        data_time_us=micros(backend.broker.data_time()),
        account=backend.config.broker.account_nickname or "",
        stream=stream(feed.stream, resubscribing=feed.resubscribing),
        subscriptions=subscriptions(feed),
        rest=rest(backend.broker.rest_status()),
        live_since_us=_us(backend.market.live_since),
    )


MOVING_INSTANTS = ("token_expires_at_us", "renews_from_us", "held_until_us")
"""The broker client computes these anew from two clocks, so they tremble by microseconds."""
TREMBLE_US = 1_000_000.0


def same(one: pb.Status, other: pb.Status) -> bool:
    """Whether two statuses say the same to a person: what changes all the time is left out, which
    is the times of the data and the counts of the last minute."""
    for name in MOVING_INSTANTS:
        if one.rest.HasField(name) != other.rest.HasField(name):
            return False
        if abs(getattr(one.rest, name) - getattr(other.rest, name)) >= TREMBLE_US:
            return False
    return _steady(one) == _steady(other)


def figures(status: pb.Status) -> tuple[float, int, int]:
    """What :func:`same` leaves out and a person wants to see now and then. The time of the data is
    in every heartbeat."""
    return (
        status.stream.last_data_at_us,
        status.subscriptions.changes_last_minute,
        status.rest.requests_last_minute,
    )


def _steady(status: pb.Status) -> pb.Status:
    copy = pb.Status()
    copy.CopyFrom(status)
    copy.ClearField("data_time_us")
    copy.stream.ClearField("last_data_at_us")
    copy.subscriptions.ClearField("changes_last_minute")
    for name in ("requests_last_minute", *MOVING_INSTANTS):
        copy.rest.ClearField(name)
    return copy
