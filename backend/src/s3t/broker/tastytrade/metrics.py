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

Not typed yet: everything else in the document (WP-1.2 takes the earnings and the IV per
expiration).
"""

from datetime import date
from typing import Any

from s3t.broker.interface import BrokerProtocolError, DividendFacts, JsonObject, MarketMetrics

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


def parse_metrics(doc: JsonObject) -> MarketMetrics:
    symbol = doc.get("symbol")
    if not isinstance(symbol, str) or not symbol:
        raise BrokerProtocolError("market metrics: a document without a symbol")
    return MarketMetrics(symbol, parse_dividend(doc))
