"""The volatility surface of one underlying at one moment: a smile per expiration, its checks,
and the risk-neutral distribution it implies (PRC-05 to PRC-07).

Source: `volcalc` at commit f53be83: src/vrp/surface/build.py, `candidates` (the fit weights)
and `fit_snapshot` (the order of the fit); src/vrp/surface/fixed_delta.py, `q_itm` (here
`below`). The rules are unchanged:

- Candidates are the out-of-the-money quotes with an IV; those with |delta| >= 0.02 are fitted.
  Weights 1 / (noise^2 + s^2), s the half spread in IV, (ask - bid) / (2 vega).
- A slice with enough strikes (svi.eligible) gets a raw SVI fit, warm-started from the fit
  before: status "ok", or "fail" when no arbitrage-free solution is found. Else it is "sparse".
- SSVI across the "ok" slices: theta = SVI w(0) made monotone, one global shape (rho, eta,
  gamma) on their pooled points. A sparse or failed slice takes theta from its own points at
  that shape, held between the neighbouring fitted slices, and the SSVI smile is its surface.

Changed in the port (2026-11-27, WP-1.1):
- One call for one moment, on arrays of quotes (`SliceInput`), instead of a day of polars
  frames. The forward of each slice is fitted here too (parity.py), so the whole of a fit runs
  in one place: in the application, a worker process (PRF-05).
- A slice has one theta. The source's second level for the at-the-money IV of a fallback slice
  (`theta_atm`, its plan F2) is not ported; the at-the-money IV is the smile's.
- Quotes without a vega above zero are no candidates (the source gave them the weight 0).
- Not ported: the two passes with the surface trade sign, the tables, model-free IV, the
  moments and the strikes at fixed deltas.

New here:
- The checks of PRC-06 on every final smile: svi.butterfly, and a calendar flag where a slice's
  total variance falls below that of the slice before it.
- `Marginal`, the distribution of the underlying at one date (PRC-07), in closed form from the
  smile and its first two derivatives (Gatheral & Jacquier 2014, eq. 2.2; Breeden-Litzenberger):

      density of ln(S_T / F) = g(k) n(d2) / sqrt(w),  d2 = -k / sqrt(w) - sqrt(w) / 2
      P(S_T < K)             = N(-d2) + n(d2) w'(k) / (2 sqrt(w))

- Interpolation in time (`Surface.marginal_at`): total variance linear in time at fixed forward
  moneyness between the two neighbouring slices; before the first one, that slice's IV at the
  same moneyness. A linear mix of two smiles keeps the closed form. Decided 2026-11-27.
- `Surface.basis_at`: the ratio of the fitted forward to the model forward, between and before
  the slices, for the forward at a date that is no expiration.

Everything here is a plain value that can be pickled: the result crosses a process boundary.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from typing import NamedTuple

import numpy as np

from s3t.pricing import parity, ssvi, svi
from s3t.pricing.black76 import black_greeks, black_iv
from s3t.pricing.bsm import _N, _n

OK, SPARSE, FAIL, NO_FIT = "ok", "sparse", "fail", "none"  # status of a slice
SVI, SSVI = "svi", "ssvi"  # the model of its smile
FUTURE = "future"  # a forward that was given, beside parity.PARITY, MODEL and NONE
CAL_TOL = 1e-3  # a later slice may lie this share below the earlier one before the flag
CAL_Z = 6.0  # the calendar check looks this many at-the-money deviations each way
CAL_GRID = 41


@dataclass(frozen=True)
class Settings:
    """The fit's settings: `volcalc`'s [surface] and [iv] values."""

    min_strikes: int = 5  # distinct strikes for an own SVI fit ...
    min_wing: int = 2  # ... with this many beyond |delta| 0.25 on each side
    outlier_mad: float = 4.0  # the consistency filter drops |residual| > max(this many MADs,
    outlier_volpts: float = 3.0  # this many vol points)
    noise_volpts: float = 0.1  # sigma_noise of the fit weights, vol points
    min_abs_delta: float = 0.02  # quotes below it are not fitted
    band: float = parity.FWD_BAND  # the pair band of the parity forward


@dataclass(frozen=True, eq=False)
class SliceInput:
    """The quotes of one expiration. The arrays are parallel, strikes ascending, NaN where a
    side has no quote."""

    key: str  # the caller's name of the expiration; the result carries it
    tau: float  # years to the settlement instant
    df: float  # discount factor to it
    strike: np.ndarray
    call_bid: np.ndarray
    call_ask: np.ndarray
    put_bid: np.ndarray
    put_ask: np.ndarray
    spot: float = np.nan  # the underlying's price; NaN when it has none
    model: float = np.nan  # the model forward (forward.model_forward); NaN without a spot
    pv_div: float = 0.0  # PV of the schedule's dividends before the settlement
    exdiv_near: bool = False  # one of them goes ex within parity.EXDIV_DAYS sessions
    forward: float = np.nan  # a given forward (the future of a futures option): no parity fit


class Warm(NamedTuple):
    """What a fit hands to the next one of the same underlying."""

    x: Mapping[str, tuple[float, ...]]  # annualized SVI parameters by key
    shape: tuple[float, ...] | None  # the SSVI shape


def below(k, w, wp) -> np.ndarray:
    """P(S_T < K) at k = ln(K / F) on a smile with total variance w and slope w' there."""
    sw = np.sqrt(np.maximum(w, svi.W_MIN))
    d2 = -k / sw - sw / 2
    return np.clip(_N(-d2) + _n(d2) * wp / (2 * sw), 0.0, 1.0)


@dataclass(frozen=True, eq=False)
class Slice:
    """One expiration of a fitted surface."""

    key: str
    tau: float
    df: float
    spot: float
    forward: float  # NaN when there is none
    forward_source: str  # parity.PARITY | parity.MODEL | FUTURE | parity.NONE
    forward_model: float  # the model forward that was given
    parity: parity.ParityForward | None  # the parity fit; None for a given forward
    status: str  # OK | SPARSE | FAIL | NO_FIT
    model: str | None  # SVI | SSVI; None when the slice has no smile
    x: tuple[float, ...] | None  # annualized SVI parameters (a', b', rho, m, sigma) of an own fit
    theta: float | None  # at-the-money total variance on the SSVI curve
    shape: tuple[float, ...] | None  # the surface's SSVI shape (rho, eta, gamma)
    atm_iv: float  # the smile's at k = 0; without a smile the quoted IV nearest the money
    rmse: float  # RMS of quoted IV less smile over the fitted quotes; NaN without a smile
    quotes: int  # candidates: out-of-the-money quotes with an IV
    fitted: int  # of them, in the final fit
    outliers: int  # dropped by the consistency filter
    k_min: float  # the range of log-moneyness the fit saw; NaN without a smile. A value read
    k_max: float  # outside it is an extrapolation
    butterfly: svi.Butterfly | None
    calendar_ok: bool | None  # against the slice before it; None for the first, or no smile

    @property
    def svi_params(self) -> tuple[float, ...] | None:
        """Total-variance parameters (a, b, rho, m, sigma) of an own fit."""
        return None if self.x is None else tuple(svi.total(np.array(self.x), self.tau).tolist())

    def w(self, k) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """Total variance and its first two derivatives at log-moneyness k; NaN without a
        smile."""
        k = np.asarray(k, dtype=np.float64)
        if self.model == SVI and self.x is not None:
            return svi.w_derivs(svi.total(np.array(self.x), self.tau), k)
        if self.model == SSVI and self.theta is not None and self.shape is not None:
            return ssvi.w_derivs(self.theta, np.array(self.shape), k)
        nan = np.full(k.shape, np.nan)
        return nan, nan, nan

    def marginal(self) -> "Marginal | None":
        """The distribution at this expiration; None without a smile."""
        if self.model is None:
            return None
        return Marginal(self.tau, self.forward, self.model, False, ((1.0, self),))


@dataclass(frozen=True, eq=False)
class Marginal:
    """The risk-neutral distribution of the underlying at one date (PRC-07): the smile of an
    expiration, or a mix of two neighbouring ones in total variance."""

    tau: float
    forward: float
    model: str  # SVI, SSVI, or both names when the two slices differ (GEN-06)
    interpolated: bool  # the date is no expiration
    parts: tuple[tuple[float, Slice], ...]  # weights and slices: w(k) = sum of weight w_i(k)

    def w(self, k) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        k = np.asarray(k, dtype=np.float64)
        total = [np.zeros(k.shape), np.zeros(k.shape), np.zeros(k.shape)]
        for weight, part in self.parts:
            for acc, term in zip(total, part.w(k), strict=True):
                acc += weight * term
        return total[0], total[1], total[2]

    def _k(self, strike) -> np.ndarray:
        return np.log(np.asarray(strike, dtype=np.float64) / self.forward)

    def iv(self, strike) -> np.ndarray:
        """Implied volatility at a strike."""
        return np.sqrt(np.maximum(self.w(self._k(strike))[0], 0.0) / self.tau)

    def density(self, strike) -> np.ndarray:
        """Probability density of the underlying's price at `strike`, per unit of price."""
        k = self._k(strike)
        return svi.density(k, *self.w(k)) / np.asarray(strike, dtype=np.float64)

    def prob_below(self, strike) -> np.ndarray:
        """P(S_T < strike)."""
        k = self._k(strike)
        w, wp, _ = self.w(k)
        return below(k, w, wp)

    def prob_above(self, strike) -> np.ndarray:
        """P(S_T > strike)."""
        return 1.0 - self.prob_below(strike)

    def butterfly(self) -> svi.Butterfly:
        """The butterfly check of this date's smile, over eight at-the-money deviations."""
        s = 8 * float(np.sqrt(max(float(self.w(0.0)[0]), svi.W_MIN)))
        return svi.butterfly(self.w, np.linspace(-s, s, svi.N_GRID))


@dataclass(frozen=True, eq=False)
class Surface:
    """The fit of one underlying at one moment. Slices are in the order of time to settlement."""

    slices: tuple[Slice, ...]
    shape: tuple[float, ...] | None  # the SSVI shape (rho, eta, gamma); None without an own fit

    def slice(self, key: str) -> Slice | None:
        return next((s for s in self.slices if s.key == key), None)

    def warm(self) -> Warm:
        return Warm({s.key: s.x for s in self.slices if s.x is not None}, self.shape)

    def basis_at(self, tau: float) -> float:
        """Fitted forward over model forward at a time to a date: 1 at tau 0, log-linear between
        the slices that have both, held after the last. The forward at a date that is no
        expiration is the model forward to it times this."""
        have = [
            (s.tau, float(np.log(s.forward / s.forward_model)))
            for s in self.slices
            if s.forward_source in (parity.PARITY, FUTURE)
            and np.isfinite(s.forward_model)
            and s.forward_model > 0
            and s.forward > 0
        ]
        if not have:
            return 1.0
        taus, logs = zip(*have, strict=True)
        return float(np.exp(np.interp(tau, (0.0, *taus), (0.0, *logs))))

    def marginal_at(self, tau: float, forward: float) -> Marginal | None:
        """The distribution at a date `tau` years ahead, where the forward is `forward`: total
        variance linear in time at fixed forward moneyness between the slices around it; before
        the first, that slice's IV at the same moneyness. None after the last slice, or
        without a smile."""
        smiles = [s for s in self.slices if s.model is not None]
        if not smiles or tau <= 0 or tau > smiles[-1].tau:
            return None
        first = smiles[0]
        if tau <= first.tau:
            return Marginal(tau, forward, first.model or "", True, ((tau / first.tau, first),))
        hi = next(i for i, s in enumerate(smiles) if s.tau >= tau)
        lo = max(i for i in range(hi) if smiles[i].tau < tau)
        a, b = smiles[lo], smiles[hi]
        share = (tau - a.tau) / (b.tau - a.tau)
        names = sorted({a.model or "", b.model or ""})
        return Marginal(tau, forward, " and ".join(names), True, ((1 - share, a), (share, b)))


class _Work:
    """A slice while it is fitted."""

    def __init__(self, inp: SliceInput, forward: float, source: str, fit: object) -> None:
        self.inp, self.forward, self.source, self.parity = inp, forward, source, fit
        self.tau = inp.tau
        empty = np.zeros(0)
        self.k, self.iv, self.wt, self.absd = empty, empty, empty, empty
        self.fit_ok = np.zeros(0, dtype=bool)
        self.used = np.zeros(0, dtype=bool)
        self.outlier = np.zeros(0, dtype=bool)
        self.e = empty
        self.status = NO_FIT
        self.x: np.ndarray | None = None
        self.theta: float | None = None
        self.shape: np.ndarray | None = None
        self.rmse = np.nan

    @property
    def model(self) -> str | None:
        if self.status == OK and self.x is not None:
            return SVI
        return SSVI if self.theta is not None and self.shape is not None else None

    def theta_svi(self) -> float:
        return float(self.tau * svi.w_derivs(self.x, 0.0)[0])


def _prepare(inp: SliceInput, st: Settings) -> _Work:
    """The forward of a slice and its candidates: out-of-the-money quotes with an IV."""
    call_mid = parity.mid(inp.call_bid, inp.call_ask)
    put_mid = parity.mid(inp.put_bid, inp.put_ask)
    if np.isfinite(inp.forward) and inp.forward > 0:
        work = _Work(inp, float(inp.forward), FUTURE, None)
    else:
        fit = parity.parity_forward(
            inp.strike,
            call_mid,
            put_mid,
            inp.df,
            spot=inp.spot,
            model=inp.model,
            pv_div=inp.pv_div,
            exdiv_near=inp.exdiv_near,
            band=st.band,
        )
        work = _Work(inp, fit.forward, fit.source, fit)
    forward = work.forward
    if not (np.isfinite(forward) and forward > 0 and inp.tau > 0 and inp.df > 0):
        return work
    strike = np.asarray(inp.strike, dtype=np.float64)
    call = strike >= forward
    price = np.where(call, call_mid, put_mid)
    spread = np.where(call, inp.call_ask, inp.put_ask) - np.where(call, inp.call_bid, inp.put_bid)
    ok = np.isfinite(price) & (strike > 0)
    strike, call, price, spread = strike[ok], call[ok], price[ok], spread[ok]
    if strike.size == 0:
        return work
    f, tau, df = (np.full(strike.shape, v) for v in (forward, inp.tau, inp.df))
    iv = black_iv(price, f, strike, tau, df, call).iv
    with np.errstate(invalid="ignore", divide="ignore"):
        greeks = black_greeks(f, strike, tau, df, iv, call)
        vega = greeks["vega"]
        good = np.isfinite(iv) & (vega > 0)
        s_iv = spread / (2 * vega)
        wt = 1 / ((st.noise_volpts / 100) ** 2 + s_iv**2)
    work.k = np.log(strike[good] / forward)
    work.iv, work.wt = iv[good], wt[good]
    work.absd = np.abs(greeks["delta"][good])
    work.fit_ok = work.absd >= st.min_abs_delta
    n = work.k.size
    work.used, work.outlier = np.zeros(n, dtype=bool), np.zeros(n, dtype=bool)
    work.e = np.full(n, np.nan)
    return work


def _fit_svi(f: _Work, warm: Warm, st: Settings) -> None:
    m = f.fit_ok
    if not np.isfinite(f.forward) or f.k.size == 0:
        return
    f.status = SPARSE
    if not svi.eligible(f.k[m], f.absd[m], st.min_strikes, st.min_wing):
        return
    x0 = warm.x.get(f.inp.key)
    res = svi.fit_slice(
        f.k[m],
        f.iv[m],
        f.wt[m],
        f.absd[m],
        f.tau,
        None if x0 is None else np.array(x0, dtype=np.float64),
        st.min_strikes,
        st.min_wing,
        st.outlier_mad,
        st.outlier_volpts,
    )
    f.status, f.x, f.rmse = res.status, res.x, res.rmse
    f.used[m], f.outlier[m], f.e[m] = res.used, res.outlier, res.e


def _fit_ssvi(works: list[_Work], warm: Warm) -> np.ndarray | None:
    """The global shape on the pooled points of the slices with an own fit, and a theta for
    every slice that has points."""
    ok = [f for f in works if f.status == OK]
    if not ok:
        return None
    theta_ok = ssvi.monotone(np.array([f.theta_svi() for f in ok], dtype=np.float64))
    for f, th in zip(ok, theta_ok, strict=True):
        f.theta = float(th)
    pooled = [
        (f.k[f.used], f.iv[f.used], f.wt[f.used], np.full(int(f.used.sum()), f.theta), f.tau)
        for f in ok
    ]
    k, iv, wt, th, tau = (
        np.concatenate([np.broadcast_to(p[i], p[0].shape) for p in pooled]) for i in range(5)
    )
    shape0 = None if warm.shape is None else np.array(warm.shape, dtype=np.float64)
    shape = ssvi.fit_global(k, iv, wt, th, tau, shape0)
    if shape is None:
        return None
    taus_ok = np.array([f.tau for f in ok])
    for f in works:
        f.shape = shape
        m = f.fit_ok
        if f.status == OK or not m.any():
            continue
        th = ssvi.fit_theta(f.k[m], f.iv[m], f.wt[m], f.tau, shape)
        lo, hi = theta_ok[taus_ok < f.tau], theta_ok[taus_ok > f.tau]
        f.theta = float(np.clip(th, lo[-1] if lo.size else 0.0, hi[0] if hi.size else np.inf))
    # Consecutive slices without an own fit between the same neighbours can still cross: one
    # more cumulative max (it leaves the fitted slices' theta untouched).
    have = [f for f in works if f.theta is not None]
    for f, th in zip(have, ssvi.monotone(np.array([f.theta for f in have])), strict=True):
        f.theta = float(th)
    for f in have:
        if f.status != OK:
            m = f.fit_ok
            f.e[m] = f.iv[m] - np.sqrt(ssvi.w_derivs(f.theta, shape, f.k[m])[0] / f.tau)
            f.used[m] = True
            f.rmse = float(np.sqrt(np.mean(f.e[m] ** 2)))
    return shape


def _tuple(a: np.ndarray | None) -> tuple[float, ...] | None:
    return None if a is None else tuple(float(v) for v in a)


def _slice(f: _Work) -> Slice:
    inp, model = f.inp, f.model
    out = Slice(
        key=inp.key,
        tau=inp.tau,
        df=inp.df,
        spot=inp.spot,
        forward=f.forward,
        forward_source=f.source,
        forward_model=inp.model,
        parity=f.parity,  # type: ignore[arg-type]
        status=f.status,
        model=model,
        x=_tuple(f.x) if f.status == OK else None,
        theta=f.theta,
        shape=_tuple(f.shape),
        atm_iv=np.nan,
        rmse=float(f.rmse) if model is not None else np.nan,
        quotes=int(f.k.size),
        fitted=int(f.used.sum()) if model is not None else 0,
        outliers=int(f.outlier.sum()),
        k_min=float(f.k[f.used].min()) if model is not None and f.used.any() else np.nan,
        k_max=float(f.k[f.used].max()) if model is not None and f.used.any() else np.nan,
        butterfly=None,
        calendar_ok=None,
    )
    if model is None:
        atm = float(f.iv[np.argmin(np.abs(f.k))]) if f.k.size else np.nan
        return replace(out, atm_iv=atm)
    w0 = float(out.w(0.0)[0])
    kg = svi.grid(f.k[f.used] if f.used.any() else np.zeros(1), w0)
    atm = float(np.sqrt(max(w0, 0.0) / f.tau))
    return replace(out, atm_iv=atm, butterfly=svi.butterfly(out.w, kg))


def _calendar(slices: list[Slice]) -> list[Slice]:
    """The calendar flag: a slice's total variance against the smile before it, at the same
    forward moneyness."""
    out, before = [], None
    for s in slices:
        if s.model is None:
            out.append(s)
            continue
        if before is not None:
            z = CAL_Z * float(np.sqrt(max(float(before.w(0.0)[0]), svi.W_MIN)))
            kk = np.linspace(-z, z, CAL_GRID)
            above = s.w(kk)[0] >= before.w(kk)[0] * (1 - CAL_TOL)
            s = replace(s, calendar_ok=bool(np.all(above)))
        out.append(s)
        before = s
    return out


def fit_surface(
    inputs: Sequence[SliceInput], warm: Warm | None = None, settings: Settings | None = None
) -> Surface:
    """Fit the surface of one underlying from the quotes of its expirations. `warm` is
    `Surface.warm()` of the fit before, which makes the fit faster and steadier."""
    st = settings or Settings()
    warm = warm or Warm({}, None)
    works = sorted((_prepare(inp, st) for inp in inputs), key=lambda f: f.tau)
    for f in works:
        _fit_svi(f, warm, st)
    shape = _fit_ssvi(works, warm)
    return Surface(tuple(_calendar([_slice(f) for f in works])), _tuple(shape))
