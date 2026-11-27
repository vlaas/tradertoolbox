"""The forward of one expiration from put-call parity over quoted strikes (PRC-05).

Source: `volcalc` src/vrp/iv/forward.py at commit f53be83: the fixed-discount-factor part of
`fit_forward` and the rules of `slice_forwards`, unchanged: the pair band around the model
forward, the exclusion of in-the-money calls near an ex-date, three pairs at least, the implied
dividend and its flag.

Changed in the port (2026-11-27, WP-1.1):
- Arrays of one expiration instead of a polars table of many slices.
- Quote mids instead of trade prints. The quotes of one moment see one spot, so the source's
  transport of each pair to the snapshot spot (`pair_prints`, the scaling by S / S_pair) is gone.
- Without a model forward (an index without a price) the band is centred on the pair whose call
  and put mids are nearest, where K is nearest to F.
- The result says where the forward came from: "parity", "model" or "none".
- Not ported: the free-discount-factor fit and its box rate (a diagnostic in the source), and
  `fwd_model_share`.

From the source docstring: put-call parity makes Y = C - P linear in strike, Y = DF (F - K). With
DF fixed from the rate curve every call/put pair at one strike measures F_i = K + Y_i / DF.
Robust location as in bt_dataset spx_parity: Theil-Sen seed (the median, the slope being known),
3-MAD inlier screen with a $0.05 floor, weighted mean. Fewer than MIN_PAIRS inliers -> model
forward F = (S - PV(div)) e^{r tau}. American roots: a call that is in the money within a few
sessions before an ex-date its expiry spans is exercised early and breaks parity; such pairs
(K < F_model) are dropped.

Not handled, as in the source: the early-exercise premium of American puts. It lowers the parity
forward of a long-dated stock or ETF expiration a little (PRC-10; the American pricer is carried
in the plan, section 8).
"""

from typing import NamedTuple

import numpy as np

MIN_PAIRS = 3  # fewer inliers -> model forward
MAD_K = 3.0  # inlier screen half-width in MADs ...
MAD_EPS = 0.05  # ... with a one-nickel floor, so an exact chain keeps every pair
FWD_BAND = 0.05  # pairs within |K / F_model - 1| < FWD_BAND (`volcalc` [iv] fwd_band)
EXDIV_DAYS = (
    5  # sessions before an ex-date in which ITM calls are dropped ([iv] exdiv_exclude_days)
)
DIV_DEV = 0.10  # implied dividend off the schedule by more than this share is flagged

PARITY, MODEL, NONE = "parity", "model", "none"


class Fit(NamedTuple):
    forward: float  # NaN below MIN_PAIRS inliers
    rmse: float  # weighted RMS of the inliers' forwards around it, in the price of F
    pairs: int  # inliers


class ParityForward(NamedTuple):
    forward: float  # the forward to use: parity where it holds, else the model; NaN without both
    source: str  # PARITY | MODEL | NONE
    parity: float  # the parity forward; NaN below MIN_PAIRS inliers
    rmse: float  # of the parity fit; NaN without one
    offered: int  # pairs inside the band, after the ex-date exclusion
    pairs: int  # inliers of the screen
    div_implied: float  # PV of the dividends that parity implies: S - F DF; NaN without S or F
    div_flag: bool | None  # it differs from the schedule's PV by more than DIV_DEV; None when
    # the schedule has no dividend before the expiration, or there is no parity forward


def mid(bid, ask) -> np.ndarray:
    """The mid of a quote, NaN where a side is missing, the bid is not above zero or the ask is
    below the bid: a quote without a bid says nothing of the price."""
    bid, ask = np.asarray(bid, dtype=np.float64), np.asarray(ask, dtype=np.float64)
    with np.errstate(invalid="ignore"):
        ok = (bid > 0) & (ask >= bid)
    return np.where(ok, (bid + ask) / 2, np.nan)


def fit_forward(strike, y, df: float, weight=None) -> Fit:
    """F from pairs with Y = C - P at a known DF: F_i = K + Y / DF, the median as seed, the
    screen |F_i - median| <= max(MAD_K MAD, MAD_EPS), then the weighted mean of the inliers."""
    f = np.asarray(strike, dtype=np.float64) + np.asarray(y, dtype=np.float64) / df
    w = np.ones_like(f) if weight is None else np.asarray(weight, dtype=np.float64)
    if f.size == 0:
        return Fit(np.nan, np.nan, 0)
    ad = np.abs(f - np.median(f))
    inlier = ad <= max(MAD_K * float(np.median(ad)), MAD_EPS)
    n = int(inlier.sum())
    if n < MIN_PAIRS:
        return Fit(np.nan, np.nan, n)
    f, w = f[inlier], w[inlier]
    mean = float(np.sum(w * f) / np.sum(w))
    return Fit(mean, float(np.sqrt(np.sum(w * (f - mean) ** 2) / np.sum(w))), n)


def parity_forward(
    strike,
    call_mid,
    put_mid,
    df: float,
    *,
    spot: float = np.nan,
    model: float = np.nan,
    pv_div: float = 0.0,
    exdiv_near: bool = False,
    band: float = FWD_BAND,
) -> ParityForward:
    """The forward of one expiration. strike, call_mid and put_mid are parallel arrays, NaN where
    a strike has no usable quote on that side (`mid`). df is the discount factor to the
    settlement instant, from the rate curve. model is the model forward (s3t.pricing.forward) and
    pv_div the PV of the schedule's dividends before the expiration; exdiv_near says that one of
    them goes ex within EXDIV_DAYS sessions, which the caller counts on its calendar."""
    k = np.asarray(strike, dtype=np.float64)
    y = np.asarray(call_mid, dtype=np.float64) - np.asarray(put_mid, dtype=np.float64)
    ok = np.isfinite(k) & np.isfinite(y) & (k > 0)
    k, y = k[ok], y[ok]
    has_model = bool(np.isfinite(model)) and model > 0
    if has_model:
        centre = float(model)
    elif k.size:
        i = int(np.argmin(np.abs(y)))
        centre = float(k[i] + y[i] / df)
    else:
        centre = np.nan
    fit = Fit(np.nan, np.nan, 0)
    offered = 0
    if np.isfinite(centre) and centre > 0:
        keep = np.abs(k / centre - 1) < band
        if exdiv_near:
            keep &= k >= centre
        offered = int(keep.sum())
        fit = fit_forward(k[keep], y[keep], df)
    parity = fit.forward
    if np.isfinite(parity):
        forward, source = parity, PARITY
    elif has_model:
        forward, source = float(model), MODEL
    else:
        forward, source = np.nan, NONE
    implied = float(spot - parity * df) if np.isfinite(parity) and np.isfinite(spot) else np.nan
    flag = None
    if pv_div > 0 and np.isfinite(implied):
        flag = bool(abs(implied - pv_div) > DIV_DEV * pv_div)
    return ParityForward(forward, source, parity, fit.rmse, offered, fit.pairs, implied, flag)
