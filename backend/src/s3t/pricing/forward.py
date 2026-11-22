"""Discrete cash dividends and the model forward of a stock or ETF.

Source: `volcalc` src/vrp/iv/forward.py at commit f53be83: `dividend_schedule`, `pv_dividends`
and `model_forward`, with the rules unchanged (product definition 11.1, REF-02).

Changed in the port (2026-11-22, WP-0.2):
- `dividend_schedule` takes plain values instead of a polars table: the dividends known on day d
  and the payment frequency. Choosing what is known on d (the source's `known_by`) is the
  caller's job; in the app the broker's market metrics give the next ex-date and amount.
- Added `pv_dividends_dr`, the rate derivative of the PV, which the spot rho of escrowed.py
  needs.
- Not ported: the put-call parity fit (`pair_prints`, `fit_forward`, `slice_forwards`,
  `slice_inputs`, `fwd_model_share`). It is PRC-05 and comes with WP-1.1.

From the source docstring: ex-dates in (d, settle_date] known on d, plus the last known dividend
projected every 365/frequency days where none is declared yet (SPY declares a year's ex-dates
in early January, GDX days before its ex-date).
"""

from collections.abc import Iterable, Sequence
from datetime import date, timedelta
from typing import NamedTuple

import numpy as np

DAYS = 365.0  # rates are continuous per 365-day year


class Dividend(NamedTuple):
    ex_date: date
    amount: float  # cash per share
    projected: bool  # False: declared; True: the last known amount repeated by frequency


def dividend_schedule(
    known: Iterable[tuple[date, float]], frequency: int | None, d: date, settle_date: date
) -> list[Dividend]:
    """Cash dividends a holder on day d forgoes by settle_date: the known ex-dates in
    (d, settle_date] (`projected` false) plus the last known dividend repeated every
    365/frequency days from its ex-date, where no declared ex-date lies within half a period
    (`projected` true). `known` holds (ex_date, amount) of the dividends known on d, past ones
    included: the latest is the one projected. A frequency of None counts as quarterly."""
    known = sorted(known)
    sched = [Dividend(e, a, False) for e, a in known if d < e <= settle_date]
    if not known:
        return sched
    last_ex, last_amount = known[-1]
    freq = 4 if frequency is None else max(frequency, 1)
    period = timedelta(days=round(DAYS / freq))
    declared = [x.ex_date for x in sched]
    e = last_ex + period
    while e <= settle_date:
        if e > d and all(abs((e - k).days) >= period.days / 2 for k in declared):
            sched.append(Dividend(e, last_amount, True))
        e += period
    return sorted(sched, key=lambda x: x.ex_date)


def _times(sched: Sequence[Dividend], d: date) -> tuple[np.ndarray, np.ndarray]:
    t = np.array([(x.ex_date - d).days for x in sched], dtype=np.float64) / DAYS
    return t, np.array([x.amount for x in sched], dtype=np.float64)


def pv_dividends(sched: Sequence[Dividend], d: date, r: float) -> float:
    """PV on day d of the schedule: sum D_i e^{-r t_i}, t_i = days from d to the ex-date / 365."""
    t, amount = _times(sched, d)
    return float(np.sum(amount * np.exp(-r * t)))


def pv_dividends_dr(sched: Sequence[Dividend], d: date, r: float) -> float:
    """d PV / d r = -sum D_i t_i e^{-r t_i}: zero or negative."""
    t, amount = _times(sched, d)
    return float(-np.sum(amount * t * np.exp(-r * t)))


def model_forward(S, r, tau, pv_div):
    """F = (S - PV(div)) e^{r tau}: escrowed dividends (the source notes that S e^{r tau} -
    PV(div) would mix a PV at t with a value at T)."""
    return (S - pv_div) * np.exp(r * tau)
