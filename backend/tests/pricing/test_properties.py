"""Property tests of the pricing core (QA-01; implementation plan 3.4): parity, monotonicity in
strike, volatility and time, no-arbitrage bounds, Greek signs. New in WP-0.2.

Tolerance: EPS times the larger of F (or S) and K, for rounding in the N(d) differences. The
IV round trip is in test_black76.py (ported) and test_escrowed.py."""

import numpy as np
from hypothesis import given, settings
from hypothesis import strategies as st

from s3t.pricing.black76 import black_greeks, black_price
from s3t.pricing.escrowed import escrowed_greeks, escrowed_price

EPS = 1e-12
CP = np.array([True, False])

log_f = st.floats(np.log(5.0), np.log(6000.0))
log_k = st.floats(-1.0, 1.0)  # ln(K/F)
tau_s = st.floats(1.0 / (365 * 24), 2.5)  # an hour to 2.5 years
sigma_s = st.floats(0.01, 3.0)
rate_s = st.floats(-0.01, 0.10)


def _b(F, K, tau, r, sigma):
    """Call and put on the forward."""
    return black_price(F, K, tau, np.exp(-r * tau), sigma, CP)


@settings(max_examples=300, deadline=None)
@given(log_f, log_k, tau_s, rate_s, sigma_s)
def test_black76_parity_and_bounds(lf, lk, tau, r, sigma):
    F, df = np.exp(lf), np.exp(-r * tau)
    K = F * np.exp(lk)
    eps = EPS * max(F, K)
    C, P = _b(F, K, tau, r, sigma)
    assert abs((C - P) - df * (F - K)) <= eps
    assert max(0.0, df * (F - K)) - eps <= C <= df * F + eps
    assert max(0.0, df * (K - F)) - eps <= P <= df * K + eps


@settings(max_examples=300, deadline=None)
@given(log_f, log_k, st.floats(1e-4, 0.5), tau_s, rate_s, sigma_s)
def test_black76_monotone_in_strike(lf, lk, dk, tau, r, sigma):
    """Calls fall and puts rise with the strike, by at most DF per unit of strike."""
    F, df = np.exp(lf), np.exp(-r * tau)
    K1 = F * np.exp(lk)
    K2 = K1 * np.exp(dk)
    eps = EPS * max(F, K2)
    (C1, P1), (C2, P2) = _b(F, K1, tau, r, sigma), _b(F, K2, tau, r, sigma)
    assert -eps <= C1 - C2 <= df * (K2 - K1) + eps
    assert -eps <= P2 - P1 <= df * (K2 - K1) + eps


@settings(max_examples=300, deadline=None)
@given(log_f, log_k, tau_s, rate_s, sigma_s, st.floats(1e-4, 1.0))
def test_black76_monotone_in_volatility(lf, lk, tau, r, sigma, ds):
    F = np.exp(lf)
    K = F * np.exp(lk)
    eps = EPS * max(F, K)
    lo, hi = _b(F, K, tau, r, sigma), _b(F, K, tau, r, sigma + ds)
    assert (hi - lo >= -eps).all()


@settings(max_examples=300, deadline=None)
@given(log_f, log_k, tau_s, st.floats(1.0 / (365 * 24), 1.0), sigma_s)
def test_black76_monotone_in_time_undiscounted(lf, lk, tau, dt, sigma):
    """With DF = 1 the price grows with the total variance, so with time. (Discounted, a deep
    in-the-money option can lose value with time; that case is not a property.)"""
    F = np.exp(lf)
    K = F * np.exp(lk)
    eps = EPS * max(F, K)
    lo = black_price(F, K, tau, 1.0, sigma, CP)
    hi = black_price(F, K, tau + dt, 1.0, sigma, CP)
    assert (hi - lo >= -eps).all()


@settings(max_examples=300, deadline=None)
@given(log_f, log_k, tau_s, rate_s, sigma_s)
def test_black76_greek_signs(lf, lk, tau, r, sigma):
    F, df = np.exp(lf), np.exp(-r * tau)
    K = F * np.exp(lk)
    g = black_greeks(F, K, tau, df, sigma, CP)
    assert -1e-15 <= g["delta"][0] <= df + 1e-15
    assert -df - 1e-15 <= g["delta"][1] <= 1e-15
    assert abs(g["delta"][0] - g["delta"][1] - df) <= 1e-12
    assert (g["gamma"] >= 0).all() and (g["vega"] >= 0).all()


@settings(max_examples=300, deadline=None)
@given(log_f, log_k, tau_s, rate_s, sigma_s, st.floats(0.0, 0.05))
def test_escrowed_parity_bounds_and_delta(ls, lk, tau, r, sigma, div_share):
    """C - P = S - PV(div) - K e^{-r tau}; the call is worth at most S - PV(div); spot delta
    of a call lies in [0, 1] and put delta is call delta minus one."""
    S = np.exp(ls)
    K = S * np.exp(lk)
    pv = div_share * S
    eps = EPS * max(S, K)
    C, P = escrowed_price(S, K, tau, r, pv, sigma, CP)
    disc_k = K * np.exp(-r * tau)
    assert abs((C - P) - (S - pv - disc_k)) <= eps
    assert max(0.0, S - pv - disc_k) - eps <= C <= S - pv + eps
    assert max(0.0, disc_k - (S - pv)) - eps <= P <= disc_k + eps
    g = escrowed_greeks(S, K, tau, r, pv, 0.0, sigma, CP)
    assert -1e-15 <= g["delta"][0] <= 1.0 + 1e-12
    assert abs(g["delta"][0] - g["delta"][1] - 1.0) <= 1e-12
    assert (g["gamma"] >= 0).all() and (g["vega"] >= 0).all()


@settings(max_examples=300, deadline=None)
@given(log_f, log_k, tau_s, st.floats(1.0 / (365 * 24), 1.0), st.floats(0.0, 0.10), sigma_s)
def test_escrowed_call_monotone_in_time_without_dividends(ls, lk, tau, dt, r, sigma):
    """With r >= 0 and no dividends the European call is worth the American one, so a longer call
    is worth at least a shorter one."""
    S = np.exp(ls)
    K = S * np.exp(lk)
    eps = EPS * max(S, K)
    lo = escrowed_price(S, K, tau, r, 0.0, sigma, True)
    hi = escrowed_price(S, K, tau + dt, r, 0.0, sigma, True)
    assert hi - lo >= -eps
