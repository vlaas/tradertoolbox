"""SSVI across the expirations of one underlying: the fallback smile of a thin one (PRC-06).

Source: `volcalc` src/vrp/surface/ssvi.py at commit f53be83, with the rules unchanged.

Changed in the port (2026-11-27, WP-1.1): `fit_theta` returns theta only; the leverage of its
points went with the leave-one-out sign (see svi.py).

From the source docstring (Gatheral & Jacquier 2014; port of bt_dataset surface.py's
fit_ssvi_global with a free gamma): w(k, theta) = (theta / 2) (1 + rho phi k +
sqrt((phi k + rho)^2 + 1 - rho^2)), phi(theta) = eta / (theta^gamma (1 + theta)^(1 - gamma)).
Free of static arbitrage when 0 < gamma <= 1/2, eta (1 + |rho|) <= 2 and theta(T) is
non-decreasing (GJ 2014 Thm 4.1, Cor. 4.1): the first two are kept by bounds and a penalty, the
last by a cumulative max over the slices' ATM total variances (`monotone`).

The global (rho, eta, gamma) are fitted in IV space over the pooled points of the slices with a
raw SVI fit, each at its own theta. A slice without one gets theta from a one-parameter fit of
its own points at the global shape (`fit_theta`, parameterised by the ATM vol s = sqrt(theta /
tau)); the caller clamps it between the neighbouring fitted slices' theta.
"""

import numpy as np
from scipy.optimize import least_squares

from s3t.pricing.svi import MAX_NFEV, PENALTY, W_MIN, Smile

ETA_MAX = 2.0  # eta (1 + |rho|) <= ETA_MAX
LO = np.array([-0.999, 1e-4, 0.01])  # rho, eta, gamma
HI = np.array([0.999, ETA_MAX, 0.5])
SEED = np.array([-0.5, 1.0, 0.3])
S_LO, S_HI = 1e-3, 5.0  # ATM vol bounds of the one-parameter theta fit


def phi(theta, eta, gamma):
    return eta / (np.power(theta, gamma) * np.power(1 + theta, 1 - gamma))


def w_derivs(theta, shape, k):
    """Total variance and its first two k-derivatives at theta and the shape (rho, eta,
    gamma); every element broadcasts."""
    rho, eta, gamma = shape
    p = phi(theta, eta, gamma)
    u = p * k + rho
    R = np.sqrt(u * u + 1 - rho * rho)
    w = theta / 2 * (1 + rho * p * k + R)
    wp = theta / 2 * (rho * p + p * u / R)
    wpp = theta / 2 * p * p * (1 - rho * rho) / R**3
    return w, wp, wpp


def smile(theta: float, shape: np.ndarray) -> Smile:
    return lambda k: w_derivs(theta, shape, np.asarray(k, dtype=np.float64))


def monotone(theta: np.ndarray) -> np.ndarray:
    """Calendar condition: theta non-decreasing in maturity (slices in tau order)."""
    return np.maximum.accumulate(theta)


def _res_global(x, k, iv, sw, theta, tau):
    w = w_derivs(theta, x, k)[0]
    pen = max(x[1] * (1 + abs(x[0])) - ETA_MAX, 0.0)
    return np.concatenate([sw * (np.sqrt(np.maximum(w, W_MIN) / tau) - iv), [PENALTY * pen]])


def fit_global(
    k: np.ndarray,
    iv: np.ndarray,
    wt: np.ndarray,
    theta: np.ndarray,
    tau: np.ndarray,
    warm: np.ndarray | None,
) -> np.ndarray | None:
    """(rho, eta, gamma) over pooled points, each with its slice's theta and tau; seeds = warm
    start + SEED, lowest cost wins; eta clamped to the no-arbitrage bound after the fit."""
    if k.size < 5:
        return None
    sw = np.sqrt(wt)
    best, best_cost = None, np.inf
    for x0 in [SEED] + ([warm] if warm is not None else []):
        try:
            res = least_squares(
                _res_global,
                np.clip(x0, LO, HI),
                bounds=(LO, HI),
                args=(k, iv, sw, theta, tau),
                x_scale="jac",
                max_nfev=MAX_NFEV,
            )
        except (ValueError, np.linalg.LinAlgError):
            continue
        if np.all(np.isfinite(res.x)) and res.cost < best_cost:
            best, best_cost = res.x, float(res.cost)
    if best is None:
        return None
    best[1] = min(best[1], ETA_MAX / (1 + abs(best[0])))
    return best


def _res_theta(s, k, iv, sw, tau, shape):
    w = w_derivs(s[0] * s[0] * tau, shape, k)[0]
    return sw * (np.sqrt(np.maximum(w, W_MIN) / tau) - iv)


def fit_theta(
    k: np.ndarray, iv: np.ndarray, wt: np.ndarray, tau: float, shape: np.ndarray
) -> float:
    """theta of a slice at the global shape (rho, eta, gamma) from its own points."""
    sw = np.sqrt(wt)
    s0 = float(np.clip(np.median(iv), S_LO, S_HI))
    res = least_squares(
        _res_theta,
        [s0],
        bounds=([S_LO], [S_HI]),
        args=(k, iv, sw, tau, shape),
        max_nfev=MAX_NFEV,
    )
    s = float(res.x[0])
    return s * s * tau
