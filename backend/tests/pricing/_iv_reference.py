# Source: `volcalc` tests/_iv_reference.py at commit f53be83, unchanged below this comment.
"""
FROZEN reference copy of bt_dataset pipeline/greeks.py (now vrp/iv/bsm.py): the BSM kernel
and IV solver as of 2026-10-10 (pre-WP1, scipy.stats.norm based). It exists only so
tests/test_bsm_bitexact.py can prove the optimized kernels are bit-identical
to what produced the live store. Do not edit, do not import from production.
"""

import logging

import numpy as np
from scipy.stats import norm

log = logging.getLogger(__name__)

_MIN_T = 1.0 / 525960.0
_MIN_SIGMA = 0.001
_MAX_SIGMA = 20.0
_BRACKET_TOL = 0.01
_IDENT_TOL_MULT = 2.0

_N = norm.cdf
_n = norm.pdf


def _d1_d2(
    S: np.ndarray, K: np.ndarray, T: np.ndarray, r: np.ndarray, q: np.ndarray, sigma: np.ndarray
):
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
    """
    BSM option price with continuous dividend yield.

    Args:
        S: Underlying price
        K: Strike
        T: Time to expiry (years)
        r: Risk-free rate (annualized decimal)
        q: Continuous dividend yield (annualized decimal)
        sigma: Implied volatility (annualized decimal)
        is_call: True for call, False for put

    Returns:
        Option price array
    """
    d1, d2 = _d1_d2(S, K, T, r, q, sigma)
    T_safe = np.maximum(T, _MIN_T)
    disc_K = K * np.exp(-r * T_safe)
    disc_S = S * np.exp(-q * T_safe)

    call_price = disc_S * _N(d1) - disc_K * _N(d2)
    put_price = disc_K * _N(-d2) - disc_S * _N(-d1)

    return np.where(is_call, call_price, put_price)


def _bsm_vega_only(
    S: np.ndarray,
    K: np.ndarray,
    T: np.ndarray,
    r: np.ndarray,
    q: np.ndarray,
    sigma: np.ndarray,
) -> np.ndarray:
    """
    Fast path for the IV solver: computes only vega (∂V/∂σ), skipping the
    other Greeks. Returns vega in per-unit form (NOT divided by 100), which
    is what the Newton-Raphson step uses.
    """
    T_safe = np.maximum(T, _MIN_T)
    sigma_safe = np.maximum(sigma, _MIN_SIGMA)
    d1, _ = _d1_d2(S, K, T, r, q, sigma)
    sqrt_T = np.sqrt(T_safe)
    disc_S = S * np.exp(-q * T_safe)
    return disc_S * _n(d1) * sqrt_T


# Public alias: per-unit BSM vega (∂V/∂σ, NOT per 1% IV). Used as the fit
# weight for the 0DTE smile (pipeline_0dte/smile.py) — σ_IV ≈ σ_price / vega,
# so vega-weighting is the statistically-correct de-noising of observed IVs.
bsm_vega = _bsm_vega_only


def bsm_greeks(
    S: np.ndarray,
    K: np.ndarray,
    T: np.ndarray,
    r: np.ndarray,
    q: np.ndarray,
    sigma: np.ndarray,
    is_call: np.ndarray,
) -> dict[str, np.ndarray]:
    """
    Compute all BSM Greeks for vectorized arrays.

    Returns:
        Dict with keys: delta, gamma, theta (per day), vega (per 1% IV), rho (per 1%)
    """
    T_safe = np.maximum(T, _MIN_T)
    sigma_safe = np.maximum(sigma, _MIN_SIGMA)
    d1, d2 = _d1_d2(S, K, T, r, q, sigma)
    sqrt_T = np.sqrt(T_safe)

    disc_S = S * np.exp(-q * T_safe)
    disc_K = K * np.exp(-r * T_safe)

    n_d1 = _n(d1)

    # Delta: ∂V/∂S
    call_delta = np.exp(-q * T_safe) * _N(d1)
    put_delta = -np.exp(-q * T_safe) * _N(-d1)
    delta = np.where(is_call, call_delta, put_delta)

    # Gamma: ∂²V/∂S² (same for calls and puts)
    gamma = n_d1 * np.exp(-q * T_safe) / (S * sigma_safe * sqrt_T)

    # Theta: ∂V/∂t (per calendar day, negative for long options)
    call_theta = (
        -disc_S * n_d1 * sigma_safe / (2 * sqrt_T) - r * disc_K * _N(d2) + q * disc_S * _N(d1)
    ) / 365.0
    put_theta = (
        -disc_S * n_d1 * sigma_safe / (2 * sqrt_T) + r * disc_K * _N(-d2) - q * disc_S * _N(-d1)
    ) / 365.0
    theta = np.where(is_call, call_theta, put_theta)

    # Vega: ∂V/∂σ (per 1% change in IV, same for calls and puts)
    vega = disc_S * n_d1 * sqrt_T / 100.0

    # Rho: ∂V/∂r (per 1% change in rate)
    call_rho = disc_K * T_safe * _N(d2) / 100.0
    put_rho = -disc_K * T_safe * _N(-d2) / 100.0
    rho = np.where(is_call, call_rho, put_rho)

    return {"delta": delta, "gamma": gamma, "theta": theta, "vega": vega, "rho": rho}


# ---------------------------------------------------------------------------
# Implied volatility solver
# ---------------------------------------------------------------------------


def bsm_extrinsic(
    price: np.ndarray,
    S: np.ndarray,
    K: np.ndarray,
    T: np.ndarray,
    r: np.ndarray,
    q: np.ndarray,
    is_call: np.ndarray,
) -> np.ndarray:
    """Extrinsic (time) value: price − BSM(σ→_MIN_SIGMA).

    BSM at the sigma floor is the discounted intrinsic
    (max(S·e^(−qT) − K·e^(−rT), 0) for calls, mirrored for puts), so this is
    the portion of the price that actually carries vol information. Negative
    where the price sits below discounted intrinsic.
    """
    T_safe = np.maximum(T, _MIN_T)
    lo_sig = np.full_like(np.asarray(price, dtype=np.float64), _MIN_SIGMA)
    return price - bsm_price(S, K, T_safe, r, q, lo_sig, is_call)


def iv_unidentifiable_mask(
    price: np.ndarray,
    S: np.ndarray,
    K: np.ndarray,
    T: np.ndarray,
    r: np.ndarray,
    q: np.ndarray,
    is_call: np.ndarray,
    price_uncertainty: np.ndarray | float | None = None,
    *,
    extrinsic: np.ndarray | None = None,
) -> np.ndarray:
    """True where the price cannot identify sigma.

    A row is unidentifiable when its extrinsic value is at or below
    max(_IDENT_TOL_MULT × _BRACKET_TOL, price_uncertainty): every sigma from
    ~0 up to some level prices within that band, so an inverted IV would be
    arbitrary. Includes prices below discounted intrinsic (extrinsic < 0) —
    they equally carry no vol information.

    Args:
        price_uncertainty: per-row (or scalar) price uncertainty to widen the
            gate, typically the quoting tick / 2 for the option root. None →
            solver-tolerance floor only.
        extrinsic: precomputed bsm_extrinsic() result, to avoid a second BSM
            evaluation when the caller already has it.

    Returns:
        Boolean array. NaN inputs yield False (such rows fail the BSM bracket
        check anyway).
    """
    if extrinsic is None:
        extrinsic = bsm_extrinsic(price, S, K, T, r, q, is_call)
    threshold = np.full_like(
        np.asarray(extrinsic, dtype=np.float64), _IDENT_TOL_MULT * _BRACKET_TOL
    )
    if price_uncertainty is not None:
        threshold = np.maximum(threshold, price_uncertainty)
    return extrinsic <= threshold


def compute_iv(
    price: np.ndarray,
    S: np.ndarray,
    K: np.ndarray,
    T: np.ndarray,
    r: np.ndarray,
    q: np.ndarray,
    is_call: np.ndarray,
    price_uncertainty: np.ndarray | float | None = None,
) -> np.ndarray:
    """
    Compute implied volatility using Newton-Raphson with bisection fallback.

    Args: all arrays, must be same shape.
        price_uncertainty: optional per-row/scalar price uncertainty widening
            the identifiability gate (see iv_unidentifiable_mask), typically
            the root's quoting tick / 2.

    Returns:
        IV array. nan where IV cannot be computed: below intrinsic, above the
        BSM upper bound, or unidentifiable (extrinsic value at/below
        max(2×$0.01, price_uncertainty) — the price carries no measurable vol
        information and any returned sigma would be arbitrary).
    """
    iv = np.full_like(price, 0.3, dtype=np.float64)  # initial guess
    T_safe = np.maximum(T, _MIN_T)
    result = np.full_like(price, np.nan, dtype=np.float64)

    # True BSM bracket: [BSM(MIN_SIGMA), BSM(MAX_SIGMA)]. Anything outside
    # this range is unphysical (below intrinsic, or above the asymptotic
    # upper bound) and the solver cannot produce a valid IV — typically bad
    # trade prints or stale/late trades that slipped past Tier-1 filtering.
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

    # Identifiability gate: in-bracket prices whose extrinsic value sits
    # within the price-uncertainty envelope of the discounted intrinsic
    # cannot identify sigma — NaN instead of a confidently-wrong value.
    unident = iv_unidentifiable_mask(
        price,
        S,
        K,
        T,
        r,
        q,
        is_call,
        price_uncertainty,
        extrinsic=price - bsm_lo,
    )
    n_gated = int((valid & unident).sum())
    if n_gated:
        log.debug(
            "compute_iv: %d in-bracket rows gated as unidentifiable "
            "(extrinsic within price uncertainty)",
            n_gated,
        )
    valid &= ~unident

    if not valid.any():
        return result

    # Newton-Raphson iterations on valid elements
    idx = np.where(valid)[0]
    S_v, K_v, T_v, r_v, q_v = S[idx], K[idx], T_safe[idx], r[idx], q[idx]
    price_v = price[idx]
    is_call_v = is_call[idx]
    sigma = iv[idx]

    # Newton-Raphson with a vega-only fast path. The previous version called
    # bsm_greeks() which computes delta/gamma/theta/vega/rho on every
    # iteration — ~5× the work we actually need here.
    for _ in range(50):
        model_price = bsm_price(S_v, K_v, T_v, r_v, q_v, sigma, is_call_v)
        vega = _bsm_vega_only(S_v, K_v, T_v, r_v, q_v, sigma)
        vega = np.maximum(vega, 1e-10)  # avoid div by zero
        diff = model_price - price_v
        if np.abs(diff).max() < 1e-8:
            break
        sigma -= diff / vega
        sigma = np.clip(sigma, _MIN_SIGMA, _MAX_SIGMA)

    # Bisection fallback for any that didn't converge. These rows *are*
    # bracketed (pre-filtered above), so a successful bisection is expected.
    price_check = bsm_price(S_v, K_v, T_v, r_v, q_v, sigma, is_call_v)
    not_converged = np.abs(price_check - price_v) > 0.01

    if not_converged.any():
        idx2 = np.where(not_converged)[0]
        bisection_result = _bisection_iv(
            price_v[idx2], S_v[idx2], K_v[idx2], T_v[idx2], r_v[idx2], q_v[idx2], is_call_v[idx2]
        )
        sigma[idx2] = bisection_result

        # Sanity: verify bisection actually converged. Since inputs are
        # bracketed, this should never fire. If it does, the bisection
        # implementation has a genuine bug — warn loudly.
        bisection_check = bsm_price(
            S_v[idx2],
            K_v[idx2],
            T_v[idx2],
            r_v[idx2],
            q_v[idx2],
            bisection_result,
            is_call_v[idx2],
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
    return result


def _bisection_iv(price, S, K, T, r, q, is_call) -> np.ndarray:
    """Bisection IV solver for fallback."""
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
