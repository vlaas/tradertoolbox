"""The rate curve: quoted yields to continuous rates, and the curve over tenors (REF-01, pure part).

Source: `volcalc` src/vrp/refdata/rates.py at commit f53be83: `SERIES`, `TENOR_DAYS`,
`to_continuous` and `interpolate_rate`. The conversions and the interpolation are unchanged.

Changed in the port (2026-11-22, WP-0.2):
- `to_continuous` works on floats and numpy arrays instead of a polars expression.
- Added `RateCurve`: the curve as a value, with the flat configured rate as its fallback form
  and a `source` the app can show.
- Not ported here: the FRED download and the as-of selection of the latest published values
  (`build_rates`, `rates_asof`). They are I/O and stored history, which PRC-01 keeps out of
  `pricing`; they come with the reference data of WP-0.6. The source's merge with a local
  parquet file is dropped (architecture note, extraction table).
"""

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Literal

import numpy as np

SERIES = ("SOFR", "DTB3", "DGS1MO", "DGS3MO", "DGS6MO", "DGS1")
TENOR_DAYS = MappingProxyType({"SOFR": 1, "DGS1MO": 30, "DGS3MO": 91, "DGS6MO": 182, "DGS1": 365})
DAYS = 365.0  # rates are continuous per 365-day year


def to_continuous(series: str, y):
    """Quoted yield (decimal) -> continuously compounded rate per 365-day year."""
    y = np.asarray(y, dtype=np.float64)
    if series == "SOFR":  # overnight, act/360, compounded daily
        return np.log(1 + y / 360) * 365
    if series == "DTB3":  # 3-month bill, bank-discount basis, act/360
        return -np.log(1 - y * 91 / 360) * 365 / 91
    if series == "DGS1":  # CMT 1 y, bond-equivalent (semiannual)
        return np.log(1 + y / 2) * 2
    n = TENOR_DAYS[series]  # CMT bills <= 6 m, investment basis = simple act/365 over the tenor
    return np.log(1 + y * n / 365) * 365 / n


def interpolate_rate(rates: Mapping[str, float | None], days):
    """Piecewise-linear in calendar days on the tenor knots present, flat beyond both ends.
    `rates` maps series to continuous rates; series without a value are skipped. Raises when
    no tenor is available."""
    knots = sorted((TENOR_DAYS[s], r) for s, r in rates.items() if r is not None)
    if not knots:
        raise ValueError("no rate tenor available")
    xs, ys = zip(*knots, strict=True)
    return np.interp(days, xs, ys)


@dataclass(frozen=True)
class RateCurve:
    """Continuous rates on tenor knots (calendar days), linear between them and flat beyond.
    `source` says whether it was built from quotes or is the flat configured fallback."""

    days: tuple[float, ...]
    rates: tuple[float, ...]
    source: Literal["curve", "flat"]

    def __post_init__(self) -> None:
        if not self.days or len(self.days) != len(self.rates):
            raise ValueError("a rate curve needs one rate per knot and at least one knot")
        if any(b <= a for a, b in zip(self.days, self.days[1:], strict=False)):
            raise ValueError("rate curve knots must be strictly increasing")

    @classmethod
    def from_rates(cls, rates: Mapping[str, float | None]) -> "RateCurve":
        """The curve on the series that have a value (continuous rates, as interpolate_rate)."""
        knots = sorted((float(TENOR_DAYS[s]), float(r)) for s, r in rates.items() if r is not None)
        if not knots:
            raise ValueError("no rate tenor available")
        days, values = zip(*knots, strict=True)
        return cls(days, values, "curve")

    @classmethod
    def flat(cls, rate: float) -> "RateCurve":
        return cls((1.0,), (float(rate),), "flat")

    def rate(self, tau):
        """Continuous rate for a horizon of tau years (365-day years)."""
        return np.interp(np.asarray(tau, dtype=np.float64) * DAYS, self.days, self.rates)

    def discount(self, tau):
        """Discount factor e^{-r(tau) tau}."""
        tau = np.asarray(tau, dtype=np.float64)
        return np.exp(-self.rate(tau) * tau)
