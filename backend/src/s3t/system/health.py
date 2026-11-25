"""The state of everything (SEC-08): the broker connection, the stream, the use of the
subscriptions, the stores and the reference data in use.

The records are the ones the parts give out about themselves, so there is no second definition of
them. Only the use of the subscriptions is written anew, with the names of the classes in place of
their numbers. Every text passes the redactor once more on its way out.
"""

from collections.abc import Sequence
from dataclasses import fields, replace
from datetime import date, datetime
from typing import TYPE_CHECKING, Any, Literal

from pydantic import BaseModel

from s3t import __version__
from s3t.broker.interface import DataSource, RestStatus, StreamStatus
from s3t.market.state import MarketStatus
from s3t.refdata.calendar import CalendarStatus
from s3t.refdata.rates import RatesStatus
from s3t.secrets import redact
from s3t.store.daily import CopiesStatus
from s3t.store.state import StoreStatus
from s3t.subs.feed import ClassUse, ConsumerUse, FeedStatus, Priority

if TYPE_CHECKING:
    from s3t.system.backend import Backend

type ClassName = Literal["recorder", "positions", "views", "background"]

CLASS_NAMES: dict[Priority, ClassName] = {
    Priority.RECORDER: "recorder",
    Priority.POSITIONS: "positions",
    Priority.VIEWS: "views",
    Priority.BACKGROUND: "background",
}


class SourceReport(BaseModel):
    kind: Literal["live", "fake", "replay"]
    mark: str | None
    """The permanent mark for every screen (GEN-11); none on the live broker."""
    recorded_on: date | None


class ClassReport(BaseModel):
    demand_class: ClassName
    wanted: int
    granted: int
    counted: int


class ConsumerReport(BaseModel):
    name: str
    demand_class: ClassName
    wanted: int
    granted: int


class SubscriptionsReport(BaseModel):
    """The use of the subscriptions (SUB-07), and the faults of the feed."""

    resubscribing: bool
    granted: int
    budget: int
    candles: int
    candle_budget: int
    sent: int
    pending: int
    changes_last_minute: int
    change_budget: int
    rate_errors: int
    lost: int
    classes: list[ClassReport]
    consumers: list[ConsumerReport]
    failures: int
    """Unexpected errors of the manager. Not zero means a fault."""
    cap_refusals: int
    """Calls that the session refused for its hard cap. Not zero means a fault."""


class Health(BaseModel):
    version: str
    started_at: datetime
    data_time: datetime
    """The instant the broker's data belongs to."""
    source: SourceReport
    account: str | None
    """The nickname of the account (GEN-03)."""
    rest: RestStatus
    stream: StreamStatus
    subscriptions: SubscriptionsReport
    market: MarketStatus
    store: StoreStatus
    copies: CopiesStatus
    calendar: CalendarStatus
    rates: RatesStatus


def redacted[T](record: T) -> T:
    """The record with every text of its own redacted."""
    texts: dict[str, Any] = {}
    for field in fields(record):  # type: ignore[arg-type]
        value = getattr(record, field.name)
        if isinstance(value, str):
            texts[field.name] = redact(value)
    return replace(record, **texts)  # type: ignore[type-var]


def stream_report(status: StreamStatus) -> StreamStatus:
    gap = status.last_gap
    return replace(redacted(status), last_gap=None if gap is None else redacted(gap))


def source_report(source: DataSource) -> SourceReport:
    return SourceReport(kind=source.kind, mark=source.mark, recorded_on=source.recorded_on)


def subscriptions_report(
    status: FeedStatus, *, failures: int, cap_refusals: int
) -> SubscriptionsReport:
    return SubscriptionsReport(
        resubscribing=status.resubscribing,
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
        classes=_classes(status.classes),
        consumers=_consumers(status.consumers),
        failures=failures,
        cap_refusals=cap_refusals,
    )


def _classes(classes: Sequence[ClassUse]) -> list[ClassReport]:
    return [
        ClassReport(
            demand_class=CLASS_NAMES[use.priority],
            wanted=use.wanted,
            granted=use.granted,
            counted=use.counted,
        )
        for use in classes
    ]


def _consumers(consumers: Sequence[ConsumerUse]) -> list[ConsumerReport]:
    return [
        ConsumerReport(
            name=use.name,
            demand_class=CLASS_NAMES[use.priority],
            wanted=use.wanted,
            granted=use.granted,
        )
        for use in consumers
    ]


async def health(backend: "Backend") -> Health:
    feed = backend.feed.status()
    data_time = backend.broker.data_time()
    return Health(
        version=__version__,
        started_at=backend.started_at,
        data_time=data_time,
        source=source_report(backend.broker.source()),
        account=backend.config.broker.account_nickname,
        rest=redacted(backend.broker.rest_status()),
        stream=stream_report(feed.stream),
        subscriptions=subscriptions_report(
            feed, failures=backend.feed.failures, cap_refusals=backend.feed.cap_refusals
        ),
        market=backend.market.status(),
        store=await backend.store.status(),
        copies=redacted(backend.copies.status()),
        calendar=backend.calendar.status(),
        rates=redacted(backend.rates.status(data_time)),
    )
