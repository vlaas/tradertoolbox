"""The chain's rows on the wire (``chain.proto``) and as Arrow (API-06; WP-1.2).

The same columns go out both ways: the ``chain`` topic sends the rows of its windows as Protocol
Buffers, and the bulk route sends every strike of one expiration as one Arrow IPC stream. The rules
of ``market.proto`` hold for both: a missing number is NaN, an instant is microseconds since 1970
in UTC as a double, a symbol is the application's, and a side that is not listed has an empty
symbol.
"""

import math
from collections.abc import Callable, Iterable, Sequence
from datetime import date
from typing import Any

from s3t.analysis.chain import (
    BROKER,
    OWN,
    ExpirationInfo,
    ExpirationSnapshot,
    Head,
    Part,
    Row,
    Side,
    ViewNote,
)
from s3t.api.rows import micros
from s3t.arrow import MEDIA_TYPE, ColumnType, ipc_stream
from s3t.wire.v1.chain_pb2 import (
    GREEKS_SOURCE_BROKER,
    GREEKS_SOURCE_NONE,
    GREEKS_SOURCE_OWN,
    ChainExpiration,
    ChainHead,
    ChainRows,
    ChainSide,
    GreeksSource,
)
from s3t.wire.v1.channel_pb2 import (
    NOTE_KIND_CUT,
    NOTE_KIND_LEFT_OUT,
    NOTE_KIND_UNKNOWN,
    NOTE_KIND_UNREACHABLE,
    NOTE_KIND_WINDOW,
    Chain,
    Note,
    NoteKind,
    Notes,
)

ARROW_TYPE = MEDIA_TYPE

_NOTE_KINDS: dict[str, NoteKind] = {
    "unknown": NOTE_KIND_UNKNOWN,
    "unreachable": NOTE_KIND_UNREACHABLE,
    "cut": NOTE_KIND_CUT,
    "left_out": NOTE_KIND_LEFT_OUT,
    "window": NOTE_KIND_WINDOW,
}
_SOURCES: dict[str, GreeksSource] = {
    OWN: GREEKS_SOURCE_OWN,
    BROKER: GREEKS_SOURCE_BROKER,
}

_SIDE_NUMBERS = (
    "bid",
    "ask",
    "bid_size",
    "ask_size",
    "iv",
    "delta",
    "gamma",
    "theta",
    "vega",
    "volume",
    "open_interest",
)
"""The numbers of a side, as they are named in ``ChainSide`` and in the Arrow columns."""


def _optional(x: float) -> float | None:
    return x if math.isfinite(x) else None


def _iso(day: date | None) -> str:
    return "" if day is None else day.isoformat()


def head_message(head: Head) -> ChainHead:
    return ChainHead(
        underlying=head.underlying,
        kind=head.kind or "",
        spot=_optional(head.spot),
        spot_source=head.spot_source,
        data_time_us=micros(head.at),
        stale_after_s=head.stale_after_s,
        earnings=_iso(head.earnings),
        earnings_time=head.earnings_time or "",
        earnings_estimated=head.earnings_estimated,
    )


def expiration_message(index: int, e: ExpirationInfo) -> ChainExpiration:
    settles = None if e.settles_at is None else micros(e.settles_at)
    return ChainExpiration(
        index=index,
        key=e.key,
        root=e.root,
        date=e.expiration_date.isoformat(),
        settlement=e.settlement,
        settles_at_us=settles,
        kind=e.kind,
        multiplier=e.multiplier,
        standard=e.standard,
        strikes=e.strikes,
        forward=_optional(e.forward),
        forward_source=e.forward_source,
        atm_iv=_optional(e.atm_iv),
        expected_move=_optional(e.expected_move),
        expected_move_method=e.expected_move_method,
        broker_iv=_optional(e.broker_iv),
        ex_dividend=[d.ex_date.isoformat() for d in e.ex_dividends],
        ex_dividend_projected=[d.projected for d in e.ex_dividends],
        earnings_before=e.earnings_before,
        note=e.note or "",
    )


def _side_columns(sides: Sequence[Side | None]) -> dict[str, list[Any]]:
    """The columns of one side of the rows, by the names of ``ChainSide``."""
    columns: dict[str, list[Any]] = {
        "symbol": [],
        "quote_us": [],
        "stale": [],
        "source": [],
        **{name: [] for name in _SIDE_NUMBERS},
    }
    nan = math.nan
    for side in sides:
        if side is None:
            columns["symbol"].append("")
            columns["quote_us"].append(nan)
            columns["stale"].append(False)
            columns["source"].append("none")
            for name in _SIDE_NUMBERS:
                columns[name].append(nan)
            continue
        columns["symbol"].append(side.symbol)
        columns["quote_us"].append(micros(side.quote_at))
        columns["stale"].append(side.stale)
        columns["source"].append(side.source)
        for name in _SIDE_NUMBERS:
            columns[name].append(getattr(side, name))
    return columns


def rows_message(rows: Sequence[Row]) -> ChainRows:
    def side(get: Callable[[Row], Side | None]) -> ChainSide:
        columns = _side_columns([get(r) for r in rows])
        sources = [_SOURCES.get(s, GREEKS_SOURCE_NONE) for s in columns.pop("source")]
        return ChainSide(**columns, source=sources)

    return ChainRows(
        expiration=[r.expiration for r in rows],
        strike=[r.strike for r in rows],
        iv=[r.iv for r in rows],
        call=side(lambda r: r.call),
        put=side(lambda r: r.put),
    )


def notes_message(notes: Iterable[ViewNote]) -> Notes:
    return Notes(
        note=[Note(symbol=n.subject, kind=_NOTE_KINDS[n.kind], text=n.text) for n in notes]
    )


def chain_message(part: Part) -> Chain:
    """One message of the ``chain`` topic."""
    message = Chain(expiration=[expiration_message(i, e) for i, e in part.expirations])
    if part.head is not None:
        message.head.CopyFrom(head_message(part.head))
    if part.rows:
        message.rows.CopyFrom(rows_message(part.rows))
    if part.notes is not None:
        message.notes.CopyFrom(notes_message(part.notes))
    return message


# ---------------------------------------------------------------------- Arrow


def arrow_stream(snapshot: ExpirationSnapshot) -> bytes:
    """A whole expiration as an Arrow IPC stream: one row per listed strike, the columns of
    ``ChainRows`` with the sides as ``call_`` and ``put_`` columns. The head and the figures of
    the expiration travel as the schema's metadata, as text."""
    rows = snapshot.rows
    columns: dict[str, tuple[ColumnType, Sequence[float | str | bool | int]]] = {
        "strike": ("float64", [r.strike for r in rows]),
        "iv": ("float64", [r.iv for r in rows]),
    }
    for prefix, sides in (("call", [r.call for r in rows]), ("put", [r.put for r in rows])):
        side = _side_columns(sides)
        columns[f"{prefix}_symbol"] = ("string", side["symbol"])
        for name in _SIDE_NUMBERS:
            columns[f"{prefix}_{name}"] = ("float64", side[name])
        columns[f"{prefix}_quote_us"] = ("float64", side["quote_us"])
        columns[f"{prefix}_stale"] = ("bool", side["stale"])
        columns[f"{prefix}_source"] = ("string", side["source"])
    return ipc_stream(columns, _metadata(snapshot))


def _metadata(snapshot: ExpirationSnapshot) -> dict[str, str]:
    head, e = snapshot.head, snapshot.expiration

    def number(x: float | None) -> str:
        return "" if x is None or not math.isfinite(x) else repr(float(x))

    return {
        "underlying": head.underlying,
        "kind": head.kind or "",
        "spot": number(head.spot),
        "spot_source": head.spot_source,
        "data_time_us": number(micros(head.at)),
        "stale_after_s": number(head.stale_after_s),
        "earnings": _iso(head.earnings),
        "earnings_time": head.earnings_time or "",
        "key": e.key,
        "root": e.root,
        "date": e.expiration_date.isoformat(),
        "settlement": e.settlement,
        "settles_at_us": "" if e.settles_at is None else number(micros(e.settles_at)),
        "multiplier": number(e.multiplier),
        "standard": "true" if e.standard else "false",
        "forward": number(e.forward),
        "forward_source": e.forward_source,
        "atm_iv": number(e.atm_iv),
        "expected_move": number(e.expected_move),
        "expected_move_method": e.expected_move_method,
        "broker_iv": number(e.broker_iv),
        "ex_dividend": ",".join(d.ex_date.isoformat() for d in e.ex_dividends),
        "earnings_before": "true" if e.earnings_before else "false",
        "note": e.note or "",
    }
