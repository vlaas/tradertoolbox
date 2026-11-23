"""Local recordings of a broker session (implementation plan 3.2; BRK-05).

:class:`RecordingBroker` wraps any broker and writes what crosses the broker interface: the
REST documents and every item of the stream. ``s3t.broker.replay`` serves a recording back
through the same interface. A recording is market data: it goes to the git-ignored data
directory named by the configuration and never into the repository (GEN-07). ``make check``
records only the fake broker, into a temporary directory.

The format, version 1 (decided 2026-11-23). A directory with three files:

- ``meta.json``: the format version, the name, the kind of broker recorded, the start in
  UTC and the New York date of the start (for the "REPLAY" mark, GEN-11). It is written at
  the start with ``complete`` false. Closing the recording adds the end and the number of
  items written and lost, and sets ``complete``.
- ``rest.jsonl``: one line per REST read: ``at``, ``call``, ``args`` and ``result``, or
  ``error`` with the name of the error.
- ``stream.jsonl.zst``: one line per stream item, in order, compressed with zstd. ``k`` says
  what the line is:

  - ``f``: the field names of an event type's record, written before its first batch. A
    field added to a record later leaves an older recording readable.
  - ``b``: a batch. ``t`` is the receipt time, ``e`` the event type and ``c`` the columns
    in the order of the ``f`` line.
  - ``s`` a state, ``g`` a gap, ``n`` a notice;
  - ``+`` and ``-``: a subscribe and an unsubscribe call, with the time of the data;
  - ``x``: items lost by the writer.

  Values: an instant is microseconds since 1970 UTC, or ``null``. A missing number (NaN) is
  ``null``. A candle period is text (``15m``).

The stream file is one zstd frame. The writer flushes a block every few seconds and when it
falls idle, so a recording that was cut off is readable up to its last flush. A block flush
keeps what the compressor has learnt. Ending a frame each time, as first built, cost about a
third of the compression (``docs/measurements.md``; decided 2026-11-24).

The recording is at the interface level, not the wire level: it holds what the application
saw, the application's own records, whichever broker gave them. The streaming token is
never written. The account is not in any of these documents.

Writing never holds up the stream. Items go to a bounded queue and a thread encodes and
writes them. A full queue loses the item, counts it and says so in the file.
"""

import json
import logging
import math
import queue
import threading
import time
from collections.abc import Collection, Sequence
from dataclasses import fields
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal, get_args
from zoneinfo import ZoneInfo

from backports import zstd

from s3t import __version__
from s3t.broker.interface import (
    FIELDS,
    RECORDS,
    Broker,
    BrokerError,
    CandlePeriod,
    DataSource,
    EventBatch,
    EventType,
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
META, REST, STREAM = "meta.json", "rest.jsonl", "stream.jsonl.zst"
QUEUE_SIZE = 50_000
FLUSH_SECONDS = 3.0
_NY = ZoneInfo("America/New_York")
_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)

LOG = logging.getLogger(__name__)

type ColumnKind = Literal["text", "number", "time", "period", "mark"]


def _kind(annotation: Any) -> ColumnKind:
    if annotation is float:
        return "number"
    if annotation is bool:
        return "mark"
    if annotation is CandlePeriod:
        return "period"
    if annotation is datetime or datetime in get_args(annotation):
        return "time"
    return "text"


KINDS: dict[EventType, dict[str, ColumnKind]] = {
    event: {f.name: _kind(f.type) for f in fields(record)} for event, record in RECORDS.items()
}
"""How each field of a record is written: by the type the record gives it."""


def to_micros(instant: datetime) -> int:
    delta = instant - _EPOCH
    return (delta.days * 86_400 + delta.seconds) * 1_000_000 + delta.microseconds


def encode_sub(sub: Sub) -> list[Any]:
    period = None if sub.period is None else sub.period.text()
    start = None if sub.from_time is None else to_micros(sub.from_time)
    return [sub.symbol, sub.event.value, period, start]


def encode_column(kind: ColumnKind, column: Sequence[Any]) -> list[Any]:
    if kind == "number":
        return [x if math.isfinite(x) else None for x in column]
    if kind == "time":
        return [None if x is None else to_micros(x) for x in column]
    if kind == "period":
        return [x.text() for x in column]
    return list(column)


def encode_item(item: StreamItem) -> dict[str, Any]:
    """The line of a stream item."""
    if isinstance(item, EventBatch):
        kinds = KINDS[item.event].values()
        columns = [encode_column(k, c) for k, c in zip(kinds, item.columns, strict=True)]
        return {"k": "b", "t": to_micros(item.received_at), "e": item.event.value, "c": columns}
    if isinstance(item, StreamState):
        return {"k": "s", "t": to_micros(item.at), "p": item.phase, "r": item.reason}
    if isinstance(item, Gap):
        line = {"k": "g", "t": to_micros(item.until), "since": to_micros(item.since)}
        return {**line, "r": item.reason}
    event = None if item.event is None else item.event.value
    return {"k": "n", "t": to_micros(item.at), "kind": item.kind, "m": item.message, "e": event}


class RecordingWriter:
    """Writes the lines of one recording on its own thread."""

    def __init__(
        self,
        directory: Path,
        meta: dict[str, Any] | None = None,
        *,
        queue_size: int = QUEUE_SIZE,
        flush_seconds: float = FLUSH_SECONDS,
    ) -> None:
        directory.mkdir(parents=True, exist_ok=True)
        if (directory / META).exists():
            raise FileExistsError(f"a recording exists already in {directory.name}")
        self.directory = directory
        self.written = 0
        self.lost = 0
        self._meta = {**(meta or {}), "format": FORMAT}
        self._write_meta({"complete": False})
        self._flush_seconds = flush_seconds
        self._queue: queue.Queue[tuple[str, Any] | None] = queue.Queue(queue_size)
        self._named: set[EventType] = set()
        self._thread = threading.Thread(target=self._run, name="recording-writer", daemon=True)
        self._thread.start()

    def _write_meta(self, more: dict[str, Any]) -> None:
        text = json.dumps({**self._meta, **more}, indent=2) + "\n"
        (self.directory / META).write_text(text, encoding="utf-8")

    def put(self, file: str, line: Any) -> None:
        """Queue a line for a file, or a stream item. Never waits: a full queue loses it
        and counts it."""
        try:
            self._queue.put_nowait((file, line))
        except queue.Full:
            self.lost += 1
            if self.lost == 1:
                LOG.warning("recording: the writer cannot keep up; items are being lost")

    def item(self, item: StreamItem) -> None:
        self.put(STREAM, item)

    def _lines(self, entry: Any) -> list[dict[str, Any]]:
        """The lines of one queued entry. Runs on the writer's thread."""
        if isinstance(entry, dict):
            return [entry]
        lines: list[dict[str, Any]] = []
        if isinstance(entry, EventBatch) and entry.event not in self._named:
            self._named.add(entry.event)
            names = list(FIELDS[entry.event])
            lines.append({"k": "f", "e": entry.event.value, "f": names})
        lines.append(encode_item(entry))
        return lines

    def _run(self) -> None:
        reported = 0
        dirty = False
        ended = time.monotonic()
        with (
            (self.directory / REST).open("w", encoding="utf-8") as rest,
            zstd.ZstdFile(self.directory / STREAM, "w") as stream,
        ):

            def write(line: dict[str, Any]) -> None:
                stream.write(json.dumps(line, separators=(",", ":")).encode() + b"\n")

            def flush() -> None:
                stream.flush(stream.FLUSH_BLOCK)
                rest.flush()

            while True:
                try:
                    entry = self._queue.get(timeout=self._flush_seconds)
                except queue.Empty:
                    if dirty:
                        flush()
                        dirty, ended = False, time.monotonic()
                    continue
                if entry is None:
                    break
                file, content = entry
                if self.lost > reported:
                    write({"k": "x", "lost": self.lost - reported})
                    reported = self.lost
                if file == REST:
                    rest.write(json.dumps(content, separators=(",", ":")) + "\n")
                else:
                    for line in self._lines(content):
                        write(line)
                self.written += 1
                dirty = True
                if time.monotonic() - ended >= self._flush_seconds:
                    flush()
                    dirty, ended = False, time.monotonic()

    def close(self, meta: dict[str, Any]) -> None:
        """Write what is queued, then the description of the recording."""
        self._queue.put(None)
        self._thread.join()
        self._meta.update(meta)
        self._write_meta({"items": self.written, "lost": self.lost, "complete": True})


class _RecordingStream:
    def __init__(self, inner: Stream, writer: RecordingWriter, broker: Broker) -> None:
        self._inner, self._writer, self._broker = inner, writer, broker

    def start(self) -> None:
        self._inner.start()

    async def subscribe(self, subs: Collection[Sub]) -> None:
        await self._inner.subscribe(subs)
        self._note("+", subs)

    async def unsubscribe(self, subs: Collection[Sub]) -> None:
        await self._inner.unsubscribe(subs)
        self._note("-", subs)

    def _note(self, kind: str, subs: Collection[Sub]) -> None:
        at = to_micros(self._broker.data_time())
        self._writer.put(STREAM, {"k": kind, "t": at, "subs": [encode_sub(s) for s in subs]})

    def status(self) -> StreamStatus:
        return self._inner.status()

    async def aclose(self) -> None:
        await self._inner.aclose()


class RecordingBroker:
    """A broker that records the broker it wraps. Close it to finish the recording."""

    def __init__(self, inner: Broker, directory: Path, *, queue_size: int = QUEUE_SIZE) -> None:
        self._inner = inner
        self.directory = directory
        started = inner.data_time()
        self._writer = RecordingWriter(
            directory,
            {
                "name": directory.name,
                "source": inner.source().kind,
                "started_at": started.isoformat(),
                "recorded_on": started.astimezone(_NY).date().isoformat(),
                "s3t_version": __version__,
            },
            queue_size=queue_size,
        )
        self._closed = False

    @property
    def lost(self) -> int:
        return self._writer.lost

    async def _read(self, call: str, *args: Any) -> Any:
        at = self._inner.data_time().isoformat()
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

    def data_time(self) -> datetime:
        return self._inner.data_time()

    async def aclose(self) -> None:
        if self._closed:
            return
        self._closed = True
        ended = self._inner.data_time()
        await self._inner.aclose()
        self._writer.close({"ended_at": ended.isoformat()})
