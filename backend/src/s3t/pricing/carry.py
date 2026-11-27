"""Spot Greeks of an option priced on a forward that moves in proportion to the spot.

New in WP-1.1 (2026-11-27). It settles the item carried from WP-0.2: an index option is priced
by Black-76 on its parity forward, whose Greeks hold the forward fixed, and a trader reads delta
and gamma against the index level. An index pays its dividends in proportion to its level, so
the forward is the spot times a carry factor that the spot does not move:

    F = c S,   c = e^{(r - q) tau},   V = Black-76(F, K, tau, DF, sigma),   DF = e^{-r tau}

c = F / S is read off the parity forward and the index level; q, the implied dividend yield, is
held fixed with it. By the chain rule from black76's forward Greeks, with b = r - q = ln(c) / tau:

    delta = c delta_F                 gamma = c^2 gamma_F            vanna = c vanna_F
    vega, volga as in black76
    theta = theta_F - b F delta_F     (S held fixed while time passes: dF/dt = -b F)
    rho   = rho_F + tau F delta_F     (q held fixed: dF/dr = tau F)
    charm = c (charm_F - b delta_F - b F gamma_F)

All raw (PRC-04). A stock or an ETF pays cash: there the spot Greeks are escrowed.py's, and on a
parity forward they take the dividend PV that parity implies, S - F DF (`implied_pv`).
"""

import numpy as np

from s3t.pricing.black76 import black_greeks
from s3t.pricing.bsm import _MIN_T


def proportional_greeks(S, F, K, tau, df, sigma, is_call) -> dict[str, np.ndarray]:
    """Raw spot Greeks, keyed as black76.GREEKS, of an option priced on the forward F while the
    underlying stands at S. With S at or below zero the Greeks that depend on it are NaN."""
    S, F, tau = (np.asarray(a, dtype=np.float64) for a in (S, F, tau))
    with np.errstate(divide="ignore", invalid="ignore"):
        c = np.where(S > 0, F / S, np.nan)
        b = np.log(c) / np.maximum(tau, _MIN_T)
    fwd = black_greeks(F, K, tau, df, sigma, is_call)
    d_f, gamma_f = fwd["delta"], fwd["gamma"]
    return {
        "delta": c * d_f,
        "gamma": c * c * gamma_f,
        "theta": fwd["theta"] - b * F * d_f,
        "vega": fwd["vega"],
        "rho": fwd["rho"] + tau * F * d_f,
        "vanna": c * fwd["vanna"],
        "charm": c * (fwd["charm"] - b * d_f - b * F * gamma_f),
        "volga": fwd["volga"],
    }


def implied_pv(S, F, df) -> np.ndarray:
    """The PV of the cash dividends that a parity forward implies for a stock or an ETF:
    S - F DF. It is what escrowed.escrowed_greeks takes as `pv_div` on such a forward."""
    return np.asarray(S, dtype=np.float64) - np.asarray(F, dtype=np.float64) * df
