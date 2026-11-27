"""Spot Greeks on a forward in proportion to the spot (WP-1.1): against finite differences of
the price, and against the escrowed model where the two agree."""

import numpy as np
import pytest

from s3t.pricing.black76 import GREEKS, black_price
from s3t.pricing.carry import implied_pv, proportional_greeks
from s3t.pricing.escrowed import escrowed_greeks

S0, R0, Q, TAU0, SIGMA0 = 6000.0, 0.043, 0.013, 0.31, 0.17
K = np.array([5400.0, 5900.0, 6000.0, 6100.0, 6600.0])
CALL = np.array([False, False, True, True, True])


def price(S=S0, t=0.0, sigma=SIGMA0, r=R0):
    """The price with the yield held fixed: F = S e^{(r - q) tau}; t is time passed."""
    tau = TAU0 - t
    return black_price(S * np.exp((r - Q) * tau), K, tau, np.exp(-r * tau), sigma, CALL)


def greeks(S=S0, t=0.0, sigma=SIGMA0, r=R0):
    tau = TAU0 - t
    F = S * np.exp((r - Q) * tau)
    return proportional_greeks(S, F, K, tau, np.exp(-r * tau), sigma, CALL)


def diff(f, h):
    """A central difference; its own error is of the order of h squared."""
    return (f(h) - f(-h)) / (2 * h)


def test_keys():
    assert tuple(greeks()) == GREEKS


def test_first_order_against_the_price():
    g = greeks()
    assert g["delta"] == pytest.approx(diff(lambda h: price(S=S0 + h), 0.5), rel=1e-6)
    assert g["theta"] == pytest.approx(diff(lambda h: price(t=h), 1e-5), rel=1e-6)
    assert g["vega"] == pytest.approx(diff(lambda h: price(sigma=SIGMA0 + h), 1e-5), rel=1e-7)
    assert g["rho"] == pytest.approx(diff(lambda h: price(r=R0 + h), 1e-6), rel=1e-6)


def test_second_order_against_the_first():
    g = greeks()
    assert g["gamma"] == pytest.approx(diff(lambda h: greeks(S=S0 + h)["delta"], 0.5), rel=1e-6)
    assert g["charm"] == pytest.approx(diff(lambda h: greeks(t=h)["delta"], 1e-5), rel=1e-6)
    vanna = diff(lambda h: greeks(sigma=SIGMA0 + h)["delta"], 1e-5)
    assert g["vanna"] == pytest.approx(vanna, rel=1e-6)
    volga = diff(lambda h: greeks(sigma=SIGMA0 + h)["vega"], 1e-5)
    assert g["volga"] == pytest.approx(volga, rel=1e-5, abs=1e-6)


def test_without_dividends_it_is_the_escrowed_model():
    """q = 0 and no cash dividend: both models say F = S e^{r tau}."""
    F = S0 * np.exp(R0 * TAU0)
    a = proportional_greeks(S0, F, K, TAU0, np.exp(-R0 * TAU0), SIGMA0, CALL)
    b = escrowed_greeks(S0, K, TAU0, R0, 0.0, 0.0, SIGMA0, CALL)
    for name in GREEKS:
        assert a[name] == pytest.approx(b[name], rel=1e-10, abs=1e-12), name


def test_implied_pv_gives_the_escrowed_forward_back():
    df = np.exp(-R0 * TAU0)
    F = (S0 - 21.5) / df
    assert implied_pv(S0, F, df) == pytest.approx(21.5)


def test_no_spot():
    g = proportional_greeks(0.0, 6000.0, K, TAU0, 0.99, SIGMA0, CALL)
    assert np.isnan(g["delta"]).all() and np.isfinite(g["vega"]).all()
