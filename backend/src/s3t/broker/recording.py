"""Local recordings of a broker session (implementation plan 3.2; BRK-05).

:class:`RecordingBroker` wraps any broker and writes what crosses the broker interface: the
REST documents and every item of the stream. ``s3t.broker.replay`` serves a recording back
through the same interface. A recording is market data: it goes to the git-ignored data
directory named by the configuration and never into the repository (GEN-07). ``make check``
records only the fake broker, into a temporary directory.

The format, version 1, proposed 2026-11-23 (WP-0.4). A directory with three files:

- ``meta.json``: the format version, the name, the kind of broker recorded, the start and
  the end in UTC, the New York date of the start (for the "REPLAY" mark, GEN-11), and the
  number of items written and lost.
- ``rest.jsonl``: one line per REST read: ``at``, ``call``, ``args`` and ``result``, or
  ``error`` with the name of the error.
- ``stream.jsonl.gz``: one line per stream item, in order. ``k`` says what the line is:
  ``f`` the field names of an event type, written before its first batch and when they
  change; ``b`` a batch (``t`` receipt time in microseconds since 1970 UTC, ``e`` event
  type, ``v`` the values as received); ``s`` a state; ``g`` a gap; ``n`` a notice; ``+`` and
  ``-`` a subscribe and an unsubscribe call; ``x`` items lost by the writer.

The recording is at the interface level, not the wire level: it holds what the application
saw, whichever broker gave it. The streaming token is never written. The account is not in
any of these documents.

Writing never holds up the stream. Items go to a bounded queue and a thread writes them. A
full queue loses the item, counts it and says so in the file.
"""

import gzip
import json
import logging
import queue
import threading
from collections.abc import Collection, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from s3t import __version__
from s3t.broker.interface import (
    Broker,
    BrokerError,
    DataSource,
    EventBatch,
    Gap,
    JsonObject,
    QuoteToken,
    RestStatus,
    Stream,
    StreamItem,
    StreamSink,
    StreamState,
    StreamStatus,
    Sub,
)

FORMAT = 1
META, REST, STREAM = "meta.json", "rest.jsonl", "stream.jsonl.gz"
QUEUE_SIZE = 50_000
_NY = ZoneInfo("America/New_York")
_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)

LOG = logging.getLogger(__name__)


def to_micros(instant: datetime) -> int:
    delta = instant - _EPOCH
    return (delta.days * 86_400 + delta.seconds) * 1_000_000 + delta.microseconds


def encode_sub(sub: Sub) -> list[Any]:
    start = None if sub.from_time is None else to_micros(sub.from_time)
    return [sub.symbol, sub.event.value, start]


class RecordingWriter:
    """Writes the lines of one recording on its own thread."""

    def __init__(self, directory: Path, *, queue_size: int = QUEUE_SIZE) -> None:
        directory.mkdir(parents=True, exist_ok=True)
        if (directory / META).exists():
            raise FileExistsError(f"a recording exists already in {directory.name}")
        self.directory = directory
        self.written = 0
        self.lost = 0
        self._queue: queue.Queue[tuple[str, dict[str, Any]] | None] = queue.Queue(queue_size)
        self._fields: dict[str, tuple[str, ...]] = {}
        self._thread = threading.Thread(target=self._run, name="recording-writer", daemon=True)
        self._thread.start()

    def put(self, file: str, line: dict[str, Any]) -> None:
        """Queue a line. Never waits: a full queue loses the line and counts it."""
        try:
            self._queue.put_nowait((file, line))
        except queue.Full:
            self.lost += 1
            if self.lost == 1:
                LOG.warning("recording: the writer cannot keep up; items are being lost")

    def item(self, item: StreamItem) -> None:
        if isinstance(item, EventBatch):
            if self._fields.get(item.event) != item.fields:
                self._fields[item.event] = item.fields
                self.put(STREAM, {"k": "f", "e": item.event.value, "f": list(item.fields)})
            line = {"k": "b", "t": to_micros(item.received_at), "e": item.event.value}
            self.put(STREAM, {**line, "v": item.values})
        elif isinstance(item, StreamState):
            self.put(STREAM, {"k": "s", "t": to_micros(item.at), "p": item.phase, "r": item.reason})
        elif isinstance(item, Gap):
            line = {"k": "g", "t": to_micros(item.until), "since": to_micros(item.since)}
            self.put(STREAM, {**line, "r": item.reason})
        else:
            line = {"k": "n", "t": to_micros(item.at), "kind": item.kind}
            self.put(STREAM, {**line, "m": item.message})

    def _run(self) -> None:
        reported = 0
        with (
            (self.directory / REST).open("w", encoding="utf-8") as rest,
            gzip.open(self.directory / STREAM, "wt", encoding="utf-8", compresslevel=5) as stream,
        ):
            while (entry := self._queue.get()) is not None:
                file, line = entry
                out = rest if file == REST else stream
                if self.lost > reported:
                    stream.write(json.dumps({"k": "x", "lost": self.lost - reported}) + "\n")
                    reported = self.lost
                out.write(json.dumps(line, separators=(",", ":")) + "\n")
                self.written += 1

    def close(self, meta: dict[str, Any]) -> None:
        """Write what is queued, then the description of the recording."""
        self._queue.put(None)
        self._thread.join()
        meta = {**meta, "format": FORMAT, "items": self.written, "lost": self.lost}
        (self.directory / META).write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")


class _RecordingStream:
    def __init__(self, inner: Stream, writer: RecordingWriter, broker: Broker) -> None:
        self._inner, self._writer, self._broker = inner, writer, broker

    def start(self) -> None:
        self._inner.start()

    def subscribe(self, subs: Collection[Sub]) -> None:
        self._note("+", subs)
        self._inner.subscribe(subs)

    def unsubscribe(self, subs: Collection[Sub]) -> None:
        self._note("-", subs)
        self._inner.unsubscribe(subs)

    def _note(self, kind: str, subs: Collection[Sub]) -> None:
        at = to_micros(self._broker.now())
        self._writer.put(STREAM, {"k": kind, "t": at, "subs": [encode_sub(s) for s in subs]})

    async def synced(self) -> None:
        await self._inner.synced()

    def status(self) -> StreamStatus:
        return self._inner.status()

    async def aclose(self) -> None:
        await self._inner.aclose()


class RecordingBroker:
    """A broker that records the broker it wraps. Close it to finish the recording."""

    def __init__(self, inner: Broker, directory: Path, *, queue_size: int = QUEUE_SIZE) -> None:
        self._inner = inner
        self.directory = directory
        self._writer = RecordingWriter(directory, queue_size=queue_size)
        self._started = inner.now()
        self._closed = False

    @property
    def lost(self) -> int:
        return self._writer.lost

    async def _read(self, call: str, *args: Any) -> Any:
        at = self._inner.now().isoformat()
        line: dict[str, Any] = {"at": at, "call": call, "args": list(args)}
        try:
            result = await getattr(self._inner, call)(*args)
        except BrokerError as e:
            self._writer.put(REST, {**line, "error": type(e).__name__})
            raise
        self._writer.put(REST, {**line, "result": result})
        return result

    async def quote_token(self) -> QuoteToken:
        return await self._inner.quote_token()  # a secret: never recorded

    async def equity(self, symbol: str) -> JsonObject:
        return await self._read("equity", symbol)

    async def equity_option(self, symbol: str) -> JsonObject:
        return await self._read("equity_option", symbol)

    async def option_chain(self, underlying: str) -> JsonObject:
        return await self._read("option_chain", underlying)

    async def future(self, symbol: str) -> JsonObject:
        return await self._read("future", symbol)

    async def future_option(self, symbol: str) -> JsonObject:
        return await self._read("future_option", symbol)

    async def futures_option_chain(self, root: str) -> JsonObject:
        return await self._read("futures_option_chain", root)

    async def market_metrics(self, symbols: Sequence[str]) -> list[JsonObject]:
        return await self._read("market_metrics", list(symbols))

    def rest_status(self) -> RestStatus:
        return self._inner.rest_status()

    def stream(self, sink: StreamSink) -> Stream:
        def tee(item: StreamItem) -> None:
            self._writer.item(item)
            sink(item)

        return _RecordingStream(self._inner.stream(tee), self._writer, self._inner)

    def source(self) -> DataSource:
        return self._inner.source()

    def now(self) -> datetime:
        return self._inner.now()

    async def aclose(self) -> None:
        if self._closed:
            return
        self._closed = True
        ended = self._inner.now()
        await self._inner.aclose()
        self._writer.close(
            {
                "name": self.directory.name,
                "source": self._inner.source().kind,
                "started_at": self._started.isoformat(),
                "ended_at": ended.isoformat(),
                "recorded_on": self._started.astimezone(_NY).date().isoformat(),
                "s3t_version": __version__,
            }
        )
