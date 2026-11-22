"""The pricing core: a pure library (PRC-01). No I/O, no global state, numpy arrays in and out.

The conventions are those of the product definition, section 11.1. Units are raw (PRC-04); the
service layer converts them (s3t.analysis.units).

    time      years on the calendar clock, 365 days (clocks.tau_cal)
    rates     continuous, per year (rates.to_continuous)
    sigma     per square root of a year: 0.20 is 20 vol points
    price     per unit of the underlying (per share, per index point)
    delta     dV/dS                  gamma  d2V/dS2
    theta     dV/dt per year         vega   dV/dsigma per unit sigma
    rho       dV/dr per unit r       vanna  d2V/dS dsigma
    charm     d(delta)/dt per year   volga  d2V/dsigma2

black76 takes S to be the forward F and holds it fixed as time passes; escrowed takes the spot
of a stock with discrete dividends and holds the spot fixed. Each module says where it was taken
from (PRC-03).
"""

from s3t.pricing.black76 import (
    FAIL_ABOVE_CAP,
    FAIL_BELOW_INTRINSIC,
    FAIL_INVALID,
    FAIL_NAMES,
    FAIL_NO_CONVERGENCE,
    FAIL_OK,
    FAIL_UNIDENTIFIABLE,
    GREEKS,
    IV_CAP,
    BlackIV,
    black_greeks,
    black_iv,
    black_price,
)
from s3t.pricing.clocks import MINUTES_PER_YEAR, tau_cal, tau_cal_array
from s3t.pricing.escrowed import escrowed_greeks, escrowed_iv, escrowed_price
from s3t.pricing.forward import (
    Dividend,
    dividend_schedule,
    model_forward,
    pv_dividends,
    pv_dividends_dr,
)
from s3t.pricing.rates import RateCurve, interpolate_rate, to_continuous

__all__ = (
    "FAIL_ABOVE_CAP",
    "FAIL_BELOW_INTRINSIC",
    "FAIL_INVALID",
    "FAIL_NAMES",
    "FAIL_NO_CONVERGENCE",
    "FAIL_OK",
    "FAIL_UNIDENTIFIABLE",
    "GREEKS",
    "IV_CAP",
    "MINUTES_PER_YEAR",
    "BlackIV",
    "Dividend",
    "RateCurve",
    "black_greeks",
    "black_iv",
    "black_price",
    "dividend_schedule",
    "escrowed_greeks",
    "escrowed_iv",
    "escrowed_price",
    "interpolate_rate",
    "model_forward",
    "pv_dividends",
    "pv_dividends_dr",
    "tau_cal",
    "tau_cal_array",
    "to_continuous",
)
