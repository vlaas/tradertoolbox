"""The expected move of an expiration (s3t.pricing.moves).

The first five tests are `backtester` tests/unit/test_expected_move.py at commit 0a296d2, the
`em_platform` part, on arrays instead of a polars table: the same chain, the same hand-computed
expectations, the same tolerance (PRICE_TOL, a cent). The others are new.
"""

import math

import numpy as np
import pytest

from s3t.pricing.moves import METHOD, expected_move, mids

PRICE_TOL = 0.01

# (strike, call bid, call ask, put bid, put ask): the source's symmetric chain around 5000.
CHAIN = np.array(
    [
        (4980.0, 30.0, 30.2, 5.0, 5.2),
        (4990.0, 22.0, 22.2, 7.0, 7.2),
        (5000.0, 15.0, 15.2, 10.0, 10.2),  # at the money
        (5010.0, 9.0, 9.2, 14.0, 14.2),
        (5020.0, 5.0, 5.2, 21.0, 21.2),
    ]
)


def em(chain: np.ndarray, spot: float = 5000.0) -> float:
    return expected_move(chain[:, 0], chain[:, 1], chain[:, 2], chain[:, 3], chain[:, 4], spot)


def test_em_platform_hand_recomputed() -> None:
    # ATM straddle 15.1 + 10.1; 1st OTM strangle 7.1 + 9.1; 2nd OTM 5.1 + 5.1.
    expected = 0.6 * 25.2 + 0.3 * 16.2 + 0.1 * 10.2
    assert em(CHAIN) == pytest.approx(expected, abs=PRICE_TOL)


def test_em_platform_renormalizes_when_2nd_otm_missing() -> None:
    chain = CHAIN[CHAIN[:, 0] != 5020.0]
    expected = (0.6 * 25.2 + 0.3 * 16.2) / 0.9
    assert em(chain) == pytest.approx(expected, abs=PRICE_TOL)


def test_em_platform_returns_nan_when_atm_missing() -> None:
    """The source drops the at-the-money call's row; here its quote is missing."""
    chain = CHAIN.copy()
    chain[2, 1:3] = np.nan
    expected = (0.3 * 16.2 + 0.1 * 10.2) / 0.4
    assert em(chain) == pytest.approx(expected, abs=PRICE_TOL)


def test_em_platform_empty_chain() -> None:
    assert math.isnan(em(np.empty((0, 5))))


def test_em_platform_ignores_bad_quotes() -> None:
    chain = CHAIN.copy()
    chain[2, 1:3] = 0.0  # the at-the-money call at a bid and ask of zero
    expected = (0.3 * 16.2 + 0.1 * 10.2) / 0.4
    assert em(chain) == pytest.approx(expected, abs=PRICE_TOL)


# ---------------------------------------------------------------------- new in the port


def test_the_money_is_the_strike_nearest_the_spot() -> None:
    """Between two strikes the lower one, as `min` in the source picks the first."""
    assert em(CHAIN, 5004.9) == em(CHAIN, 5000.0)
    assert em(CHAIN, 5005.0) == em(CHAIN, 5000.0)
    # At 5010 the 2nd strangle would need 5030, which is not listed.
    assert em(CHAIN, 5010.0) == pytest.approx((0.6 * (9.1 + 14.1) + 0.3 * (10.1 + 5.1)) / 0.9)


def test_nothing_without_a_spot_or_without_legs() -> None:
    assert math.isnan(em(CHAIN, math.nan))
    assert math.isnan(em(CHAIN, 0.0))
    empty = CHAIN.copy()
    empty[:, 1:] = np.nan
    assert math.isnan(em(empty))


def test_a_mid_needs_both_sides() -> None:
    got = mids([1.0, 0.0, np.nan, 2.0, 1.0], [1.2, 0.1, 1.0, 1.9, np.nan])
    assert got[0] == pytest.approx(1.1)
    assert np.isnan(got[1:]).all()  # no bid; missing bid; crossed; missing ask


def test_the_method_is_named() -> None:
    assert METHOD == "straddle 0.6/0.3/0.1"
