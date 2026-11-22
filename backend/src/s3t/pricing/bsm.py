"""BSM on spot with a continuous yield: the reference solver that the Black-76 layer inverts with.

Source: `volcalc` src/vrp/iv/bsm.py at commit f53be83 (itself a port of bt_dataset
pipeline/greeks.py). The kernels and the IV solver are unchanged and stay bit-identical to the
frozen copy in tests/pricing/_iv_reference.py (tests/pricing/test_bsm_bitexact.py).

From the source docstring: numpy kernels on scipy.special.ndtr, Newton from sigma = 0.3 in row
blocks, a bisection fallback for rows still off by more than $0.01, null outside the BSM bracket
[BSM(0.001), BSM(20)] +/- $0.01, and the identifiability gate (extrinsic <= max($0.02, tick/2)
-> null). The IV > 5 cap and the forward (Black-76) parameterisation live in black76.py.

Changed in the port (2026-11-22, WP-0.2):
- Removed `bsm_greeks`. It returned theta per day and vega and rho per 1 %, and PRC-04 keeps raw
  units inside `pricing`. The Greeks are in black76.py (forward) and escrowed.py (spot).
- Removed the tick tables per root (`TICK_SCHEDULE`, `TICK_CLASS`, `tick_schedule`,
  `tick_size`): `volcalc`'s universe. Here the tick comes from the broker's chain and is passed
  in as `price_uncertainty` (or `tick` in black76.py).
- Removed `enforce_iv_greeks_contract` and with it the polars import. The rule it carried (no
  Greeks where there is no IV) belongs to the service layer.
- The module logger stays. It has no handler of its own, so `pricing` does no I/O through it.
"""

import logging
from typing import Literal, NamedTuple, overload

import numpy as np
from scipy.special import ndtr

log = logging.getLogger(__name__)

# Safety floors to prevent divide-by-zero
_MIN_T = 1.0 / 525960.0  # 1 minute as fraction of year (1 / (365.25*24*60))
_MIN_SIGMA = 0.001
_MAX_SIGMA = 20.0

# IV solver price contract. _BRACKET_TOL is the absolute price tolerance the solver converges
# to; a price whose extrinsic (time) value is within _IDENT_TOL_MULT x that tolerance of the
# discounted intrinsic cannot identify sigma — every vol from ~0 up to some level prices inside
# the band, and the solver would return a confidently-wrong value near the lower bracket. Such
# rows are gated to NaN (iv_unidentifiable_mask). The instrument-dependent part of the gate
# (tick/2) flows in via compute_iv's `price_uncertainty` argument.
_BRACKET_TOL = 0.01  # $0.01 absolute tolerance on price bracket
_IDENT_TOL_MULT = 2.0

# Normal cdf/pdf straight from scipy.special: bit-identical to scipy.stats.norm.cdf / .pdf
# (including NaN payloads) without the rv_continuous overhead.
_SQRT_2PI = np.sqrt(2.0 * np.pi)  # == scipy.stats._continuous_distns._norm_pdf_C
_N = ndtr


def _n(x: np.ndarray) -> np.ndarray:
    """Standard normal pdf, bit-identical to scipy.stats.norm.pdf — the same expression scipy
    evaluates, with scipy's canonical NaN restored on NaN inputs (the bare formula flips the
    NaN sign bit)."""
    p = np.exp(-(x**2) / 2.0) / _SQRT_2PI
    return np.where(np.isnan(x), np.nan, p)


# ---------------------------------------------------------------------------
# Core BSM formulas
# ---------------------------------------------------------------------------


def _d1_d2(S, K, T, r, q, sigma):
    """Compute d1 and d2 for BSM. Inputs must be broadcastable arrays."""
    T_safe = np.maximum(T, _MIN_T)
    sigma_safe = np.maximum(sigma, _MIN_SIGMA)
    sqrt_T = np.sqrt(T_safe)
    d1 = (np.log(S / K) + (r - q + 0.5 * sigma_safe**2) * T_safe) / (sigma_safe * sqrt_T)
    d2 = d1 - sigma_safe * sqrt_T
    return d1, d2


def bsm_price(
    S: np.ndarray,
    K: np.ndarray,
    T: np.ndarray,
    r: np.ndarray,
    q: np.ndarray,
    sigma: np.ndarray,
    is_call: np.ndarray,
) -> np.ndarray:
    """BSM option price with continuous dividend yield (T in years, rates annualized decimal)."""
    d1, d2 = _d1_d2(S, K, T, r, q, sigma)
    T_safe = np.maximum(T, _MIN_T)
    disc_K = K * np.exp(-r * T_safe)
    disc_S = S * np.exp(-q * T_safe)

    call_price = disc_S * _N(d1) - disc_K * _N(d2)
    put_price = disc_K * _N(-d2) - disc_S * _N(-d1)

    return np.where(is_call, call_price, put_price)


def _bsm_vega_only(S, K, T, r, q, sigma) -> np.ndarray:
    """Fast path for the IV solver: vega only (dV/dsigma), per unit (NOT per 1 %)."""
    T_safe = np.maximum(T, _MIN_T)
    d1, _ = _d1_d2(S, K, T, r, q, sigma)
    sqrt_T = np.sqrt(T_safe)
    disc_S = S * np.exp(-q * T_safe)
    return disc_S * _n(d1) * sqrt_T


# Public alias: per-unit BSM vega (dV/dsigma, NOT per 1 % IV).
bsm_vega = _bsm_vega_only


# ---------------------------------------------------------------------------
# Implied volatility solver
# ---------------------------------------------------------------------------


def bsm_extrinsic(price, S, K, T, r, q, is_call) -> np.ndarray:
    """Extrinsic (time) value: price - BSM(sigma -> _MIN_SIGMA), i.e. price minus the discounted
    intrinsic. Negative where the price sits below discounted intrinsic."""
    T_safe = np.maximum(T, _MIN_T)
    lo_sig = np.full_like(np.asarray(price, dtype=np.float64), _MIN_SIGMA)
    return price - bsm_price(S, K, T_safe, r, q, lo_sig, is_call)


def iv_unidentifiable_mask(
    price,
    S,
    K,
    T,
    r,
    q,
    is_call,
    price_uncertainty: np.ndarray | float | None = None,
    *,
    extrinsic: np.ndarray | None = None,
) -> np.ndarray:
    """True where the price cannot identify sigma: extrinsic value at or below
    max(_IDENT_TOL_MULT x _BRACKET_TOL, price_uncertainty), prices below discounted intrinsic
    included. `price_uncertainty` is typically the quoting tick / 2. NaN inputs yield False
    (such rows fail the BSM bracket check anyway)."""
    if extrinsic is None:
        extrinsic = bsm_extrinsic(price, S, K, T, r, q, is_call)
    threshold = np.full_like(
        np.asarray(extrinsic, dtype=np.float64), _IDENT_TOL_MULT * _BRACKET_TOL
    )
    if price_uncertainty is not None:
        threshold = np.maximum(threshold, price_uncertainty)
    return extrinsic <= threshold


class _NewtonInvariants(NamedTuple):
    """The sigma-independent terms of BSM price and vega, computed once per solver call. Each is
    literally the intermediate _d1_d2 / bsm_price / _bsm_vega_only form on the same operands,
    so the fused kernel below reproduces their bits."""

    logSK: np.ndarray  # log(S / K)
    rq: np.ndarray  # r - q
    T_safe: np.ndarray  # max(T, _MIN_T)
    sqrt_T: np.ndarray
    disc_S: np.ndarray  # S * e^{-qT}
    disc_K: np.ndarray  # K * e^{-rT}

    def take(self, idx: np.ndarray | slice) -> "_NewtonInvariants":
        return _NewtonInvariants(*(a[idx] for a in self))


def _newton_invariants(S, K, T_safe, r, q) -> _NewtonInvariants:
    return _NewtonInvariants(
        np.log(S / K),
        r - q,
        T_safe,
        np.sqrt(T_safe),
        S * np.exp(-q * T_safe),
        K * np.exp(-r * T_safe),
    )


def _bsm_price_and_vega(
    inv: _NewtonInvariants, sigma: np.ndarray, is_call: np.ndarray
) -> tuple[np.ndarray, np.ndarray]:
    """BSM price and per-unit vega in one pass — bit-identical to bsm_price followed by
    _bsm_vega_only. `(rq + 0.5*sigma^2)*T / vol` is the parse order of the original expression;
    do not re-associate it — that changes rounding."""
    sigma_safe = np.maximum(sigma, _MIN_SIGMA)
    vol = sigma_safe * inv.sqrt_T
    d1 = (inv.logSK + (inv.rq + 0.5 * sigma_safe**2) * inv.T_safe) / vol
    d2 = d1 - vol
    N1 = _N(np.where(is_call, d1, -d1))
    N2 = _N(np.where(is_call, d2, -d2))
    price = np.where(is_call, inv.disc_S * N1 - inv.disc_K * N2, inv.disc_K * N2 - inv.disc_S * N1)
    vega = inv.disc_S * _n(d1) * inv.sqrt_T
    return price, vega


_NEWTON_BLOCK = 4096  # rows per block: an iteration's ~30 temporaries then stay in the L2 cache


def _newton(
    inv: _NewtonInvariants, price: np.ndarray, is_call: np.ndarray, n_iter: int
) -> tuple[np.ndarray, np.ndarray]:
    """sigma after n_iter Newton-Raphson updates from 0.3 and, per iteration, the largest
    |model - price| over all rows before that iteration's update. A row's path depends on its own
    inputs alone, so each block runs all its iterations before the next one starts: the same
    values as whole-array iterations, without streaming every temporary through memory."""
    sigma = np.full(price.size, 0.3)  # initial guess
    worst = np.full(n_iter, -np.inf)
    for lo in range(0, price.size, _NEWTON_BLOCK):
        rows = slice(lo, lo + _NEWTON_BLOCK)
        inv_b, price_b, call_b, sigma_b = inv.take(rows), price[rows], is_call[rows], sigma[rows]
        for it in range(n_iter):
            model_price, vega = _bsm_price_and_vega(inv_b, sigma_b, call_b)
            vega = np.maximum(vega, 1e-10)  # avoid div by zero
            diff = model_price - price_b
            worst[it] = np.maximum(worst[it], np.abs(diff).max())
            sigma_b[:] = np.clip(sigma_b - diff / vega, _MIN_SIGMA, _MAX_SIGMA)
    return sigma, worst


class IVResult(NamedTuple):
    """compute_iv(..., return_details=True): the IV array plus the two by-products."""

    iv: np.ndarray
    unidentifiable: np.ndarray  # iv_unidentifiable_mask(...) for the same inputs
    extrinsic: np.ndarray  # bsm_extrinsic(...) for the same inputs


@overload
def compute_iv(
    price,
    S,
    K,
    T,
    r,
    q,
    is_call,
    price_uncertainty: np.ndarray | float | None = None,
    *,
    return_details: Literal[False] = False,
) -> np.ndarray: ...


@overload
def compute_iv(
    price,
    S,
    K,
    T,
    r,
    q,
    is_call,
    price_uncertainty: np.ndarray | float | None = None,
    *,
    return_details: Literal[True],
) -> IVResult: ...


def compute_iv(
    price,
    S,
    K,
    T,
    r,
    q,
    is_call,
    price_uncertainty: np.ndarray | float | None = None,
    *,
    return_details: bool = False,
) -> np.ndarray | IVResult:
    """Implied volatility by Newton-Raphson (from 0.3, 50 iterations in row blocks) with a
    bisection fallback for rows still off by more than $0.01. All inputs are same-shape arrays.
    NaN where IV cannot be computed: below intrinsic, above the BSM upper bound, or
    unidentifiable (extrinsic at/below max(2 x $0.01, price_uncertainty))."""
    price = np.asarray(price, dtype=np.float64)  # every caller passes float64
    T_safe = np.maximum(T, _MIN_T)
    result = np.full_like(price, np.nan, dtype=np.float64)

    # True BSM bracket: [BSM(MIN_SIGMA), BSM(MAX_SIGMA)]. Anything outside is unphysical
    # (below intrinsic, or above the asymptotic upper bound).
    lo_sig = np.full_like(price, _MIN_SIGMA)
    hi_sig = np.full_like(price, _MAX_SIGMA)
    bsm_lo = bsm_price(S, K, T_safe, r, q, lo_sig, is_call)
    bsm_hi = bsm_price(S, K, T_safe, r, q, hi_sig, is_call)

    valid = (price >= bsm_lo - _BRACKET_TOL) & (price <= bsm_hi + _BRACKET_TOL)
    n_below = int((price < bsm_lo - _BRACKET_TOL).sum())
    n_above = int((price > bsm_hi + _BRACKET_TOL).sum())
    if n_below or n_above:
        log.debug(
            "compute_iv: %d rows below BSM(MIN_SIGMA), %d above BSM(MAX_SIGMA) — "
            "marking NaN (unphysical trades)",
            n_below,
            n_above,
        )

    # Identifiability gate: in-bracket prices whose extrinsic value sits within the
    # price-uncertainty envelope of the discounted intrinsic cannot identify sigma.
    extrinsic = price - bsm_lo
    unident = iv_unidentifiable_mask(
        price, S, K, T, r, q, is_call, price_uncertainty, extrinsic=extrinsic
    )
    n_gated = int((valid & unident).sum())
    if n_gated:
        log.debug(
            "compute_iv: %d in-bracket rows gated as unidentifiable "
            "(extrinsic within price uncertainty)",
            n_gated,
        )
    valid &= ~unident

    def _out(iv: np.ndarray):
        return IVResult(iv, unident, extrinsic) if return_details else iv

    if not valid.any():
        return _out(result)

    idx = np.where(valid)[0]
    S_v, K_v, T_v, r_v, q_v = S[idx], K[idx], T_safe[idx], r[idx], q[idx]
    price_v = price[idx]
    is_call_v = is_call[idx]

    # 50 Newton-Raphson updates, unless every row is within 1e-8 before one of them: the updates
    # then stop there for all rows, as they do when the whole array is iterated at once. Real
    # batches hold rows on a limit cycle and run all 50, so the second pass is the rare case.
    inv = _newton_invariants(S_v, K_v, T_v, r_v, q_v)
    sigma, worst = _newton(inv, price_v, is_call_v, 50)
    stop = np.flatnonzero(worst < 1e-8)
    if stop.size:
        sigma, _ = _newton(inv, price_v, is_call_v, int(stop[0]))

    # Bisection fallback for any that didn't converge. These rows *are* bracketed.
    price_check = bsm_price(S_v, K_v, T_v, r_v, q_v, sigma, is_call_v)
    not_converged = np.abs(price_check - price_v) > 0.01

    if not_converged.any():
        idx2 = np.where(not_converged)[0]
        bisection_result = _bisection_iv(
            price_v[idx2], S_v[idx2], K_v[idx2], T_v[idx2], r_v[idx2], q_v[idx2], is_call_v[idx2]
        )
        sigma[idx2] = bisection_result

        bisection_check = bsm_price(
            S_v[idx2], K_v[idx2], T_v[idx2], r_v[idx2], q_v[idx2], bisection_result, is_call_v[idx2]
        )
        still_bad = np.abs(bisection_check - price_v[idx2]) > 0.01
        if still_bad.any():
            log.warning(
                "IV bisection on bracketed rows did not converge: %d / %d "
                "(max residual %.4f) — this indicates a solver bug, not bad data",
                int(still_bad.sum()),
                len(idx2),
                float(np.abs(bisection_check - price_v[idx2]).max()),
            )

    result[idx] = sigma
    return _out(result)


def _bisection_iv(price, S, K, T, r, q, is_call) -> np.ndarray:
    """Bisection IV solver (100 halvings of [_MIN_SIGMA, _MAX_SIGMA], stop at 1e-8 width)."""
    lo = np.full_like(price, _MIN_SIGMA)
    hi = np.full_like(price, _MAX_SIGMA)

    for _ in range(100):
        mid = (lo + hi) / 2
        f_mid = bsm_price(S, K, T, r, q, mid, is_call) - price
        f_lo = bsm_price(S, K, T, r, q, lo, is_call) - price
        hi = np.where(np.sign(f_mid) == np.sign(f_lo), hi, mid)
        lo = np.where(np.sign(f_mid) == np.sign(f_lo), mid, lo)
        if (hi - lo).max() < 1e-8:
            break

    return (lo + hi) / 2


bisection_iv = _bisection_iv
