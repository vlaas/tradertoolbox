"""The expected move of an expiration from its quotes, as Tastytrade's option chain shows it.

Source: `backtester` backtester/lib/expected_move.py at commit 0a296d2 (the file unchanged since
2406358): `em_platform` and its helpers `_row_mid`, `_atm_strike` and `_atm_neighbors`. The rule
is unchanged:

    EM = 0.6 straddle(K0) + 0.3 strangle(K-1, K+1) + 0.1 strangle(K-2, K+2)

K0 is the listed strike nearest the spot, K-1 and K+1 the strikes next to it below and above,
K-2 and K+2 the ones after them. A straddle or strangle is the sum of the mids of its put and its
call. A leg without a usable mid drops its term, and the weights of the terms left are scaled to
sum to one. The source says the blend matches the platform's "±" number on the chain; that rests
on third parties who matched it, and was not checked here (WP-1.9 compares it with Tastytrade's
chain).

Changed in the port (2026-11-28, WP-1.2):
- Arrays of one expiration, strikes ascending with parallel call and put quotes, instead of a
  polars table with a row per contract. A side that is not listed is NaN.
- A mid needs a bid above zero and an ask at or above it, as the source's `_row_mid`; there is no
  stored mid to prefer.
- The price the strikes are placed around is the caller's (`money`). The source takes the spot;
  the chain gives the forward of the expiration where it has one, which is the spot for a short
  expiration and lies above it for a long one (s3t.analysis.chain).
- Not ported: `em_binary`, `em_iv` and the dispatch. The application's surface has the
  at-the-money IV for an IV form (s3t.pricing.surface.Slice.atm_iv).
"""

import math

import numpy as np

METHOD = "straddle 0.6/0.3/0.1"
"""The name of the rule, for every result that shows it (GEN-06)."""
WEIGHTS = (0.6, 0.3, 0.1)


def mids(bid, ask) -> np.ndarray:
    """The mid of each quote; NaN without a bid above zero and an ask at or above it."""
    bid, ask = np.asarray(bid, dtype=np.float64), np.asarray(ask, dtype=np.float64)
    with np.errstate(invalid="ignore"):
        good = (bid > 0) & (ask >= bid)
    return np.where(good, (bid + ask) / 2.0, np.nan)


def expected_move(strike, call_bid, call_ask, put_bid, put_ask, money: float) -> float:
    """The expected move in price units of the underlying, around the strike nearest ``money``;
    NaN when no term has its legs or there is no price."""
    strike = np.asarray(strike, dtype=np.float64)
    if strike.size == 0 or not (math.isfinite(money) and money > 0):
        return math.nan
    call, put = mids(call_bid, call_ask), mids(put_bid, put_ask)
    i = int(np.argmin(np.abs(strike - money)))
    terms: list[tuple[float, float]] = []
    straddle = call[i] + put[i]
    if math.isfinite(straddle):
        terms.append((WEIGHTS[0], float(straddle)))
    for step, weight in ((1, WEIGHTS[1]), (2, WEIGHTS[2])):
        lo, hi = i - step, i + step
        if lo < 0 or hi >= strike.size:
            continue
        strangle = put[lo] + call[hi]
        if math.isfinite(strangle):
            terms.append((weight, float(strangle)))
    if not terms:
        return math.nan
    return sum(w * p for w, p in terms) / sum(w for w, _ in terms)
