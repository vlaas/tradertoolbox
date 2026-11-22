"""Trader units at the service edge (PRC-04): s3t/analysis/units.py."""

import numpy as np
import pytest

from s3t.analysis.units import SCALE, position_units
from s3t.pricing.black76 import GREEKS
from s3t.pricing.escrowed import escrowed_greeks, escrowed_price


def test_every_greek_has_a_unit():
    assert set(SCALE) == {"value", *GREEKS}


def test_long_call_spread_of_spy_style_contracts():
    """Raw per-share values of S=K=100, T=1, r=5 %, sigma=20 % (the textbook call) for two
    long contracts of 100 shares."""
    S, K, T, r, sig, c = 100.0, 100.0, 1.0, 0.05, 0.2, True
    raw = escrowed_greeks(S, K, T, r, 0.0, 0.0, sig, c) | {
        "value": escrowed_price(S, K, T, r, 0.0, sig, c)
    }
    pos = position_units(raw, 100, 2)
    assert float(pos["value"]) == pytest.approx(2 * 100 * 10.4506, abs=0.02)  # dollars
    assert float(pos["delta"]) == pytest.approx(2 * 100 * 0.63683, abs=0.01)  # shares
    assert float(pos["theta"]) == pytest.approx(200 * float(raw["theta"]) / 365)  # $ per day
    assert float(pos["vega"]) == pytest.approx(200 * float(raw["vega"]) / 100)  # $ per vol pt
    assert float(pos["theta"]) == pytest.approx(-3.5145, abs=1e-3)
    assert float(pos["vega"]) == pytest.approx(75.048, abs=1e-3)
    assert float(pos["volga"]) == pytest.approx(200 * float(raw["volga"]) * 1e-4)
    assert float(pos["charm"]) == pytest.approx(200 * float(raw["charm"]) / 365)


def test_short_positions_flip_the_sign_and_arrays_broadcast():
    raw = {"theta": np.array([-36.5, -73.0]), "vega": np.array([20.0, 40.0])}
    pos = position_units(raw, np.array([100, 10]), -3)
    assert pos["theta"].tolist() == pytest.approx([30.0, 6.0])
    assert pos["vega"].tolist() == pytest.approx([-60.0, -12.0])


def test_unknown_keys_are_refused():
    with pytest.raises(KeyError):
        position_units({"speed": np.array([1.0])}, 100, 1)
