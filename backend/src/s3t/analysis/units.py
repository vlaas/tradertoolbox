"""Trader units at the service edge (PRC-04).

`pricing` returns raw per-share values (s3t.pricing, module docstring). What the screens and the
tools show is per position: multiplied by the contract multiplier and the signed quantity
(positive long, negative short), with time and volatility in the units traders quote:

    value   dollars
    delta   share equivalents: the shares that move like the position
    gamma   change of the position delta for a one-dollar move of the underlying
    theta   dollars per calendar day
    vega    dollars per volatility point
    rho     dollars per rate point (1 %)
    vanna   change of the position delta per volatility point
    charm   change of the position delta per calendar day
    volga   change of the dollar vega per volatility point

Decided 2026-11-22 with the owner (requirements 4.6). The option chain shows the same units
per contract: multiplier and quantity 1.
"""

from collections.abc import Mapping
from types import MappingProxyType

import numpy as np

DAYS_PER_YEAR = 365.0  # the calendar clock of s3t.pricing.clocks
POINT = 0.01  # one volatility point, one rate point

SCALE = MappingProxyType(
    {
        "value": 1.0,
        "delta": 1.0,
        "gamma": 1.0,
        "theta": 1.0 / DAYS_PER_YEAR,
        "vega": POINT,
        "rho": POINT,
        "vanna": POINT,
        "charm": 1.0 / DAYS_PER_YEAR,
        "volga": POINT * POINT,
    }
)


def position_units(raw: Mapping[str, np.ndarray], multiplier, quantity) -> dict[str, np.ndarray]:
    """Raw per-share values (the price under "value", Greeks under their names) to trader units
    per position. Keys outside SCALE are refused, so a new Greek cannot pass unconverted."""
    unknown = set(raw) - set(SCALE)
    if unknown:
        raise KeyError(f"no trader unit for {sorted(unknown)}")
    size = np.asarray(multiplier, dtype=np.float64) * np.asarray(quantity, dtype=np.float64)
    return {k: np.asarray(v, dtype=np.float64) * SCALE[k] * size for k, v in raw.items()}
