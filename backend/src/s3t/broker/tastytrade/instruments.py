"""From Tastytrade's instrument and chain documents to the instrument model (MKT-01, GEN-09).

Every key of these documents is named here and nowhere else in the application. The records
are in ``s3t.broker.instruments``.

How a document is read:

- A key the model needs and the document lacks, or a value of the wrong form, is a
  :class:`BrokerProtocolError` that names the key and never the value.
- One expiration of a chain that cannot be read is left out and logged, so that one odd
  expiration does not take the chain away. A chain with expirations of which none can be read
  is an error.
- Numbers come as text (``"0.05"``) and become floats. A missing number is None.
- The broker's lists are not in order (the live futures list was not, note B, addendum
  "WP-0.3"). Strikes, expirations and futures are sorted here.

Facts the mapping rests on (note B, addendum "WP-0.5"):

- An equity chain states no exercise style and no ``expires-at``. Both are None.
- ``active`` is false for SPX and XSP, so it is not read.
- An index option's deliverable is of the type Cash. ``market-time-instrument-collection`` says
  "Cash Settled Equity Option" even for stock options, so it is not read.
- A futures option's dollars per point are ``notional-value / display-factor``: 50 on /ES, as its
  future says (live, 2026-11-23). Other products were not looked at. ``strike-factor`` is not
  understood: an expiration where it is not 1 is left out. On /ES it is 1 everywhere.
- A futures option read alone has a ``settlement-type`` that is neither AM nor PM (live,
  2026-11-23; the value itself was not looked at). Whether it settles in the morning is read
  from ``future-option-product.is-am-settled``, a key the live document has. In the chain the
  ``settlement-type`` is AM or PM.
"""

import logging
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, date, datetime
from typing import Any, Literal

from s3t.broker.instruments import (
    Exercise,
    Expiration,
    ExpirationKind,
    Family,
    Future,
    FutureListing,
    OptionChain,
    OptionContract,
    Right,
    Security,
    Settlement,
    TickRule,
)
from s3t.broker.interface import BrokerProtocolError

LOG = logging.getLogger(__name__)

type _Doc = Mapping[str, Any]

_SETTLEMENT: dict[str, Settlement] = {"AM": "am", "PM": "pm"}
_EXERCISE: dict[str, Exercise] = {"American": "american", "European": "european"}
_RIGHT: dict[str, Right] = {"C": "call", "P": "put"}
_KIND: dict[str, ExpirationKind] = {
    "Regular": "regular",
    "Weekly": "weekly",
    "Quarterly": "quarterly",
    "End-Of-Month": "end_of_month",
}
_STANDARD = "Standard"


# ---------------------------------------------------------------------- values


def _wrong(key: str, problem: Literal["missing", "unreadable"]) -> BrokerProtocolError:
    return BrokerProtocolError(f"{key} is {problem}")


def _text(doc: _Doc, key: str) -> str:
    value = doc.get(key)
    if not isinstance(value, str) or not value:
        raise _wrong(key, "missing")
    return value


def _optional_text(doc: _Doc, key: str) -> str | None:
    value = doc.get(key)
    return value if isinstance(value, str) and value else None


def _number(doc: _Doc, key: str) -> float:
    value = doc.get(key)
    if value is None:
        raise _wrong(key, "missing")
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise _wrong(key, "unreadable") from None
    if number - number != 0:  # NaN or infinite
        raise _wrong(key, "unreadable")
    return number


def _positive(doc: _Doc, key: str) -> float:
    number = _number(doc, key)
    if number <= 0:
        raise _wrong(key, "unreadable")
    return number


def _date(doc: _Doc, key: str) -> date:
    try:
        return date.fromisoformat(_text(doc, key))
    except ValueError:
        raise _wrong(key, "unreadable") from None


def _instant(doc: _Doc, key: str) -> datetime:
    try:
        when = datetime.fromisoformat(_text(doc, key))
    except ValueError:
        raise _wrong(key, "unreadable") from None
    if when.tzinfo is None:
        raise _wrong(key, "unreadable")  # an instant without a zone is not an instant
    return when.astimezone(UTC)


def _optional_instant(doc: _Doc, key: str) -> datetime | None:
    return None if doc.get(key) is None else _instant(doc, key)


def _one_of[T](table: Mapping[str, T], doc: _Doc, key: str) -> T:
    found = table.get(_text(doc, key))
    if found is None:
        raise _wrong(key, "unreadable")
    return found


def _docs(doc: _Doc, key: str) -> list[_Doc]:
    value = doc.get(key)
    if not isinstance(value, list) or not all(isinstance(x, dict) for x in value):
        raise _wrong(key, "missing" if value is None else "unreadable")
    return value


def _ticks(value: Any) -> TickRule | None:
    """A tick rule; None where the document has none or one that cannot be read."""
    if not isinstance(value, list) or not value:
        return None
    try:
        steps = [
            (
                _positive(step, "value"),
                None if step.get("threshold") is None else _number(step, "threshold"),
            )
            for step in value
        ]
        steps.sort(key=lambda s: (s[1] is None, s[1] or 0.0))
        return TickRule(tuple(steps))
    except (AttributeError, BrokerProtocolError, ValueError):
        LOG.warning("a tick rule of the broker could not be read and is left out")
        return None


def _reading[T](what: str, read: Callable[[], T]) -> T:
    """Name the document in the error of any key inside it."""
    try:
        return read()
    except BrokerProtocolError as e:
        raise BrokerProtocolError(f"{what}: {e}") from None


# ---------------------------------------------------------------------- instruments


def parse_security(doc: _Doc) -> Security:
    """The document of a stock, an ETF or an index."""

    def read() -> Security:
        kind = "index" if doc.get("is-index") else "etf" if doc.get("is-etf") else "stock"
        return Security(
            symbol=_text(doc, "symbol"),
            stream_symbol=_text(doc, "streamer-symbol"),
            kind=kind,
            description=_optional_text(doc, "description")
            or _optional_text(doc, "short-description")
            or "",
            ticks=_ticks(doc.get("tick-sizes")),
            option_ticks=_ticks(doc.get("option-tick-sizes")),
        )

    return _reading("equity", read)


def parse_future(doc: _Doc) -> Future:
    def read() -> Future:
        product = doc.get("future-product")
        product = product if isinstance(product, dict) else {}
        root = _optional_text(product, "root-symbol") or f"/{_text(doc, 'product-code')}"
        ticks = doc.get("tick-sizes") or [{"value": doc.get("tick-size")}]
        return Future(
            symbol=_text(doc, "symbol"),
            stream_symbol=_text(doc, "streamer-symbol"),
            root=root,
            description=_optional_text(product, "description") or "",
            multiplier=_positive(doc, "notional-multiplier"),
            ticks=_ticks(ticks),
            option_ticks=_ticks(doc.get("option-tick-sizes")),
            expiration_date=_date(doc, "expiration-date"),
            expires_at=_instant(doc, "expires-at"),
            stops_trading_at=_instant(doc, "stops-trading-at"),
            active_month=bool(doc.get("active-month")),
        )

    return _reading("future", read)


def _contract(
    doc: _Doc,
    family: Family,
    root_key: str,
    multiplier: float,
    standard: bool,
    settlement: Settlement,
) -> OptionContract:
    return OptionContract(
        symbol=_text(doc, "symbol"),
        stream_symbol=_text(doc, "streamer-symbol"),
        family=family,
        underlying=_text(doc, "underlying-symbol"),
        root=_text(doc, root_key),
        expiration_date=_date(doc, "expiration-date"),
        strike=_positive(doc, "strike-price"),
        right=_one_of(_RIGHT, doc, "option-type"),
        settlement=settlement,
        exercise=_one_of(_EXERCISE, doc, "exercise-style"),
        multiplier=multiplier,
        standard=standard,
        expires_at=_instant(doc, "expires-at"),
        stops_trading_at=_optional_instant(doc, "stops-trading-at"),
    )


def parse_equity_option(doc: _Doc) -> OptionContract:
    def read() -> OptionContract:
        multiplier = _positive(doc, "shares-per-contract")
        standard = doc.get("option-chain-type") == _STANDARD
        settlement = _one_of(_SETTLEMENT, doc, "settlement-type")
        return _contract(doc, "equity", "root-symbol", multiplier, standard, settlement)

    return _reading("equity option", read)


def parse_future_option(doc: _Doc) -> OptionContract:
    def read() -> OptionContract:
        multiplier = _futures_multiplier(doc)
        standard = bool(doc.get("is-vanilla", True))
        settlement = _futures_settlement(doc)
        return _contract(doc, "future", "option-root-symbol", multiplier, standard, settlement)

    return _reading("future option", read)


def _futures_settlement(doc: _Doc) -> Settlement:
    product = doc.get("future-option-product")
    morning = product.get("is-am-settled") if isinstance(product, dict) else None
    if isinstance(morning, bool):
        return "am" if morning else "pm"
    return _one_of(_SETTLEMENT, doc, "settlement-type")


def _futures_multiplier(doc: _Doc) -> float:
    """Dollars per point of a futures option. A strike factor other than 1 is not understood:
    better no contract than one with a wrong strike."""
    if doc.get("strike-factor") is not None and _number(doc, "strike-factor") != 1.0:
        raise BrokerProtocolError("strike-factor is not 1, which is not understood")
    return _positive(doc, "notional-value") / _positive(doc, "display-factor")


# ---------------------------------------------------------------------- chains


def _strike_columns(doc: _Doc) -> dict[str, tuple[Any, ...]]:
    rows = [
        (
            _positive(row, "strike-price"),
            _optional_text(row, "call"),
            _optional_text(row, "call-streamer-symbol"),
            _optional_text(row, "put"),
            _optional_text(row, "put-streamer-symbol"),
        )
        for row in _docs(doc, "strikes")
    ]
    rows.sort(key=lambda row: row[0])
    strikes, calls, call_streams, puts, put_streams = zip(*rows, strict=True) if rows else ((),) * 5
    return {
        "strikes": strikes,
        "calls": calls,
        "call_streams": call_streams,
        "puts": puts,
        "put_streams": put_streams,
    }


def _cash_settled(deliverables: Any) -> bool | None:
    kinds = (
        {d.get("deliverable-type") for d in deliverables if isinstance(d, dict)}
        if isinstance(deliverables, list)
        else set()
    )
    if not kinds:
        return None
    return kinds == {"Cash"}


def _expirations(
    what: str, docs: Sequence[_Doc], root_key: str | None, read: Callable[[_Doc], Expiration]
) -> list[Expiration]:
    """The expirations that can be read. The others are logged and left out."""
    found: list[Expiration] = []
    for doc in docs:
        try:
            found.append(read(doc))
        except BrokerProtocolError as e:
            root = doc.get(root_key) if root_key else None
            LOG.warning(
                "%s: the expiration %s%s is left out: %s",
                what,
                doc.get("expiration-date"),
                f" of {root}" if root else "",
                e,
            )
    return found


def _chain(
    what: str, underlying: str, futures: list[FutureListing], found: list[Expiration], listed: int
) -> OptionChain:
    if listed and not found:
        raise BrokerProtocolError(f"{what}: none of its expirations can be read")
    futures.sort(key=lambda f: f.expiration_date)
    found.sort(key=lambda e: (e.expiration_date, e.settlement, e.root))
    return OptionChain(underlying, tuple(futures), tuple(found))


def parse_option_chain(doc: _Doc, underlying: str) -> OptionChain:
    """The nested chain of a stock, an ETF or an index: every root, also the adjusted ones."""
    what = f"option chain {underlying}"
    found: list[Expiration] = []
    listed = 0
    for item in _reading(what, lambda: _docs(doc, "items")):

        def read(e: _Doc, item: _Doc = item) -> Expiration:
            return Expiration(
                family="equity",
                underlying=_text(item, "underlying-symbol"),
                root=_text(item, "root-symbol"),
                expiration_date=_date(e, "expiration-date"),
                kind=_KIND.get(_optional_text(e, "expiration-type") or "", "other"),
                settlement=_one_of(_SETTLEMENT, e, "settlement-type"),
                exercise=None,
                cash_settled=_cash_settled(item.get("deliverables")),
                multiplier=_positive(item, "shares-per-contract"),
                standard=item.get("option-chain-type") == _STANDARD,
                ticks=_ticks(item.get("tick-sizes")),
                expires_at=None,
                stops_trading_at=None,
                **_strike_columns(e),
            )

        docs = _reading(what, lambda item=item: _docs(item, "expirations"))
        listed += len(docs)
        found += _expirations(what, docs, None, read)
    return _chain(what, underlying, [], found, listed)


def parse_futures_option_chain(doc: _Doc, root: str) -> OptionChain:
    """The nested chain of a futures product: its futures and the options on each of them."""
    what = f"futures option chain {root}"

    def listing(f: _Doc) -> FutureListing:
        return FutureListing(
            symbol=_text(f, "symbol"),
            stream_symbol=_text(f, "streamer-symbol"),
            root=_text(f, "root-symbol"),
            expiration_date=_date(f, "expiration-date"),
            expires_at=_instant(f, "expires-at"),
            stops_trading_at=_instant(f, "stops-trading-at"),
            active_month=bool(f.get("active-month")),
        )

    futures = _reading(what, lambda: [listing(f) for f in _docs(doc, "futures")])
    found: list[Expiration] = []
    listed = 0
    for item in _reading(what, lambda: _docs(doc, "option-chains")):

        def read(e: _Doc, item: _Doc = item) -> Expiration:
            cash = e.get("cash-settled")
            return Expiration(
                family="future",
                underlying=_text(e, "underlying-symbol"),
                root=_text(e, "option-root-symbol"),
                expiration_date=_date(e, "expiration-date"),
                kind=_KIND.get(_optional_text(e, "expiration-type") or "", "other"),
                settlement=_one_of(_SETTLEMENT, e, "settlement-type"),
                exercise=_one_of(_EXERCISE, item, "exercise-style"),
                cash_settled=cash if isinstance(cash, bool) else None,
                multiplier=_futures_multiplier(e),
                standard=True,
                ticks=_ticks(e.get("tick-sizes")),
                expires_at=_instant(e, "expires-at"),
                stops_trading_at=_instant(e, "stops-trading-at"),
                **_strike_columns(e),
            )

        docs = _reading(what, lambda item=item: _docs(item, "expirations"))
        listed += len(docs)
        found += _expirations(what, docs, "option-root-symbol", read)
    return _chain(what, root, futures, found, listed)
