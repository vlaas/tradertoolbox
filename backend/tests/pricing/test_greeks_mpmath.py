"""Independent cross-check of price and Greeks (implementation plan 3.4): mpmath at 40 digits.

The reference prices are written here from the textbook formulas in mpmath's arbitrary
precision, and every Greek is mpmath's numerical derivative of that price, so neither numpy,
scipy nor the closed forms of black76.py and escrowed.py enter the reference. mpmath replaces
the py-vollib check of `volcalc` (py-vollib brings numba and pandas).
"""

import mpmath as mp
import numpy as np
import pytest

from s3t.pricing.black76 import GREEKS, black_greeks, black_price
from s3t.pricing.escrowed import escrowed_greeks, escrowed_price
from s3t.pricing.forward import Dividend, pv_dividends, pv_dividends_dr

mp.mp.dps = 40

# (variable, order) per Greek; theta and charm differentiate in elapsed time t.
ORDERS = {
    "delta": {"x": 1},
    "gamma": {"x": 2},
    "theta": {"t": 1},
    "vega": {"s": 1},
    "rho": {"r": 1},
    "vanna": {"x": 1, "s": 1},
    "charm": {"x": 1, "t": 1},
    "volga": {"s": 2},
}
VARS = ("x", "s", "r", "t")


def _black_mp(F, K, tau, df, sigma, is_call):
    sw = sigma * mp.sqrt(tau)
    d1 = (mp.log(F / K) + sw**2 / 2) / sw
    d2 = d1 - sw
    if is_call:
        return df * (F * mp.ncdf(d1) - K * mp.ncdf(d2))
    return df * (K * mp.ncdf(-d2) - F * mp.ncdf(-d1))


def _reference(price_fn, point):
    """Price and Greeks of price_fn(x, s, r, t) at point, by mpmath differentiation."""
    args = [mp.mpf(point[v]) for v in VARS]
    out = {"price": price_fn(*args)}
    for name, order in ORDERS.items():
        n = tuple(order.get(v, 0) for v in VARS)
        out[name] = mp.diff(price_fn, args, n)
    return out


def _cases(seed, n):
    g = np.random.default_rng(seed)
    for _ in range(n):
        x = float(g.uniform(20.0, 5000.0))
        yield {
            "x": x,
            "K": float(x * np.exp(g.normal(0.0, 0.12))),
            "tau": float(np.exp(g.uniform(np.log(2 / 365), np.log(2.0)))),
            "r": float(g.uniform(0.0, 0.07)),
            "s": float(g.uniform(0.08, 1.2)),
            "is_call": bool(g.random() < 0.5),
        }


def _check(ours, ref, scale):
    for k in ("price", *GREEKS):
        want = float(ref[k])
        # Relative to the size of the Greek, with a floor for values near zero.
        assert ours[k] == pytest.approx(want, rel=1e-9, abs=1e-11 * scale), k


@pytest.mark.parametrize("case", list(_cases(101, 24)))
def test_black76_against_mpmath(case):
    K, tau0, c = mp.mpf(case["K"]), mp.mpf(case["tau"]), case["is_call"]

    def price(F, s, r, t):  # F held fixed while time passes
        tau = tau0 - t
        return _black_mp(F, K, tau, mp.exp(-r * tau), s, c)

    ref = _reference(price, {"x": case["x"], "s": case["s"], "r": case["r"], "t": 0.0})
    F_, K_, tau_ = (np.array([v]) for v in (case["x"], case["K"], case["tau"]))
    df = np.exp(-case["r"] * tau_)
    sig, call = np.array([case["s"]]), np.array([c])
    ours = {k: v[0] for k, v in black_greeks(F_, K_, tau_, df, sig, call).items()}
    ours["price"] = black_price(F_, K_, tau_, df, sig, call)[0]
    _check(ours, ref, case["x"])


@pytest.mark.parametrize("case", list(_cases(202, 24)))
def test_escrowed_against_mpmath(case):
    """Quarterly cash dividends of about 0.4 % of spot, the first one 20 days out."""
    K, tau0, c = mp.mpf(case["K"]), mp.mpf(case["tau"]), case["is_call"]
    d0 = np.datetime64("2026-01-05")
    days = [d for d in range(20, int(case["tau"] * 365), 91)]
    amount = round(0.004 * case["x"], 2)
    t_div = [mp.mpf(d) / 365 for d in days]

    def price(S, s, r, t):  # S held fixed while time passes; ex-dates stay at their dates
        tau = tau0 - t
        pv = sum((amount * mp.exp(-r * (ti - t)) for ti in t_div), mp.mpf(0))
        F = (S - pv) * mp.exp(r * tau)
        return _black_mp(F, K, tau, mp.exp(-r * tau), s, c)

    ref = _reference(price, {"x": case["x"], "s": case["s"], "r": case["r"], "t": 0.0})
    sched = [Dividend((d0 + np.timedelta64(d, "D")).item(), amount, True) for d in days]
    d = d0.item()
    pv, dpv = pv_dividends(sched, d, case["r"]), pv_dividends_dr(sched, d, case["r"])
    S_, K_, tau_, r_ = (np.array([v]) for v in (case["x"], case["K"], case["tau"], case["r"]))
    sig, call = np.array([case["s"]]), np.array([c])
    ours = {k: v[0] for k, v in escrowed_greeks(S_, K_, tau_, r_, pv, dpv, sig, call).items()}
    ours["price"] = escrowed_price(S_, K_, tau_, r_, pv, sig, call)[0]
    _check(ours, ref, case["x"])
