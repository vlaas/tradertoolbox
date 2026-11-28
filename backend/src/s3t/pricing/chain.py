"""IV and spot Greeks of the contracts of an option chain, in one array call (MKT-04).

New in WP-1.2 (2026-11-28). It puts together what the pricing core has, for the chain service
that runs it on every flush:

- the IV of each contract's price on its expiration's forward, by Black-76 (black76.black_iv),
  with the quoting tick of the contract widening the identifiability gate;
- the spot Greeks at that IV: against the level of an index with the implied yield held fixed
  (carry.proportional_greeks), and against the spot of a stock or an ETF with the cash dividends
  that the forward implies (escrowed.escrowed_greeks on carry.implied_pv), as decided at WP-1.1
  (requirements, section 3, "The surface package");
- no Greeks where there is no IV: `volcalc`'s `enforce_iv_greeks_contract`, the rule carried
  from WP-0.2.

The rows may belong to several expirations and several underlyings; every input is per row.
Units are raw (PRC-04); the service converts them.
"""

from typing import NamedTuple

import numpy as np

from s3t.pricing.black76 import GREEKS, black_iv
from s3t.pricing.bsm import _MIN_T
from s3t.pricing.carry import implied_pv, proportional_greeks
from s3t.pricing.escrowed import escrowed_greeks


class ContractValues(NamedTuple):
    iv: np.ndarray
    """NaN where `fail` is not black76.FAIL_OK."""
    fail: np.ndarray
    """black76's reason codes, uint8."""
    greeks: dict[str, np.ndarray]
    """Raw spot Greeks keyed as black76.GREEKS; NaN where there is no IV."""


def contract_values(
    price, F, K, tau, df, spot, is_call, proportional, tick=None, dpv_dr=None
) -> ContractValues:
    """`price` is the price of each contract (the mid of its quote), `F` and `df` the forward
    and the discount factor of its expiration, `spot` the price of its underlying, NaN where
    there is none. `proportional` marks the contracts of an index. `tick` is each contract's
    quoting tick; None keeps the solver's floor. `dpv_dr` is the rate derivative of the
    dividends' PV of a stock or an ETF (forward.pv_dividends_dr of its schedule); None is zero,
    since the PV that parity implies has none of its own."""
    price, F, K, tau, df, spot = (
        np.asarray(a, dtype=np.float64) for a in (price, F, K, tau, df, spot)
    )
    is_call = np.asarray(is_call, dtype=bool)
    proportional = np.asarray(proportional, dtype=bool)
    n = price.shape[0]
    result = black_iv(price, F, K, tau, df, is_call, tick)
    greeks = {name: np.full(n, np.nan) for name in GREEKS}
    solved = np.isfinite(result.iv)
    index = np.flatnonzero(solved & proportional)
    if index.size:
        found = proportional_greeks(
            spot[index], F[index], K[index], tau[index], df[index], result.iv[index], is_call[index]
        )
        for name in GREEKS:
            greeks[name][index] = found[name]
    cash = np.flatnonzero(solved & ~proportional)
    if cash.size:
        # The floored time, as black76 prices with it, so that the escrowed forward is F.
        t, d, s, f = np.maximum(tau[cash], _MIN_T), df[cash], spot[cash], F[cash]
        rate = -np.log(d) / t
        dr = np.zeros(cash.size) if dpv_dr is None else np.asarray(dpv_dr, np.float64)[cash]
        found = escrowed_greeks(
            s, K[cash], t, rate, implied_pv(s, f, d), dr, result.iv[cash], is_call[cash]
        )
        for name in GREEKS:
            greeks[name][cash] = found[name]
    return ContractValues(result.iv, result.fail, greeks)
