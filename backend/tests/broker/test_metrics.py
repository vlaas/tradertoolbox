"""The market metrics as a record (GEN-09; WP-1.1: the dividend fields, REF-02; WP-1.2: the
earnings and the IV per expiration, REF-03). The cases are the shapes of the live documents (note
B, addenda "WP-1.1" and "WP-1.2"), with invented values."""

from datetime import date, timedelta

import pytest

from s3t.broker.fake.market import SyntheticMarket
from s3t.broker.interface import (
    BrokerProtocolError,
    DividendFacts,
    EarningsFacts,
    ExpirationIV,
    MarketMetrics,
)
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


# ---------------------------------------------------------------------- WP-1.2: earnings, IVs
# The shapes of the 133 captured documents (note B, addendum "WP-1.2"), with invented values.

EARNINGS = {
    "visible": True,
    "expected-report-date": "2027-01-28",
    "estimated": False,
    "late-flag": 0,
    "quarter-end-date": "2026-12-31",
    "actual-eps": "2.4",
    "consensus-estimate": "2.35",
    "time-of-day": "AMC",
    "updated-at": "2026-11-12T13:01:02.003Z",
}


def test_the_earnings_of_a_stock() -> None:
    parsed = parse_metrics({**PAYER, "earnings": EARNINGS})
    assert parsed.earnings == EarningsFacts(date(2027, 1, 28), "after_close", False)


@pytest.mark.parametrize(
    ("change", "expected"),
    [
        ({"time-of-day": "BMO"}, EarningsFacts(date(2027, 1, 28), "before_open", False)),
        ({"time-of-day": None}, EarningsFacts(date(2027, 1, 28), None, False)),
        ({"time-of-day": "DURING"}, EarningsFacts(date(2027, 1, 28), None, False)),
        ({"estimated": "yes"}, EarningsFacts(date(2027, 1, 28), "after_close", None)),
        ({"expected-report-date": None}, None),  # ETFs and indexes: visible false, no date
    ],
)
def test_what_an_earnings_object_can_hold(
    change: dict[str, object], expected: EarningsFacts | None
) -> None:
    earnings = {k: v for k, v in {**EARNINGS, **change}.items() if v is not None}
    assert parse_metrics({**PAYER, "earnings": earnings}).earnings == expected


def test_a_past_report_date_is_kept_as_stated() -> None:
    """13 of the 133 documents held the report before; the reader decides what is ahead."""
    earnings = {**EARNINGS, "expected-report-date": "2026-10-30"}
    found = parse_metrics({**PAYER, "earnings": earnings}).earnings
    assert found is not None and found.report_date == date(2026, 10, 30)


def test_no_earnings() -> None:
    assert parse_metrics({**PAYER, "earnings": None}).earnings is None
    assert parse_metrics(PAYER).earnings is None
    with pytest.raises(BrokerProtocolError, match="earnings is not an object"):
        parse_metrics({**PAYER, "earnings": "2027-01-28"})
    with pytest.raises(BrokerProtocolError, match="expected-report-date is not a date"):
        parse_metrics({**PAYER, "earnings": {**EARNINGS, "expected-report-date": "soon"}})


def _iv(day: str, settlement: str, iv: object = "0.1734") -> dict[str, object]:
    entry: dict[str, object] = {
        "expiration-date": day,
        "settlement-type": settlement,
        "option-chain-type": "Standard",
    }
    if iv is not None:
        entry["implied-volatility"] = iv
    return entry


def test_the_iv_per_expiration() -> None:
    """Sorted by date, AM before PM: SPX and SPXW share a date and differ by settlement."""
    doc = {
        **PAYER,
        "option-expiration-implied-volatilities": [
            _iv("2026-12-18", "PM", "0.151"),
            _iv("2026-12-18", "AM", "0.149"),
            _iv("2026-11-30", "PM", "0.12"),
            _iv("2026-12-04", "PM", None),  # one of 4,288 lacked the value
            _iv("2026-12-11", "PM", "0"),
        ],
    }
    assert parse_metrics(doc).expiration_ivs == (
        ExpirationIV(date(2026, 11, 30), "pm", 0.12),
        ExpirationIV(date(2026, 12, 4), "pm", None),
        ExpirationIV(date(2026, 12, 11), "pm", None),
        ExpirationIV(date(2026, 12, 18), "am", 0.149),
        ExpirationIV(date(2026, 12, 18), "pm", 0.151),
    )


@pytest.mark.parametrize(
    ("ivs", "message"),
    [
        ({"a": 1}, "not a list"),
        (["0.15"], "not an object"),
        ([_iv("2026-12-18", "XX")], "without expiration-date or settlement-type"),
        ([{"settlement-type": "PM", "implied-volatility": "0.1"}], "without expiration-date"),
        ([_iv("2026-12-18", "PM", "high")], "implied-volatility is not a number"),
    ],
)
def test_an_iv_list_that_cannot_be_read(ivs: object, message: str) -> None:
    with pytest.raises(BrokerProtocolError, match=message):
        parse_metrics({**PAYER, "option-expiration-implied-volatilities": ivs})


def test_the_fakes_earnings_and_ivs(market: SyntheticMarket) -> None:
    aapl = parse_metrics(market.market_metrics("AAPL") or {})
    assert aapl.earnings == EarningsFacts(market.today + timedelta(days=9), "after_close", True)
    spx = parse_metrics(market.market_metrics("SPX") or {})
    assert spx.earnings is None
    settlements = {x.settlement for x in spx.expiration_ivs}
    assert settlements == {"am", "pm"}
    assert all(x.iv is not None and 0 < x.iv < 3 for x in spx.expiration_ivs)
    dates = [(x.expiration_date, x.settlement) for x in spx.expiration_ivs]
    assert dates == sorted(dates)
