"""The feed interface (SUB-01; architecture note, section 1, "Feed interface").

Everything outside ``s3t.subs`` and ``s3t.broker`` reaches the stream through a
:class:`Feed`. Decided 2026-11-23 (owner, WP-0.4 plan review):

- **Demand is declarative.** A *consumer* has a name and one priority class. It states the
  full set of pairs it wants, the most important first, and states it again when it
  changes. There is no call-by-call history. A module that needs two classes registers two
  consumers.
- **Reading is separate from asking.** A *reader* gets every batch of the event types it
  names, whoever asked for the pairs. The market state is the one reader of everything;
  the views ask and then read the market state.
- **The stream's items pass through unchanged,** with the stream's own numbers, so
  ``Gap.last_seq`` means the same thing at the session, in a recording and at a reader. The
  feed's own items carry a time and no number.
- **The connection state is in the stream** (BRK-06): the session's :class:`StreamState`
  and :class:`Gap`, and the manager's :class:`FillState`. "Resubscribing" lasts from the
  opening of a session until everything granted was sent.
- **A consumer is told of a reduction** (SUB-03) by a :class:`Reduction` item, which every
  reader gets, and by :meth:`Feed.status`. ``Demand.set`` returns nothing: the grant is
  computed a moment later, after quick changes have been gathered.
- **One queueing rule.** Each reader has a bounded queue. A full queue loses the newest
  batch, counts it and tells the reader with a :class:`Lost` item. States, gaps, notices
  and the feed's own items are never lost.

The interface holds plain data and no callback, so a socket can carry it after the split.
REST stays outside it.
"""

from collections.abc import AsyncIterator, Collection, Sequence
from dataclasses import dataclass
from datetime import datetime
from enum import IntEnum
from typing import Literal, Protocol

from s3t.broker.interface import EventType, StreamItem, StreamStatus, Sub


class Priority(IntEnum):
    """The priority classes of demand (SUB-03), the highest first. When the budget is short
    the lowest class is reduced first."""

    RECORDER = 0
    POSITIONS = 1
    """Open positions and alerts."""
    VIEWS = 2
    """Visible views."""
    BACKGROUND = 3
    """Background work: surface snapshots, scanners."""


@dataclass(frozen=True, slots=True)
class FillState:
    """The manager began or finished sending the granted set to a new session (BRK-06)."""

    at: datetime
    """UTC, the time of the data."""
    phase: Literal["resubscribing", "resubscribed"]
    granted: int
    pending: int
    """Changes not sent yet."""


@dataclass(frozen=True, slots=True)
class Reduction:
    """A consumer gets less than it asked for, or gets all of it again (SUB-03)."""

    at: datetime
    consumer: str
    priority: Priority
    wanted: int
    granted: int
    cut: tuple[Sub, ...]
    """The pairs not granted, in the consumer's order. Empty: the reduction is over."""


@dataclass(frozen=True, slots=True)
class Lost:
    """This reader's queue was full: so many batches did not reach it."""

    at: datetime
    batches: int


type FeedItem = StreamItem | FillState | Reduction | Lost


class Demand(Protocol):
    """A consumer's handle on its demand."""

    @property
    def name(self) -> str: ...

    @property
    def priority(self) -> Priority: ...

    def set(self, subs: Sequence[Sub]) -> None:
        """State the full wanted set, the most important pair first. It replaces what was
        stated before. Returns at once."""
        ...

    def close(self) -> None:
        """Want nothing any more. The name is free again."""
        ...


class Reader(Protocol):
    """The items of the feed, in order: ``async for item in reader``."""

    def __aiter__(self) -> AsyncIterator[FeedItem]: ...

    async def __anext__(self) -> FeedItem: ...

    def close(self) -> None: ...


@dataclass(frozen=True)
class ClassUse:
    """The use of one priority class (SUB-07)."""

    priority: Priority
    wanted: int
    """Different pairs the consumers of the class ask for."""
    granted: int
    """How many of them are granted, to this class or to a higher one."""
    counted: int
    """Pairs that count against the budget for this class: the ones no higher class asked
    for. The counts of all classes add up to the total."""


@dataclass(frozen=True)
class ConsumerUse:
    name: str
    priority: Priority
    wanted: int
    granted: int


@dataclass(frozen=True)
class FeedStatus:
    """The state of the feed at this moment (BRK-06, SUB-07): what WP-0.7 publishes."""

    stream: StreamStatus
    resubscribing: bool
    granted: int
    budget: int
    """Subscriptions the manager may grant: the limit less the reserve."""
    candles: int
    candle_budget: int
    sent: int
    """Pairs the current connection was sent and still holds."""
    pending: int
    """Changes not sent yet."""
    changes_last_minute: int
    change_budget: int
    """Changes the manager allows itself in any 60 s."""
    rate_errors: int
    lost: int
    """Batches lost by full reader queues, over all readers."""
    classes: tuple[ClassUse, ...]
    consumers: tuple[ConsumerUse, ...]


class Feed(Protocol):
    def start(self) -> None:
        """Begin connecting. Returns at once."""
        ...

    def consumer(self, name: str, priority: Priority) -> Demand:
        """Register a consumer. Inside a class the one registered first keeps its pairs
        when the budget is short."""
        ...

    def reader(
        self,
        name: str,
        *,
        events: Collection[EventType] | None = None,
        maxsize: int = 2000,
    ) -> Reader:
        """A reader of the feed's items from now on. ``events`` limits the batches to these
        event types; every other kind of item always comes. ``maxsize`` is the number of
        batches the queue holds."""
        ...

    async def settled(self) -> None:
        """Wait until every change asked so far has been sent on an open session."""
        ...

    def status(self) -> FeedStatus: ...

    async def aclose(self) -> None: ...
