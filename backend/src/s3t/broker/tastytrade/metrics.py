"""The market metrics of Tastytrade as a record of the interface (GEN-09; WP-1.1: dividends).

The keys of the broker's document stay in this file. What the document states of dividends,
read from 133 documents of nine symbols that ``tt`` captured from 2026-11-11 to 2026-11-19 and
from the two of the recording of 2026-11-27 (note B, addendum "WP-1.1"):

- ``dividend-ex-date`` is the latest ex-date the broker knows. For every payer it was a past
  date, the last dividend. A symbol that pays none has ``1970-01-01``, or no key at all (XSP).
- ``dividend-rate-per-share`` is the cash of that payment, as text; ``0.0`` for a non-payer and
  for an index. SPX carries dates of 2020 with a rate of zero.
- ``dividend-pay-date`` belongs to the same payment.
- ``dividend-next-date`` held dates of 2022 for every payer and is not read.
- ``annual-dividend-per-share`` was ``0.0`` for every symbol and is not read. There is no key
  for the payment frequency.

What it states of earnings and of the IV per expiration, read from the same 133 documents (note
B, addendum "WP-1.2"; types and counts, no values):

- ``earnings`` is an object in 119 and null in 14. Its ``expected-report-date`` is there in 23:
  a coming date in 10, a past one in 13. ``time-of-day`` is ``AMC`` in 18 and absent in the
  rest; ``BMO`` is the other text the platform uses (not seen). ``estimated`` is a boolean.
  ``visible``, ``late-flag``, ``quarter-end-date``, ``actual-eps`` and ``consensus-estimate``
  are not read.
- ``option-expiration-implied-volatilities`` is a list with one entry per expiration and
  settlement type of every root (4,288 entries, none twice per document). An entry has
  ``expiration-date``, ``settlement-type`` (``AM`` or ``PM``), ``option-chain-type`` and
  ``implied-volatility``, a fraction as text; one entry lacked the value. There is no root key.

Not typed yet: everything else in the document.
"""

from datetime import date
from typing import Any

from s3t.broker.interface import (
    BrokerProtocolError,
    DividendFacts,
    EarningsFacts,
    ExpirationIV,
    JsonObject,
    MarketMetrics,
    TimeOfDay,
)

NO_DATE = date(1970, 1, 1)
"""What the broker writes where it knows no ex-date."""


def _date(doc: JsonObject, key: str) -> date | None:
    value = doc.get(key)
    if value is None or value == "":
        return None
    try:
        day = date.fromisoformat(str(value))
    except ValueError:
        raise BrokerProtocolError(f"market metrics: {key} is not a date") from None
    return None if day <= NO_DATE else day


def _number(doc: JsonObject, key: str) -> float | None:
    value: Any = doc.get(key)
    if value is None or value == "":
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise BrokerProtocolError(f"market metrics: {key} is not a number") from None
    return number if number == number else None


def parse_dividend(doc: JsonObject) -> DividendFacts | None:
    """The payment the document states; None without an ex-date or without an amount above
    zero."""
    ex_date = _date(doc, "dividend-ex-date")
    amount = _number(doc, "dividend-rate-per-share")
    if ex_date is None or amount is None or amount <= 0:
        return None
    return DividendFacts(ex_date, amount, _date(doc, "dividend-pay-date"))


_TIME_OF_DAY: dict[str, TimeOfDay] = {"BMO": "before_open", "AMC": "after_close"}
_SETTLEMENT = {"AM": "am", "PM": "pm"}


def parse_earnings(doc: JsonObject) -> EarningsFacts | None:
    """The report date the document states; None without one. A time of day that is not known
    is None: an unexpected text does not cost the dividends of the same document."""
    earnings = doc.get("earnings")
    if earnings is None:
        return None
    if not isinstance(earnings, dict):
        raise BrokerProtocolError("market metrics: earnings is not an object")
    report = _date(earnings, "expected-report-date")
    if report is None:
        return None
    estimated = earnings.get("estimated")
    return EarningsFacts(
        report,
        _TIME_OF_DAY.get(str(earnings.get("time-of-day"))),
        estimated if isinstance(estimated, bool) else None,
    )


def parse_expiration_ivs(doc: JsonObject) -> tuple[ExpirationIV, ...]:
    entries = doc.get("option-expiration-implied-volatilities")
    if entries is None:
        return ()
    if not isinstance(entries, list):
        raise BrokerProtocolError("market metrics: the IVs per expiration are not a list")
    found: list[ExpirationIV] = []
    for entry in entries:
        if not isinstance(entry, dict):
            raise BrokerProtocolError("market metrics: an IV per expiration is not an object")
        day = _date(entry, "expiration-date")
        settlement = _SETTLEMENT.get(str(entry.get("settlement-type")))
        if day is None or settlement is None:
            raise BrokerProtocolError(
                "market metrics: an IV per expiration without expiration-date or settlement-type"
            )
        iv = _number(entry, "implied-volatility")
        iv = iv if iv is not None and iv > 0 else None
        found.append(ExpirationIV(day, "am" if settlement == "am" else "pm", iv))
    return tuple(sorted(found, key=lambda x: (x.expiration_date, x.settlement)))


def parse_metrics(doc: JsonObject) -> MarketMetrics:
    symbol = doc.get("symbol")
    if not isinstance(symbol, str) or not symbol:
        raise BrokerProtocolError("market metrics: a document without a symbol")
    return MarketMetrics(
        symbol, parse_dividend(doc), parse_earnings(doc), parse_expiration_ivs(doc)
    )
