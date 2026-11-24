"""The in-process feed: the subscription manager and the fan-out to readers (SUB-01 to
SUB-03, SUB-07; the manager's part of BRK-02 and BRK-06).

:class:`LocalFeed` owns the broker's one stream. It is the only caller of ``subscribe`` and
``unsubscribe`` (SUB-02; a test of the repository holds the rule). What it does, as decided
on 2026-11-23 (requirements, section 3):

- **It holds the content.** It knows which pairs each consumer wants and in which order,
  computes the grant (``s3t.subs.budget``), and knows what the current connection was sent.
  The session knows none of this.
- **One rule for every change.** A worker sends the difference between what is granted and
  what the current connection was sent: removals first, then additions, the highest class
  first, per event type in chunks of 100. A changed demand, a new session after a
  reconnect and a rate error are all served by that rule. After ``connected`` nothing was
  sent, so the whole grant goes out again (BRK-02).
- **It paces** (``s3t.broker.limits.ChangePacer``): 150 changes a second and at most the
  change budget in any 60 s. When the server says the rate is too high, every change waits,
  the rate is halved, and what was sent for that event type in the 2 s before is sent
  again: the server confirms nothing, and sending an accepted pair twice is harmless.
- **It gathers quick changes.** The grant is computed ``gather`` seconds after the first
  change, so a view that scrolls costs one computation. A pair wanted and dropped before
  its turn is never sent.
- **It says where it is** (BRK-06): :class:`FillState` ``resubscribing`` when a session
  opens and something is granted, ``resubscribed`` when all of it was sent.
- **It reports its use** (SUB-07): :meth:`LocalFeed.status`.
- **The hard cap of the session** is never reached in correct operation. If the session
  refuses for it, the refusal is counted and logged, and nothing is sent until the demand
  changes or a new session opens.
- **The worker does not end by an error.** An unexpected error is logged with its trace
  and counted, and the worker goes on after 5 s.

Items reach the readers inside the session's sink call, which must return at once: a reader
has a bounded queue and never waits (``s3t.subs.feed``).
"""

import asyncio
import logging
import time
from collections import deque
from collections.abc import Collection, Sequence
from typing import Literal

from s3t.broker.interface import (
    Broker,
    EventBatch,
    EventType,
    StreamItem,
    StreamNotice,
    StreamRefused,
    StreamState,
    Sub,
)
from s3t.broker.limits import ChangePacer, Clock, Sleep
from s3t.config import SubscriptionsConfig
from s3t.subs.budget import EMPTY, Grant, Limits, Wanted, allocate
from s3t.subs.feed import (
    Demand,
    FeedItem,
    FeedStatus,
    FillState,
    Lost,
    Priority,
    Reader,
    Reduction,
)

GATHER_S = 0.05
RESEND_WINDOW_S = 2.0
RETRY_AFTER_FAILURE_S = 5.0
_EVENT_ORDER = {event: n for n, event in enumerate(EventType)}

LOG = logging.getLogger(__name__)

type _Kind = Literal["add", "remove"]
type _Chunk = tuple[_Kind, EventType, tuple[Sub, ...]]


class _Demand:
    def __init__(self, feed: "LocalFeed", name: str, priority: Priority) -> None:
        self._feed, self._name, self._priority = feed, name, priority
        self.subs: tuple[Sub, ...] = ()
        self.closed = False

    @property
    def name(self) -> str:
        return self._name

    @property
    def priority(self) -> Priority:
        return self._priority

    def set(self, subs: Sequence[Sub]) -> None:
        if self.closed:
            raise RuntimeError(f"the consumer {self._name} is closed")
        self.subs = tuple(subs)
        self._feed._demand_changed()

    def close(self) -> None:
        if not self.closed:
            self.closed = True
            self.subs = ()
            self._feed._forget(self)


class _Reader:
    def __init__(
        self, feed: "LocalFeed", name: str, events: Collection[EventType] | None, maxsize: int
    ) -> None:
        if maxsize < 1:
            raise ValueError("a reader's queue holds at least one batch")
        self._feed, self.name = feed, name
        self._events = None if events is None else frozenset(events)
        self._maxsize = maxsize
        self._queue: deque[FeedItem] = deque()
        self._batches = 0
        self._untold = 0
        self.lost = 0
        """Batches this reader did not get."""
        self._ready = asyncio.Event()
        self._closed = False

    def put(self, item: FeedItem) -> None:
        """Queue an item. Never waits: a full queue loses a batch and counts it."""
        if self._closed:
            return
        batch = isinstance(item, EventBatch)
        if batch:
            if self._events is not None and item.event not in self._events:
                return
            if self._batches >= self._maxsize:
                self._untold += 1
                self.lost += 1
                self._feed._lost += 1
                if self.lost == 1:
                    LOG.warning("feed: the reader %s cannot keep up; batches are lost", self.name)
                return
        if self._untold:
            self._queue.append(Lost(self._feed._now(), self._untold))
            self._untold = 0
        self._batches += batch
        self._queue.append(item)
        self._ready.set()

    def __aiter__(self) -> "_Reader":
        return self

    async def __anext__(self) -> FeedItem:
        while not self._queue:
            if self._closed:
                raise StopAsyncIteration
            self._ready.clear()
            await self._ready.wait()
        item = self._queue.popleft()
        if isinstance(item, EventBatch):
            self._batches -= 1
        return item

    def close(self) -> None:
        self._closed = True
        self._ready.set()
        self._feed._readers.discard(self)


class LocalFeed:
    """The implementation of :class:`s3t.subs.feed.Feed` inside the backend process."""

    def __init__(
        self,
        broker: Broker,
        config: SubscriptionsConfig | None = None,
        *,
        clock: Clock = time.monotonic,
        sleep: Sleep = asyncio.sleep,
        pacer: ChangePacer | None = None,
        gather: float = GATHER_S,
    ) -> None:
        config = config or SubscriptionsConfig()
        self._limits = Limits(config.budget, config.candle_budget)
        self._clock, self._sleep, self._gather = clock, sleep, gather
        self._now = broker.data_time
        self._pacer = pacer or ChangePacer(
            window_cap=config.change_budget, clock=clock, sleep=sleep
        )
        self._stream = broker.stream(self._on_item)
        # demand and grant
        self._consumers: dict[str, _Demand] = {}
        self._readers: set[_Reader] = set()
        self._grant: Grant = EMPTY
        self._granted: dict[Sub, Priority] = {}
        self._told: dict[str, tuple[Sub, ...]] = {}
        self._dirty = False
        # the current connection
        self._connected = False
        self._filling = False
        self._blocked = False
        self._sent: set[Sub] = set()
        self._plan: deque[_Chunk] = deque()
        self._version = 0
        self._recent: deque[tuple[float, _Chunk]] = deque()
        # the worker
        self._wake = asyncio.Event()
        self._settled = asyncio.Event()
        self._task: asyncio.Task[None] | None = None
        self._closed = False
        self._lost = 0
        self.cap_refusals = 0
        """Calls the session refused for its hard cap. Not zero means a fault."""
        self.failures = 0
        """Unexpected errors of the worker. It logs each one and goes on after a pause."""

    def __repr__(self) -> str:
        return f"LocalFeed(granted={len(self._granted)}, sent={len(self._sent)})"

    # ------------------------------------------------------------------ Feed

    def start(self) -> None:
        if self._task is None and not self._closed:
            self._stream.start()
            self._task = asyncio.create_task(self._run(), name="feed-manager")

    def consumer(self, name: str, priority: Priority) -> Demand:
        if name in self._consumers:
            raise ValueError(f"a consumer named {name} is registered already")
        demand = self._consumers[name] = _Demand(self, name, priority)
        return demand

    def reader(
        self,
        name: str,
        *,
        events: Collection[EventType] | None = None,
        maxsize: int = 2000,
    ) -> Reader:
        reader = _Reader(self, name, events, maxsize)
        self._readers.add(reader)
        return reader

    def granted(self) -> Sequence[Sub]:
        return self._grant.subs

    async def settled(self) -> None:
        await self._settled.wait()

    def status(self) -> FeedStatus:
        grant = self._grant
        return FeedStatus(
            stream=self._stream.status(),
            resubscribing=self._filling,
            granted=len(self._granted),
            budget=self._limits.total,
            candles=grant.candles,
            candle_budget=self._limits.candles,
            sent=len(self._sent),
            pending=sum(len(subs) for _, _, subs in self._plan),
            changes_last_minute=self._pacer.used(),
            change_budget=self._pacer.window_cap,
            rate_errors=self._pacer.rate_errors,
            lost=self._lost,
            classes=grant.classes,
            consumers=grant.consumers,
        )

    async def aclose(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self._task is not None:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
            self._task = None
        await self._stream.aclose()
        for reader in list(self._readers):
            reader.close()

    # ------------------------------------------------------------------ demand

    def _demand_changed(self) -> None:
        self._dirty = True
        self._settled.clear()
        self._wake.set()

    def _forget(self, demand: _Demand) -> None:
        if self._consumers.get(demand.name) is demand:
            del self._consumers[demand.name]
            self._demand_changed()

    def _regrant(self) -> None:
        """Compute the grant from the demand as it stands, and tell who is reduced."""
        wanted = [Wanted(d.name, d.priority, d.subs) for d in self._consumers.values()]
        at = self._now()
        grant = self._grant = allocate(wanted, self._limits, at)
        self._granted = dict(zip(grant.subs, grant.priorities, strict=True))
        cuts = {r.consumer: r for r in grant.reductions}
        for name, reduction in cuts.items():
            if self._told.get(name) != reduction.cut:
                self._publish(reduction)
        for use in grant.consumers:
            if use.name in self._told and use.name not in cuts:
                over = Reduction(at, use.name, use.priority, use.wanted, use.granted, ())
                self._publish(over)
        self._told = {name: reduction.cut for name, reduction in cuts.items()}
        self._blocked = False
        self._replan()

    def _replan(self) -> None:
        """The changes still to send: the difference between the grant and what the
        current connection was sent. Removals first, then the highest class first."""
        self._version += 1
        self._plan.clear()
        removes: dict[EventType, list[Sub]] = {}
        for sub in self._sent:
            if sub not in self._granted:
                removes.setdefault(sub.event, []).append(sub)
        size = self._pacer.burst
        for event, subs in removes.items():
            for i in range(0, len(subs), size):
                self._plan.append(("remove", event, tuple(subs[i : i + size])))
        adds: dict[tuple[Priority, int], list[Sub]] = {}
        for sub, priority in self._granted.items():
            if sub not in self._sent:
                adds.setdefault((priority, _EVENT_ORDER[sub.event]), []).append(sub)
        for key in sorted(adds):
            subs = adds[key]
            for i in range(0, len(subs), size):
                self._plan.append(("add", subs[0].event, tuple(subs[i : i + size])))

    # ------------------------------------------------------------------ the stream's items

    def _publish(self, item: FeedItem) -> None:
        for reader in tuple(self._readers):
            reader.put(item)

    def _on_item(self, item: StreamItem) -> None:
        """The sink of the session. Returns at once."""
        self._publish(item)
        if isinstance(item, StreamState):
            self._on_state(item)
        elif isinstance(item, StreamNotice) and item.kind == "rate":
            self._on_rate_error(item)

    def _on_state(self, state: StreamState) -> None:
        self._sent.clear()
        self._recent.clear()
        self._settled.clear()
        self._connected = state.phase == "connected"
        self._filling = False
        if self._connected:
            # A session that has just opened holds nothing: the whole grant is to be sent.
            self._blocked = False
            self._replan()
            if self._plan:
                self._filling = True
                pending = sum(len(subs) for _, _, subs in self._plan)
                fill = FillState(state.at, "resubscribing", len(self._granted), pending)
                self._publish(fill)
        else:
            self._plan.clear()
            self._version += 1
        self._wake.set()

    def _on_rate_error(self, notice: StreamNotice) -> None:
        """What was sent for this event type lately may not have counted: send it again."""
        pause = self._pacer.on_rate_error()
        now = self._clock()
        for at, (kind, event, subs) in self._recent:
            if now - at > RESEND_WINDOW_S or notice.event not in (None, event):
                continue
            if kind == "add":
                self._sent.difference_update(subs)
            else:
                self._sent.update(subs)
        self._recent = deque(
            entry for entry in self._recent if notice.event not in (None, entry[1][1])
        )
        if pause:
            LOG.warning(
                "feed: the server refused for the rate; changes wait %.0f s, now %.0f a second",
                pause,
                self._pacer.rate,
            )
        self._settled.clear()
        self._replan()
        self._wake.set()

    # ------------------------------------------------------------------ the worker

    async def _run(self) -> None:
        """The worker never ends by an error: a fault in the session or in the grant must
        not leave the demand unsent without a word."""
        while True:
            try:
                await self._work()
            except Exception:
                self.failures += 1
                LOG.exception(
                    "feed: the manager failed; it goes on in %.0f s", RETRY_AFTER_FAILURE_S
                )
                await self._sleep(RETRY_AFTER_FAILURE_S)

    async def _work(self) -> None:
        while True:
            self._wake.clear()
            if self._dirty:
                await self._sleep(self._gather)  # gather the changes that come together
                self._dirty = False
                self._regrant()
                continue
            if not self._connected or self._blocked:
                await self._wake.wait()
                continue
            if not self._plan:
                if self._filling:
                    self._filling = False
                    self._publish(FillState(self._now(), "resubscribed", len(self._granted), 0))
                self._settled.set()
                await self._wake.wait()
                continue
            version = self._version
            kind, event, subs = chunk = self._plan[0]
            await self._pacer.acquire(len(subs))
            if version != self._version or self._dirty:
                continue  # the demand or the connection changed during the wait
            try:
                if kind == "add":
                    await self._stream.subscribe(subs)
                else:
                    await self._stream.unsubscribe(subs)
            except StreamRefused as e:
                self._refused(e, kind, len(subs))
                continue
            if version != self._version:
                continue  # the session was lost while sending: nothing of it counts
            self._plan.popleft()
            if kind == "add":
                self._sent.update(subs)
            else:
                self._sent.difference_update(subs)
            now = self._clock()
            self._recent.append((now, chunk))
            while self._recent and now - self._recent[0][0] > RESEND_WINDOW_S:
                self._recent.popleft()

    def _refused(self, refusal: StreamRefused, kind: _Kind, count: int) -> None:
        if refusal.reason == "not_connected":
            # The next `connected` starts from an empty session and plans again.
            self._connected = False
            return
        self.cap_refusals += 1
        self._blocked = True
        LOG.error(
            "feed: the session refused %d subscriptions for its hard cap (%s); "
            "the budget and the cap do not fit. Nothing is sent until the demand changes",
            count,
            refusal.reason,
        )
