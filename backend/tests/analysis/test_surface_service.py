"""The surface service on the fake broker (WP-1.1, part 2): the watches, the subscriptions it
chooses, the fits on the timer and on demand, and what a fit says of its inputs. For SPY (an ETF
with dividends), SPX (an index with three option roots) and AAPL (a stock with a dividend inside
its nearest expirations).

The fits run in place here (``conftest.InPlace``). ``tests/api/test_health.py`` has the one test
through real worker processes.
"""

import asyncio
import copy
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest

from s3t.analysis.surface import FILL_S, RETRY_S, SurfaceUnavailable
from s3t.broker.fake import market as synthetic
from s3t.broker.interface import EventType, Sub
from s3t.pricing import parity
from s3t.pricing.surface import NO_FIT, OK, SPARSE, SSVI, SVI, SliceInput, Warm
from s3t.subs.feed import Priority
from s3t.system.pool import PoolBroken

from .conftest import QUIET, REFRESH, SMALL, surfaces

pytestmark = pytest.mark.anyio

TODAY = date(2026, 11, 20)
DAYS_90 = TODAY + timedelta(days=90)


def day_of(key: str) -> date:
    return date.fromisoformat(key.split(":")[1])


# --- the three underlyings, with the default settings ---------------------------------------------


async def test_spy_the_set_is_chosen_in_steps_and_the_surface_is_the_markets(
    tmp_path: Path,
) -> None:
    async with surfaces(tmp_path, QUIET) as rig:
        watch = rig.service.watch("SPY")
        assert watch.latest is None and watch.problem is None

        # First the spot: the quote and the trade of the underlying.
        await rig.until(lambda: rig.use() is not None and rig.use().wanted > 0)  # type: ignore[union-attr]
        use = rig.use()
        assert use is not None and (use.wanted, use.priority) == (2, Priority.VIEWS)

        # Then the cores around it: 6 strikes each side, calls and puts, of every expiration.
        await rig.until(lambda: rig.status("SPY").pairs > 2)
        status = rig.status("SPY")
        assert status.expirations == 28 and status.pairs == 2 + 28 * 24
        assert status.fits == 0 and status.filling

        # Then the wings, which need the at-the-money IV of a first fit.
        await rig.until(lambda: watch.latest is not None)
        assert rig.status("SPY").pairs > 2 + 28 * 24
        fit = await rig.filled(watch)
        status = rig.status("SPY")
        assert status.pairs == 1460  # of the synthetic chain, which lists 10% each way densely
        assert fit.values == 1458 and fit.not_live == 0
        assert rig.use().granted == 1460  # type: ignore[union-attr]
        assert status.slices == {OK: 17, SPARSE: 11} and status.problem is None
        assert status.loop_ms_max > 0 and status.last_fit_at == fit.at

        assert fit.underlying == "SPY" and fit.number == status.fits
        assert abs(fit.spot - rig.api.fake.dxlink.ticks.price("SPY", fit.at)) < 0.02  # type: ignore[operator]
        assert [s.key for s in fit.surface.slices] == list(fit.expirations)
        for s in fit.surface.slices:
            expiry = fit.expirations[s.key]
            assert (expiry.key, expiry.root, expiry.expiration_date) == (
                s.key,
                "SPY",
                day_of(s.key),
            )
            # Priced to the close, not to the 16:15 the broker states.
            assert expiry.settles_at.astimezone(synthetic.NY).hour in (16, 13)
            assert expiry.settles_at.astimezone(synthetic.NY).minute == 0
            assert s.forward_source == parity.PARITY
            market = rig.market_forward("SPY", "SPY", s.key, fit.at)
            assert s.forward == pytest.approx(market, rel=1e-4)
            vol = rig.api.fake.market.atm_vol(0.14, day_of(s.key))
            assert s.atm_iv == pytest.approx(vol, abs=0.004)
            assert s.butterfly is not None and s.butterfly.ok
            if day_of(s.key) <= DAYS_90:  # its wings are subscribed
                assert (s.status, s.model) == (OK, SVI) and s.fitted >= 15
            else:  # the strikes around the money only: the surface's SSVI smile
                assert (s.status, s.model) == (SPARSE, SSVI) and s.quotes == 12
        # The dividends that the forwards imply are the schedule's.
        spanning = [
            s for s in fit.surface.slices if s.parity is not None and s.parity.div_flag is not None
        ]
        assert len(spanning) > 15 and not any(s.parity.div_flag for s in spanning)  # type: ignore[union-attr]


async def test_spx_an_index_has_no_dividends_and_a_slice_per_root(tmp_path: Path) -> None:
    async with surfaces(tmp_path, QUIET) as rig:
        rig.api.fake.dxlink.index_without_quote = True  # its spot is the last trade
        watch = rig.service.watch("SPX")
        fit = await rig.filled(watch)
        status = rig.status("SPX")
        assert (status.expirations, status.pairs) == (41, 1934)
        assert status.slices == {OK: 19, SPARSE: 22}
        assert abs(fit.spot - rig.api.fake.dxlink.ticks.price("SPX", fit.at)) < 0.02  # type: ignore[operator]

        keys = list(fit.expirations)
        # SPX (AM) and SPXW (PM) of one date are two slices, the morning one first.
        assert keys.index("SPX:2026-12-18") + 1 == keys.index("SPXW:2026-12-18")
        am, pm = fit.expirations["SPX:2026-12-18"], fit.expirations["SPXW:2026-12-18"]
        assert pm.settles_at - am.settles_at == timedelta(hours=6, minutes=30)
        # What settled at this morning's open is no slice; today's afternoon expiration is one.
        assert "SPX:2026-11-20" not in keys and "SPXO:2026-11-20" not in keys
        assert keys[0] == "SPXW:2026-11-20"

        for s in fit.surface.slices:
            assert s.forward_source == parity.PARITY
            root = s.key.split(":")[0]
            market = rig.market_forward("SPX", root, s.key, fit.at)
            assert s.forward == pytest.approx(market, rel=1e-4)
            assert s.parity is not None and s.parity.div_flag is None  # no schedule
            near = day_of(s.key) <= DAYS_90
            assert s.status == (OK if near else SPARSE)
        # The broker was not asked for the metrics of an index: it states no dividend.
        paths = [r.path for r in rig.api.fake.rest.requests]
        assert any("option-chains" in p for p in paths)
        assert not any("market-metrics" in p for p in paths)


async def test_aapl_a_dividend_inside_the_nearest_expirations(tmp_path: Path) -> None:
    async with surfaces(tmp_path, QUIET) as rig:
        watch = rig.service.watch("AAPL")
        fit = await rig.filled(watch)
        status = rig.status("AAPL")
        assert (status.expirations, status.pairs) == (21, 591)
        ex_date = TODAY + timedelta(days=11)  # the last one 80 days ago, and 91 days on
        amount = 1.04 / 4
        for s in fit.surface.slices:
            assert s.forward_source == parity.PARITY and s.parity is not None
            market = rig.market_forward("AAPL", "AAPL", s.key, fit.at)
            assert s.forward == pytest.approx(market, rel=2e-4)
            assert s.forward_model == pytest.approx(market, rel=2e-4)
            if day_of(s.key) < ex_date:
                assert s.parity.div_flag is None and abs(s.parity.div_implied) < 0.03
            elif day_of(s.key) < ex_date + timedelta(days=91):
                # One dividend before it, and the forward implies it within a few cents.
                assert s.parity.div_implied == pytest.approx(amount, abs=0.03)
        inputs: list[SliceInput] = rig.pool.calls[-1][0]
        assert all(i.pv_div > 0.25 for i in inputs if day_of(i.key) >= ex_date)
        assert all(i.pv_div == 0 for i in inputs if day_of(i.key) < ex_date)
        assert not any(i.exdiv_near for i in inputs)  # 7 sessions away


async def test_in_the_money_calls_are_left_out_near_an_ex_date(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """PRC-05: within 5 sessions before an ex-date that the expiration spans."""
    sooner = replace(synthetic.UNDERLYINGS["AAPL"], dividend_days_ago=88)  # ex in 3 days
    monkeypatch.setitem(synthetic.UNDERLYINGS, "AAPL", sooner)
    async with surfaces(tmp_path) as rig:
        watch = rig.service.watch("AAPL")
        fit = await rig.filled(watch)
        ex_date = TODAY + timedelta(days=3)
        inputs: list[SliceInput] = rig.pool.calls[-1][0]
        assert [i.exdiv_near for i in inputs] == [day_of(i.key) >= ex_date for i in inputs]
        assert sum(i.exdiv_near for i in inputs) == len(inputs) - 1  # all but today's
        spanning = fit.surface.slices[1]
        assert spanning.parity is not None and spanning.forward_source == parity.PARITY
        assert 3 <= spanning.parity.offered <= 6  # the strikes at and above the money only


async def test_an_adjusted_root_is_left_out_and_counted(tmp_path: Path) -> None:
    """Its contracts deliver something else than the underlying, so parity on the underlying's
    price does not hold for them. The mark is the broker's chain type."""
    async with surfaces(tmp_path) as rig:
        doc = rig.api.fake.market.option_chain("AAPL")
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
        chain = await rig.api.backend.instruments.chain("AAPL")
        assert chain.roots() == ("AAPL", "AAPL1") and len(chain.of_root("AAPL1")) == 21

        watch = rig.service.watch("AAPL")
        fit = await rig.filled(watch)
        status = rig.status("AAPL")
        assert (status.expirations, status.left_out) == (21, 21)
        assert {expiry.root for expiry in fit.expirations.values()} == {"AAPL"}
        assert len(fit.surface.slices) == 21
        assert rig.use().wanted == fit.values + 2  # type: ignore[union-attr]


async def test_nothing_is_left_out_of_the_synthetic_chains(tmp_path: Path) -> None:
    async with surfaces(tmp_path) as rig:
        watch = rig.service.watch("SPX")
        await rig.until(lambda: watch.latest is not None)
        # What settled this morning is over, not left out.
        assert rig.status("SPX").left_out == 0


# --- when it fits ---------------------------------------------------------------------------------


async def test_a_fit_on_the_timer_and_none_when_nothing_changed(tmp_path: Path) -> None:
    async with surfaces(tmp_path) as rig:
        watch = rig.service.watch("AAPL")
        first = await rig.filled(watch)
        await rig.api.pass_time(REFRESH + 1)  # the timer: the quotes move every second
        second = watch.latest
        assert second is not None and second.number == first.number + 1
        assert (second.at - first.at).total_seconds() == pytest.approx(REFRESH, abs=FILL_S + 1)
        assert second.surface is not first.surface

        rig.api.fake.dxlink.silent = True  # the market says nothing more
        await rig.api.pass_time(REFRESH + 1)  # what came before the silence
        latest, skipped = watch.latest, rig.status("AAPL").skipped
        assert latest is not None
        await rig.api.pass_time(3 * REFRESH)
        assert watch.latest is latest and rig.status("AAPL").skipped == skipped + 3

        # On demand it fits at once, whatever has changed, and warm from the fit before.
        asked = await rig.on_demand(watch)
        assert asked.number == latest.number + 1 and asked.at > latest.at
        assert asked.values == latest.values
        assert rig.pool.calls[0][1] is None
        warm = rig.pool.calls[-1][1]
        assert isinstance(warm, Warm) and warm.shape == latest.surface.shape
        assert set(warm.x) == {s.key for s in latest.surface.slices if s.status == OK}
        assert len(warm.x) >= 4


async def test_next_waits_for_the_fit_after_the_one_it_was_given(tmp_path: Path) -> None:
    async with surfaces(tmp_path) as rig:
        watch = rig.service.watch("AAPL")
        waiting = asyncio.create_task(watch.next())
        fit = await rig.filled(watch)
        assert waiting.done() and waiting.result().number == 1
        assert (await watch.next(0)) is fit  # there already
        coming = asyncio.create_task(watch.next())
        await rig.api.clock.settle()
        assert not coming.done()
        await rig.api.pass_time(REFRESH + 1)
        assert coming.done() and coming.result().number == fit.number + 1


async def test_values_that_the_session_has_not_confirmed_are_used_and_counted(
    tmp_path: Path,
) -> None:
    async with surfaces(tmp_path) as rig:
        watch = rig.service.watch("AAPL")
        before = await rig.filled(watch)
        assert before.not_live == 0
        rig.api.fake.dxlink.drop()
        await rig.api.clock.settle()
        assert rig.api.backend.market.live_since is None
        during = await rig.on_demand(watch)
        assert during.not_live == during.values == before.values
        statuses = [s.status for s in during.surface.slices]
        assert statuses == [s.status for s in before.surface.slices] and OK in statuses
        assert rig.status("AAPL").not_live == during.values
        # The new session brings every value again.
        await rig.until(lambda: rig.api.backend.market.live_since is not None)
        await rig.api.pass_time(15)
        assert (await rig.on_demand(watch)).not_live == 0


async def test_no_slice_after_its_settlement_instant(tmp_path: Path) -> None:
    """AAPL's options are quoted until 16:15 and settle at 16:00."""
    start = datetime(2026, 11, 20, 20, 59, 0, tzinfo=UTC)  # 15:59 in New York
    async with surfaces(tmp_path, SMALL, start=start) as rig:
        watch = rig.service.watch("AAPL")
        await rig.until(lambda: watch.latest is not None)
        today = "AAPL:2026-11-20"
        early = watch.latest
        assert early is not None and today in early.expirations
        assert early.surface.slices[0].key == today and early.surface.slices[0].tau < 1e-5
        pairs = rig.status("AAPL").pairs
        await rig.until(
            lambda: rig.api.backend.broker.data_time() > early.expirations[today].settles_at
        )
        late = await rig.on_demand(watch)
        assert today not in late.expirations and late.surface.slice(today) is None
        assert len(late.surface.slices) == len(early.surface.slices) - 1
        # Its pairs are asked for no longer.
        assert rig.status("AAPL").expirations == len(late.surface.slices)
        assert rig.status("AAPL").pairs < pairs


async def test_the_documents_are_read_again_when_the_day_of_the_data_changes(
    tmp_path: Path,
) -> None:
    async with surfaces(tmp_path) as rig:
        watch = rig.service.watch("AAPL")
        fit = await rig.filled(watch)
        state = rig.service._watched["AAPL"]
        assert state.day == TODAY
        state.day = TODAY - timedelta(days=1)  # as if the fits had begun the day before
        chosen, rows, read = dict(state.choices), state.rows, state.exps
        await rig.api.pass_time(REFRESH + 1)
        assert state.day == TODAY and state.choices == chosen
        # The expirations are the new reading's, and the rows were made from them.
        assert state.exps is not read and state.rows is not rows and state.rows is not None
        assert {id(x) for x, _, _ in state.rows.spans} == {id(x) for x in state.exps}
        assert watch.latest is not None and watch.latest.number == fit.number + 1
        assert rig.use().wanted == fit.values + 2  # type: ignore[union-attr]


# --- watches --------------------------------------------------------------------------------------


async def test_two_watches_share_one_surface_and_the_last_release_ends_everything(
    tmp_path: Path,
) -> None:
    async with surfaces(tmp_path) as rig:
        market = rig.api.backend.market
        one, two = rig.service.watch("AAPL"), rig.service.watch("AAPL")
        assert rig.status("AAPL").watchers == 2 and len(rig.service.status().watched) == 1
        fit = await rig.filled(one)
        assert two.latest is fit
        assert [t.name for t in market._takers] == ["surface"]

        one.release()
        one.release()  # once is enough
        assert rig.status("AAPL").watchers == 1
        with pytest.raises(SurfaceUnavailable, match="released"):
            await one.next()
        with pytest.raises(SurfaceUnavailable, match="released"):
            await one.fit()
        await rig.api.pass_time(REFRESH + 1)
        assert two.latest is not None and two.latest.number == fit.number + 1

        two.release()
        await rig.api.pass_time(1)
        assert rig.service.status().watched == () and rig.use() is None
        assert market._takers == []
        assert not [t for t in asyncio.all_tasks() if t.get_name().startswith("surface")]
        calls = len(rig.pool.calls)
        await rig.api.pass_time(3 * REFRESH)
        assert len(rig.pool.calls) == calls
        # The values of its pairs are gone from the market state with the grant.
        assert market.status().values[EventType.QUOTE] == 0

        # A new watch begins anew.
        again = rig.service.watch("AAPL")
        assert again.latest is None
        await rig.until(lambda: again.latest is not None)
        assert again.latest is not None and again.latest.number == 1
        assert rig.pool.calls[-1][1] is None  # no warm start from the watch before


async def test_a_set_is_not_filled_while_the_feed_still_sends_its_pairs(tmp_path: Path) -> None:
    """The manager sends 150 pairs a second, in the order of the demand: with three underlyings
    at once the pairs of the last wait for those of the first. Its set fills when they came, not
    when nothing came for a while."""
    async with surfaces(tmp_path) as rig:
        names = ("SPY", "SPX", "AAPL")
        watches = {name: rig.service.watch(name) for name in names}
        filled: dict[str, tuple[int, int, int]] = {}

        def all_filled() -> bool:
            for name in names:
                status, fit = rig.status(name), watches[name].latest
                if name not in filled and fit is not None and not status.filling:
                    pending = rig.api.backend.feed.status().pending
                    present = rig.service._watched[name].present
                    filled[name] = (status.pairs - 2, present, pending)
            return len(filled) == len(names)

        await rig.until(all_filled, 180)
        for name in names:
            pairs, present, pending = filled[name]
            assert (present, pending) == (pairs, 0), name
        await rig.api.pass_time(2)
        for name in names:
            fit = await rig.on_demand(watches[name])
            assert fit.values == rig.status(name).pairs - 2, name
            assert {s.forward_source for s in fit.surface.slices} == {parity.PARITY}, name
            assert fit.surface.slices[2].status == OK, name


async def test_the_demand_is_spots_then_cores_then_wings_and_a_short_budget_cuts_wings(
    tmp_path: Path,
) -> None:
    """SUB-03: the consumer states the most important pair first. A slice without its wings is
    sparse."""
    budget = 4 + 28 * 24 + 21 * 24 + 60  # both spots, every core, and 60 pairs of wings
    more = SMALL + f"[subscriptions]\nlimit = {budget + 100}\nreserve = 100\n"
    async with surfaces(tmp_path, more) as rig:
        spy, aapl = rig.service.watch("SPY"), rig.service.watch("AAPL")
        await rig.filled(spy)
        await rig.filled(aapl)
        await rig.api.pass_time(2)  # the last grant is flushed
        fit, etf = await rig.on_demand(aapl), await rig.on_demand(spy)
        states = [rig.service._watched[name] for name in ("SPY", "AAPL")]
        spots = [sub for u in states for sub in u.spot_subs]
        cores = [sub for u in states for sub in u.rows.core]  # type: ignore[union-attr]
        wings = [sub for u in states for sub in u.rows.wings]  # type: ignore[union-attr]
        stated = rig.api.backend.feed._consumers["surface"].subs  # type: ignore[attr-defined]
        assert list(stated) == spots + cores + wings
        assert spots[:2] == [Sub("SPY", EventType.QUOTE), Sub("SPY", EventType.TRADE)]
        assert len(cores) == (28 + 21) * 24 and len(wings) > 200

        use = rig.use()
        assert use is not None and use.wanted == len(stated) and use.granted == budget
        granted = set(rig.api.backend.feed.granted())
        assert granted == set(stated[:budget])  # the 60 nearest pairs of SPY's wings
        # AAPL has its cores and no wing at all: every slice still has its forward.
        assert not granted & set(states[1].rows.wings)  # type: ignore[union-attr]
        assert fit.values == 21 * 24 < rig.status("AAPL").pairs - 2
        assert {s.forward_source for s in fit.surface.slices} == {parity.PARITY}
        # SPY: a near slice has its wings; one that lost them is sparse, on its 12 strikes.
        month = [s for s in etf.surface.slices if day_of(s.key) <= TODAY + timedelta(days=30)]
        assert month[1].status == OK and month[1].quotes > 12
        assert (month[-1].status, month[-1].model, month[-1].quotes) == (SPARSE, SSVI, 12)


# --- what goes wrong ------------------------------------------------------------------------------


@pytest.mark.parametrize("symbol", ["NOPE", "/ES"])
async def test_a_symbol_without_a_surface_has_a_problem(tmp_path: Path, symbol: str) -> None:
    """A futures product is not fitted yet (the plan, section 8)."""
    async with surfaces(tmp_path) as rig:
        watch = rig.service.watch(symbol)
        waiting = asyncio.create_task(watch.next())
        await rig.api.pass_time(3)
        assert watch.problem == "the broker knows no stock, ETF or index of this symbol"
        assert rig.status(symbol).problem == watch.problem and watch.latest is None
        with pytest.raises(SurfaceUnavailable, match="knows no stock"):
            await waiting
        with pytest.raises(SurfaceUnavailable, match="knows no stock"):
            await watch.fit()
        assert rig.use() is None  # nothing is asked of the feed for it
        watch.release()
        assert rig.service.status().watched == ()


async def test_a_broker_that_cannot_be_asked_is_asked_again(tmp_path: Path) -> None:
    async with surfaces(tmp_path) as rig:
        rig.api.fake.rest.disconnect(5)  # as many as the client tries
        watch = rig.service.watch("AAPL")
        await rig.until(lambda: watch.problem is not None)
        assert watch.problem == "the broker could not be asked; it is tried again"
        assert rig.status("AAPL").pairs == 0
        await rig.api.pass_time(RETRY_S + 1)
        assert watch.problem is None
        fit = await rig.filled(watch)
        assert fit.surface.slices[1].status == OK


@pytest.mark.parametrize("error", [RuntimeError("boom"), PoolBroken("a worker ended")])
async def test_a_fit_that_fails_is_counted_and_the_last_surface_stays(
    tmp_path: Path, error: Exception, caplog: pytest.LogCaptureFixture
) -> None:
    async with surfaces(tmp_path) as rig:
        watch = rig.service.watch("AAPL")
        good = await rig.filled(watch)
        rig.pool.error = error
        with pytest.raises(SurfaceUnavailable, match=f"the fit failed .{type(error).__name__}"):
            await rig.on_demand(watch)
        status = rig.status("AAPL")
        assert status.failures == 1 and status.fits == good.number
        assert status.problem == f"the fit failed ({type(error).__name__})"
        assert watch.latest is good and status.last_fit_at == good.at
        assert f"surface: AAPL: the fit failed ({type(error).__name__})" in caplog.text
        await rig.api.pass_time(REFRESH + 1)  # the timer's fit fails too
        assert rig.status("AAPL").failures == 2 and watch.latest is good

        rig.pool.error = None
        after = await rig.on_demand(watch)
        assert after.number == good.number + 3 and watch.problem is None
        assert rig.status("AAPL").fits == good.number + 1


async def test_a_fit_on_demand_before_there_are_quotes_waits_for_them(tmp_path: Path) -> None:
    async with surfaces(tmp_path) as rig:
        watch = rig.service.watch("AAPL")
        asked = asyncio.create_task(watch.fit())
        await rig.api.clock.settle()
        assert not asked.done()
        await rig.until(asked.done)
        fit = asked.result()
        assert fit.number == 1 and fit.values > 0
        assert NO_FIT not in {s.status for s in fit.surface.slices[:3]}


async def test_closing_the_service_ends_its_watches(tmp_path: Path) -> None:
    async with surfaces(tmp_path) as rig:
        watch = rig.service.watch("AAPL")
        await rig.filled(watch)
        await rig.service.aclose()
        await rig.api.pass_time(1)
        assert rig.service.status().watched == () and rig.use() is None
        with pytest.raises(SurfaceUnavailable):
            await watch.next()
        with pytest.raises(RuntimeError, match="closed"):
            rig.service.watch("SPY")
        watch.release()  # after the end it does nothing
