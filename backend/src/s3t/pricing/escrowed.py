"""Black-Scholes with discrete cash dividends (PRC-02), on the escrowed-dividend model.

New in WP-0.2 (2026-11-22). It is `volcalc`'s model (forward.model_forward, then Black-76 on that
forward) written out with spot Greeks:

    F = (S - PV(div)) e^{r tau},   DF = e^{-r tau},   V = Black-76(F, K, tau, DF, sigma)

The price and the IV are black76's on (F, DF). The Greeks are against the spot S, by the chain
rule from black76's forward Greeks, with g = dF/dS = e^{r tau}:

    delta = g delta_F                 gamma = g^2 gamma_F            vanna = g vanna_F
    vega, volga as in black76
    theta = theta_F - r S g delta_F   (S held fixed while time passes: dF/dt = -r S g)
    rho   = rho_F + delta_F (tau F - g dPV/dr)
    charm = g (charm_F - r S g gamma_F - r delta_F)

All raw (PRC-04): per share, theta and charm per year. One rate r discounts the strike and the
dividends, as forward.pv_dividends does. theta and charm are the smooth derivatives between
ex-dates; the PV steps down when an ex-date passes.

European exercise only. Stock and ETF options are American; until an American pricer exists,
in-the-money values are slightly understated (product definition 11.1, PRC-10).
"""

import numpy as np

from s3t.pricing.black76 import BlackIV, black_greeks, black_iv, black_price
from s3t.pricing.forward import model_forward


def escrowed_price(S, K, tau, r, pv_div, sigma, is_call) -> np.ndarray:
    """European price on the escrowed forward."""
    return black_price(model_forward(S, r, tau, pv_div), K, tau, np.exp(-r * tau), sigma, is_call)


def escrowed_iv(price, S, K, tau, r, pv_div, is_call, tick=None) -> BlackIV:
    """IV of a European price on the escrowed forward, with black76's reason codes. A forward
    at or below zero (PV of the dividends at or above S) is FAIL_INVALID."""
    F = model_forward(S, r, tau, pv_div)
    return black_iv(price, F, K, tau, np.exp(-r * tau), is_call, tick)


def escrowed_greeks(S, K, tau, r, pv_div, dpv_dr, sigma, is_call) -> dict[str, np.ndarray]:
    """Raw spot Greeks, keyed as black76.GREEKS. `pv_div` and `dpv_dr` are
    forward.pv_dividends and forward.pv_dividends_dr of the schedule at the same r."""
    S, r, tau = (np.asarray(a, dtype=np.float64) for a in (S, r, tau))
    F = model_forward(S, r, tau, pv_div)
    g = np.exp(r * tau)
    fwd = black_greeks(F, K, tau, np.exp(-r * tau), sigma, is_call)
    d_f, gamma_f = fwd["delta"], fwd["gamma"]
    return {
        "delta": g * d_f,
        "gamma": g * g * gamma_f,
        "theta": fwd["theta"] - r * S * g * d_f,
        "vega": fwd["vega"],
        "rho": fwd["rho"] + d_f * (tau * F - g * np.asarray(dpv_dr, dtype=np.float64)),
        "vanna": g * fwd["vanna"],
        "charm": g * (fwd["charm"] - r * S * g * gamma_f - r * d_f),
        "volga": fwd["volga"],
    }
