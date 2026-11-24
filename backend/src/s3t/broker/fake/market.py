"""The synthetic market: instruments, option chains and market metrics, made up, not recorded
(GEN-07; implementation plan 3.2).

Every value comes from code and two inputs: the as-of instant and a seed. The same inputs give
the same documents. The shapes follow Tastytrade's REST responses. For equities, equity options,
equity chains and metrics, they come from `tt` tests/fake_tt.py, which was compared with live
answers in 2026-11. The futures shapes follow the published API. All were compared key by key with
the live API on 2026-11-22 (`tools/broker_check.py`; note B, addendum of that date). The fake
holds no key that the live documents lack. The live documents hold more keys than the fake,
and the fake adds them when a package needs them.

Contents, decided 2026-11-22 (WP-0.3). The tickers are real and the values invented, so the
app marks every screen as synthetic when it runs on this market (GEN-11):

- SPY: an ETF. PM-settled options with `expires-at` at 16:15 ET, as `tt` saw it live.
- SPX: an index. SPXW PM-settled dailies, weeklies and monthlies, SPX AM-settled monthlies,
  and SPXO AM-settled weeklies (one expiration here, two live on 2026-11-23), all European.
- AAPL: a stock with an earnings date and a dividend. Its numbers are synthetic, like
  everything here.
- /ES: three quarterly futures with standard (AM) and weekly (PM) options.

Expiration rules: every trading day up to 14 days ahead, every Friday up to 63 days, the third
Friday of each month up to a year, and the December third Friday two years ahead (a LEAPS).
SPX AM has the third Fridays only; AAPL has no dailies. The last trading day of a month is an
end-of-month expiration where it is listed.

Holidays and early closes are those of the exchange calendar (``s3t.refdata.calendar``; WP-0.6).
A Friday without a session gives its expiration to the trading day before, as live: the SPY weekly
of Christmas week 2026 is on Thursday (note B, addendum "WP-0.3"). `expires-at` follows the close
of the day. Assumed: a future whose third Friday has no session ends the trading day before, and
the weekly options of /ES expire at 15:00 Chicago on an early close too.

Quotes, Greeks, candles and the seeded paths are in ``ticks.py``, and the stream that serves
them in ``stream.py`` (WP-0.4).
"""

import math
import random
import re
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from typing import Any, Literal
from zoneinfo import ZoneInfo

from s3t.refdata.calendar import ExchangeCalendar, load_calendar

NY = ZoneInfo("America/New_York")
CHICAGO = ZoneInfo("America/Chicago")

MONTH_CODES = "FGHJKMNQUVXZ"
DXLINK_URL = "wss://dxlink.fake.invalid/realtime"
"""The address the fake stream answers to, in memory. `.invalid` never resolves."""

type Doc = dict[str, Any]

_DAY = timedelta(days=1)


@dataclass(frozen=True)
class Root:
    symbol: str
    settlement: Literal["AM", "PM"]
    dailies: bool
    weeklies: bool
    monthlies_only: bool = False
    only_first: bool = False
    """The root lists its first expiration only."""


@dataclass(frozen=True)
class Underlying:
    symbol: str
    kind: Literal["etf", "index", "stock"]
    description: str
    spot: float
    roots: tuple[Root, ...]
    near_step: float
    far_step: float
    atm_vol: float
    pm_after_close: timedelta = timedelta(minutes=15)
    """When the broker says a PM-settled option expires, after the close of the session. Live it
    is 16:15 for every stock and ETF option, 13:15 on an early close, and 16:00 for index options
    (note B, addendum "WP-0.5")."""
    tick_sizes: tuple[Doc, ...] = ({"value": "0.01"},)
    dividend: float = 0.0
    """Annual dividend per share."""
    earnings_in_days: int | None = None


UNDERLYINGS: dict[str, Underlying] = {
    u.symbol: u
    for u in (
        Underlying(
            "SPY",
            "etf",
            "SPDR S&P 500 ETF TRUST (synthetic)",
            600.0,
            (Root("SPY", "PM", dailies=True, weeklies=True),),
            near_step=1.0,
            far_step=5.0,
            atm_vol=0.14,
            dividend=7.0,
        ),
        Underlying(
            "SPX",
            "index",
            "S&P 500 INDEX (synthetic)",
            6000.0,
            (
                Root("SPXW", "PM", dailies=True, weeklies=True),
                Root("SPXO", "AM", dailies=False, weeklies=True, only_first=True),
                Root("SPX", "AM", dailies=False, weeklies=False, monthlies_only=True),
            ),
            near_step=5.0,
            far_step=25.0,
            atm_vol=0.135,
            pm_after_close=timedelta(0),
            tick_sizes=({"value": "0.05", "threshold": "3.0"}, {"value": "0.1"}),
        ),
        Underlying(
            "AAPL",
            "stock",
            "APPLE INC (synthetic)",
            200.0,
            (Root("AAPL", "PM", dailies=False, weeklies=True),),
            near_step=2.5,
            far_step=10.0,
            atm_vol=0.26,
            tick_sizes=({"threshold": "3.0", "value": "0.01"}, {"value": "0.05"}),
            dividend=1.04,
            earnings_in_days=9,
        ),
    )
}

FUTURE_PRODUCT = "ES"
FUTURE_SPOT = 6030.0
FUTURE_ATM_VOL = 0.14
FUTURE_QUARTERS = 3


def third_friday(year: int, month: int) -> date:
    d = date(year, month, 15)
    return d + timedelta(days=(4 - d.weekday()) % 7)


def strike_text(strike: float) -> str:
    """A strike as the streamer symbol writes it: no trailing zeros, no trailing point."""
    return f"{strike:.3f}".rstrip("0").rstrip(".")


def occ_symbol(root: str, expiration: date, option_type: str, strike: float) -> str:
    """OCC: root padded to six, yymmdd, C or P, strike times 1000 in eight digits."""
    return f"{root:<6}{expiration:%y%m%d}{option_type}{round(strike * 1000):08d}"


def streamer_symbol(root: str, expiration: date, option_type: str, strike: float) -> str:
    return f".{root}{expiration:%y%m%d}{option_type}{strike_text(strike)}"


_OCC = re.compile(r"(.{6})(\d{6})([CP])(\d{8})")


def parse_occ(symbol: str) -> tuple[str, date, str, float] | None:
    m = _OCC.fullmatch(symbol)
    if not m:
        return None
    try:
        expiration = datetime.strptime(m.group(2), "%y%m%d").date()
    except ValueError:
        return None
    return m.group(1).rstrip(), expiration, m.group(3), int(m.group(4)) / 1000


def _iso(instant: datetime) -> str:
    # As Tastytrade writes it (seen 2026-11-22): 2026-12-24T18:15:00.000+00:00
    return instant.astimezone(UTC).isoformat(timespec="milliseconds")


def _dec(x: float, places: int = 2) -> str:
    return f"{x:.{places}f}"


class SyntheticMarket:
    """The synthetic market at one instant. Documents are built on first use and cached.

    Without a ``calendar`` it loads the exchange calendar, which takes its time once in a
    process."""

    def __init__(
        self, asof: datetime, seed: int = 1, calendar: ExchangeCalendar | None = None
    ) -> None:
        if asof.tzinfo is None:
            raise ValueError("asof must be an aware instant")
        self.asof = asof.astimezone(UTC)
        self.seed = seed
        self.today = self.asof.astimezone(NY).date()
        self.calendar = calendar or load_calendar(self.today)
        self._cache: dict[tuple[str, str], Doc | None] = {}
        self._expirations: dict[str, list[tuple[date, str]]] = {}

    def _cached(self, kind: str, key: str, build: Any) -> Doc | None:
        if (kind, key) not in self._cache:
            self._cache[(kind, key)] = build()
        return self._cache[(kind, key)]

    def _rng(self, *parts: object) -> random.Random:
        return random.Random(":".join(str(p) for p in (self.seed, *parts)))

    # ------------------------------------------------------------------ calendar

    def expirations(self, root: Root) -> list[tuple[date, str]]:
        """The expiration dates of a root with their expiration type."""
        if root.symbol not in self._expirations:
            self._expirations[root.symbol] = self._list_expirations(root)
        return self._expirations[root.symbol]

    def _traded(self, nominal: date) -> date:
        """A day, or the trading day before it when the market is closed on it."""
        trading = self.calendar.is_trading_day(nominal)
        return nominal if trading else self.calendar.previous_trading_day(nominal)

    def _list_expirations(self, root: Root) -> list[tuple[date, str]]:
        found: dict[date, str] = {}
        d = self.today
        month_ends = {
            self._traded(date(d.year + (d.month + n) // 12, (d.month + n) % 12 + 1, 1) - _DAY)
            for n in range(4)
        }
        end = date(self.today.year + 2, 12, 31)
        while d <= end:
            days = (d - self.today).days
            day = self._traded(d)
            monthly = d == third_friday(d.year, d.month)
            if monthly and (days <= 366 or (d.month == 12 and d.year == self.today.year + 2)):
                found[day] = "Regular"
            elif not root.monthlies_only and (
                (root.dailies and days <= 14 and day == d)
                or (root.weeklies and d.weekday() == 4 and days <= 63)
            ):
                found.setdefault(day, "End-Of-Month" if day in month_ends else "Weekly")
            d += _DAY
        # A Friday without a session can have moved to a day that is over.
        listed = sorted((day, kind) for day, kind in found.items() if day >= self.today)
        return listed[:1] if root.only_first else listed

    def strikes(self, u: Underlying, expiration: date) -> list[float]:
        spot, near, far = u.spot, u.near_step, u.far_step
        ks: set[float] = set()
        k = math.floor(spot * 0.7 / far) * far
        while k <= spot * 1.3:
            ks.add(round(k, 3))
            k += far
        k = math.floor(spot * 0.9 / near) * near
        while k <= spot * 1.1:
            ks.add(round(k, 3))
            k += near
        if u.kind == "etf" and (expiration - self.today).days <= 14:
            k = math.floor(spot * 0.98)
            while k <= spot * 1.02:
                ks.add(round(k + 0.5, 3))
                k += 1.0
        return sorted(ks)

    def _expires_at(self, u: Underlying, root: Root, expiration: date) -> datetime:
        opens, closes = self.session(expiration)
        return opens if root.settlement == "AM" else closes + u.pm_after_close

    def _stops_trading_at(self, u: Underlying, root: Root, expiration: date) -> datetime:
        """An AM-settled option trades until the afternoon before (live, 2026-11-23: 16:15 for
        SPX)."""
        if root.settlement == "PM":
            return self._expires_at(u, root, expiration)
        before = self.calendar.previous_trading_day(expiration)
        return self.session(before)[1] + timedelta(minutes=15)

    def session(self, day: date) -> tuple[datetime, datetime]:
        """The open and the close of a trading day, in UTC."""
        found = self.calendar.session(day)
        assert found is not None, f"{day} is not a trading day"
        return found

    def atm_vol(self, base: float, expiration: date) -> float:
        """The fixed term structure: rising from the base by up to two points."""
        days = max((expiration - self.today).days, 0)
        return base + 0.02 * (1 - math.exp(-days / 90))

    # ------------------------------------------------------------------ equities

    def equity(self, symbol: str) -> Doc | None:
        u = UNDERLYINGS.get(symbol)
        if u is None:
            return None
        doc: Doc = {
            "id": 1000 + sorted(UNDERLYINGS).index(symbol),
            "symbol": u.symbol,
            "instrument-type": "Equity",
            "short-description": u.description,
            "description": u.description,
            "is-index": u.kind == "index",
            "is-etf": u.kind == "etf",
            "listed-market": "INDX" if u.kind == "index" else "ARCX",
            "lendability": "Easy To Borrow",
            "borrow-rate": "0.0",
            "market-time-instrument-collection": "Equity Index" if u.kind == "index" else "Equity",
            "instrument-sub-type": {"index": "INDEX", "etf": "ETF"}.get(u.kind),
            "is-closing-only": False,
            "is-options-closing-only": False,
            "active": u.kind != "index",  # live: SPX and XSP are inactive
            "is-fractional-quantity-eligible": u.kind != "index",
            "is-illiquid": False,
            "streamer-symbol": u.symbol,
            "option-tick-sizes": list(u.tick_sizes),
        }
        if u.kind != "index":  # live, 2026-11-22: SPX has no tick sizes of its own
            doc["tick-sizes"] = [{"value": "0.0001", "threshold": "1.0"}, {"value": "0.01"}]
        return doc

    def option_chain(self, underlying: str) -> Doc | None:
        return self._cached("chain", underlying, lambda: self._option_chain(underlying))

    def _option_chain(self, underlying: str) -> Doc | None:
        u = UNDERLYINGS.get(underlying)
        if u is None:
            return None
        items = []
        for root in u.roots:
            expirations = []
            for exp, exp_type in self.expirations(root):
                strikes = [
                    {
                        "strike-price": _dec(k, 1),
                        "call": occ_symbol(root.symbol, exp, "C", k),
                        "call-streamer-symbol": streamer_symbol(root.symbol, exp, "C", k),
                        "put": occ_symbol(root.symbol, exp, "P", k),
                        "put-streamer-symbol": streamer_symbol(root.symbol, exp, "P", k),
                    }
                    for k in self.strikes(u, exp)
                ]
                expirations.append(
                    {
                        "expiration-type": exp_type,
                        "expiration-date": exp.isoformat(),
                        "days-to-expiration": (exp - self.today).days,
                        "settlement-type": root.settlement,
                        "strikes": strikes,
                    }
                )
            items.append(
                {
                    "underlying-symbol": u.symbol,
                    "root-symbol": root.symbol,
                    "option-chain-type": "Standard",
                    "shares-per-contract": 100,
                    "tick-sizes": list(u.tick_sizes),
                    "deliverables": [
                        {
                            "id": 1,
                            "root-symbol": root.symbol,
                            "deliverable-type": "Shares",
                            "description": f"100 shares of {u.symbol}",
                            "amount": "100.0",
                            "symbol": u.symbol,
                            "instrument-type": "Equity",
                            "percent": "100",
                        }
                        if u.kind != "index"
                        else {
                            "id": 1,
                            "root-symbol": root.symbol,
                            "deliverable-type": "Cash",
                            "description": "0.0 cash",
                            "amount": "0.0",
                            "percent": "100",
                        }
                    ],
                    "expirations": expirations,
                }
            )
        return {"items": items}

    def equity_option(self, symbol: str) -> Doc | None:
        parsed = parse_occ(symbol)
        if parsed is None:
            return None
        root_symbol, exp, option_type, strike = parsed
        for u in UNDERLYINGS.values():
            for root in u.roots:
                if root.symbol != root_symbol:
                    continue
                types = dict(self.expirations(root))
                if exp not in types or strike not in self.strikes(u, exp):
                    return None
                expires = self._expires_at(u, root, exp)
                return {
                    "symbol": symbol,
                    "instrument-type": "Equity Option",
                    "active": True,
                    "strike-price": _dec(strike, 1),
                    "root-symbol": root.symbol,
                    "underlying-symbol": u.symbol,
                    "expiration-date": exp.isoformat(),
                    "exercise-style": "European" if u.kind == "index" else "American",
                    "shares-per-contract": 100,
                    "option-type": option_type,
                    "option-chain-type": "Standard",
                    "expiration-type": types[exp],
                    "settlement-type": root.settlement,
                    "stops-trading-at": _iso(self._stops_trading_at(u, root, exp)),
                    "market-time-instrument-collection": "Cash Settled Equity Option",
                    "days-to-expiration": (exp - self.today).days,
                    "expires-at": _iso(expires),
                    "is-closing-only": False,
                    "streamer-symbol": streamer_symbol(root.symbol, exp, option_type, strike),
                }
        return None

    # ------------------------------------------------------------------ futures

    def _futures(self) -> list[tuple[str, date]]:
        """The next quarterly ES contracts: symbol and last trading day (the third Friday, or the
        trading day before it)."""
        out: list[tuple[str, date]] = []
        year, month = self.today.year, self.today.month
        while len(out) < FUTURE_QUARTERS:
            if month in (3, 6, 9, 12):
                last = self._traded(third_friday(year, month))
                if last >= self.today:
                    out.append((f"/{FUTURE_PRODUCT}{MONTH_CODES[month - 1]}{year % 10}", last))
            month += 1
            if month == 13:
                year, month = year + 1, 1
        return out

    @staticmethod
    def future_streamer(symbol: str, last: date) -> str:
        return f"{symbol[:-1]}{last:%y}:XCME"

    def future(self, symbol: str) -> Doc | None:
        symbol = symbol if symbol.startswith("/") else f"/{symbol}"
        futures = self._futures()
        for i, (sym, last) in enumerate(futures):
            if sym != symbol:
                continue
            expires = datetime.combine(last, time(8, 30), CHICAGO)
            return {
                "symbol": sym,
                "product-code": FUTURE_PRODUCT,
                "contract-size": "50.0",
                "tick-size": "0.25",
                "notional-multiplier": "50.0",
                "display-factor": "0.01",
                "last-trade-date": last.isoformat(),
                "expiration-date": last.isoformat(),
                "closing-only-date": last.isoformat(),
                "active": True,
                "active-month": i == 0,
                "next-active-month": i == 1,
                "is-closing-only": False,
                "stops-trading-at": _iso(expires),
                "expires-at": _iso(expires),
                "product-group": "CME_ES",
                "exchange": "CME",
                "exchange-symbol": sym[1:],
                "main-fraction": "0.0",
                "sub-fraction": "0.0",
                "streamer-exchange-code": "XCME",
                "streamer-symbol": self.future_streamer(sym, last),
                "is-tradeable": True,
                "future-product": {
                    "root-symbol": f"/{FUTURE_PRODUCT}",
                    "code": FUTURE_PRODUCT,
                    "description": "E-Mini S&P 500 (synthetic)",
                    "exchange": "CME",
                    "product-type": "Financial",
                    "listed-months": ["H", "M", "U", "Z"],
                    "active-months": ["H", "M", "U", "Z"],
                    "notional-multiplier": "50.0",
                    "tick-size": "0.25",
                    "display-factor": "0.01",
                    "streamer-exchange-code": "XCME",
                    "cash-settled": True,
                    "market-sector": "Equity Index",
                },
                "tick-sizes": [{"value": "0.25"}],
                "option-tick-sizes": [{"value": "0.05", "threshold": "10.0"}, {"value": "0.25"}],
            }
        return None

    def _future_option_expirations(self) -> list[Doc]:
        """Per future: the standard quarterly (AM) and Friday weeklies (PM) before it."""
        out: list[Doc] = []
        futures = self._futures()
        previous = self.today - timedelta(days=1)
        for fut, last in futures:
            fut_month = fut[-2:]
            d = previous + timedelta(days=1)
            while d <= last:
                days = (d - self.today).days
                quarterly = d.month % 3 == 0 and d == third_friday(d.year, d.month)
                day = d if d == last else self._traded(d)
                if d == last:
                    root, contract, settlement, at = "ES", f"ES{fut_month}", "AM", time(8, 30)
                    exp_type = "Regular"
                elif d.weekday() == 4 and days <= 63 and day >= self.today and not quarterly:
                    # The name is that of the Friday, also when it expires the day before.
                    week = (d.day - 1) // 7 + 1
                    month = f"{MONTH_CODES[d.month - 1]}{d.year % 10}"
                    root, contract, settlement, at = f"EW{week}", f"EW{week}{month}", "PM", time(15)
                    exp_type = "Weekly"
                else:
                    d += timedelta(days=1)
                    continue
                out.append(
                    {
                        "future": fut,
                        "future_last": last,
                        "root": root,
                        "contract": contract,
                        "date": day,
                        "type": exp_type,
                        "settlement": settlement,
                        "expires": datetime.combine(day, at, CHICAGO),
                    }
                )
                d += timedelta(days=1)
            previous = last
        return out

    def _future_strikes(self) -> list[float]:
        spot, ks = FUTURE_SPOT, set()
        k = math.floor(spot * 0.8 / 25) * 25
        while k <= spot * 1.2:
            ks.add(float(k))
            k += 25
        k = math.floor(spot * 0.95 / 5) * 5
        while k <= spot * 1.05:
            ks.add(float(k))
            k += 5
        return sorted(ks)

    @staticmethod
    def future_option_symbol(e: Doc, option_type: str, strike: float) -> str:
        future, contract, day = e["future"][1:], e["contract"], f"{e['date']:%y%m%d}"
        return f"./{future} {contract} {day}{option_type}{strike_text(strike)}"

    @staticmethod
    def future_option_streamer(e: Doc, option_type: str, strike: float) -> str:
        code = e["contract"]
        return f"./{code[:-1]}{e['date']:%y}{option_type}{strike_text(strike)}:XCME"

    def futures_option_chain(self, root: str) -> Doc | None:
        return self._cached("fchain", root, lambda: self._futures_option_chain(root))

    def _futures_option_chain(self, root: str) -> Doc | None:
        if root.removeprefix("/") != FUTURE_PRODUCT:
            return None
        futures = []
        for i, (sym, last) in enumerate(self._futures()):
            expires = datetime.combine(last, time(8, 30), CHICAGO)
            futures.append(
                {
                    "symbol": sym,
                    "root-symbol": f"/{FUTURE_PRODUCT}",
                    "expiration-date": last.isoformat(),
                    "days-to-expiration": (last - self.today).days,
                    "active-month": i == 0,
                    "next-active-month": i == 1,
                    "stops-trading-at": _iso(expires),
                    "expires-at": _iso(expires),
                    "streamer-symbol": self.future_streamer(sym, last),
                }
            )
        strikes = self._future_strikes()
        expirations = [
            {
                "underlying-symbol": e["future"],
                "root-symbol": f"/{FUTURE_PRODUCT}",
                "option-root-symbol": e["root"],
                "option-contract-symbol": e["contract"],
                "asset": FUTURE_PRODUCT,
                "expiration-date": e["date"].isoformat(),
                "days-to-expiration": (e["date"] - self.today).days,
                "expiration-type": e["type"],
                "settlement-type": e["settlement"],
                "notional-value": "0.5",
                "display-factor": "0.01",
                "strike-factor": "1.0",
                "cash-settled": False,
                "stops-trading-at": _iso(e["expires"]),
                "expires-at": _iso(e["expires"]),
                "tick-sizes": [{"value": "0.05", "threshold": "10.0"}, {"value": "0.25"}],
                "strikes": [
                    {
                        "strike-price": _dec(k, 1),
                        "call": self.future_option_symbol(e, "C", k),
                        "call-streamer-symbol": self.future_option_streamer(e, "C", k),
                        "put": self.future_option_symbol(e, "P", k),
                        "put-streamer-symbol": self.future_option_streamer(e, "P", k),
                    }
                    for k in strikes
                ],
            }
            for e in self._future_option_expirations()
        ]
        return {
            "futures": futures[::-1],  # live, 2026-11-22: not in date order
            "option-chains": [
                {
                    "underlying-symbol": f"/{FUTURE_PRODUCT}",
                    "root-symbol": f"/{FUTURE_PRODUCT}",
                    "exercise-style": "American",
                    "expirations": expirations,
                }
            ],
        }

    def future_option(self, symbol: str) -> Doc | None:
        m = re.fullmatch(r"\./(\S+) (\S+) (\d{6})([CP])([\d.]+)", symbol)
        if not m:
            return None
        for e in self._future_option_expirations():
            if (
                e["future"][1:] != m.group(1)
                or e["contract"] != m.group(2)
                or f"{e['date']:%y%m%d}" != m.group(3)
            ):
                continue
            strike, option_type = float(m.group(5)), m.group(4)
            if strike not in self._future_strikes():
                return None
            return {
                "symbol": symbol,
                "underlying-symbol": e["future"],
                "product-code": FUTURE_PRODUCT,
                "expiration-date": e["date"].isoformat(),
                "root-symbol": f"/{FUTURE_PRODUCT}",
                "option-root-symbol": e["root"],
                "strike-price": _dec(strike, 1),
                "exchange": "CME",
                "exchange-symbol": f"{e['contract']} {option_type}{strike_text(strike)}",
                "streamer-symbol": self.future_option_streamer(e, option_type, strike),
                "option-type": option_type,
                "exercise-style": "American",
                "is-vanilla": True,
                "is-primary-deliverable": True,
                "future-price-ratio": "1.0",
                "multiplier": "1.0",
                "underlying-count": "1.0",
                "is-confirmed": True,
                "notional-value": "0.5",
                "display-factor": "0.01",
                # As seen live, 2026-11-24: neither AM nor PM.
                "settlement-type": "Future",
                "strike-factor": "1.0",
                "maturity-date": e["date"].isoformat(),
                "is-exercisable-weekly": True,
                "last-trade-time": "0",
                "days-to-expiration": (e["date"] - self.today).days,
                "is-closing-only": False,
                "active": True,
                "stops-trading-at": _iso(e["expires"]),
                "expires-at": _iso(e["expires"]),
                "future-option-product": {
                    "root-symbol": e["root"],
                    "exchange": "CME",
                    "settlement-delay-days": 0,
                    "market-sector": "Equity Index",
                    "clearing-price-multiplier": "1.0",
                    "is-am-settled": e["settlement"] == "AM",
                    "is-rollover": False,
                },
            }
        return None

    # ------------------------------------------------------------------ metrics

    def market_metrics(self, symbol: str) -> Doc | None:
        """Units as `tt` saw them live in 2026-11. The IV index, its rank and percentile are
        fractions. The 30-day IV and the historical volatilities are percent."""
        u = UNDERLYINGS.get(symbol)
        if u is None:
            return None
        rng = self._rng("metrics", symbol)
        ivs = []
        for root in u.roots:
            for exp, _ in self.expirations(root):
                ivs.append(
                    {
                        "expiration-date": exp.isoformat(),
                        "settlement-type": root.settlement,
                        "option-chain-type": "Standard",
                        "implied-volatility": _dec(self.atm_vol(u.atm_vol, exp), 4),
                    }
                )
        iv30 = self.atm_vol(u.atm_vol, self.today + timedelta(days=30))
        hv = [iv30 * (0.8 + 0.4 * rng.random()) for _ in range(3)]
        doc: Doc = {
            "symbol": u.symbol,
            "implied-volatility-index": _dec(iv30, 4),
            "implied-volatility-index-5-day-change": _dec(rng.uniform(-0.02, 0.02), 4),
            "implied-volatility-index-rank": _dec(rng.random(), 4),
            "tos-implied-volatility-index-rank": _dec(rng.random(), 4),
            "tw-implied-volatility-index-rank": _dec(rng.random(), 4),
            "implied-volatility-index-rank-source": "tos",
            "implied-volatility-percentile": _dec(rng.random(), 4),
            "implied-volatility-30-day": _dec(100 * iv30, 2),
            "historical-volatility-30-day": _dec(100 * hv[0], 2),
            "historical-volatility-60-day": _dec(100 * hv[1], 2),
            "historical-volatility-90-day": _dec(100 * hv[2], 2),
            "iv-hv-30-day-difference": _dec(100 * (iv30 - hv[0]), 2),
            "liquidity-value": _dec(rng.uniform(0.5, 5), 4),
            "liquidity-rank": _dec(rng.random(), 4),
            "liquidity-rating": 4,
            "beta": _dec(1.0 if u.kind != "stock" else 1.2, 4),
            "corr-spy-3month": _dec(1.0 if u.kind != "stock" else 0.7, 2),
            "market-cap": 0 if u.kind == "index" else 3_000_000_000_000,
            "borrow-rate": "0.0",
            "lendability": "Easy To Borrow",
            "updated-at": _iso(self.asof - timedelta(minutes=9)),
            "implied-volatility-updated-at": _iso(self.asof - timedelta(minutes=29)),
            "option-expiration-implied-volatilities": ivs,
        }
        if u.dividend:
            ex = self.today + timedelta(days=24)
            doc.update(
                {
                    "dividend-rate-per-share": _dec(u.dividend / 4, 6),
                    "annual-dividend-per-share": _dec(u.dividend, 6),
                    "dividend-yield": _dec(u.dividend / u.spot, 6),
                    "dividend-ex-date": ex.isoformat(),
                    "dividend-next-date": ex.isoformat(),
                    "dividend-pay-date": (ex + timedelta(days=14)).isoformat(),
                }
            )
        if u.earnings_in_days is not None:
            doc["earnings"] = {
                "visible": True,
                "expected-report-date": (
                    self.today + timedelta(days=u.earnings_in_days)
                ).isoformat(),
                "estimated": True,
                "late-flag": 0,
                "time-of-day": "AMC",
                "updated-at": _iso(self.asof - timedelta(days=2)),
            }
        return doc
