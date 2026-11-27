"""The surface of one underlying (WP-1.1): the fit of all expirations from quotes, its statuses
and checks, the distribution of PRC-07 and the interpolation in time. New tests; the pieces
ported from `volcalc` have theirs in test_svi.py and test_parity.py.

The quotes are made from known smiles with a symmetric spread, so the mids are exact and the
fit has a truth to recover."""

import pickle
from dataclasses import replace

import numpy as np
import pytest
from scipy.special import ndtr

from s3t.pricing import parity, ssvi
from s3t.pricing.black76 import black_price
from s3t.pricing.surface import (
    FAIL,
    FUTURE,
    NO_FIT,
    OK,
    SPARSE,
    SSVI,
    SVI,
    Settings,
    SliceInput,
    Surface,
    fit_surface,
)

R = 0.04
SPOT = 100.0
SHAPE = np.array([-0.6, 1.2, 0.35])  # one SSVI surface: every slice of it is a raw SVI too
TAUS = {"7d": 7 / 365, "30d": 30 / 365, "90d": 90 / 365}
VOL = {"7d": 0.20, "30d": 0.21, "90d": 0.22}


def theta(key: str) -> float:
    return VOL[key] ** 2 * TAUS[key]


def true_w(key: str, k):
    return ssvi.w_derivs(theta(key), SHAPE, np.asarray(k, dtype=np.float64))[0]


def strikes_for(key: str, n: int = 41, width: float = 3.0) -> np.ndarray:
    """n strikes over `width` at-the-money deviations each way."""
    s = width * np.sqrt(theta(key))
    return SPOT * np.exp(np.linspace(-s, s, n))


def chain(key: str, strikes=None, *, market: float | None = None, w=None, **more) -> SliceInput:
    """The quotes of one expiration on a smile w(k). `market` is the forward the quotes are
    priced on, the model forward unless given; `more` goes to the input as it is."""
    tau = TAUS[key]
    df = float(np.exp(-R * tau))
    model = SPOT * float(np.exp(R * tau))
    f = model if market is None else market
    strike = strikes_for(key) if strikes is None else np.asarray(strikes, dtype=np.float64)
    k = np.log(strike / f)
    iv = np.sqrt((true_w(key, k) if w is None else w(k)) / tau)
    call = black_price(f, strike, tau, df, iv, True)
    put = black_price(f, strike, tau, df, iv, False)
    hc, hp = np.maximum(0.005 * call, 1e-4), np.maximum(0.005 * put, 1e-4)
    fields = dict(
        key=key,
        tau=tau,
        df=df,
        strike=strike,
        call_bid=call - hc,
        call_ask=call + hc,
        put_bid=put - hp,
        put_ask=put + hp,
        spot=SPOT,
        model=model,
    )
    return SliceInput(**{**fields, **more})


@pytest.fixture(scope="module")
def surface() -> Surface:
    return fit_surface([chain("90d"), chain("7d"), chain("30d")])


# --- the fit ------------------------------------------------------------------------------------


def test_slices_come_in_the_order_of_time(surface: Surface):
    assert [s.key for s in surface.slices] == ["7d", "30d", "90d"]
    assert surface.slice("30d") is surface.slices[1] and surface.slice("1y") is None


def test_the_fit_recovers_forward_and_smile(surface: Surface):
    for s in surface.slices:
        assert s.status == OK and s.model == SVI and s.outliers == 0
        assert s.forward_source == parity.PARITY
        assert s.forward == pytest.approx(SPOT * np.exp(R * s.tau), abs=1e-6)
        k = np.linspace(s.k_min, s.k_max, 50)
        assert s.w(k)[0] == pytest.approx(true_w(s.key, k), rel=1e-4)
        assert s.atm_iv == pytest.approx(VOL[s.key], abs=1e-5)
        assert s.rmse < 1e-5 and s.quotes >= s.fitted > 20
        assert s.butterfly is not None and s.butterfly.ok
    assert surface.slices[0].calendar_ok is None  # nothing before it
    assert all(s.calendar_ok for s in surface.slices[1:])
    assert surface.shape == pytest.approx(tuple(SHAPE), abs=1e-3)


def test_quotes_far_out_are_candidates_but_not_fitted():
    wide = fit_surface([chain("30d", strikes_for("30d", 61, 4.0))]).slices[0]
    assert wide.status == OK and wide.quotes > wide.fitted  # |delta| < 0.02 is left out
    assert wide.k_max < 4.0 * np.sqrt(theta("30d"))


def test_a_side_without_a_bid_is_no_quote():
    full = chain("30d")
    bid = full.put_bid.copy()
    bid[:5] = 0.0  # the five lowest puts have no bid
    cut = fit_surface([replace(full, put_bid=bid)]).slices[0]
    whole = fit_surface([full]).slices[0]
    assert cut.quotes == whole.quotes - 5 and cut.status == OK


def test_a_thin_expiration_takes_the_ssvi_smile():
    near = strikes_for("30d", 5, 0.4)  # five strikes near the money: no wings
    out = fit_surface([chain("7d"), chain("30d", near), chain("90d")])
    thin = out.slice("30d")
    assert thin is not None and thin.status == SPARSE and thin.model == SSVI
    assert thin.x is None and thin.svi_params is None and thin.fitted == 5
    lo, hi = out.slices[0].theta, out.slices[2].theta
    assert lo is not None and hi is not None and thin.theta is not None
    assert lo <= thin.theta <= hi
    assert thin.theta == pytest.approx(theta("30d"), rel=1e-3)
    k = np.linspace(-0.15, 0.1, 30)  # far beyond what it was quoted on
    assert thin.w(k)[0] == pytest.approx(true_w("30d", k), rel=2e-2)
    assert thin.butterfly is not None and thin.butterfly.ok and thin.calendar_ok


def test_thin_expirations_alone_have_no_smile():
    out = fit_surface([chain("30d", strikes_for("30d", 5, 0.4))])
    thin = out.slices[0]
    assert thin.status == SPARSE and thin.model is None and out.shape is None
    assert thin.forward_source == parity.PARITY  # the forward does not need the smile
    assert thin.atm_iv == pytest.approx(VOL["30d"], abs=2e-3)  # the quote nearest the money
    assert np.isnan(thin.rmse) and thin.butterfly is None and thin.marginal() is None
    assert np.isnan(thin.w(0.0)[0])


def test_a_smile_with_arbitrage_fails_and_falls_back():
    def frown(k):
        return (0.2 - 6.0 * k * k) ** 2 * TAUS["30d"]

    odd = chain("30d", strikes_for("30d", 41, 2.0), w=frown)
    out = fit_surface([chain("7d"), odd, chain("90d")])
    bad = out.slice("30d")
    assert bad is not None and bad.status in (FAIL, OK)
    assert bad.butterfly is not None and bad.butterfly.ok  # whichever smile it got is clean
    if bad.status == FAIL:
        assert bad.model == SSVI and bad.x is None


def test_without_quotes_there_is_no_forward_and_no_fit():
    nan = np.full(3, np.nan)
    empty = SliceInput("x", 0.1, 0.99, np.array([95.0, 100.0, 105.0]), nan, nan, nan, nan)
    out = fit_surface([empty]).slices[0]
    assert out.status == NO_FIT and out.model is None and out.forward_source == parity.NONE
    assert np.isnan(out.forward) and np.isnan(out.atm_iv) and out.quotes == 0
    assert fit_surface([]).slices == ()


def test_a_given_forward_is_used_as_it_is():
    """A futures option: the forward is the price of its future, not a parity fit."""
    f = 100.7
    out = fit_surface([chain("30d", market=f, forward=f)]).slices[0]
    assert out.forward == f and out.forward_source == FUTURE and out.parity is None
    assert out.status == OK and out.atm_iv == pytest.approx(VOL["30d"], abs=1e-5)


def test_the_parity_forward_carries_the_dividend():
    """A stock: parity sees the forward that the market prices, whatever the schedule says."""
    tau = TAUS["30d"]
    market = (SPOT - 0.9) * float(np.exp(R * tau))  # the market prices a dividend of 0.90
    model = (SPOT - 0.5) * float(np.exp(R * tau))  # the schedule says 0.50
    out = fit_surface([chain("30d", market=market, model=model, pv_div=0.5)]).slices[0]
    assert out.forward == pytest.approx(market, abs=1e-6) and out.forward_source == parity.PARITY
    assert out.parity is not None and out.parity.div_flag is True
    assert out.parity.div_implied == pytest.approx(0.9, abs=1e-6)
    assert out.forward_model == model


def test_a_warm_start_gives_the_same_surface(surface: Surface):
    warm = surface.warm()
    assert set(warm.x) == {"7d", "30d", "90d"} and warm.shape == surface.shape
    again = fit_surface([chain("7d"), chain("30d"), chain("90d")], warm)
    for a, b in zip(surface.slices, again.slices, strict=True):
        assert b.status == OK and b.x == pytest.approx(a.x, rel=1e-4, abs=1e-7)


def test_settings_decide_who_is_eligible():
    strikes = strikes_for("30d", 11, 2.0)
    assert fit_surface([chain("30d", strikes)]).slices[0].status == OK
    strict = Settings(min_strikes=12)
    assert fit_surface([chain("30d", strikes)], settings=strict).slices[0].status == SPARSE


def test_the_result_crosses_a_process_boundary(surface: Surface):
    back = pickle.loads(pickle.dumps(surface))
    assert [s.key for s in back.slices] == ["7d", "30d", "90d"]
    assert back.slices[1].x == surface.slices[1].x and back.shape == surface.shape
    k = np.linspace(-0.1, 0.1, 5)
    assert back.slices[1].w(k)[0] == pytest.approx(surface.slices[1].w(k)[0])
    pickle.loads(pickle.dumps(chain("30d")))  # and the input


# --- the distribution (PRC-07) --------------------------------------------------------------------


def test_the_density_integrates_to_one_and_has_the_forward_as_mean(surface: Surface):
    """Plan 3.4. Tolerance 1e-8: the trapezoid rule on 200,001 points over 30 at-the-money
    deviations each way. Over 12 deviations two millionths are missing for the 90-day slice:
    the left wing of the smile is that fat."""
    for s in surface.slices:
        m = s.marginal()
        assert m is not None and m.model == SVI and not m.interpolated
        width = 30 * VOL[s.key] * np.sqrt(s.tau)
        strike = m.forward * np.exp(np.linspace(-width, width, 200001))
        dens = m.density(strike)
        assert dens.min() >= 0
        assert np.trapezoid(dens, strike) == pytest.approx(1.0, abs=1e-8)
        assert np.trapezoid(dens * strike, strike) == pytest.approx(m.forward, rel=1e-8)


def test_the_probability_is_the_integral_of_the_density(surface: Surface):
    m = surface.slices[1].marginal()
    assert m is not None
    width = 30 * VOL["30d"] * np.sqrt(m.tau)
    strike = m.forward * np.exp(np.linspace(-width, width, 200001))
    dens = m.density(strike)
    steps = np.diff(strike) * (dens[1:] + dens[:-1]) / 2
    cumulative = np.concatenate([[0.0], np.cumsum(steps)])
    at = slice(80000, 120001, 2500)  # six deviations each way
    assert m.prob_below(strike[at]) == pytest.approx(cumulative[at], abs=1e-8)
    assert m.prob_above(strike[at]) == pytest.approx(1 - cumulative[at], abs=1e-8)
    assert m.prob_below(strike[at]).min() < 0.001 and m.prob_below(strike[at]).max() > 0.999
    assert np.all(np.diff(m.prob_below(strike)) >= -1e-12)  # it never falls


def test_the_probability_is_the_strike_derivative_of_the_call_price(surface: Surface):
    """Breeden and Litzenberger, by finite differences of Black prices on the smile: a check
    that does not use the closed form."""
    m = surface.slices[2].marginal()
    assert m is not None
    strike = m.forward * np.exp(np.linspace(-0.25, 0.2, 10))
    h = 1e-3

    def call(k_):
        return black_price(m.forward, k_, m.tau, 1.0, m.iv(k_), True)

    slope = (call(strike + h) - call(strike - h)) / (2 * h)
    assert m.prob_below(strike) == pytest.approx(1 + slope, abs=1e-7)


def test_a_flat_smile_is_the_lognormal():
    flat = fit_surface([chain("30d", w=lambda k: np.full(np.shape(k), 0.2**2 * TAUS["30d"]))])
    m = flat.slices[0].marginal()
    assert m is not None
    strike = np.array([85.0, 95.0, 100.0, 105.0, 115.0])
    sw = 0.2 * np.sqrt(m.tau)
    d2 = np.log(m.forward / strike) / sw - sw / 2
    assert m.prob_below(strike) == pytest.approx(ndtr(-d2), abs=2e-4)
    assert m.iv(strike) == pytest.approx(0.2, abs=2e-4)


# --- interpolation in time ----------------------------------------------------------------------


def test_between_two_expirations_total_variance_is_linear_in_time(surface: Surface):
    a, b = surface.slices[1], surface.slices[2]
    k = np.linspace(-0.2, 0.15, 15)
    for share in (0.25, 0.5, 0.9):
        tau = a.tau + share * (b.tau - a.tau)
        m = surface.marginal_at(tau, 101.0)
        assert m is not None and m.interpolated and m.model == SVI and m.forward == 101.0
        assert m.w(k)[0] == pytest.approx((1 - share) * a.w(k)[0] + share * b.w(k)[0])
        assert m.butterfly().ok
        width = 30 * VOL["90d"] * np.sqrt(tau)
        strike = 101.0 * np.exp(np.linspace(-width, width, 200001))
        assert np.trapezoid(m.density(strike), strike) == pytest.approx(1.0, abs=1e-8)


def test_at_an_expiration_it_is_that_slice(surface: Surface):
    for s in surface.slices:
        m = surface.marginal_at(s.tau, s.forward)
        own = s.marginal()
        assert m is not None and own is not None
        strike = s.forward * np.exp(np.linspace(-0.2, 0.2, 9))
        assert m.prob_below(strike) == pytest.approx(own.prob_below(strike), abs=1e-12)


def test_before_the_first_expiration_it_keeps_that_slices_iv(surface: Surface):
    first = surface.slices[0]
    own = first.marginal()
    m = surface.marginal_at(first.tau / 4, 100.0)
    assert m is not None and own is not None and m.interpolated
    k = np.linspace(-0.05, 0.05, 7)
    assert m.iv(100.0 * np.exp(k)) == pytest.approx(own.iv(first.forward * np.exp(k)))
    # Less time, the same IV: less probability of a move of the same size.
    assert m.prob_below(97.0) < own.prob_below(first.forward * 0.97)


def test_no_distribution_beyond_the_last_expiration_or_without_a_smile(surface: Surface):
    assert surface.marginal_at(surface.slices[-1].tau * 1.01, 100.0) is None
    assert surface.marginal_at(0.0, 100.0) is None
    assert fit_surface([]).marginal_at(0.1, 100.0) is None


def test_two_models_are_both_named():
    out = fit_surface([chain("7d"), chain("30d", strikes_for("30d", 5, 0.4)), chain("90d")])
    m = out.marginal_at((TAUS["7d"] + TAUS["30d"]) / 2, 100.0)
    assert m is not None and m.model == "ssvi and svi"


def test_the_calendar_flag_fires_on_crossed_slices():
    """The 90-day smile is given less total variance than the 30-day one."""
    low = 0.5 * theta("30d")
    crossed = chain("90d", w=lambda k: ssvi.w_derivs(low, SHAPE, k)[0])
    out = fit_surface([chain("30d"), crossed])
    assert out.slices[0].calendar_ok is None and out.slices[1].calendar_ok is False
    assert out.slices[1].butterfly is not None and out.slices[1].butterfly.ok  # each alone is fine


def test_the_basis_runs_from_one_to_the_fitted_forwards():
    """An index: the model forward knows no dividends, parity sees a yield of 1.5%."""
    q = 0.015
    inputs = [chain(key, market=SPOT * float(np.exp((R - q) * tau))) for key, tau in TAUS.items()]
    out = fit_surface(inputs)
    assert out.basis_at(0.0) == 1.0
    for s in out.slices:
        assert out.basis_at(s.tau) == pytest.approx(np.exp(-q * s.tau), abs=1e-8)
    mid = (TAUS["30d"] + TAUS["90d"]) / 2
    assert out.basis_at(mid) == pytest.approx(np.exp(-q * mid), abs=1e-8)
    assert out.basis_at(TAUS["7d"] / 2) == pytest.approx(np.exp(-q * TAUS["7d"] / 2), abs=1e-8)
    assert out.basis_at(1.0) == out.basis_at(TAUS["90d"])  # held after the last
    assert fit_surface([]).basis_at(0.3) == 1.0
