"""The frozen reference values from `volcalc` (QA-01; implementation plan 3.2).

reference/volcalc.json was written by tools/freeze_pricing_reference.py, run with volcalc's own
interpreter at the commit and library versions recorded in its "meta" block. Inputs and outputs
are stored, so the file is the reference on its own.

The kernels, the solver and the conversions must reproduce volcalc's numbers bit for bit, as
volcalc's own test_bsm_bitexact demands of its port. Measured on 2026-11-22 with numpy 2.5.4 and
scipy 1.18.1 against volcalc's numpy 2.3.5 and scipy 1.17.0: every value identical. If an
upgrade breaks this, look at which rows moved and by how much before relaxing a comparison.

The new spot Greeks are compared with volcalc's bsm_greeks, a separate formula, to a tolerance.
"""

import json
from datetime import date, datetime, timedelta
from pathlib import Path

import numpy as np
import pytest

from s3t.pricing import bsm
from s3t.pricing.black76 import black_greeks, black_iv, black_price
from s3t.pricing.clocks import tau_cal
from s3t.pricing.escrowed import escrowed_greeks
from s3t.pricing.forward import Dividend, model_forward, pv_dividends
from s3t.pricing.rates import interpolate_rate, to_continuous

REF = json.loads((Path(__file__).parent / "reference" / "volcalc.json").read_text())


def _f(x) -> np.ndarray:
    return np.asarray(x, dtype=np.float64)


def _group(name: str) -> tuple[dict[str, np.ndarray], dict]:
    g = REF[name]
    inputs = {k: np.asarray(v, dtype=bool if k == "is_call" else np.float64)
              for k, v in g["inputs"].items()}  # fmt: skip
    return inputs, g["outputs"]


def assert_bitwise(ours, theirs, what: str) -> None:
    a = np.ascontiguousarray(_f(ours)).view(np.uint64)
    b = np.ascontiguousarray(_f(theirs)).view(np.uint64)
    # JSON keeps NaN but not its payload; any NaN matches any NaN.
    same = (a == b) | (np.isnan(_f(ours)) & np.isnan(_f(theirs)))
    assert a.shape == b.shape, what
    if not same.all():
        idx = np.flatnonzero(~same)
        pytest.fail(f"{what}: {idx.size} of {a.size} differ; first {_f(ours)[idx[:3]]} "
                    f"vs {_f(theirs)[idx[:3]]}")  # fmt: skip


def test_meta_names_the_source():
    meta = REF["meta"]
    assert meta["repository"] == "volcalc"
    assert meta["commit"].startswith("f53be83")


def test_bsm_kernels_and_solver():
    i, o = _group("bsm")
    S, K, T, r, q, sig, c = (i[k] for k in ("S", "K", "T", "r", "q", "sigma", "is_call"))
    quote, pu = i["quote"], i["price_uncertainty"]
    assert_bitwise(bsm.bsm_price(S, K, T, r, q, sig, c), o["price"], "price")
    assert_bitwise(bsm.bsm_vega(S, K, T, r, q, sig), o["vega"], "vega")
    assert_bitwise(bsm.compute_iv(quote, S, K, T, r, q, c), o["iv"], "iv")
    assert_bitwise(bsm.compute_iv(quote, S, K, T, r, q, c, pu), o["iv_pu"], "iv with tick")
    assert_bitwise(bsm.bsm_extrinsic(quote, S, K, T, r, q, c), o["extrinsic"], "extrinsic")
    mask = bsm.iv_unidentifiable_mask(quote, S, K, T, r, q, c, pu)
    assert mask.tolist() == o["unidentifiable_pu"]


def test_black76():
    i, o = _group("black")
    F, K, tau, df, sig, c = (i[k] for k in ("F", "K", "tau", "df", "sigma", "is_call"))
    with np.errstate(all="ignore"):
        assert_bitwise(black_price(F, K, tau, df, sig, c), o["price"], "price")
        g = black_greeks(F, K, tau, df, sig, c)
        plain = black_iv(i["quote"], F, K, tau, df, c)
        ticked = black_iv(i["quote"], F, K, tau, df, c, tick=i["tick"])
    for k in ("delta", "gamma", "vega"):
        assert_bitwise(g[k], o[k], k)
    assert_bitwise(plain.iv, o["iv"], "iv")
    assert plain.fail.tolist() == o["fail"]
    assert_bitwise(ticked.iv, o["iv_tick"], "iv with tick")
    assert ticked.fail.tolist() == o["fail_tick"]
    # The set exercises every reason code but no_convergence, which no input reaches.
    assert set(o["fail"]) == {0, 1, 2, 4, 5}


def test_spot_greeks_against_volcalc_bsm_greeks():
    """Without dividends the escrowed spot Greeks are volcalc's BSM Greeks; volcalc gives theta
    per day and vega and rho per 1 %."""
    i, o = _group("bsm_greeks_q0")
    S, K, T, r, sig, c = (i[k] for k in ("S", "K", "T", "r", "sigma", "is_call"))
    g = escrowed_greeks(S, K, T, r, 0.0, 0.0, sig, c)
    scale = {"delta": 1.0, "gamma": 1.0, "theta": 1 / 365, "vega": 0.01, "rho": 0.01}
    for k, s in scale.items():
        np.testing.assert_allclose(g[k] * s, o[k], rtol=1e-10, atol=1e-13, err_msg=k)


def test_forward_and_dividends():
    g = REF["model_forward"]
    i = {k: _f(v) for k, v in g["inputs"].items()}
    F = model_forward(i["S"], i["r"], i["tau"], i["pv_div"])
    assert_bitwise(F, g["outputs"]["F"], "model forward")
    for case in REF["pv_dividends"]:
        d = date.fromisoformat(case["d"])
        sched = [
            Dividend(d + timedelta(days=o), a, False)
            for o, a in zip(case["offsets"], case["amounts"], strict=True)
        ]
        assert_bitwise(pv_dividends(sched, d, case["r"]), case["pv"], "pv")


def test_rates():
    conv = REF["to_continuous"]
    for series, want in conv["r"].items():
        assert_bitwise(to_continuous(series, _f(conv["y"])), want, series)
    for case in REF["interpolate_rate"]:
        got = interpolate_rate(case["rates"], _f(case["days"]))
        assert_bitwise(got, case["r"], "interpolate_rate")


def test_tau_cal():
    for case in REF["tau_cal"]:
        ts, settle = (datetime.fromisoformat(case[k]) for k in ("ts", "settle"))
        assert_bitwise(tau_cal(ts, settle), case["tau"], f"{case['ts']} -> {case['settle']}")
