"""Synthetic stream events over the synthetic market: quotes, trades, Greeks, summaries,
profiles and candles (GEN-07; implementation plan 3.2). Added 2026-11-23 (WP-0.4).

Every value is a function of the market's seed and of the instant asked for. Nothing is
stored between calls except caches, so the same instant gives the same events in any order
of asking.

The price model:

- **Days.** Weekdays are trading days; the fake has no holidays (WP-0.6 brings the calendar).
  Each day has a seeded close-to-close return. The close of the last trading day before the
  market's as-of date is the underlying's configured spot, and the days before and after
  follow from it. Daily volatility is the underlying's at-the-money volatility over the
  square root of 252.
- **Minutes.** Inside the regular session, 09:30 to 16:00 New York, the price follows a
  seeded Brownian bridge from the day's open to its close, one point a minute, linear in
  between. Outside the session nothing moves: every event is the one of the last close, so
  a closed market is silent. The live feed moves around the clock on weekdays; the fake
  does not.
- **Candles** of every period are cut from that one path, so a 15-minute bar, an hourly bar
  and the daily bar of the same day agree. Intraday bars are aligned to the UTC clock and
  daily bars are stamped at midnight UTC of their day, as the live feed does (note B,
  addendum of 2026-11-22). A snapshot arrives newest first. Its first event carries the
  snapshot-begin flag. Its last event is not a bar: it has a time and no prices, and
  carries the remove flag with the snapshot-end flag, as the live feed showed on 2026-11-23
  (note B, addendum "WP-0.4"), or with the snapshot-snip flag when the history was cut at
  the depth of 8,000 bars (note B, addendum of 2026-11-24). A reader drops events with the
  remove flag.
- **Options.** One fixed smile per expiration: a raw SVI slice in total variance
  (:func:`svi_params`), on the forward ``F = S exp(r tau)`` with a flat rate of 4% and no
  dividends. Prices and Greeks come from ``s3t.pricing.black76``. The quote is the model
  price rounded to the tick with a spread of one tick or 2% of the price. An option whose
  bid would be below one tick has no bid, which dxFeed sends as a price of 0 with the size
  ``"NaN"`` (note B, addendum "WP-0.5").
- **Greeks units**, assumed and not compared with the live feed (WP-1.9 compares them):
  delta and gamma against the spot, theta per calendar day, vega and rho per percentage
  point.

Known symbols: the underlyings of ``market.py``, their options by streamer symbol, the /ES
futures and their options. Anything else gives no event, as an unknown symbol does live.
"""

import math
import re
import zlib
from collections import OrderedDict
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from typing import Any

import numpy as np

from s3t.broker.fake.market import (
    CHICAGO,
    FUTURE_ATM_VOL,
    FUTURE_SPOT,
    NY,
    UNDERLYINGS,
    Doc,
    SyntheticMarket,
)
from s3t.pricing import black_greeks, black_price, tau_cal

RATE = 0.04
SESSION_OPEN = time(9, 30)
SESSION_MINUTES = 390
NAN = "NaN"
"""A missing number, as dxFeed writes it in JSON."""

REMOVE_EVENT = 0x2
SNAPSHOT_BEGIN = 0x4
SNAPSHOT_END = 0x8
SNAPSHOT_SNIP = 0x10

CANDLE_DEPTH_BARS = 8000  # minute and daily candles, as live (note B, section 6)
CANDLE_DEPTH_DAYS = 90  # the other intraday periods, about what live gives for 15 minutes

_OPTION = re.compile(r"\.([A-Z]+)(\d{6})([CP])(\d+(?:\.\d+)?)")
_CANDLE = re.compile(
    r"(?P<symbol>[^{]+)\{=(?P<n>\d*)(?P<unit>[mhd])(?P<rest>(?:,[a-z]+=[a-z]+)*)\}"
)
_PERIOD_SECONDS = {"m": 60, "h": 3600, "d": 86400}
_FUTURE_TICK_SIZES: tuple[Doc, ...] = ({"value": "0.25"},)
_FUTURE_OPTION_TICK_SIZES: tuple[Doc, ...] = (
    {"value": "0.05", "threshold": "10.0"},
    {"value": "0.25"},
)


def svi_params(atm_vol: Any, tau: Any) -> tuple[Any, Any, float, float, Any]:
    """The raw SVI parameters ``(a, b, rho, m, s)`` of the fixed smile of an expiration
    (scalars or arrays).

    Total variance at log-moneyness ``k = ln(K / F)`` is
    ``a + b (rho (k - m) + sqrt((k - m)^2 + s^2))``. At the money it is ``atm_vol^2 tau``.
    The skew is negative and flattens with time, as an equity index smile does.
    """
    w = atm_vol * atm_vol * tau
    root = np.sqrt(w)
    b = 0.5 * root
    s = 0.5 * root + 0.01
    return w - b * s, b, -0.6, 0.0, s


def smile_vol(atm_vol: Any, tau: Any, k: Any) -> Any:
    """The implied volatility of the fixed smile at log-moneyness ``k``.

    The total variance is kept at a quarter of the at-the-money one or more. The raw slice
    turns negative on the call side in the last half hour before expiry (at 16% volatility),
    and a 0DTE option is priced until its expiry, 15 minutes after the close.
    """
    a, b, rho, m, s = svi_params(atm_vol, tau)
    w = a + b * (rho * (k - m) + np.sqrt((k - m) ** 2 + s * s))
    return np.sqrt(np.maximum(w, 0.25 * atm_vol * atm_vol * tau) / tau)


def tick_for(price: float, tick_sizes: tuple[Doc, ...]) -> float:
    """The quoting tick at a price: the first entry whose threshold lies above it."""
    for entry in tick_sizes:
        threshold = entry.get("threshold")
        if threshold is None or price < float(threshold):
            return float(entry["value"])
    return float(tick_sizes[-1]["value"])


def parse_candle_symbol(symbol: str) -> tuple[str, int] | None:
    """``SPY{=15m}`` gives ``("SPY", 900)``. None for a symbol the fake does not serve:
    another period unit, or an attribute other than ``tho=true``."""
    m = _CANDLE.fullmatch(symbol)
    if m is None or m["rest"] not in ("", ",tho=true"):
        return None
    seconds = int(m["n"] or 1) * _PERIOD_SECONDS[m["unit"]]
    if m["unit"] == "d" and seconds != 86400:
        return None
    return m["symbol"], seconds


def is_trading_day(d: date) -> bool:
    return d.weekday() < 5


def previous_trading_day(d: date) -> date:
    d -= timedelta(days=1)
    while not is_trading_day(d):
        d -= timedelta(days=1)
    return d


def market_instant(at: datetime) -> datetime:
    """The instant itself inside a session, else the close of the last session before it.
    Events are made for this instant, so a closed market sends nothing new."""
    day = at.astimezone(NY).date()
    if is_trading_day(day):
        opened = datetime.combine(day, SESSION_OPEN, NY)
        closed = opened + timedelta(minutes=SESSION_MINUTES)
        if opened <= at <= closed:
            return at
        if at > closed:
            return closed.astimezone(UTC)
    last = previous_trading_day(day)
    closed = datetime.combine(last, SESSION_OPEN, NY) + timedelta(minutes=SESSION_MINUTES)
    return closed.astimezone(UTC)


def _ms(instant: datetime) -> int:
    return round(instant.timestamp() * 1000)


def _day_id(d: date) -> int:
    return d.toordinal() - date(1970, 1, 1).toordinal()


@dataclass(frozen=True)
class _Instrument:
    symbol: str
    kind: str
    """``equity``, ``index``, ``future`` or ``option``."""
    path: str
    """The key of the price path it follows."""
    base: float
    vol: float
    tick_sizes: tuple[Doc, ...]
    description: str = ""
    dividend: float = 0.0
    expires: datetime | None = None
    strike: float = 0.0
    is_call: bool = False


class _Path:
    """The price path of one underlying or future."""

    def __init__(self, seed: int, key: str, base: float, vol: float, anchor: date) -> None:
        self._seed = (seed, zlib.crc32(key.encode()))
        self._daily = vol / math.sqrt(252)
        self._volume = 2e7 * 100.0 / base
        self._closes = {anchor.toordinal(): base}
        self._low = self._high = anchor
        self._bars: dict[int, tuple[float, float, float, float, float]] = {}
        self._minutes: OrderedDict[int, tuple[np.ndarray, np.ndarray]] = OrderedDict()

    def _draws(self, kind: int, day: date, n: int) -> np.ndarray:
        return np.random.default_rng([*self._seed, kind, day.toordinal()]).standard_normal(n)

    def _return(self, day: date) -> tuple[float, float]:
        """The close-to-close return of a trading day and its opening gap, both in logs."""
        z = self._draws(0, day, 2)
        return self._daily * float(z[0]), 0.2 * self._daily * float(z[1])

    def close(self, day: date) -> float:
        """The close of a trading day."""
        while day > self._high:
            nxt = self._high + timedelta(days=1)
            while not is_trading_day(nxt):
                nxt += timedelta(days=1)
            self._closes[nxt.toordinal()] = self._closes[self._high.toordinal()] * math.exp(
                self._return(nxt)[0]
            )
            self._high = nxt
        while day < self._low:
            prev = previous_trading_day(self._low)
            self._closes[prev.toordinal()] = self._closes[self._low.toordinal()] / math.exp(
                self._return(self._low)[0]
            )
            self._low = prev
        return self._closes[day.toordinal()]

    def open(self, day: date) -> float:
        return self.close(previous_trading_day(day)) * math.exp(self._return(day)[1])

    def minutes(self, day: date) -> tuple[np.ndarray, np.ndarray]:
        """The session of a trading day: 391 prices, one per minute boundary, and the 390
        volumes of the minutes between them."""
        key = day.toordinal()
        found = self._minutes.get(key)
        if found is not None:
            self._minutes.move_to_end(key)
            return found
        z = self._draws(1, day, 2 * SESSION_MINUTES)
        walk = np.concatenate(([0.0], np.cumsum(z[:SESSION_MINUTES])))
        share = np.arange(SESSION_MINUTES + 1) / SESSION_MINUTES
        bridge = (walk - share * walk[-1]) * self._daily / math.sqrt(SESSION_MINUTES)
        lo, lc = math.log(self.open(day)), math.log(self.close(day))
        prices = np.exp(lo + share * (lc - lo) + bridge)
        # More volume at the open and the close, as a session has.
        shape = 1.0 + 1.5 * (2.0 * share[:-1] - 1.0) ** 2
        volumes = np.round(
            self._volume / SESSION_MINUTES * shape * np.exp(0.3 * z[SESSION_MINUTES:])
        )
        self._minutes[key] = (prices, volumes)
        if len(self._minutes) > 40:
            self._minutes.popitem(last=False)
        return prices, volumes

    def day_bar(self, day: date) -> tuple[float, float, float, float, float]:
        """Open, high, low, close and volume of a finished trading day."""
        key = day.toordinal()
        if key not in self._bars:
            prices, volumes = self.minutes(day)
            self._bars[key] = (
                float(prices[0]),
                float(prices.max()),
                float(prices.min()),
                float(prices[-1]),
                float(volumes.sum()),
            )
        return self._bars[key]

    def position(self, at: datetime) -> tuple[date, float]:
        """The trading day an instant belongs to and the minutes since its open; the
        minutes are negative before the open and above 390 after the close. On a day
        without trading it is the previous trading day, after its close."""
        local = at.astimezone(NY)
        day = local.date()
        if not is_trading_day(day):
            return previous_trading_day(day), float(SESSION_MINUTES) + 1.0
        opened = datetime.combine(day, SESSION_OPEN, NY)
        return day, (at - opened).total_seconds() / 60.0

    def price(self, at: datetime) -> float:
        day, x = self.position(at)
        if x <= 0.0:
            return self.close(previous_trading_day(day))
        if x >= SESSION_MINUTES:
            return self.close(day)
        prices, _ = self.minutes(day)
        i = int(x)
        return float(prices[i] + (x - i) * (prices[i + 1] - prices[i]))

    def session_so_far(self, at: datetime) -> tuple[float, float, float, float, float] | None:
        """Open, high, low, last and volume of the instant's day up to the instant; None
        before the open."""
        day, x = self.position(at)
        if x <= 0.0:
            return None
        if x >= SESSION_MINUTES:
            return self.day_bar(day)
        prices, volumes = self.minutes(day)
        i = int(x)
        last = self.price(at)
        seen = prices[: i + 1]
        volume = float(volumes[:i].sum() + (x - i) * volumes[i])
        return (
            float(prices[0]),
            max(float(seen.max()), last),
            min(float(seen.min()), last),
            last,
            round(volume),
        )


class Ticks:
    """The stream events of a synthetic market."""

    def __init__(self, market: SyntheticMarket) -> None:
        self.market = market
        self._anchor = previous_trading_day(market.today)
        self._paths: dict[str, _Path] = {}
        self._instruments: dict[str, _Instrument | None] = {}
        self._futures: dict[str, _Instrument] | None = None

    # ------------------------------------------------------------------ instruments

    def _path(self, inst: _Instrument) -> _Path:
        if inst.path not in self._paths:
            base, vol = inst.base, inst.vol
            self._paths[inst.path] = _Path(self.market.seed, inst.path, base, vol, self._anchor)
        return self._paths[inst.path]

    def _future_instruments(self) -> dict[str, _Instrument]:
        """The futures and their options, by streamer symbol."""
        if self._futures is not None:
            return self._futures
        m = self.market
        found: dict[str, _Instrument] = {}
        streamers: dict[str, str] = {}
        for symbol, last in m._futures():
            streamer = m.future_streamer(symbol, last)
            streamers[symbol] = streamer
            found[streamer] = _Instrument(
                streamer,
                "future",
                streamer,
                FUTURE_SPOT,
                FUTURE_ATM_VOL,
                _FUTURE_TICK_SIZES,
                description=f"E-Mini S&P 500 {symbol} (synthetic)",
                expires=datetime.combine(last, time(8, 30), CHICAGO),
            )
        strikes = m._future_strikes()
        for e in m._future_option_expirations():
            under = streamers[e["future"]]
            vol = m.atm_vol(FUTURE_ATM_VOL, e["date"])
            for option_type in "CP":
                for k in strikes:
                    streamer = m.future_option_streamer(e, option_type, k)
                    found[streamer] = _Instrument(
                        streamer,
                        "option",
                        under,
                        FUTURE_SPOT,
                        vol,
                        _FUTURE_OPTION_TICK_SIZES,
                        expires=e["expires"],
                        strike=k,
                        is_call=option_type == "C",
                    )
        self._futures = found
        return found

    def instrument(self, symbol: str) -> _Instrument | None:
        if symbol not in self._instruments:
            self._instruments[symbol] = self._resolve(symbol)
        return self._instruments[symbol]

    def _resolve(self, symbol: str) -> _Instrument | None:
        u = UNDERLYINGS.get(symbol)
        if u is not None:
            kind = "index" if u.kind == "index" else "equity"
            ticks: tuple[Doc, ...] = ({"value": "0.01"},)
            return _Instrument(
                symbol,
                kind,
                symbol,
                u.spot,
                u.atm_vol,
                ticks,
                description=u.description,
                dividend=u.dividend,
            )
        if symbol.startswith(("/", "./")):
            return self._future_instruments().get(symbol)
        m = _OPTION.fullmatch(symbol)
        if m is None:
            return None
        try:
            expiration = datetime.strptime(m[2], "%y%m%d").date()
        except ValueError:
            return None
        for u in UNDERLYINGS.values():
            for root in u.roots:
                if root.symbol != m[1]:
                    continue
                listed = {d for d, _ in self.market.expirations(root)}
                strike = float(m[4])
                if expiration not in listed or strike not in self.market.strikes(u, expiration):
                    return None
                return _Instrument(
                    symbol,
                    "option",
                    u.symbol,
                    u.spot,
                    self.market.atm_vol(u.atm_vol, expiration),
                    u.tick_sizes,
                    expires=self.market._expires_at(u, root, expiration),
                    strike=strike,
                    is_call=m[3] == "C",
                )
        return None

    def _underlying_of(self, inst: _Instrument) -> _Instrument:
        under = self.instrument(inst.path)
        assert under is not None
        return under

    # ------------------------------------------------------------------ prices

    def price(self, symbol: str, at: datetime) -> float | None:
        """The price of an underlying or a future at an instant."""
        inst = self.instrument(symbol)
        if inst is None or inst.kind == "option":
            return None
        return self._path(inst).price(at)

    def option_values(self, symbols: list[str], at: datetime) -> dict[str, dict[str, float]]:
        """Model values of the options among ``symbols`` that are listed and not expired:
        price, volatility and the Greeks in the units of the module docstring."""
        insts = [i for i in map(self.instrument, symbols) if i is not None and i.kind == "option"]
        insts = [i for i in insts if i.expires is not None and i.expires > at]
        if not insts:
            return {}
        spot = np.array([self._path(self._underlying_of(i)).price(at) for i in insts])
        on_future = np.array([self._underlying_of(i).kind == "future" for i in insts])
        tau = np.array([tau_cal(at, i.expires) for i in insts if i.expires is not None])
        strike = np.array([i.strike for i in insts])
        is_call = np.array([i.is_call for i in insts])
        growth = np.where(on_future, 1.0, np.exp(RATE * tau))
        forward = spot * growth
        df = np.exp(-RATE * tau)
        sigma = smile_vol(np.array([i.vol for i in insts]), tau, np.log(strike / forward))
        price = black_price(forward, strike, tau, df, sigma, is_call)
        g = black_greeks(forward, strike, tau, df, sigma, is_call)
        columns = {
            "price": price,
            "volatility": sigma,
            "delta": g["delta"] * growth,
            "gamma": g["gamma"] * growth * growth,
            "theta": g["theta"] / 365.0,
            "vega": g["vega"] / 100.0,
            "rho": g["rho"] / 100.0,
            "underlying": spot,
        }
        return {
            inst.symbol: {name: float(column[n]) for name, column in columns.items()}
            for n, inst in enumerate(insts)
        }

    # ------------------------------------------------------------------ events

    def events(self, event: str, symbols: list[str], at: datetime) -> list[Doc]:
        """One event of this type per symbol that has one at this instant. Candles are
        served by :meth:`candles` and :meth:`candle`."""
        at = market_instant(at)
        options = self.option_values(symbols, at) if event != "Profile" else {}
        out: list[Doc] = []
        for symbol in symbols:
            inst = self.instrument(symbol)
            if inst is None:
                continue
            if inst.kind == "option":
                values = options.get(symbol)
                doc = None if values is None else self._option_event(event, inst, values, at)
            else:
                doc = self._underlying_event(event, inst, at)
            if doc is not None:
                out.append({"eventSymbol": symbol, "eventTime": 0, **doc})
        return out

    def _sizes(self, inst: _Instrument, at: datetime, scale: int) -> tuple[int, int]:
        """Bid and ask size: new every second of a session, standing still outside one."""
        day, x = self._path(self._underlying_of(inst)).position(at)
        moment = int(at.timestamp()) if 0.0 < x < SESSION_MINUTES else day.toordinal()
        rng = self.market._rng("size", inst.symbol, moment)
        return rng.randint(1, scale), rng.randint(1, scale)

    def _underlying_event(self, event: str, inst: _Instrument, at: datetime) -> Doc | None:
        path = self._path(inst)
        price = path.price(at)
        day, x = path.position(at)
        previous = path.close(previous_trading_day(day))
        if event == "Quote":
            if inst.kind == "index":
                # Live, note B section 5: an index quote has no sizes and is far too wide
                # to be a price.
                return {
                    "sequence": 0,
                    "timeNanoPart": 0,
                    "bidTime": 0,
                    "bidExchangeCode": "\u0000",
                    "askTime": 0,
                    "askExchangeCode": "\u0000",
                    "bidPrice": round(price * 0.992, 2),
                    "askPrice": round(price * 1.008, 2),
                    "bidSize": NAN,
                    "askSize": NAN,
                }
            tick = tick_for(price, inst.tick_sizes)
            bid = math.floor(price / tick) * tick
            bid_size, ask_size = self._sizes(inst, at, 500)
            return {
                "sequence": 0,
                "timeNanoPart": 0,
                "bidTime": 0,
                "bidExchangeCode": "Q",
                "askTime": 0,
                "askExchangeCode": "Q",
                "bidPrice": round(bid, 2),
                "askPrice": round(bid + tick, 2),
                "bidSize": bid_size,
                "askSize": ask_size,
            }
        if event == "Trade":
            so_far = path.session_so_far(at)
            volume = 0.0 if so_far is None else so_far[4]
            size: Any = NAN if inst.kind == "index" else self._sizes(inst, at, 300)[0]
            return {
                "time": _ms(at),
                "timeNanoPart": 0,
                "sequence": 0,
                "exchangeCode": "Q",
                "dayId": _day_id(day),
                "tickDirection": "ZERO_UP",
                "extendedTradingHours": False,
                "price": round(price, 2),
                "change": round(price - previous, 2),
                "size": size,
                "dayVolume": NAN if inst.kind == "index" else volume,
                "dayTurnover": NAN if inst.kind == "index" else round(volume * price, 2),
            }
        if event == "Summary":
            so_far = path.session_so_far(at)
            before = previous_trading_day(day)
            return {
                "dayId": _day_id(day),
                "dayOpenPrice": NAN if so_far is None else round(so_far[0], 2),
                "dayHighPrice": NAN if so_far is None else round(so_far[1], 2),
                "dayLowPrice": NAN if so_far is None else round(so_far[2], 2),
                "dayClosePrice": round(path.close(day), 2) if x >= SESSION_MINUTES else NAN,
                "dayClosePriceType": "REGULAR",
                "prevDayId": _day_id(before),
                "prevDayClosePrice": round(previous, 2),
                "prevDayClosePriceType": "REGULAR",
                "prevDayVolume": NAN if inst.kind == "index" else path.day_bar(before)[4],
                "openInterest": 0,
            }
        if event == "Profile":
            return {
                "description": inst.description,
                "shortSaleRestriction": "INACTIVE",
                "tradingStatus": "ACTIVE",
                "statusReason": NAN,
                "haltStartTime": 0,
                "haltEndTime": 0,
                "highLimitPrice": NAN,
                "lowLimitPrice": NAN,
                "high52WeekPrice": round(inst.base * 1.08, 2),
                "low52WeekPrice": round(inst.base * 0.79, 2),
                "beta": 1.0,
                "earningsPerShare": NAN,
                "dividendFrequency": 4.0 if inst.dividend else NAN,
                "exDividendAmount": round(inst.dividend / 4, 4) if inst.dividend else NAN,
                "exDividendDayId": _day_id(self.market.today) + 30 if inst.dividend else 0,
                "shares": NAN,
                "freeFloat": NAN,
            }
        return None

    def _option_event(
        self, event: str, inst: _Instrument, values: dict[str, float], at: datetime
    ) -> Doc | None:
        price = values["price"]
        tick = tick_for(price, inst.tick_sizes)
        if event == "Quote":
            half = max(tick / 2.0, 0.01 * price)
            bid = math.floor((price - half) / tick + 1e-9) * tick
            ask = max(math.ceil((price + half) / tick - 1e-9) * tick, tick)
            bid_size, ask_size = self._sizes(inst, at, 200)
            no_bid = bid < tick - 1e-9
            return {
                "sequence": 0,
                "timeNanoPart": 0,
                "bidTime": 0,
                "bidExchangeCode": "C",
                "askTime": 0,
                "askExchangeCode": "C",
                "bidPrice": 0.0 if no_bid else round(bid, 4),
                "askPrice": round(ask, 4),
                "bidSize": NAN if no_bid else bid_size,
                "askSize": ask_size,
            }
        if event == "Greeks":
            return {
                "eventFlags": 0,
                "index": _ms(at) << 22,
                "time": _ms(at),
                "sequence": 0,
                "price": round(price, 6),
                "volatility": round(values["volatility"], 6),
                "delta": round(values["delta"], 6),
                "gamma": round(values["gamma"], 8),
                "theta": round(values["theta"], 6),
                "rho": round(values["rho"], 6),
                "vega": round(values["vega"], 6),
            }
        rng = self.market._rng("option", inst.symbol)
        interest, volume = rng.randint(0, 5000), rng.randint(0, 800)
        day, _ = self._path(self._underlying_of(inst)).position(at)
        last = max(round(price / tick) * tick, tick)
        if event == "Trade":
            return {
                "time": _ms(at),
                "timeNanoPart": 0,
                "sequence": 0,
                "exchangeCode": "C",
                "dayId": _day_id(day),
                "tickDirection": "ZERO",
                "extendedTradingHours": False,
                "price": round(last, 4),
                "change": 0.0,
                "size": 1,
                "dayVolume": volume,
                "dayTurnover": round(volume * last * 100, 2),
            }
        if event == "Summary":
            return {
                "dayId": _day_id(day),
                "dayOpenPrice": NAN,
                "dayHighPrice": NAN,
                "dayLowPrice": NAN,
                "dayClosePrice": NAN,
                "dayClosePriceType": "REGULAR",
                "prevDayId": _day_id(previous_trading_day(day)),
                "prevDayClosePrice": round(last, 4),
                "prevDayClosePriceType": "REGULAR",
                "prevDayVolume": volume,
                "openInterest": interest,
            }
        return None

    # ------------------------------------------------------------------ candles

    def candles(self, symbol: str, from_time: datetime, at: datetime) -> list[Doc]:
        """The candle snapshot of a candle symbol from ``from_time`` to ``at``: the bars
        newest first, the bar in progress included, then the end marker (module
        docstring). Empty for a symbol the fake does not serve."""
        parsed = parse_candle_symbol(symbol)
        inst = None if parsed is None else self.instrument(parsed[0])
        if parsed is None or inst is None or inst.kind == "option":
            return []
        period = parsed[1]
        path = self._path(inst)
        today, _ = path.position(at)
        if period == 86400:
            limit = CANDLE_DEPTH_BARS
            first_day = from_time.astimezone(UTC).date()
        else:
            limit = CANDLE_DEPTH_BARS if period == 60 else 10**9
            depth = 30 if period == 60 else CANDLE_DEPTH_DAYS
            first_day = max(from_time.astimezone(NY).date(), today - timedelta(days=depth))
        bars: list[tuple[int, float, float, float, float, float]] = []
        day = today
        while day >= first_day and len(bars) < limit:
            if is_trading_day(day):
                found = self._bars_of_day(path, day, period, at)
                bars.extend(b for b in reversed(found) if b[0] >= _ms(from_time))
            day -= timedelta(days=1)
        cut = len(bars) >= limit
        bars = bars[:limit]
        vol = self.market.atm_vol(inst.vol, self.market.today + timedelta(days=30))
        out = [self._candle(symbol, inst, bar, vol) for bar in bars]
        if out:
            out[0]["eventFlags"] |= SNAPSHOT_BEGIN
            # The last event of a snapshot is not a bar: it has a time and no prices.
            # A history that is complete ends with "snapshot end" and the remove flag (seen
            # live 2026-11-23). One that was cut at the depth of 8,000 bars ends with
            # "snapshot snip" and the remove flag, at a midnight before its oldest bar (the
            # owner's streamer dumps, read 2026-11-24). Assumed: the time of the first is
            # the start asked for, and the midnight of the second is that of the oldest bar.
            nan = math.nan
            stamp = bars[-1][0] // 86_400_000 * 86_400_000 if cut else _ms(from_time)
            marker = self._candle(symbol, inst, (stamp, nan, nan, nan, nan, nan), vol)
            marker.update({k: NAN for k in ("open", "high", "low", "close", "vwap", "count")})
            marker["volume"] = NAN
            marker["eventFlags"] = (SNAPSHOT_SNIP if cut else SNAPSHOT_END) | REMOVE_EVENT
            out.append(marker)
        return out

    def candle(self, symbol: str, at: datetime) -> Doc | None:
        """The bar in progress at an instant, or the last finished one outside a session."""
        parsed = parse_candle_symbol(symbol)
        inst = None if parsed is None else self.instrument(parsed[0])
        if parsed is None or inst is None or inst.kind == "option":
            return None
        path = self._path(inst)
        day, _ = path.position(at)
        bars = self._bars_of_day(path, day, parsed[1], at)
        if not bars:
            bars = self._bars_of_day(path, previous_trading_day(day), parsed[1], at)
        vol = self.market.atm_vol(inst.vol, self.market.today + timedelta(days=30))
        return self._candle(symbol, inst, bars[-1], vol)

    @staticmethod
    def _bars_of_day(
        path: _Path, day: date, period: int, at: datetime
    ) -> list[tuple[int, float, float, float, float, float]]:
        """The bars of one trading day up to ``at``, oldest first: start in milliseconds,
        open, high, low, close, volume."""
        opened = datetime.combine(day, SESSION_OPEN, NY)
        minutes_in = (at - opened).total_seconds() / 60.0
        if minutes_in <= 0.0:
            return []
        if period == 86400:
            stamp = int(datetime.combine(day, time(0), UTC).timestamp()) * 1000
            if minutes_in >= SESSION_MINUTES:
                return [(stamp, *path.day_bar(day))]
            so_far = path.session_so_far(at)
            assert so_far is not None
            return [(stamp, *so_far)]
        prices, volumes = path.minutes(day)
        done = min(int(minutes_in), SESSION_MINUTES)
        starts = int(opened.timestamp()) + 60 * np.arange(done + (minutes_in < SESSION_MINUTES))
        buckets = starts // period
        last = path.price(at)
        out = []
        for bucket in np.unique(buckets):
            index = np.nonzero(buckets == bucket)[0]
            i, j = int(index[0]), int(index[-1])
            whole = j < done
            seen = prices[i : j + 2] if whole else prices[i : j + 1]
            close = float(prices[j + 1]) if whole else last
            volume = float(volumes[i : j + 1].sum()) if whole else float(volumes[i:j].sum())
            out.append(
                (
                    int(bucket) * period * 1000,
                    float(prices[i]),
                    max(float(seen.max()), close),
                    min(float(seen.min()), close),
                    close,
                    volume,
                )
            )
        return out

    @staticmethod
    def _candle(
        symbol: str,
        inst: _Instrument,
        bar: tuple[int, float, float, float, float, float],
        vol: float,
    ) -> Doc:
        stamp, o, h, low, c, volume = bar
        index = inst.kind == "index"
        return {
            "eventSymbol": symbol,
            "eventTime": 0,
            "eventFlags": 0,
            "index": (stamp // 1000) << 32,
            "time": stamp,
            "sequence": 0,
            "count": NAN if index or volume != volume else max(1, round(volume / 100)),
            "open": round(o, 2),
            "high": round(h, 2),
            "low": round(low, 2),
            "close": round(c, 2),
            "volume": NAN if index else volume,
            "vwap": round((h + low + c) / 3, 4),
            "bidVolume": NAN,
            "askVolume": NAN,
            "impVolatility": round(vol, 4),
            "openInterest": NAN,
        }
