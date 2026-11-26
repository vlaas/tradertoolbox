"""The topics of the connection (API-05): ``status`` and ``quotes``. The others come with their
packages: ``chain``, ``ticket``, ``candles``.

**status.** The whole state in every message. One goes out at once when something changed that a
person would call a change, and otherwise every few seconds while the running figures move.

**quotes.** A snapshot of all rows at once, then the rows that changed, at the flush of the market
state (MKT-03). The kinds of symbol were decided on 2026-11-26, the rest on 2026-11-27 (owner,
WP-0.7 review):

- The symbols are the application's. The instrument of each is read from the broker, one at a time:
  the request limit is shared with the token that a reconnect needs (BRK-02), and a long list must
  not stand in front of it. A row is empty until its instrument was read. Those that were read
  before are there at once.
- Stocks, ETFs and indexes. Which of the broker's calls a symbol needs is the broker module's to
  know, and it has no such call yet; futures come with one.
- A symbol without data has a *note* that says why: the broker does not know it, the subscription
  budget cut it (SUB-03), or the broker could not be asked, which is tried again.
- A row has no mark for a value that is not live. The ``status`` says since when values are live,
  and a row has the receipt times (UI-05). One rule for every topic, and nothing is sent again when
  a session is lost.

A topic asks the feed for what it shows, and for no longer than it shows it.
"""

import asyncio
import logging
from collections.abc import Iterable, Mapping, Sequence

from s3t.api import status as wire
from s3t.api.channel import Emit, Topic, TopicRefused, Work
from s3t.api.rows import ROW_EVENTS, changed_symbols, quote_rows
from s3t.broker.interface import (
    BrokerError,
    BrokerUnavailable,
    ForbiddenCall,
    NotFound,
    RateLimited,
    Sub,
)
from s3t.market.state import Changes
from s3t.subs.feed import Priority, Reader, Reduction
from s3t.system.backend import Backend
from s3t.system.features import QUOTES, Feature
from s3t.wire.v1.channel_pb2 import (
    NOTE_KIND_CUT,
    NOTE_KIND_UNKNOWN,
    NOTE_KIND_UNREACHABLE,
    REFUSAL_KIND_INVALID,
    Note,
    Notes,
    Quotes,
    ServerMessage,
    Subscribe,
)
from s3t.wire.v1.status_pb2 import Status

LOOK_EVERY_S = 1.0
"""How often the status is looked at when the stream says nothing."""
REFRESH_S = 5.0
"""How often the running figures of the status are sent."""
RETRY_S = 30.0
MOST_SYMBOLS = 100
LONGEST_SYMBOL = 32

LOG = logging.getLogger(__name__)


class StatusTopic:
    feature: Feature | None = None

    def __init__(self, backend: Backend) -> None:
        self._backend = backend

    async def run(self, request: Subscribe, emit: Emit, name: str) -> None:
        backend, clock = self._backend, self._backend.time.clock
        work = Work()
        reader = backend.feed.reader(f"status:{name}", events=())
        last: Status | None = None
        sent_at = 0.0
        try:
            work.start(self._watch(reader, work))
            work.start(self._look(work))
            while True:
                now = wire.status(backend)
                if (
                    last is None
                    or not wire.same(now, last)
                    or (clock() - sent_at >= REFRESH_S and wire.figures(now) != wire.figures(last))
                ):
                    await emit(ServerMessage(snapshot=True, status=now))
                    last, sent_at = now, clock()
                await work.wait()
        finally:
            reader.close()
            await work.aclose()

    async def _watch(self, reader: Reader, work: Work) -> None:
        async for _ in reader:
            work.wake()

    async def _look(self, work: Work) -> None:
        while True:
            await self._backend.time.sleep(LOOK_EVERY_S)
            work.wake()


class QuotesTopic:
    feature: Feature | None = QUOTES

    def __init__(self, backend: Backend) -> None:
        self._backend = backend

    async def run(self, request: Subscribe, emit: Emit, name: str) -> None:
        symbols = tuple(dict.fromkeys(request.quotes.symbol))
        if len(symbols) > MOST_SYMBOLS:
            raise TopicRefused(REFUSAL_KIND_INVALID, f"at most {MOST_SYMBOLS} symbols")
        if any(not s or len(s) > LONGEST_SYMBOL for s in symbols):
            raise TopicRefused(REFUSAL_KIND_INVALID, "not a symbol")
        await _QuotesView(self._backend, symbols, f"quotes:{name}").run(emit)


class _QuotesView:
    """One subscription to quotes."""

    def __init__(self, backend: Backend, symbols: Sequence[str], name: str) -> None:
        self._backend, self._symbols, self._name = backend, symbols, name
        self._market = backend.market
        self._work = Work()
        self._streams: dict[str, str] = {}
        """The stream symbol of every symbol whose instrument was read."""
        self._changed: set[str] = set()
        self._problems: dict[str, Note] = {}
        self._cut: frozenset[str] = frozenset()
        self._notes_changed = False

    async def run(self, emit: Emit) -> None:
        feed = self._backend.feed
        # The taker before the snapshot, so that what changes in between is not missed.
        taker = self._market.changes(self._name)
        reader = feed.reader(self._name, events=())
        self._demand = feed.consumer(self._name, Priority.VIEWS)
        try:
            # It runs as far as the instruments are known already, before it has to wait.
            self._work.start(self._resolve())
            self._work.start(self._take(taker))
            self._work.start(self._watch(reader))
            await asyncio.sleep(0)
            rows = quote_rows(self._market, self._rows_of(self._symbols))
            self._changed.clear()
            self._notes_changed = False
            snapshot = Quotes(rows=rows, notes=self._notes())
            await emit(ServerMessage(snapshot=True, quotes=snapshot))
            while True:
                await self._work.wait()
                message = self._message()
                if message is not None:
                    await emit(ServerMessage(quotes=message))
        finally:
            self._demand.close()
            taker.close()
            reader.close()
            await self._work.aclose()

    # ------------------------------------------------------------------ what is sent

    def _rows_of(self, symbols: Iterable[str]) -> dict[str, str]:
        # No value has an empty stream symbol, so a row without an instrument is empty.
        return {symbol: self._streams.get(symbol, "") for symbol in symbols}

    def _message(self) -> Quotes | None:
        changed = [symbol for symbol in self._symbols if symbol in self._changed]
        self._changed.clear()
        if not changed and not self._notes_changed:
            return None
        message = Quotes()
        if changed:
            message.rows.CopyFrom(quote_rows(self._market, self._rows_of(changed)))
        if self._notes_changed:
            message.notes.CopyFrom(self._notes())
            self._notes_changed = False
        return message

    def _notes(self) -> Notes:
        notes: list[Note] = []
        for symbol in self._symbols:
            if symbol in self._problems:
                notes.append(self._problems[symbol])
            elif symbol in self._cut:
                text = "the subscription budget does not reach this far"
                notes.append(Note(symbol=symbol, kind=NOTE_KIND_CUT, text=text))
        return Notes(note=notes)

    def _note(self, symbol: str, note: Note | None) -> None:
        if note != self._problems.get(symbol):
            if note is None:
                del self._problems[symbol]
            else:
                self._problems[symbol] = note
            self._notes_changed = True
            self._work.wake()

    # ------------------------------------------------------------------ the instruments

    async def _resolve(self) -> None:
        waiting = self._symbols
        while waiting:
            again: list[str] = []
            for symbol in waiting:
                if not await self._read(symbol):
                    again.append(symbol)
            waiting = tuple(again)
            if waiting:
                await self._backend.time.sleep(RETRY_S)

    async def _read(self, symbol: str) -> bool:
        """Read the instrument of a symbol and ask for its events. False: try again later."""
        try:
            security = await self._backend.instruments.security(symbol)
        except (NotFound, ForbiddenCall):
            text = "the broker does not know this symbol"
            self._note(symbol, Note(symbol=symbol, kind=NOTE_KIND_UNKNOWN, text=text))
            return True
        except (BrokerUnavailable, RateLimited):
            text = "the broker could not be asked; it is tried again"
            self._note(symbol, Note(symbol=symbol, kind=NOTE_KIND_UNREACHABLE, text=text))
            return False
        except BrokerError as e:
            LOG.warning("quotes: the instrument of a symbol could not be read: %s", e)
            text = "the broker's answer is an error; the log of the backend has it"
            self._note(symbol, Note(symbol=symbol, kind=NOTE_KIND_UNREACHABLE, text=text))
            return True
        self._note(symbol, None)
        self._streams[symbol] = security.stream_symbol
        self._demand.set(self._wanted())
        if any(
            received is not None
            for event in ROW_EVENTS
            for received in self._market.received(event, [security.stream_symbol])
        ):  # another view has it already
            self._changed.add(symbol)
            self._work.wake()
        return True

    def _wanted(self) -> list[Sub]:
        """The pairs in the order of the symbols: a budget that is short cuts whole rows, from
        the end."""
        streams = [self._streams[s] for s in self._symbols if s in self._streams]
        return [Sub(stream, event) for stream in streams for event in ROW_EVENTS]

    # ------------------------------------------------------------------ what comes in

    async def _take(self, taker: Changes) -> None:
        async for flush in taker:
            changed = changed_symbols(flush, self._streams)
            if changed:
                self._changed.update(changed)
                self._work.wake()

    async def _watch(self, reader: Reader) -> None:
        async for item in reader:
            if isinstance(item, Reduction) and item.consumer == self._name:
                cut = {sub.symbol for sub in item.cut}
                self._cut = frozenset(s for s, stream in self._streams.items() if stream in cut)
                self._notes_changed = True
                self._work.wake()


def make_topics(backend: Backend) -> Mapping[str, Topic]:
    """By the name of their field in ``Subscribe``."""
    return {"status": StatusTopic(backend), "quotes": QuotesTopic(backend)}
