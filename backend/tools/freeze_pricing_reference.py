"""Freeze reference values of the pricing core from `volcalc` (QA-01; implementation plan, 3.2).

Run it with volcalc's own interpreter, so the numbers are the ones its code and its locked numpy
and scipy compute:

    <volcalc>/.venv/bin/python -I -B tools/freeze_pricing_reference.py <volcalc> <out.json>

It reads volcalc and never writes to it (-B: no bytecode files). It refuses to run when the
source files differ from volcalc's HEAD, and records the commit and the library versions. The
inputs are synthetic and stored with the outputs, so the file is the reference on its own:
numbers computed from formulas, not market data (GEN-07). The committed output is
tests/pricing/reference/volcalc.json, read by tests/pricing/test_reference.py.
"""

import argparse
import importlib
import json
import platform
import subprocess
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np

SOURCES = (
    "src/vrp/iv/bsm.py",
    "src/vrp/iv/black76.py",
    "src/vrp/iv/clocks.py",
    "src/vrp/iv/forward.py",
    "src/vrp/refdata/rates.py",
)
SEED = 20261122


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True, check=True
    ).stdout.strip()


def _list(a) -> list:
    return np.asarray(a).tolist()


def _bsm(bsm, g: np.random.Generator) -> dict:
    """Spot BSM kernels and the solver over the regimes of volcalc's bit-exact test."""
    k, parts = 20, 8
    n = k * parts
    S = g.uniform(5.0, 6000.0, n)
    K = S * np.exp(g.normal(0.0, 0.25, n))
    T = g.uniform(1e-3, 2.5, n)
    r = g.uniform(-0.02, 0.15, n)
    q = g.uniform(0.0, 0.3, n)
    sigma = g.uniform(0.01, 3.0, n)
    c = g.random(n) < 0.5
    # Last block: short-dated far out of the money, where Newton can limit-cycle.
    sl = slice(7 * k, n)
    S[sl] = 5000.0
    K[sl] = 5000.0 * np.exp(g.uniform(0.02, 0.08, k) * np.where(g.random(k) < 0.5, 1, -1))
    T[sl] = g.uniform(bsm._MIN_T, 1.0 / 365.0, k)
    r[sl], q[sl] = 0.045, 0.0
    c[sl] = K[sl] > S[sl]
    T[6 * k : 6 * k + 5] = bsm._MIN_T
    sigma[6 * k + 5 : 6 * k + 10] = bsm._MIN_SIGMA
    sigma[6 * k + 10 : 6 * k + 15] = bsm._MAX_SIGMA

    price = bsm.bsm_price(S, K, T, r, q, sigma, c)
    lo = bsm.bsm_price(S, K, T, r, q, np.full(n, bsm._MIN_SIGMA), c)
    hi = bsm.bsm_price(S, K, T, r, q, np.full(n, bsm._MAX_SIGMA), c)
    quote = price.copy()
    quote[k : 2 * k] = lo[k : 2 * k] + 0.0099  # pinned near the lower clip bound
    quote[2 * k : 3 * k] = hi[2 * k : 3 * k] + 0.0099  # pinned near the upper clip bound
    quote[3 * k : 4 * k] = lo[3 * k : 4 * k] + g.uniform(0.0, 0.03, k)  # the gate's edge
    quote[4 * k : 4 * k + 10] = -1.0  # below the bracket
    quote[4 * k + 10 : 5 * k] = 1e9  # above the bracket
    quote[sl] = price[sl] + g.uniform(0.0, 0.05, k)
    pu = np.where(quote < 3.0, 0.05, 0.10) / 2.0

    inputs = {"S": S, "K": K, "T": T, "r": r, "q": q, "sigma": sigma, "is_call": c}
    inputs |= {"quote": quote, "price_uncertainty": pu}
    outputs = {
        "price": price,
        "vega": bsm.bsm_vega(S, K, T, r, q, sigma),
        "iv": bsm.compute_iv(quote, S, K, T, r, q, c),
        "iv_pu": bsm.compute_iv(quote, S, K, T, r, q, c, price_uncertainty=pu),
        "extrinsic": bsm.bsm_extrinsic(quote, S, K, T, r, q, c),
        "unidentifiable_pu": bsm.iv_unidentifiable_mask(quote, S, K, T, r, q, c, pu),
    }
    return {"inputs": {a: _list(v) for a, v in inputs.items()}} | {
        "outputs": {a: _list(v) for a, v in outputs.items()}
    }


def _bsm_greeks_q0(bsm, g: np.random.Generator) -> dict:
    """volcalc's bsm_greeks without a dividend yield, in its own units (theta per day, vega and
    rho per 1 %): an independent formula for the spot Greeks of escrowed.py."""
    n = 100
    S = g.uniform(20.0, 600.0, n)
    K = S * np.exp(g.normal(0.0, 0.15, n))
    T = g.uniform(2.0 / 365.0, 2.0, n)
    r = g.uniform(0.0, 0.08, n)
    sigma = g.uniform(0.05, 1.2, n)
    c = g.random(n) < 0.5
    out = bsm.bsm_greeks(S, K, T, r, np.zeros(n), sigma, c)
    inputs = {"S": S, "K": K, "T": T, "r": r, "sigma": sigma, "is_call": c}
    return {
        "inputs": {a: _list(v) for a, v in inputs.items()},
        "outputs": {a: _list(v) for a, v in out.items()},
    }


def _black(bsm, black, g: np.random.Generator) -> dict:
    """Black-76 price, the forward delta, gamma and vega, and the inversion with its codes."""
    n = 160
    F = g.uniform(10.0, 5000.0, n)
    K = F * np.exp(g.normal(0.0, 0.2, n))
    tau = np.exp(g.uniform(np.log(bsm._MIN_T * 5), np.log(2.0), n))
    df = np.exp(-g.uniform(0.0, 0.08, n) * tau)
    sigma = g.uniform(0.05, 1.5, n)
    c = g.random(n) < 0.5
    with np.errstate(all="ignore"):
        price = black.black_price(F, K, tau, df, sigma, c)
        lo = black.black_price(F, K, tau, df, np.full(n, bsm._MIN_SIGMA), c)
    quote = price.copy()
    quote[:10] = lo[:10] - 0.5  # below intrinsic
    quote[10:20] = df[10:20] * np.where(c[10:20], F[10:20], K[10:20]) + 1.0  # above the bound
    quote[20:30] = lo[20:30] + g.uniform(0.0, 0.05, 10)  # the gate's edge
    F[30:33] = (np.nan, -1.0, 0.0)  # invalid
    df[33:35] = (0.0, np.inf)
    sigma[35:40] = g.uniform(4.0, 7.0, 5)  # around the IV cap
    with np.errstate(all="ignore"):
        price = black.black_price(F, K, tau, df, sigma, c)
        quote[35:40] = price[35:40]
        greeks = black.black_greeks(F, K, tau, df, sigma, c)
        plain = black.black_iv(quote, F, K, tau, df, c)
        tick = np.where(quote < 3.0, 0.01, 0.05)
        ticked = black.black_iv(quote, F, K, tau, df, c, tick=tick)
    inputs = {"F": F, "K": K, "tau": tau, "df": df, "sigma": sigma, "is_call": c}
    inputs |= {"quote": quote, "tick": tick}
    outputs = {"price": price, **greeks, "iv": plain.iv, "fail": plain.fail}
    outputs |= {"iv_tick": ticked.iv, "fail_tick": ticked.fail}
    return {
        "inputs": {a: _list(v) for a, v in inputs.items()},
        "outputs": {a: _list(v) for a, v in outputs.items()},
    }


def _forward(forward, pl, g: np.random.Generator) -> dict:
    n = 20
    S = g.uniform(20.0, 600.0, n)
    r = g.uniform(0.0, 0.08, n)
    tau = g.uniform(1.0 / 365.0, 2.0, n)
    pv = g.uniform(0.0, 5.0, n)
    cases = []
    d = date(2024, 1, 5)
    for offsets, amounts, rate in (
        ([70, 168], [1.595, 1.759], 0.05),
        ([3], [0.5], 0.0),
        ([10, 101, 192, 283, 374], [0.24] * 5, 0.043),
        ([], [], 0.05),
    ):
        sched = pl.DataFrame(
            {"ex_date": [d + timedelta(days=o) for o in offsets], "cash_amount": amounts},
            schema={"ex_date": pl.Date, "cash_amount": pl.Float64},
        )
        pv_ = forward.pv_dividends(sched, d, rate)
        cases.append({"d": d.isoformat(), "offsets": offsets, "amounts": amounts, "r": rate})
        cases[-1]["pv"] = pv_
    return {
        "model_forward": {
            "inputs": {"S": _list(S), "r": _list(r), "tau": _list(tau), "pv_div": _list(pv)},
            "outputs": {"F": _list(forward.model_forward(S, r, tau, pv))},
        },
        "pv_dividends": cases,
    }


def _rates(rates, pl) -> dict:
    ys = [0.0, 0.001, 0.0137, 0.0525, 0.1]
    conv = {
        s: [pl.select(rates.to_continuous(s, pl.lit(y))).item() for y in ys] for s in rates.SERIES
    }
    curves = [
        {"SOFR": 0.05, "DGS1MO": 0.04, "DGS3MO": None, "DGS6MO": 0.06},
        {"SOFR": 0.0431, "DGS1MO": 0.0428, "DGS3MO": 0.0419, "DGS6MO": 0.0402, "DGS1": 0.0381},
        {"DGS1": 0.03},
    ]
    days = [0.2, 1.0, 15.5, 30.0, 60.0, 91.0, 106.0, 182.0, 300.0, 365.0, 800.0]
    interp = [
        {"rates": c, "days": days, "r": _list(rates.interpolate_rate(c, days))} for c in curves
    ]
    return {"to_continuous": {"y": ys, "r": conv}, "interpolate_rate": interp}


def _clocks(clocks) -> list[dict]:
    et = ZoneInfo("America/New_York")
    pairs = [
        ("2024-01-03 09:30:00", "2024-01-03 16:00:00"),
        ("2024-07-05 09:30:00", "2024-07-05 16:00:00"),
        ("2024-01-03 09:35:00", "2024-01-04 16:00:00"),
        ("2024-11-29 09:30:00", "2024-11-29 13:00:00"),
        ("2024-01-03 15:59:30", "2024-01-03 16:00:00"),
        ("2024-01-03 16:05:00", "2024-01-03 16:00:00"),
        ("2024-03-08 15:00:00", "2024-03-11 16:00:00"),
        ("2024-11-01 10:00:00", "2024-11-04 16:00:00"),
        ("2024-01-02 09:30:00", "2026-12-18 16:15:00"),
    ]
    out = []
    for a, b in pairs:
        ta = datetime.fromisoformat(a).replace(tzinfo=et)
        tb = datetime.fromisoformat(b).replace(tzinfo=et)
        out.append({"ts": ta.isoformat(), "settle": tb.isoformat()})
        out[-1]["tau"] = clocks.tau_cal(ta, tb, 525_600)
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    ap.add_argument("volcalc", type=Path, help="the volcalc repository")
    ap.add_argument("out", type=Path, help="the JSON file to write")
    args = ap.parse_args()
    repo = args.volcalc.resolve()
    if _git(repo, "status", "--porcelain", "--", *SOURCES):
        print("volcalc's pricing sources differ from its HEAD; commit or stash first")
        return 1
    sys.path.insert(0, str(repo / "src"))
    bsm = importlib.import_module("vrp.iv.bsm")
    black = importlib.import_module("vrp.iv.black76")
    clocks = importlib.import_module("vrp.iv.clocks")
    forward = importlib.import_module("vrp.iv.forward")
    rates = importlib.import_module("vrp.refdata.rates")
    pl = importlib.import_module("polars")
    scipy = importlib.import_module("scipy")

    g = np.random.default_rng(SEED)
    ref = {
        "meta": {
            "repository": "volcalc",
            "commit": _git(repo, "rev-parse", "HEAD"),
            "sources": list(SOURCES),
            "python": platform.python_version(),
            "numpy": np.__version__,
            "scipy": scipy.__version__,
            "polars": pl.__version__,
            "seed": SEED,
        },
        "bsm": _bsm(bsm, g),
        "bsm_greeks_q0": _bsm_greeks_q0(bsm, g),
        "black": _black(bsm, black, g),
        **_forward(forward, pl, g),
        **_rates(rates, pl),
        "tau_cal": _clocks(clocks),
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(ref, separators=(",", ":")) + "\n")
    print(f"wrote {args.out} from volcalc {ref['meta']['commit'][:7]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
