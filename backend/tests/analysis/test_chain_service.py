"""The chain service on the fake broker and a virtual clock (WP-1.2): own IV and Greeks per flush
in one call (MKT-04), an index (MKT-05), stale marks (MKT-06), the broker's Greeks as the
fallback (PRC-13), the grace period (SUB-04), windows (SUB-05), earnings and ex-dates (REF-03).

The synthetic market prices its options on a known smile and states its Greeks: delta, gamma and
vega against the spot, per share, vega per volatility point, theta per calendar day. Its theta
holds the forward fixed, where the application's holds the spot fixed (requirements 4.6), so the
two differ by the carry r S delta / 365.
"""

import asyncio
import copy
import logging
import math
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from s3t.analysis import chain as chain_module
from s3t.analysis.chain import (
    BROKER,
    CONSUMER,
    HELD,
    NONE,
    OWN,
    RECOMPUTE_S,
    ChainService,
    ChainUnavailable,
    ChainView,
    Part,
    RepeatLimit,
    Row,
    Side,
    Window,
    WindowRefused,
    limit_repeats,
)
from s3t.broker.fake.ticks import RATE
from s3t.broker.interface import EventType, Greeks, Sub
from s3t.pricing.moves import METHOD, expected_move

from .conftest import NY, SMALL, SurfaceRig, surfaces

pytestmark = pytest.mark.anyio

CHAIN = SMALL + "[chain]\ngrace_s = 20.0\nstale_after_s = 30.0\n"
QUIET_AT = datetime(2026, 11, 20, 22, 0, tzinfo=UTC)
"""Friday 17:00 in New York: the synthetic market does not move outside the regular session."""


@dataclass
class ChainRig:
    surface: SurfaceRig

    @property
    def service(self) -> ChainService:
        return self.surface.api.backend.chain

    def today(self) -> date:
        return self.surface.api.backend.market.now().astimezone(NY).date()

    async def key(self, symbol: str, days: int, root: str | None = None) -> str:
        """The key of the first expiration at least ``days`` days ahead."""
        chain = await self.surface.api.backend.instruments.chain(symbol)
        today = self.today()
        e = next(
            e
            for e in chain.expirations
            if (e.expiration_date - today).days >= days and (root is None or e.root == root)
        )
        return f"{e.root}:{e.expiration_date.isoformat()}"

    async def open(self, symbol: str, *windows: Window) -> ChainView:
        view = self.service.open(symbol, windows)
        task = asyncio.ensure_future(view.ready())
        await self.surface.until(task.done)
        task.result()
        return view

    async def next(self, view: ChainView, seconds: float = 5) -> Part | None:
        """The next part of a view within the time, or None."""
        task = asyncio.ensure_future(view.next())
        for _ in range(int(seconds * 2)):
            if task.done():
                break
            await self.surface.api.pass_time(0.5)
        if task.done():
            return task.result()
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        return None

    async def until(
        self, view: ChainView, done: Callable[[Part], bool], seconds: float = 60
    ) -> Part:
        """The parts of a view until one of them makes ``done`` true."""
        for _ in range(int(seconds)):
            part = await self.next(view, 1)
            if part is not None and done(part):
                return part
        raise AssertionError("it did not come in time")

    async def holds(
        self, view: ChainView, check: Callable[[Part], bool], seconds: float = 60
    ) -> Part:
        """A snapshot of the view once it makes ``check`` true."""
        for _ in range(int(seconds * 2)):
            snapshot = view.snapshot()
            if check(snapshot):
                return snapshot
            await self.surface.api.pass_time(0.5)
        raise AssertionError("it did not come in time")

    async def computed(self, view: ChainView, count: int = 10) -> Part:
        """A snapshot once ``count`` contracts have their own values."""
        return await self.holds(view, lambda p: sum(_own(r) for r in p.rows) >= count)

    async def fitted(self, view: ChainView, key: str) -> Part:
        """A snapshot once the expiration has the forward of a fit."""
        return await self.holds(view, lambda p: info(p, key).forward_source == "parity", 90)

    async def call(self, work: Any) -> Any:
        """Await something that reads from the broker, whose limiter sleeps on the clock."""
        task = asyncio.ensure_future(work)
        await self.surface.until(task.done)
        return task.result()

    def subs(self, name: str) -> list[Sub]:
        consumers = self.surface.api.backend.feed._consumers  # type: ignore[attr-defined]
        return list(consumers[name].subs) if name in consumers else []

    def consumers(self) -> list[str]:
        return list(self.surface.api.backend.feed._consumers)  # type: ignore[attr-defined]

    def broker_greeks(self, symbol: str, side: Side) -> Greeks:
        book = self.service._books[symbol]
        stream = next(s for s, c in book.cells.items() if c.symbol == side.symbol)
        latest = self.surface.api.backend.market.latest(EventType.GREEKS, stream)
        assert latest is not None and isinstance(latest.record, Greeks)
        return latest.record

    def has_broker_greeks(self, symbol: str, side: Side) -> bool:
        stream = self.stream_of(symbol, side)
        return self.surface.api.backend.market.latest(EventType.GREEKS, stream) is not None

    def stream_of(self, symbol: str, side: Side) -> str:
        book = self.service._books[symbol]
        return next(s for s, c in book.cells.items() if c.symbol == side.symbol)


def _own(row: Row) -> int:
    return sum(1 for s in (row.call, row.put) if s is not None and s.source == OWN)


@asynccontextmanager
async def chains(
    tmp_path: Path, more: str = CHAIN, *, start: datetime | None = None
) -> AsyncIterator[ChainRig]:
    kw: dict[str, Any] = {} if start is None else {"start": start}
    async with surfaces(tmp_path, more, **kw) as rig:
        yield ChainRig(rig)


def near(rows: tuple[Row, ...], money: float, n: int = 3) -> list[Row]:
    return sorted(rows, key=lambda r: abs(r.strike - money))[:n]


def info(part: Part, key: str):
    return next(e for _, e in part.expirations if e.key == key)


# --- a view ---------------------------------------------------------------------------------------


async def test_a_view_has_its_underlying_every_expiration_and_its_window(tmp_path: Path) -> None:
    async with chains(tmp_path) as rig:
        key = await rig.key("SPY", 20)
        view = await rig.open("SPY", Window(key))
        part = view.snapshot()
        assert part.snapshot and part.head is not None
        assert (part.head.kind, part.head.spot_source) == ("etf", "quote")
        assert part.head.spot == pytest.approx(600, rel=0.01)
        assert part.head.stale_after_s == 30.0
        expirations = [e for _, e in part.expirations]
        assert [i for i, _ in part.expirations] == list(range(len(expirations)))
        assert [e.expiration_date for e in expirations] == sorted(
            e.expiration_date for e in expirations
        )
        assert key in {e.key for e in expirations}
        # 20 strikes each side of the money, all of one expiration.
        assert len(part.rows) == 40
        index = next(i for i, e in part.expirations if e.key == key)
        assert {r.expiration for r in part.rows} == {index}
        strikes = [r.strike for r in part.rows]
        assert strikes == sorted(strikes)
        # Centred on the strike nearest the money, which is the expiration's forward.
        forward = info(part, key).forward
        assert strikes[20] == min(strikes, key=lambda k: abs(k - forward))
        assert part.notes == ()
        view.close()


@pytest.mark.parametrize(
    ("symbol", "spot_source"), [("SPY", "quote"), ("SPX", "trade"), ("AAPL", "quote")]
)
async def test_own_values_agree_with_the_synthetic_market(
    tmp_path: Path, symbol: str, spot_source: str
) -> None:
    """IV, delta, gamma and vega against the market's own, theta with the carry between them
    (the module docstring): the units are trader units per share (requirements 4.6)."""
    async with chains(tmp_path) as rig:
        key = await rig.key(symbol, 20)
        view = await rig.open(symbol, Window(key))
        await rig.fitted(view, key)

        def ready(p: Part) -> bool:
            rows = near(p.rows, info(p, key).forward, 6)
            sides = [s for r in rows for s in (r.call, r.put) if s is not None]
            return all(s.source == OWN for s in sides) and all(
                rig.has_broker_greeks(symbol, s) for s in sides
            )

        part = await rig.holds(view, ready)
        head = part.head
        assert head is not None and head.spot_source == spot_source
        e = info(part, key)
        assert e.forward_source == "parity" and e.note is None
        for row in near(part.rows, e.forward, 6):
            for side in (row.call, row.put):
                assert side is not None and side.source == OWN
                fake = rig.broker_greeks(symbol, side)
                assert side.iv == pytest.approx(fake.iv, abs=0.003)
                assert side.delta == pytest.approx(fake.delta, abs=0.005)
                assert side.gamma == pytest.approx(fake.gamma, rel=0.02)
                assert side.vega == pytest.approx(fake.vega, rel=0.01)
                carry = RATE * head.spot * side.delta / 365
                assert side.theta == pytest.approx(fake.theta - carry, rel=0.03, abs=0.002)
            assert row.iv == (row.call.iv if row.strike >= e.forward else row.put.iv)  # type: ignore[union-attr]
        view.close()


# --- MKT-04: one call per flush, for what is due --------------------------------------------------


async def test_one_call_per_flush_for_the_contracts_that_are_due(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[int] = []
    real = chain_module.contract_values

    def counting(price, *args: Any, **kwargs: Any):
        calls.append(len(price))
        return real(price, *args, **kwargs)

    monkeypatch.setattr(chain_module, "contract_values", counting)
    async with chains(tmp_path, start=QUIET_AT) as rig:
        key = await rig.key("SPY", 20)
        view = await rig.open("SPY", Window(key))
        await rig.computed(view, 70)
        assert calls and calls[0] == 80  # all of them, in the first flush with their quotes
        await rig.surface.api.pass_time(5)
        calls.clear()
        await rig.surface.api.pass_time(5)
        assert calls == []  # a market that does not move: nothing is due

        # One quote moves: that contract alone, in one call. (A pushed event would be followed
        # by the market's own at once, so the test sets the quote.)
        part = view.snapshot()
        side = near(part.rows, part.head.spot, 1)[0].call  # type: ignore[union-attr]
        assert side is not None
        stream = rig.stream_of("SPY", side)
        fake = rig.surface.api.fake.dxlink
        fake.fixed_quotes[stream] = (side.bid - 0.05, side.ask)
        await rig.surface.api.pass_time(2)
        assert calls == [1]

        # Its sizes alone: not computed again, but the row is sent.
        calls.clear()
        doc = fake.events("Quote", [stream], rig.surface.api.clock.wall())[0]
        fake.sessions[-1].push("Quote", [{**doc, "bidSize": 7.0}])
        part = await rig.until(view, lambda p: bool(p.rows))
        assert calls == []
        assert side.symbol in {s.symbol for r in part.rows for s in (r.call, r.put) if s}

        # Time passes in every value: all of them again after RECOMPUTE_S, in one call.
        calls.clear()
        await rig.surface.api.pass_time(RECOMPUTE_S + 1)
        assert sum(calls) >= 79 and len(calls) <= 2  # the one moved later comes due apart
        view.close()


# --- MKT-05: an index -----------------------------------------------------------------------------


async def test_an_index_takes_its_price_from_trades_and_its_forward_from_parity(
    tmp_path: Path,
) -> None:
    async with chains(tmp_path) as rig:
        rig.surface.api.fake.dxlink.index_without_quote = True
        key = await rig.key("SPX", 20, "SPXW")
        view = await rig.open("SPX", Window(key))
        part = view.snapshot()
        assert part.head is not None and part.head.spot_source == "trade"
        await rig.fitted(view, key)
        part = await rig.computed(view, 60)
        e = info(part, key)
        assert e.forward_source == "parity"
        assert e.forward == pytest.approx(
            rig.surface.market_forward("SPX", "SPXW", key, rig.surface.api.clock.wall()), rel=2e-4
        )
        assert sum(_own(r) for r in part.rows) == 80


async def test_an_index_has_no_forward_before_the_first_fit(tmp_path: Path) -> None:
    """Its rows show the broker's Greeks until then, marked (PRC-13)."""
    async with chains(tmp_path) as rig:
        key = await rig.key("SPX", 20, "SPXW")
        rig.surface.pool.error = RuntimeError("no fit")  # the surface never has one
        view = await rig.open("SPX", Window(key))
        part = await rig.until(
            view, lambda p: any(r.call and r.call.source == BROKER for r in p.rows)
        )
        part = view.snapshot()
        e = info(part, key)
        assert (e.forward_source, math.isnan(e.forward)) == ("none", True)
        assert e.note == "no forward yet: it comes with the first surface fit"
        assert {s.source for r in part.rows for s in (r.call, r.put) if s} == {BROKER}
        rig.surface.pool.error = None
        await rig.fitted(view, key)
        await rig.computed(view, 1)


# --- MKT-06: stale --------------------------------------------------------------------------------


async def test_a_quote_older_than_the_stale_age_is_marked_and_a_new_one_clears_it(
    tmp_path: Path,
) -> None:
    async with chains(tmp_path, start=QUIET_AT) as rig:
        key = await rig.key("SPY", 20)
        view = await rig.open("SPY", Window(key))
        part = await rig.computed(view, 70)
        assert not any(s.stale for r in part.rows for s in (r.call, r.put) if s)
        # The fake's options have no quote time: the age runs from the receipt.
        side = near(part.rows, part.head.spot, 1)[0].put  # type: ignore[union-attr]
        assert side is not None and side.quote_at is not None
        part = await rig.until(
            view, lambda p: any(s.stale for r in p.rows for s in (r.call, r.put) if s), 40
        )
        assert rig.surface.api.backend.market.now() - side.quote_at > timedelta(seconds=30)
        snap = view.snapshot()
        assert all(s.stale for r in snap.rows for s in (r.call, r.put) if s)

        stream = rig.stream_of("SPY", side)
        fake = rig.surface.api.fake.dxlink
        doc = fake.events("Quote", [stream], rig.surface.api.clock.wall())[0]
        fake.sessions[-1].push("Quote", [{**doc, "askPrice": side.ask + 0.01}])
        part = await rig.until(view, lambda p: bool(p.rows))
        sent = [s for r in part.rows for s in (r.call, r.put) if s and s.symbol == side.symbol]
        assert [s.stale for s in sent] == [False]


# --- after the settlement instant -----------------------------------------------------------------


async def test_no_iv_from_the_settlement_instant_on(tmp_path: Path) -> None:
    """SPY's options settle at 16:00 and are quoted to 16:15: the quotes stay, without an IV."""
    start = datetime(2026, 11, 20, 20, 56, tzinfo=UTC)  # 15:56 in New York
    async with chains(tmp_path, start=start) as rig:
        key = f"SPY:{rig.today().isoformat()}"
        view = await rig.open("SPY", Window(key))
        part = await rig.computed(view, 2)  # minutes before the close few are identifiable
        assert info(part, key).note is None
        await rig.surface.api.pass_time(270)  # past 16:00
        part = view.snapshot()
        e = info(part, key)
        assert e.note == "settled: the quotes have no IV" and math.isnan(e.forward)
        assert math.isnan(e.atm_iv) and math.isnan(e.expected_move)
        sides = [s for r in part.rows for s in (r.call, r.put) if s]
        assert {s.source for s in sides} == {NONE}
        assert all(math.isnan(s.iv) and math.isnan(s.delta) for s in sides)
        assert sum(math.isfinite(s.ask) for s in sides) > 20  # still quoted
        assert all(math.isnan(r.iv) for r in part.rows)


# --- PRC-13: the broker's Greeks where the own inversion fails ----------------------------------


async def test_the_brokers_greeks_where_the_own_inversion_fails(tmp_path: Path) -> None:
    async with chains(tmp_path) as rig:
        key = await rig.key("SPY", 20)
        view = await rig.open("SPY", Window(key))
        part = await rig.computed(view, 70)
        side = near(part.rows, part.head.spot, 1)[0].call  # type: ignore[union-attr]
        assert side is not None and side.source == OWN
        rig.surface.api.fake.dxlink.no_bid.add(rig.stream_of("SPY", side))

        def fell_back(p: Part) -> bool:
            return any(
                s.source == BROKER
                for r in p.rows
                for s in (r.call, r.put)
                if s and s.symbol == side.symbol
            )

        await rig.until(view, fell_back)
        row = next(r for r in view.snapshot().rows if r.call and r.call.symbol == side.symbol)
        assert row.call is not None and math.isnan(row.call.bid)
        fake = rig.broker_greeks("SPY", row.call)
        assert (row.call.iv, row.call.delta, row.call.theta) == (fake.iv, fake.delta, fake.theta)


# --- SUB-04: the grace period ---------------------------------------------------------------------


async def test_a_closed_view_keeps_its_subscriptions_for_the_grace_period(tmp_path: Path) -> None:
    async with chains(tmp_path) as rig:
        key = await rig.key("SPY", 20)
        view = await rig.open("SPY", Window(key))
        await rig.computed(view, 70)
        quotes = {s for s in rig.subs(CONSUMER) if s.event == EventType.QUOTE}
        assert len(quotes) == 80 + 1  # and the underlying's
        view.close()
        assert {s for s in rig.subs(HELD) if s.event == EventType.QUOTE} == {
            s for s in quotes if s.symbol.startswith(".")
        }
        assert {s.symbol for s in rig.subs(CONSUMER)} == {"SPY"}  # the underlying's quote and trade

        # Opened again within the grace period: its values are there at once.
        await rig.surface.api.pass_time(10)
        again = await rig.open("SPY", Window(key))
        part = again.snapshot()
        assert sum(_own(r) for r in part.rows) == 80
        again.close()

        # The grace period of the second view ends 20 s after its close.
        await rig.surface.api.pass_time(15)
        assert rig.subs(HELD) != []
        await rig.surface.api.pass_time(10)
        assert CONSUMER not in rig.consumers() and HELD not in rig.consumers()
        assert rig.service._books == {}
        assert rig.surface.service.status().watched == ()
        assert "chain" not in {t.name for t in rig.surface.api.backend.market._takers}  # type: ignore[attr-defined]


async def test_without_a_grace_period_a_closed_view_releases_at_once(tmp_path: Path) -> None:
    async with chains(tmp_path, SMALL + "[chain]\ngrace_s = 0.0\n") as rig:
        view = await rig.open("AAPL", Window(await rig.key("AAPL", 10)))
        view.close()
        await rig.surface.api.pass_time(1)
        assert rig.consumers() == ["surface"] or rig.consumers() == []
        assert rig.service._books == {}


# --- SUB-05: windows ------------------------------------------------------------------------------


async def test_a_view_holds_its_windows_and_never_a_whole_chain(tmp_path: Path) -> None:
    async with chains(tmp_path) as rig:
        key = await rig.key("SPX", 20, "SPXW")
        chain = await rig.surface.api.backend.instruments.chain("SPX")
        e = chain.find("SPXW", date.fromisoformat(key.split(":")[1]))
        assert e is not None and len(e.strikes) > 200
        lo, hi = e.strikes[100], e.strikes[129]
        view = await rig.open("SPX", Window(key, lo, hi), Window(key, lo, hi))
        part = view.snapshot()
        assert [r.strike for r in part.rows] == list(e.strikes[100:130])  # once, though asked twice
        subs = rig.subs(CONSUMER)
        assert len(subs) == 2 + 4 * 60  # the index's quote and trade; four pairs a contract
        assert [s.event for s in subs[2:62]] == [EventType.QUOTE] * 60
        # From the money outwards: the window lies below the money, so its highest strike first.
        assert subs[2].symbol == e.call_streams[129]
        view.close()

        with pytest.raises(WindowRefused, match="a view holds 8"):
            rig.service.open("SPX", [Window(key)] * 9)
        with pytest.raises(WindowRefused, match="both ends"):
            rig.service.open("SPX", [Window(key, lo, None)])
        with pytest.raises(WindowRefused, match="the range"):
            rig.service.open("SPX", [Window(key, hi, lo)])
        wide = rig.service.open("SPX", [Window(key, e.strikes[0], e.strikes[150])])
        with pytest.raises(WindowRefused, match="151 strikes .* holds 120 at most"):
            await wide.ready()
        wide.close()


async def test_an_unknown_expiration_and_an_unknown_symbol_are_notes(tmp_path: Path) -> None:
    async with chains(tmp_path) as rig:
        view = await rig.open("SPY", Window("SPY:2020-01-17"), Window(await rig.key("SPY", 3)))
        part = view.snapshot()
        assert [(n.subject, n.kind) for n in part.notes or ()] == [("SPY:2020-01-17", "window")]
        assert len(part.rows) == 40
        view.close()

        view = await rig.open("NOPE", Window("NOPE:2026-12-18"))
        part = view.snapshot()
        assert part.rows == () and part.expirations == ()
        assert [(n.subject, n.kind) for n in part.notes or ()] == [("NOPE", "unknown")]
        view.close()


async def test_a_cut_by_the_budget_is_a_note(tmp_path: Path) -> None:
    more = CHAIN + "[subscriptions]\nlimit = 30\nreserve = 0\n"
    async with chains(tmp_path, more) as rig:
        view = await rig.open("AAPL", Window(await rig.key("AAPL", 10)))
        part = await rig.until(
            view, lambda p: p.notes is not None and any(n.kind == "cut" for n in p.notes)
        )
        cut = next(n for n in part.notes or () if n.kind == "cut")
        assert cut.subject == "AAPL" and cut.text.endswith(
            "quotes are cut by the subscription budget"
        )
        view.close()


async def test_the_views_come_before_the_surface_in_their_class(tmp_path: Path) -> None:
    async with chains(tmp_path) as rig:
        view = await rig.open("AAPL", Window(await rig.key("AAPL", 10)))
        assert rig.consumers()[:3] == [CONSUMER, HELD, "surface"]
        view.close()


# --- REF-03: earnings and ex-dividend dates; the expected move ------------------------------------


async def test_earnings_and_ex_dividend_dates_by_expiration(tmp_path: Path) -> None:
    async with chains(tmp_path) as rig:
        view = await rig.open("AAPL", Window(await rig.key("AAPL", 10)))
        part = view.snapshot()
        head = part.head
        report = rig.today() + timedelta(days=9)  # the fake's: after the close
        assert head is not None and (head.earnings, head.earnings_time) == (report, "after_close")
        for _, e in part.expirations:
            assert e.earnings_before == (e.expiration_date > report), e.key
        view.close()

        view = await rig.open("SPY", Window(await rig.key("SPY", 3)))
        part = view.snapshot()
        assert part.head is not None and part.head.earnings is None
        first = date(2026, 12, 21)  # the fake's last ex-date, 2026-09-21, a quarter on
        for _, e in part.expirations:
            dates = [d.ex_date for d in e.ex_dividends]
            assert all(d.projected for d in e.ex_dividends)
            assert dates == sorted(dates) and all(
                rig.today() < d <= e.expiration_date for d in dates
            )
            assert (dates[:1] == [first]) == (e.expiration_date >= first), e.key
            assert all(85 <= (b - a).days <= 95 for a, b in zip(dates, dates[1:], strict=False)), (
                e.key
            )
        view.close()


async def test_the_expected_move_and_the_ivs_of_an_expiration(tmp_path: Path) -> None:
    async with chains(tmp_path, start=QUIET_AT) as rig:
        key = await rig.key("SPY", 20)
        view = await rig.open("SPY", Window(key))
        # Every open expiration gets one once the surface's cores, which hold the quotes around the
        # money, are there. Today's are over at 17:00.
        part = await rig.holds(
            view,
            lambda p: all(
                math.isfinite(e.expected_move) == (e.note is None) for _, e in p.expirations
            ),
        )
        assert sum(e.note is None for _, e in part.expirations) > 20
        e = info(part, key)
        assert e.expected_move_method == METHOD
        assert math.isfinite(e.broker_iv) and e.atm_iv == pytest.approx(e.broker_iv, abs=0.005)
        # The rule of s3t.pricing.moves on the market's quotes around the forward.
        x = next(x for x in rig.service._books["SPY"].exps if x.key == key)
        money = e.forward
        i = min(range(len(x.strikes)), key=lambda k: abs(x.strikes[k] - money))
        market, ex = rig.surface.api.backend.market, x.expiration
        calls = [ex.call_streams[k] or "" for k in range(i - 2, i + 3)]
        puts = [ex.put_streams[k] or "" for k in range(i - 2, i + 3)]
        q = EventType.QUOTE
        expected = expected_move(
            x.strikes[i - 2 : i + 3],
            market.column(q, "bid", calls),
            market.column(q, "ask", calls),
            market.column(q, "bid", puts),
            market.column(q, "ask", puts),
            money,
        )
        assert e.expected_move == pytest.approx(expected) and 5 < expected < 40
        view.close()


async def test_an_adjusted_root_has_quotes_and_the_brokers_greeks(tmp_path: Path) -> None:
    async with chains(tmp_path) as rig:
        doc = rig.surface.api.fake.market.option_chain("AAPL")
        assert doc is not None
        adjusted = copy.deepcopy(doc["items"][0])
        adjusted.update(
            {
                "root-symbol": "AAPL1",
                "option-chain-type": "Non-standard",
                "shares-per-contract": 150,
            }
        )
        doc["items"].append(adjusted)
        chain = await rig.surface.api.backend.instruments.chain("AAPL")
        e = chain.of_root("AAPL1")[2]
        key = f"AAPL1:{e.expiration_date.isoformat()}"
        view = await rig.open("AAPL", Window(key))
        part = view.snapshot()
        assert info(part, key).note == "an adjusted root: no forward, no own IV and no surface"
        assert not info(part, key).standard
        view.close()


# --- the bulk snapshot (API-06) -------------------------------------------------------------------


async def test_a_whole_expiration_with_what_the_backend_holds(tmp_path: Path) -> None:
    async with chains(tmp_path) as rig:
        key = await rig.key("SPY", 20)
        # Without a view: every strike listed, nothing subscribed for it.
        whole = await rig.call(rig.service.expiration("SPY", key))
        assert whole.expiration.key == key and len(whole.rows) == whole.expiration.strikes
        assert CONSUMER not in rig.consumers() and rig.service._books == {}
        view = await rig.open("SPY", Window(key))
        await rig.computed(view, 70)
        whole = await rig.call(rig.service.expiration("SPY", key))
        assert len(whole.rows) == whole.expiration.strikes
        own = [r for r in whole.rows if _own(r)]
        assert len(own) >= 40  # the window's, and what the surface's wings hold
        view.close()
        with pytest.raises(ChainUnavailable, match="no open expiration"):
            await rig.call(rig.service.expiration("SPY", "SPY:2020-01-17"))
        with pytest.raises(ChainUnavailable, match="knows no stock") as refused:
            await rig.call(rig.service.expiration("NOPE", "NOPE:2026-12-18"))
        assert refused.value.unknown


# --- the status and the end -----------------------------------------------------------------------


async def test_the_status_and_nothing_stays_after_the_end(tmp_path: Path) -> None:
    async with chains(tmp_path) as rig:
        assert rig.service.status().underlyings == () and rig.consumers() == []
        view = await rig.open("SPY", Window(await rig.key("SPY", 20)))
        await rig.computed(view, 70)
        status = rig.service.status()
        assert status.grace_s == 20.0 and status.flushes > 0 and status.largest == 80
        assert status.compute_ms_max is not None and status.compute_ms_max > 0
        (spy,) = status.underlyings
        assert (spy.underlying, spy.views, spy.held, spy.contracts) == ("SPY", 1, 0, 80)
        await rig.service.aclose()
        assert rig.service._books == {} and rig.service._tasks == set()
        assert CONSUMER not in rig.consumers()


# --- the solver's warning -------------------------------------------------------------------------


def test_a_repeated_warning_is_let_through_the_first_and_every_hundredth_time(
    caplog: pytest.LogCaptureFixture,
) -> None:
    logger = logging.getLogger("tests.chain.repeat")
    limit = RepeatLimit(100)
    logger.addFilter(limit)
    try:
        with caplog.at_level(logging.DEBUG, logger.name):
            for n in range(250):
                logger.warning("IV bisection did not converge: %d / %d", n, 7)
            logger.info("below a warning nothing is limited")
            logger.info("below a warning nothing is limited")
        lines = [r.getMessage() for r in caplog.records]
        assert lines[:3] == [
            "IV bisection did not converge: 0 / 7",
            "IV bisection did not converge: 99 / 7 (100 times so far)",
            "IV bisection did not converge: 199 / 7 (200 times so far)",
        ]
        assert lines[3:] == ["below a warning nothing is limited"] * 2
    finally:
        logger.removeFilter(limit)


def test_the_filter_is_on_the_solvers_logger_once() -> None:
    first = limit_repeats()
    assert limit_repeats() is first
    assert first in logging.getLogger("s3t.pricing.bsm").filters
