"""The market metrics as a record (GEN-09; WP-1.1: the dividend fields, REF-02). The cases are
the shapes of the live documents (note B, addendum "WP-1.1"), with invented values."""

from datetime import date

import pytest

from s3t.broker.fake.market import SyntheticMarket
from s3t.broker.interface import BrokerProtocolError, DividendFacts, MarketMetrics
from s3t.broker.tastytrade.metrics import parse_dividend, parse_metrics

PAYER = {
    "symbol": "SPY",
    "annual-dividend-per-share": "0.0",
    "dividend-ex-date": "2026-09-18",
    "dividend-next-date": "2022-12-16",
    "dividend-pay-date": "2026-10-30",
    "dividend-rate-per-share": "1.83",
    "dividend-updated-at": "2026-11-27T12:03:27.357Z",
    "dividend-yield": "0.0109",
}


def test_a_payer_states_its_last_dividend() -> None:
    assert parse_metrics(PAYER) == MarketMetrics(
        "SPY", DividendFacts(date(2026, 9, 18), 1.83, date(2026, 10, 30))
    )


@pytest.mark.parametrize(
    "doc",
    [
        {"dividend-ex-date": "1970-01-01", "dividend-rate-per-share": "0.0"},  # TSLA, GLD
        {"dividend-rate-per-share": "0.0"},  # XSP: no dates at all
        {
            "dividend-ex-date": "2020-05-30",  # SPX: old dates, no amount
            "dividend-next-date": "2025-05-30",
            "dividend-pay-date": "2020-06-30",
            "dividend-rate-per-share": "0.0",
        },
        {"dividend-ex-date": "2026-09-18"},  # a date without an amount
        {"dividend-ex-date": None, "dividend-rate-per-share": "1.0"},
        {"dividend-ex-date": "1970-01-01", "dividend-rate-per-share": "1.0"},
        {"dividend-ex-date": "2026-09-18", "dividend-rate-per-share": "NaN"},
        {},
    ],
)
def test_no_dividend(doc: dict[str, object]) -> None:
    assert parse_dividend(doc) is None
    assert parse_metrics({"symbol": "X", **doc}) == MarketMetrics("X", None)


def test_the_next_date_is_not_read() -> None:
    """It held dates of 2022 for every payer in 2026."""
    doc = {**PAYER, "dividend-next-date": "not a date"}
    assert parse_metrics(doc).dividend == parse_metrics(PAYER).dividend


def test_a_pay_date_may_be_absent() -> None:
    doc = {k: v for k, v in PAYER.items() if k != "dividend-pay-date"}
    assert parse_dividend(doc) == DividendFacts(date(2026, 9, 18), 1.83, None)


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"dividend-ex-date": "18.09.2026"}, "dividend-ex-date is not a date"),
        ({"dividend-rate-per-share": "a lot"}, "dividend-rate-per-share is not a number"),
        ({"dividend-rate-per-share": []}, "dividend-rate-per-share is not a number"),
        ({"symbol": None}, "without a symbol"),
        ({"symbol": ""}, "without a symbol"),
    ],
)
def test_a_document_that_cannot_be_read(change: dict[str, object], message: str) -> None:
    with pytest.raises(BrokerProtocolError, match=message):
        parse_metrics({**PAYER, **change})


def test_the_fakes_documents_parse(market: SyntheticMarket) -> None:
    parsed = {s: parse_metrics(market.market_metrics(s) or {}) for s in ("SPY", "SPX", "AAPL")}
    assert parsed["SPY"].dividend == DividendFacts(date(2026, 9, 21), 1.75, date(2026, 10, 5))
    assert parsed["AAPL"].dividend == DividendFacts(date(2026, 9, 1), 0.26, date(2026, 9, 15))
    assert parsed["SPX"].dividend is None
