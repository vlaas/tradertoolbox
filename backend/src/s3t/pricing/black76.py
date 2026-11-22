"""Black-76 on the forward: price, forward Greeks and IV inversion on (price, F, K, tau, DF).

Source: `volcalc` src/vrp/iv/black76.py at commit f53be83. Price, IV inversion and the delta,
gamma and vega of `black_greeks` are unchanged.

From the source docstring: Black-76 is BSM with S = F and r = q = -ln(DF)/tau, so the inversion
reuses the bit-exact bsm.compute_iv (its bracket and identifiability semantics included) and
adds: a bisection polish where the ported Newton stopped short of a 1e-8 * F price residual, the
IV > IV_CAP null, and an `iv_fail` reason code per row.

Conventions: k = ln(K/F), w = sigma^2 * tau, d1 = (-k + w/2)/sqrt(w), d2 = d1 - sqrt(w),
C = DF * (F N(d1) - K N(d2)), P = DF * (K N(-d2) - F N(-d1)). tau is floored at bsm._MIN_T
(1 minute of a 525,960-minute year, below the clocks' own 1-minute floor), sigma at
bsm._MIN_SIGMA, as in the BSM port, so the bracket means the same thing in both layers.

Added in the port (2026-11-22, WP-0.2, PRC-02): theta, rho, vanna, charm and volga in
`black_greeks`. All Greeks are raw (PRC-04) and taken with the forward F held fixed, the
convention of an option on a future:

    delta = dV/dF               gamma = d2V/dF2            vega  = dV/dsigma
    theta = dV/dt, per year     rho   = dV/dr, DF = e^{-r tau}
    vanna = d2V/dF dsigma       charm = d(delta)/dt, per year
    volga = d2V/dsigma2

t is calendar time, so theta and charm are per year of the clock the caller's tau is on. The
spot Greeks of an option on a stock with discrete dividends are in escrowed.py.
"""

from typing import NamedTuple

import numpy as np

from s3t.pricing.bsm import (
    _BRACKET_TOL,
    _MAX_SIGMA,
    _MIN_SIGMA,
    _MIN_T,
    _N,
    _bisection_iv,
    _bsm_price_and_vega,
    _n,
    _newton_invariants,
    bsm_price,
    compute_iv,
)

IV_CAP = 5.0  # bt_dataset add_greeks: IV > 500 % means BSM breakdown (stale print, expiry)
RESID_TOL = 1e-8  # |Black(iv) - price| <= RESID_TOL * F
IV_TOL = 1e-6  # |iv - sigma| <= IV_TOL, i.e. |residual| <= IV_TOL * vega
POLISH_TOL = 1e-12  # polish trigger (price, relative to F): one Newton step from the ported
POLISH_STEPS = 4  # solver's 1e-8 exit reaches machine precision, which w-invariance needs

# iv_fail reason codes (uint8). 0 = ok; iv is NaN wherever the code is non-zero.
FAIL_OK = 0
FAIL_BELOW_INTRINSIC = 1  # price < Black(sigma_min) - $0.01
FAIL_ABOVE_CAP = 2  # price > Black(sigma_max) + $0.01, or solved iv > IV_CAP
FAIL_NO_CONVERGENCE = 3  # residual > RESID_TOL * F after Newton and bisection
FAIL_UNIDENTIFIABLE = 4  # extrinsic <= max($0.02, tick/2)
FAIL_INVALID = 5  # non-finite input, or F / K / DF <= 0
FAIL_NAMES = ("ok", "below_intrinsic", "above_cap", "no_convergence", "unidentifiable", "invalid")

GREEKS = ("delta", "gamma", "theta", "vega", "rho", "vanna", "charm", "volga")


class BlackIV(NamedTuple):
    iv: np.ndarray  # NaN where fail != 0
    fail: np.ndarray  # uint8 reason codes


def _d1_d2(F, K, tau, sigma):
    tau_s = np.maximum(tau, _MIN_T)
    sqrt_w = np.maximum(sigma, _MIN_SIGMA) * np.sqrt(tau_s)
    d1 = (np.log(F / K) + 0.5 * sqrt_w**2) / sqrt_w
    return d1, d1 - sqrt_w, sqrt_w


def black_price(F, K, tau, df, sigma, is_call) -> np.ndarray:
    """Black-76 price: DF * (F N(d1) - K N(d2)) for calls, DF * (K N(-d2) - F N(-d1)) for puts."""
    F, K, df = (np.asarray(a, dtype=np.float64) for a in (F, K, df))
    d1, d2, _ = _d1_d2(F, K, tau, sigma)
    call = F * _N(d1) - K * _N(d2)
    put = K * _N(-d2) - F * _N(-d1)
    return df * np.where(is_call, call, put)


def black_greeks(F, K, tau, df, sigma, is_call) -> dict[str, np.ndarray]:
    """Raw forward Greeks with F held fixed, keyed as in GREEKS.

    From the source: delta = DF N(d1) (calls) / -DF N(-d1) (puts); gamma = d2V/dF2 =
    DF n(d1) / (F sqrt(w)); vega = dV/dsigma = DF F n(d1) sqrt(tau) per unit of sigma (NOT per
    1 %). Added: with r = -ln(DF)/tau, so that DF moves at rate r as time passes,
    theta = r V - DF F n(d1) sigma / (2 sqrt(tau)); rho = -tau V; vanna = -DF n(d1) d2 / sigma;
    charm = r delta + DF n(d1) d2 / (2 tau); volga = vega d1 d2 / sigma. tau and sigma are the
    floored values the price uses, so the Greeks are derivatives of `black_price` itself."""
    F, K, df = (np.asarray(a, dtype=np.float64) for a in (F, K, df))
    d1, d2, sqrt_w = _d1_d2(F, K, tau, sigma)
    n_d1 = _n(d1)
    delta = df * np.where(is_call, _N(d1), -_N(-d1))
    gamma = df * n_d1 / (F * sqrt_w)
    vega = df * F * n_d1 * np.sqrt(np.maximum(tau, _MIN_T))
    # Added in the port.
    tau_s = np.maximum(tau, _MIN_T)
    sigma_s = np.maximum(sigma, _MIN_SIGMA)
    r = -np.log(df) / tau_s
    price = df * np.where(is_call, F * _N(d1) - K * _N(d2), K * _N(-d2) - F * _N(-d1))
    theta = r * price - df * F * n_d1 * sigma_s / (2.0 * np.sqrt(tau_s))
    rho = -tau_s * price
    vanna = -df * n_d1 * d2 / sigma_s
    charm = r * delta + df * n_d1 * d2 / (2.0 * tau_s)
    volga = vega * d1 * d2 / sigma_s
    return {
        "delta": delta,
        "gamma": gamma,
        "theta": theta,
        "vega": vega,
        "rho": rho,
        "vanna": vanna,
        "charm": charm,
        "volga": volga,
    }


def black_iv(price, F, K, tau, df, is_call, tick=None) -> BlackIV:
    """Invert Black-76 prices. `tick` (the per-row quoting tick) widens the identifiability gate
    to tick/2; None keeps the solver floor ($0.02). Returns iv (NaN on failure) and the reason
    code."""
    price, F, K, tau, df = (np.asarray(a, dtype=np.float64) for a in (price, F, K, tau, df))
    is_call = np.asarray(is_call, dtype=bool)
    ok = (
        (np.isfinite(price) & np.isfinite(F) & np.isfinite(K) & np.isfinite(tau) & np.isfinite(df))
        & (F > 0)
        & (K > 0)
        & (df > 0)
    )
    fail = np.where(ok, FAIL_OK, FAIL_INVALID).astype(np.uint8)
    tau_s = np.maximum(np.where(ok, tau, _MIN_T), _MIN_T)
    r = -np.log(np.where(ok, df, 1.0)) / tau_s  # q = r makes BSM(S=F) the Black-76 price
    F_, K_, price_ = (np.where(ok, a, 1.0) for a in (F, K, price))

    pu = None if tick is None else np.asarray(tick, dtype=np.float64) / 2.0
    res = compute_iv(
        price_, F_, K_, tau_s, r, r, is_call, price_uncertainty=pu, return_details=True
    )
    iv = np.where(ok, res.iv, np.nan)
    black_lo = price_ - res.extrinsic  # Black(sigma_min)

    below = ok & (price_ < black_lo - _BRACKET_TOL)
    fail[below] = FAIL_BELOW_INTRINSIC
    unident = ok & ~below & res.unidentifiable
    fail[unident] = FAIL_UNIDENTIFIABLE
    # The only other NaN source in compute_iv is the upper bracket.
    fail[ok & ~below & ~unident & np.isnan(iv)] = FAIL_ABOVE_CAP

    # Polish. The ported Newton exits at a 1e-8 absolute residual (IV error 1e-8 / vega) and
    # accepts up to $0.01 on limit-cycling short-dated far-OTM rows. Newton steps until the
    # residual is within both POLISH_TOL * F (price) and IV_TOL * vega (sigma), then bracketed
    # bisection for the leftovers; what still misses RESID_TOL * F is flagged.
    solved = np.isfinite(iv)
    inv = _newton_invariants(F_, K_, tau_s, r, r)
    act = np.flatnonzero(solved)
    for _ in range(POLISH_STEPS):
        p, vega = _bsm_price_and_vega(inv.take(act), iv[act], is_call[act])
        resid = p - price_[act]
        bad = np.abs(resid) > np.minimum(POLISH_TOL * F_[act], IV_TOL * vega)
        act, resid, vega = act[bad], resid[bad], vega[bad]
        if act.size == 0:
            break
        iv[act] = np.clip(iv[act] - resid / np.maximum(vega, 1e-10), _MIN_SIGMA, _MAX_SIGMA)
    if act.size:
        a = act
        iv[a] = _bisection_iv(price_[a], F_[a], K_[a], tau_s[a], r[a], r[a], is_call[a])
        resid = bsm_price(F_[a], K_[a], tau_s[a], r[a], r[a], iv[a], is_call[a]) - price_[a]
        fail[a[np.abs(resid) > RESID_TOL * F_[a]]] = FAIL_NO_CONVERGENCE

    fail[solved & (iv > IV_CAP)] = FAIL_ABOVE_CAP
    iv[fail != FAIL_OK] = np.nan
    return BlackIV(iv, fail)
