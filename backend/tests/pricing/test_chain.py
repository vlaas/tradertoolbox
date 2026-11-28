"""IV and spot Greeks of chain contracts in one call (s3t.pricing.chain; MKT-04, WP-1.2)."""

import numpy as np
import pytest

from s3t.pricing.black76 import FAIL_INVALID, FAIL_OK, FAIL_UNIDENTIFIABLE, GREEKS, black_price
from s3t.pricing.carry import implied_pv, proportional_greeks
from s3t.pricing.chain import contract_values
from s3t.pricing.escrowed import escrowed_greeks, escrowed_price

# Two underlyings in one call: an index (rows 0-3) and an ETF that pays cash (rows 4-7).
S = np.array([6000.0] * 4 + [600.0] * 4)
K = np.array([5800.0, 6000.0, 6000.0, 6300.0, 580.0, 600.0, 600.0, 630.0])
CALL = np.array([False, False, True, True, False, False, True, True])
TAU = np.array([0.05] * 4 + [0.2] * 4)
R = 0.041
DF = np.exp(-R * TAU)
INDEX = np.array([True] * 4 + [False] * 4)
SIGMA = np.array([0.21, 0.17, 0.165, 0.14, 0.24, 0.2, 0.19, 0.17])
PV = 3.1  # the ETF's dividends before its expiration
F = np.where(INDEX, S * np.exp((R - 0.012) * TAU), (S - PV) / DF)


def prices() -> np.ndarray:
    return black_price(F, K, TAU, DF, SIGMA, CALL)


def test_the_iv_comes_back() -> None:
    got = contract_values(prices(), F, K, TAU, DF, S, CALL, INDEX)
    assert (got.fail == FAIL_OK).all()
    assert got.iv == pytest.approx(SIGMA, abs=1e-9)
    assert tuple(got.greeks) == GREEKS


def test_the_greeks_are_those_of_the_pricing_core() -> None:
    got = contract_values(prices(), F, K, TAU, DF, S, CALL, INDEX)
    i = slice(0, 4)
    index = proportional_greeks(S[i], F[i], K[i], TAU[i], DF[i], SIGMA[i], CALL[i])
    c = slice(4, 8)
    cash = escrowed_greeks(
        S[c], K[c], TAU[c], R, implied_pv(S[c], F[c], DF[c]), 0.0, SIGMA[c], CALL[c]
    )
    for name in GREEKS:
        assert got.greeks[name][i] == pytest.approx(index[name], rel=1e-6, abs=1e-9), name
        assert got.greeks[name][c] == pytest.approx(cash[name], rel=1e-6, abs=1e-9), name


def test_an_etfs_price_on_its_implied_dividends_is_the_forwards() -> None:
    """S - F DF is the PV that makes the escrowed forward equal F."""
    c = slice(4, 8)
    pv = implied_pv(S[c], F[c], DF[c])
    assert pv == pytest.approx(PV)
    assert escrowed_price(S[c], K[c], TAU[c], R, pv, SIGMA[c], CALL[c]) == pytest.approx(
        prices()[c], rel=1e-12
    )


def test_delta_is_against_the_spot() -> None:
    got = contract_values(prices(), F, K, TAU, DF, S, CALL, INDEX)
    h = 0.01
    for i in range(8):
        up, down = S[i] + h, S[i] - h
        # The index keeps its yield (F moves in proportion), the ETF its cash (F moves by 1/DF).
        f_up = F[i] * up / S[i] if INDEX[i] else (up - PV) / DF[i]
        f_down = F[i] * down / S[i] if INDEX[i] else (down - PV) / DF[i]
        moved = black_price(f_up, K[i], TAU[i], DF[i], SIGMA[i], CALL[i]) - black_price(
            f_down, K[i], TAU[i], DF[i], SIGMA[i], CALL[i]
        )
        assert got.greeks["delta"][i] == pytest.approx(moved / (2 * h), rel=1e-5), i


def test_no_greeks_without_an_iv() -> None:
    price = prices()
    price[1] = np.nan  # no quote
    price[5] = 0.5 * (S[5] - K[5])  # a put priced like nothing: below any model price
    bad = F.copy()
    bad[2] = np.nan  # no forward yet
    got = contract_values(price, bad, K, TAU, DF, S, CALL, INDEX)
    for i in (1, 2):
        assert got.fail[i] == FAIL_INVALID
    assert got.fail[5] != FAIL_OK
    for i in (1, 2, 5):
        assert np.isnan(got.iv[i])
        assert all(np.isnan(got.greeks[name][i]) for name in GREEKS)
    keep = [0, 3, 4, 6, 7]
    assert np.isfinite(got.greeks["delta"][keep]).all()


def test_without_a_spot_only_the_greeks_that_need_it_go() -> None:
    spot = S.copy()
    spot[:4] = np.nan  # an index without a trade
    got = contract_values(prices(), F, K, TAU, DF, spot, CALL, INDEX)
    assert got.iv[:4] == pytest.approx(SIGMA[:4], abs=1e-9)
    assert np.isnan(got.greeks["delta"][:4]).all()
    assert np.isfinite(got.greeks["vega"][:4]).all()


def test_the_tick_widens_the_gate() -> None:
    """A far put worth a few cents of extrinsic value: identifiable at a one-cent tick, not at a
    tick of a dime (the gate is half the tick)."""
    k, f, tau, df = np.array([5000.0]), np.array([6000.0]), np.array([0.02]), np.array([0.999])
    sigma = np.array([0.4])
    price = black_price(f, k, tau, df, sigma, np.array([False]))
    assert 0.03 < price[0] < 0.1
    args = (price, f, k, tau, df, np.array([6000.0]), np.array([False]), np.array([True]))
    assert contract_values(*args, tick=np.array([0.01])).fail[0] == FAIL_OK
    assert contract_values(*args, tick=np.array([0.2])).fail[0] == FAIL_UNIDENTIFIABLE


def test_the_rate_derivative_of_a_schedule_reaches_rho() -> None:
    got = contract_values(prices(), F, K, TAU, DF, S, CALL, INDEX)
    dr = np.where(INDEX, 0.0, -0.4)
    with_dr = contract_values(prices(), F, K, TAU, DF, S, CALL, INDEX, dpv_dr=dr)
    assert with_dr.greeks["rho"][:4] == pytest.approx(got.greeks["rho"][:4])
    assert not np.allclose(with_dr.greeks["rho"][4:], got.greeks["rho"][4:])
