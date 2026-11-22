# Probability and Risk Methods for a Self-Hosted Short-Premium Options Analyzer: Survey and Build Order

**Bottom line:** Tastytrade's flat-IV lognormal POP and constant-volatility P50 are honest risk-neutral numbers, but they are the wrong measure for a premium seller. The literature consistently finds that option-implied (risk-neutral) densities are poorly calibrated forecasts of realized index returns, and that a simple power-utility or historical transform cannot be rejected while sophisticated recovery methods (Ross) fail. Build in this order: (1) smile-consistent risk-neutral probabilities plus closed-form touch probabilities and full-revaluation stress grids; (2) a physical-measure layer (filtered historical simulation of joint spot and ATM-vol moves, with skew-stickiness-ratio surface dynamics) for path probabilities (P25/P50/P75, stop-first, 21-DTE outcomes) and CVaR; (3) Heston/jump/regime models and roll-for-credit probabilities only if your own 1-minute option history shows a calibration gain.

## TL;DR

- **Expiry POP:** Replace flat-IV lognormal with the SVI-implied risk-neutral CDF (exact, cheap), and show a separate physical-measure POP from filtered historical simulation or a power-utility transform; for SPX/SPY out-of-the-money puts the risk-neutral probability of finishing in the money is systematically too high (the "overpriced puts" finding), so risk-neutral POP understates a short put's real-world win rate, while for single stocks the gap is smaller and less well documented. Ross recovery is empirically rejected and should not be used.
- **Path probabilities and what-if:** P50, P(touch), P(stop-first) need a joint spot–vol model; the cheapest defensible one is a filtered-historical or block-bootstrapped spot/ATM-vol path generator with the surface moved by a skew-stickiness ratio (SSR ≈ 1–1.5 for index, between sticky strike R=1 and local vol R=2). No peer-reviewed study validates any model's "reached 50% of max profit" frequency, so you must validate it yourself on your option history.
- **Risk:** Full revaluation (not delta-gamma) under filtered historical scenarios is feasible in well under a second; use OCC TIMS (±15% single stocks, +6/−8% high-cap broad index, 10 equidistant points) and CME SPAN (16 scenarios with a vol scan) as stress templates, tie vol shocks to spot shocks asymmetrically (IBKR's documented rule is a good default), and use ≥20,000 paths plus control variates for a stable 5% CVaR; 1,000 paths gives only ~50 tail observations.

## Key Findings

### What the sources establish

1. **Risk-neutral densities are biased forecasts of realized index distributions; a one-parameter utility adjustment fixes much of it.** Bliss and Panigirtzoglou (2004, *Journal of Finance*) find that, at least for 3–5 week horizons, risk-neutral distributions provide poor forecasts of future densities, while power-utility-adjusted densities cannot be rejected as unbiased forecasts.\[1\]\[2\] Their implied relative risk aversion is horizon-dependent: per their Table V as summarized in arXiv 1910.14522, it declines from κ̂ ∼ 8 at a one-week horizon to κ̂ ∼ 2 at a 6-week horizon, and their abstract (*Journal of Finance* 59(1):407–446) states that "the degree of RRA declines broadly with the forecast horizon and is lower during periods of high market volatility" (arXiv 1910.14522 itself argues for a more constant coefficient in the 2.5–3.5 range — the sources disagree on horizon dependence). Shackleton, Taylor and Yu (2010, *Journal of Banking & Finance*) extend real-world transforms to horizons from 1 day to 12 weeks for the S&P 500 and find that incorporating risk-neutral jumps does not improve real-world densities.\[3\]
2. **The sign of the bias for index OTM puts is well established.** Bondarenko (2014, *Quarterly Journal of Finance*, "Why Are Put Options So Expensive?") documents that historical S&P 500 put prices, especially OTM puts, have been too high and incompatible with canonical asset-pricing models, and that no model in a broad class can explain the anomaly.\[4\]\[5\] Practically: risk-neutral P(short index put finishes ITM) is an overestimate of the real-world frequency.
3. **Ross recovery does not work empirically.** Borovička, Hansen and Scheinkman ("Misspecified Recovery," NBER WP 20209, 2014; *Journal of Finance* 2016) show Ross's transition-independence assumption forces the martingale component of the stochastic discount factor to be one, so the recovered measure absorbs long-term risk adjustments and "would differ substantially from the actual probability distribution."\[6\]\[7\] Jackwerth and Menner (2020, *Journal of Financial Economics* 137:723–739) find Ross-recovered S&P 500 distributions "incompatible with future realized returns and fail to predict realized returns and variances," while "simple benchmark methods based on a power utility agent or on the historical return distribution cannot be rejected."\[8\]
4. **Smile dynamics sit between sticky strike and local vol for index options.** Bergomi's skew-stickiness ratio R(T) = β(T)/ψ(T), where β is the regression slope of ATM-vol changes on log-spot returns and ψ is the ATM skew dσ/dln K.\[9\] R=0 is sticky delta/moneyness, R=1 sticky strike, R=2 the short-maturity local-vol value (Vargas, Dao, Bouchaud 2013, arXiv 1311.4078; Bouchaud et al., arXiv 1507.00846).\[10\]\[11\] Empirical SPX values are reported as historically between 1 and 1.5 (Gatheral, Baruch slides 2023), roughly 3/2 (Bergomi 2016 as cited in arXiv 2603.10857), and between 0.9 and 1.7 across maturities in another Gatheral presentation; the SSR also "depends on the recent history" of returns.\[9\]\[12\]\[13\] Derman (1999, Goldman Sachs QS note "Regimes of Volatility") frames sticky strike, sticky delta and sticky implied tree as regimes (complacency, moderation, fear).\[14\]\[15\]
5. **Early exercise is frequently sub-optimal in both directions.** Pool, Stoll and Whaley (2008, *Journal of Financial Markets* 11:1–35) find that, for dividend-paying stocks 1996–2006, more than half of outstanding long call positions that should be exercised before the ex-date go unexercised, costing holders over $491 million.\[16\] Barraclough and Whaley (2012, *Journal of Finance* 67:1423–1456) find that, from January 1996 through September 2008, more than 3.96 million puts that should have been exercised early remained unexercised, over 3.7% of all outstanding puts, costing holders $1.9 billion in forgone interest income that was "systematically captured by market makers and proprietary firms." Jensen and Pedersen (2016, *Journal of Financial Economics* 121(2):278–299, "Early option exercise: Never say never") show frictions (short-sale costs, funding costs) make early exercise rational in more cases, explaining up to 98% of market-maker and 67% of customer early exercises.\[17\] Cosma, Galluccio, Pederzoli and Scaillet (JFQA, "Early Exercise Decision in American Options with Dividends, Stochastic Volatility, and Jumps") show the exercise boundary is higher under Merton/Heston than Black-Scholes with discrete dividends and explain up to 25% of the forgone gain.\[18\]\[19\]
6. **Broker and CCP margin methods are documented scenario grids or filtered historical VaR.** OCC TIMS revalues at ten equidistant price points; for customers the range is ±15% for equities and narrow-based indexes, +6%/−8% for high-cap broad-based indexes and ±10% for non-high-cap broad indexes (OCC Ovation RBH/CPM user guide; FINRA Rule 4210(g)).\[20\]\[21\]\[22\] CME SPAN builds 16-scenario risk arrays moving price and volatility (e.g., an S&P 500 volatility scan range of 35% in CME's own example), plus extreme scenarios for deep OTM short options.\[23\]\[24\] CME SPAN 2 uses a filtered historical VaR with EWMA-scaled returns plus a stressed-VaR component of historical and hypothetical scenarios (CME rule filing 24-425; CME SPAN 2 framework).\[25\]\[26\] Boudiaf, Scheicher and Vacirca (ECB Occasional Paper No 314, April 2023) note that "when replacing SPAN frameworks with other models, CCPs seem to converge towards the use of filtered historical simulation VaR models," which give more weight to recent returns.
7. **Expected shortfall can be backtested, but tests have low power.** Acerbi and Szekely (2014, *Risk*; MSCI Research Insight) propose Z1 (magnitude conditional on VaR exceptions) and Z2 (joint frequency and magnitude). Their Table 4 gives Z2 critical values of −0.70 at 5% and −1.8 at 0.01% for a Gaussian, but −0.82 and −4.4 for a Student-t with ν=3, so the 5% level is fairly stable across distributions while the 0.01% level is not stable under fat tails (the authors themselves describe "remarkable stability across different distribution types"). In their own Table 2, Z2 detects a Student-t ν=5 alternative to a ν=10 null only 43.4% of the time at 4.0% significance, and only 7.8% of the time against normalized Student-t alternatives, where they say Z2 and the VaR test show "very little power at all, with VaR doing slightly better."
8. **Retail tool models are mostly flat-volatility lognormal or undocumented** (details in the tools section).

### My judgement (not established by sources)

- The single largest improvement over the broker baseline is not a fancier risk-neutral model; it is showing a physical-measure number alongside the risk-neutral one and labeling which is which.
- For path probabilities, the bottleneck is how the surface moves with spot and time, not the spot distribution. A spot/ATM-vol bootstrap plus SSR mapping captures the first-order effect at a fraction of the cost of calibrated stochastic-volatility models.
- Expect the risk-neutral vs physical POP gap for a 16-delta 45-DTE SPX short put to be several percentage points (risk-neutral too pessimistic for the seller), and for the CVaR gap to go the other way in calm regimes: a constant-vol, zero-drift lognormal understates crash losses because it has no jumps and no vol spike on down moves. Verify both on your own data before trusting either magnitude.

## Details

### Q1. Probability of profit at expiry

**Formulas.**

- *Flat-IV lognormal (baseline):* P(S_T > B) = N(d₂), d₂ = [ln(F/B) − ½σ²T]/(σ√T), with one σ per expiry. Tastytrade defines POP as "the probability of making at least $0.01 on the trade at expiration" (Tastytrade platform glossary);\[27\] the specific σ choice is not documented there.
- *Smile-consistent risk-neutral CDF (Breeden–Litzenberger 1978):* Q(F_T > K) = −e^{rT}∂C/∂K. In SVI total-variance form, with k = ln(K/F), w(k) = σ²(k)T, d₂(k) = −k/√w − √w/2:
  Q(F_T > K) = N(d₂(k)) − φ(d₂(k))·w′(k)/(2√w(k)).
  This is closed form given SVI's analytic w′(k), so a four-leg POP is microseconds. For a multi-breakeven structure (condor, strangle), POP = Σ over profitable intervals of [CDF(b_upper) − CDF(b_lower)].\[28\] Arbitrage-free SVI/SSVI (Gatheral–Jacquier 2014) guarantees a non-negative density; check butterfly arbitrage (g(k) ≥ 0) per snapshot and fall back to SSVI where it fails.
- *American/discrete dividends:* for equity options, back out a forward from put–call parity on near-ATM pairs (this absorbs discrete dividends and borrow), fit SVI on OTM options only to minimize early-exercise contamination, and treat the resulting density as the forward-measure density of S_T. This is an approximation; the early-exercise premium of OTM options is small, but deep ITM quotes should not be used.
- *Physical-measure options:*
  - **Historical/bootstrapped:** empirical distribution of T-day log returns over 30 years, rescaled to current conditions. Unconditional histories ignore volatility clustering, so use only as a sanity benchmark.
  - **Filtered historical simulation (FHS; Barone-Adesi, Bourgoin and Giannopoulos 1998, *Risk*, "Don't look back"):** fit GJR-GARCH(1,1) (Glosten–Jagannathan–Runkle 1993) or EWMA to daily returns, standardize residuals z_t = ε_t/σ_t, then resample z with replacement and re-inflate with the current conditional σ along the path.\[29\] This is the same filtering idea CME SPAN 2 uses (EWMA-scaled historical returns).\[25\] Anchor the level to the options market by scaling the GARCH forecast to match the ATM implied variance times a variance-risk-premium ratio estimated from your history (realized/implied variance), if you prefer an implied anchor.
  - **Risk-neutral → physical transform:** power utility p(x) ∝ q(x)·x^γ (Bliss–Panigirtzoglou 2004), with γ estimated by maximizing the out-of-sample Berkowitz likelihood of realized PIT values, or a non-parametric calibration function (Liu, Shackleton, Taylor and Xu 2007, "Closed-form transformations from risk-neutral to real-world distributions," *Journal of Banking & Finance*):\[30\] p(x) = q(x)·c(Q(x)) where c is the density of historical PITs under the RND (a beta-distribution fit is standard). The calibration-function approach is the most direct empirical calibration and fits your validation framework exactly.
  - **Ross recovery:** do not implement (Jackwerth–Menner 2020; Borovička–Hansen–Scheinkman).\[8\]

**Evidence on size and sign.**

- *Index OTM puts:* risk-neutral probability of large declines exceeds realized frequency (Bondarenko 2014; Coval and Shumway 2001, *Journal of Finance*, "Expected Option Returns," find large negative returns on index puts and straddles).\[31\] The sign is therefore: risk-neutral POP of a short index put is too low (pessimistic for the seller); risk-neutral POP of a short index call is roughly right or slightly too high, because the physical distribution has positive drift but the left skew of the RND puts relatively less mass far above the forward.
- *Single-stock options:* individual-equity smiles are flatter and the evidence of systematic put overpricing is weaker; Driessen, Maenhout and Vilkov (2009, *Journal of Finance*, "The Price of Correlation Risk") attribute the index-option premium largely to priced correlation risk that is absent in individual options. Treat the single-stock RN-vs-physical gap as an empirical question for your data; the literature I found does not give a reliable sign at the strike level.
- *Best calibrated out of sample:* for the S&P 500, power-utility-adjusted or historically calibrated densities beat raw risk-neutral densities at 1–6 week horizons (Bliss–Panigirtzoglou 2004; Shackleton–Taylor–Yu 2010); Ross recovery is worse than both simple benchmarks (Jackwerth–Menner 2020).\[2\]\[3\]\[8\]

### Q2. Probabilities before expiry (path-dependent)

**Closed form for touch (constant vol).** For a barrier H above S, b = ln(H/S), drift ν = r − q − ½σ² (risk-neutral) or μ − ½σ² (physical):
P(max_{t≤T} S_t ≥ H) = N((−b + νT)/(σ√T)) + exp(2νb/σ²)·N((−b − νT)/(σ√T)).
With ν ≈ 0 this is ≈ 2·P(S_T ≥ H), which is the practitioner rule "probability of touch ≈ 2× probability ITM."\[32\]\[33\] For discrete monitoring in simulation, apply the Broadie–Glasserman–Kou (1997, *Mathematical Finance*) continuity correction (shift the barrier by exp(±0.5826σ√Δt)) or a Brownian-bridge crossing probability between steps. Use the strike's own smile IV, not ATM IV, and show it as risk-neutral.\[34\]

**Target and stop events.** For a position with entry credit C₀, define the P&L along a path as C₀ − V_t(S_t, Σ_t, t), where Σ_t is the surface at time t. Then:
- P_x = P(min_{t≤τ} V_t ≤ (1−x)C₀) for x ∈ {0.25, 0.5, 0.75}, with τ = min(expiry, management date such as 21 DTE).
- P(stop first) = P(V hits m·C₀ before (1−x)C₀ and before τ), for m = 2 (loss = 1× credit) or m = 3 (loss = 2× credit); be explicit about which convention "2× credit" means.
- Show the joint outcome distribution at τ: {target hit, stop hit, neither (close at τ)}, plus the conditional mean P&L of each. This trio is far more useful than P50 alone.

These require V_t, which requires Σ_t. Tastytrade describes P50 as "running [the trade] through a Monte-Carlo style simulation, and calculating the theoretical probability that your position will reach a 50% profit over 10,000 occurrences" (Tastytrade glossary/support).\[27\]\[35\] Note a discrepancy: the vendor says 10,000 occurrences, while your brief states about 1,000 and a third-party replication (zachlim98, 2021) used 1,000 GBM paths and got 86% vs Tastytrade's 89%.\[36\]\[37\] The surface dynamics in Tastytrade's simulation are undocumented.

**Model classes for the joint spot/vol path.**

| Model | Captures | Calibration from one snapshot + history | Compute (10k paths × 45 daily steps, 4 legs, numpy) | Known failure modes |
|---|---|---|---|---|
| Constant vol GBM (baseline) | Diffusive spot only; V_t repriced at fixed IV | None | ~50–100 ms | No vol spike on sell-offs; no jumps; understates stop-outs and overstates smooth decay; P50 too high in stress |
| Local vol (Dupire 1994, from SVI) | Exact fit of today's smile; spot-dependent vol | Analytic from SVI/SSVI; needs arbitrage-free surface | Path sim cheap; repricing legs along path needs PDE or proxies → seconds | Predicts the wrong smile dynamics: forward smile flattens and the smile moves opposite to spot (Hagan, Kumar, Lesniewski and Woodward 2002, *Wilmott*, "Managing Smile Risk"); SSR=2 at short end is above empirical |
| Heston (1993, *RFS*) | Stochastic variance, leverage correlation ρ, mean reversion | Fit 5 params to the chain via Fourier/COS pricing in scipy least squares; 0.1–1 s per slice set | Path sim ~100–300 ms; repricing via Fourier at each node is expensive → use SSR mapping instead | Cannot fit short-dated steep skew; term structure of skew too flat; vol-of-vol too high when forced to fit |
| SABR (Hagan et al. 2002) | Per-expiry smile with backbone β and spot-vol correlation | Per-expiry fit is fast; dynamics controlled by β/ρ | Fast for scenario smiles, not a joint multi-expiry path model | No term structure dynamics; arbitrage at low strikes |
| Jump diffusion (Merton 1976; Bates 1996 SVJ) | Gap risk, short-dated skew | Jumps poorly identified from one snapshot; use history | Similar to Heston | Jump intensity/size unstable; RN jumps do not improve physical density forecasts (Shackleton–Taylor–Yu 2010) |
| Regime switching (Hamilton 1989) | Calm/stress persistence | Fit on 30-year daily history | Cheap | Regime count arbitrary; regime probabilities lag |
| Empirical SSR regression | How ATM vol and fixed-strike vols move with spot | Regress daily ΔATMvol on returns, normalize by skew, per underlying and tenor | Negligible | Linear, small-move; SSR is time-varying |
| Block bootstrap of joint (return, ΔATM vol, Δskew) | Joint empirical dynamics, clustering, vol spikes | Stationary bootstrap (Politis–Romano 1994) on daily history; filter by GARCH | ~100 ms | Limited to historical episodes; overlapping-block dependence; needs IV history (you have it) |

**Recommended stage-2 path engine (my judgement).** Simulate daily steps with FHS-bootstrapped standardized residuals for spot and log-ATM-vol jointly (resample the same historical day for both, preserving their correlation and the leverage effect), then map the per-expiry SVI slice to the new state by (a) shifting ATM total variance to the simulated ATM vol level, (b) moving fixed-strike vols with SSR R(T) estimated from your history, and (c) rolling time forward (constant-maturity slice interpolation in SSVI's θ_t). Revalue legs with Black-Scholes at the mapped IVs (European approximation for OTM American legs, with an early-exercise flag from Q5). Ten thousand paths × 45 steps × 4 legs is ~2 million vectorized BS evaluations, which is around 0.1–0.3 s in numpy: interactive for one position, batch for a book.

**Published evidence on "reached 50% of max profit" frequencies.** I found no peer-reviewed study that tests the calibration of any model's P50 or profit-target probabilities against realized frequencies. The available evidence is practitioner backtests: Tastytrade's own research and ORATS's backtester, which tests profit targets of 25%, 50%, 75% and stop losses of −25%, −50%, −75% (ORATS blog, "Behind the Scenes of Over 50 Million Options Backtests"), report realized hit rates, not model calibration.\[38\]\[39\] Treat this as an open validation question you must answer with your option history.

### Q3. Smile dynamics for what-if

**Definitions.** Let ψ(T) = ∂σ/∂ln K at the money and x = ln(S′/S).
- *Sticky strike:* σ(K) unchanged (R = 1 in the fixed-strike sense: ATM vol moves along the existing smile, rising when spot falls).\[40\]
- *Sticky delta/moneyness:* σ(K/S) unchanged; ATM vol constant (R = 0).\[40\]\[41\]
- *Sticky local vol:* fixed-strike vols move opposite to spot by about one skew; ATM vol moves at ~2× skew at short maturities (R = 2).
- *SSR parameterization (general):* ΔσATM = R(T)·ψ(T)·x and fixed-strike Δσ(K) ≈ (R(T) − 1)·ψ(T)·x.\[12\] One parameter interpolates all three rules.

**Default (my judgement, grounded in the SSR evidence).** Index (SPX, SPY, QQQ, IWM): R = 1.5 for 7–90 DTE, estimated per underlying and tenor from your history on a rolling 120-day window (the window Gatheral uses for the Vola Dynamics SSR series).\[13\] Single stocks: start at R = 1 (sticky strike) and estimate; the evidence for single names is thinner. For large moves (|x| > 2 daily sigmas), add a level shift on top of SSR because the linear regression understates vol convexity. IBKR's documented coordinated rule is a sound stress default: when prices fall X%, vols rise up to 10·X% for short-dated options, decaying to 2·X% for longer expiries; when prices rise X%, vols fall X% for short-dated options, decaying to X/5% for longer expiries (IBKR TWS Risk Navigator scenario documentation).\[42\]

**User controls:** (1) dynamics selector {sticky strike, sticky moneyness, SSR = estimated, custom R}; (2) parallel ATM-vol shift in vol points and as a relative %; (3) term-structure damping of the vol shock (IBKR's VR(t) decreasing from 1, or a √(30/DTE) rule);\[42\] (4) skew steepening multiplier; (5) "time passes" slider that rolls each slice to constant maturity before applying the spot move.

### Q4. Probability of rolling for a credit

**Literature.** I found no academic paper or vendor documentation that estimates the probability that a tested position can be rolled for a net credit. Several tools offer roll analysis (thinkorswim lists a strategy rolling tool), but none I found document a probability.\[43\] Treat it as a new metric.

**Precise definition (proposal).** For a short put at strike K, expiry T, on evaluation date t* (e.g., when the short strike is touched, or at 21 DTE):
RollCredit(t*, K′, T′) = Bid_{t*}(K′, T′) − Ask_{t*}(K, T) − costs, with K′ ≤ K for a put (≥ K for a call) and T′ ∈ {T + 7, +14, +28, … days, listed expiries only}.
P_roll(t*) = P( max over allowed (K′, T′) of RollCredit ≥ 0 | trigger event at t*).
Compute it inside the stage-2 path engine: at each path's trigger date, evaluate the mapped surface at candidate (K′, T′), convert mids to bid/ask with a spread model fitted from your quote history (spread as a function of moneyness, DTE, vega and vol level), and record whether any candidate clears zero.

**What makes it misleading.** (1) A roll credit is an accounting artifact: it adds duration and risk and does not change expected value under any measure; it should be shown next to the change in CVaR and in max loss from rolling. (2) It is dominated by the term-structure assumption: in stress, short-dated vol spikes above longer-dated vol (inversion), which helps rolls out in time; a model with a flat term structure will understate roll feasibility in sell-offs and overstate it in calm markets. (3) Bid/ask width on the far expiry is the deciding term for deep-tested positions, so mid-based calculations are optimistic. (4) Conditioning on a trigger event makes the sample of paths small and regime-specific, which inflates Monte Carlo noise. (5) Early assignment risk on deep-ITM puts may preclude the roll entirely. Recommendation: research-grade only (stage c).

### Q5. Early assignment

**Exact conditions (no frictions).**
- *Calls:* early exercise of an American call is only optimal immediately before an ex-dividend date (Merton 1973).\[17\] Exercise just before the ex-date is optimal when the call's value ex-dividend is below S − K, approximately when D > P_ex(K, τ) + K(1 − e^{−rτ}), i.e., the dividend exceeds the time value of the corresponding European put plus interest on the strike over the remaining life (the condition used by Pool–Stoll–Whaley 2008 and Roll–Geske–Whaley valuation).
- *Puts:* exercise is optimal when the put is at or below its exercise boundary S*(t); by put–call parity, approximately when the interest earned on K over the remaining life, K(1 − e^{−rτ}), exceeds the value of the call with the same strike plus PV of dividends before expiry (Barraclough–Whaley 2012 derive a rule of this kind and apply it to OCC exercise data).\[44\]
- *Practitioner screen:* short call: flag when an ex-date falls before expiry and extrinsic value of the call < D; short put: flag when extrinsic value of the put < K·r·τ (carry on the strike) and the put is ITM. Hard-to-borrow underlyings raise early-exercise likelihood for calls even absent dividends (Jensen–Pedersen 2016).\[17\]

**Evidence on holder behaviour.** Holders under-exercise: more than half of optimally exercisable calls go unexercised before ex-dates (Pool–Stoll–Whaley 2008) and 3.7% of puts that should be exercised are not (Barraclough–Whaley 2012); market makers and proprietary firms capture most of the value.\[16\]\[44\] Exercise is also not purely rational: early call exercise unconnected to dividends is frequent (Option Investor Rationality Revisited, IU ScholarWorks).\[45\] This means a model probability of assignment is not honest: assignment is a counterparty decision with frictions you cannot observe.

**Screen-level indicator (recommend).** A three-state flag per short leg: *High* (exercise condition met: extrinsic < dividend before the ex-date for calls, or extrinsic < carry for ITM puts), *Watch* (extrinsic < 2× dividend or 2× carry), *Low* otherwise, plus the dollar amount at stake (dividend owed or carry). Do not show a percentage probability of assignment.

### Q6. Tail risk for a position and a portfolio

**Methods compared.**
- *Delta-gamma (Britten-Jones and Schaefer 1999, Review of Finance):* fast quadratic approximation; fails exactly where a short-premium book is exposed: large moves, non-monotone payoffs (condors, strangles), and vega-driven losses. Use only for intuition.
- *Full revaluation under historical simulation:* revalue every leg under each historical day's (return, ΔATM vol, Δskew) shocks, scaled to horizon. With 30 years of daily data you get ~7,500 one-day scenarios; for h-day horizons use overlapping h-day blocks (heavily dependent) or FHS paths.
- *Filtered historical simulation:* as in Q1, with GARCH/EWMA rescaling; this is the CME SPAN 2 core and the direction CCPs are converging on (ECB Occasional Paper 314).\[25\]\[46\] Best default.
- *Monte Carlo with a factor model:* for several correlated underlyings, decompose each underlying's return as β_i·market + idiosyncratic, with each underlying's ATM-vol change loaded on a market vol factor (VIX change) plus idiosyncratic; sample factor shocks by FHS and idiosyncratic residuals by bootstrap. This keeps cross-asset correlation and its rise in sell-offs, which a Gaussian copula misses.
- *Stress scenarios:* fixed grids (TIMS/SPAN) and historical episodes (Oct 1987, Oct 2008, Aug 2015, Feb 2018 "Volmageddon," Mar 2020, Aug 2024 yen-carry unwind), replayed with spot and vol moves from your data.

**Tying the volatility shock to the spot shock.** Use asymmetric coupling: on down moves vol up strongly, on up moves vol down mildly, with term-structure damping. IBKR's documented rule (vols up by up to 10·X% for X% down for short-dated options, decaying to 2·X%; down X% for X% up, decaying to X/5%) is a transparent default.\[42\] In historical and FHS scenarios this coupling is automatic because you resample the joint move.

**Broker/CCP templates.**
- *OCC TIMS / FINRA 4210(g) customer portfolio margin:* ten equidistant points across ±15% (equities, narrow indexes), +6%/−8% (high-cap broad indexes), ±10% (non-high-cap broad indexes); margin is the largest theoretical loss across points (OCC; FINRA).\[20\]\[21\]\[47\]\[48\] The CPM documentation I found does not specify the implied-volatility shifts applied, so treat that part as undocumented in the sources gathered; IBKR adds its own vol scenarios on top.
- *CME SPAN:* 16 risk-array scenarios per contract combining price moves (fractions of the price scan range) with vol up/down (vol scan range), plus extreme moves for deep OTM short options;\[23\] *SPAN 2:* FHVaR with EWMA scaling plus stressed VaR from historical and hypothetical scenarios.\[25\]
- *Use as a stress template:* yes; implement the TIMS grid × {vol −, 0, +} and SPAN's 16 scenarios exactly, and show "TIMS-like worst loss" next to CVaR. These are useful because they match what your broker will use for portfolio margin, not because they are probabilistically meaningful.

**Number of paths for a stable 5% CVaR.** The tail at 5% contains 0.05N observations. A standard approximation of the standard error is SE(ES) ≈ √{[Var(L | L > VaR) + α(ES − VaR)²]/(N(1−α))} with α = 0.95. With N = 1,000 you average 50 tail losses; for a short strangle whose tail losses are widely dispersed, relative error is on the order of 15–25% (my calculation for typical tail dispersion). N = 20,000 (1,000 tail observations) brings it to about 3–5%; N = 50,000 to about 2–3%. Full revaluation of 4 legs at 50,000 terminal states is ~200,000 BS evaluations, i.e., milliseconds.

**Variance reduction that matters here.**
- Control variates: the closed-form lognormal expiry P&L distribution (or the closed-form RND POP) as a control for the simulated expiry metrics.
- Importance sampling toward the loss region: shift the drift of the Gaussian driver toward the loss tail and reweight by the likelihood ratio (Glasserman, Heidelberger and Shahabuddin 2000, *Management Science*, "Variance reduction techniques for estimating value-at-risk," using the delta-gamma approximation to choose the shift); this is the method with the largest gain for 5% and smaller tail probabilities.
- Antithetic variates and Sobol quasi-Monte Carlo (scipy.stats.qmc) for smooth expectations; less useful for indicator events like P50.
- Common random numbers when comparing two positions or two parameter settings, so differences are not swamped by noise.

### Q7. Other risk and return measures

| Measure | Common in tools? | Evidence of usefulness | Misleading when |
|---|---|---|---|
| Expected value under RN density | Implicit everywhere | None: under the RN measure EV ≈ 0 minus spread by construction | Shown as "edge"; any positive RN EV is a model or quote artifact |
| Expected value under physical density | Option Alpha shows EV (using 30-day historical vol in a BSM N(d₂) model) and "Alpha" = EV / max loss (Option Alpha docs) | Sign and size for index puts consistent with variance-risk-premium literature | Point estimate without error band; sensitive to drift and tail assumptions |
| Return on buying power | Tastytrade, most brokers | Practical capital efficiency | Under Reg-T vs portfolio margin numbers differ drastically; ignores tail |
| Kelly sizing | Rare in retail tools | Kelly (1956) growth-optimality; fractional Kelly recommended under estimation error (MacLean, Thorp and Ziemba 2010, *The Kelly Capital Growth Investment Criterion*) | For negatively skewed payoffs, full Kelly from an estimated distribution over-bets; undefined-risk positions need a stress-bounded max loss |
| Probability-weighted max loss | Some tools | Equivalent to tail expectation if done properly | If computed from lognormal flat IV, understates crash tail |
| Theta / tail-loss ratio | Practitioner heuristic | No published evidence found | Theta is a deterministic one-day RN quantity; ratio rewards selling more tail |
| Beta-weighted delta | Tastytrade, thinkorswim, IBKR | Standard for aggregation | Betas unstable; correlation rises in sell-offs |
| Gamma/vega by expiry bucket | Professional risk systems | Standard | Vega aggregation across expiries assumes parallel shifts; use term-damped (√T-weighted) vega |

### Q8. Validation against your option history

**Unit of evaluation.** For each underlying, each entry date (daily, at a fixed time such as 15 minutes after the open), and each strategy template (e.g., 16-delta short put, 16/16 strangle, 10-wide iron condor, at 7–14, 30–45 and 90 DTE), record every model's predicted probabilities at entry and the realized outcomes from the 1-minute option history: finished profitable at expiry; touched short strike; hit 25/50/75% of max profit before τ (using executable bid/ask marks, not mids); hit stop first; P&L at 21 DTE.

**Scores and tests.**
- *Calibration curves / reliability diagrams:* bin predicted probabilities (deciles, or isotonic regression), plot observed frequency with confidence bands.
- *Brier score and its Murphy (1973) decomposition* into reliability, resolution and uncertainty; log score as a stricter alternative.
- *PIT:* for each density forecast (RN, transformed, FHS), compute u = F̂(S_T); under correct specification the u are i.i.d. uniform for non-overlapping horizons (Diebold, Gunther and Tay 1998, *International Economic Review*); test with Berkowitz (2001, *Journal of Business & Economic Statistics*) likelihood ratio after inverse-normal transform, which emphasizes tails.
- *CVaR coverage:* Acerbi–Szekely Z1 and Z2 on daily one-day ES forecasts for the book (Z2 critical value ≈ −0.70 at 5% significance), plus the Kupiec/Christoffersen (1998) VaR exception tests as a first screen.\[49\]\[50\]
- *Model comparison:* Diebold–Mariano (1995, *Journal of Business & Economic Statistics*) test on the difference in Brier or log scores, with HAC (Newey–West) standard errors.

**Overlap corrections.** Daily entries into 45-DTE trades overlap ~45-fold. Use HAC standard errors with bandwidth ≥ horizon length, or evaluate on non-overlapping subsamples (one entry every horizon length) and average across the offsets. Cross-sectional dependence (SPY, QQQ, IWM move together) further reduces effective sample size; cluster by date.

**Sample sizes (my calculation).** With effectively independent observations, a calibration bin at p = 0.8 has standard error √(0.16/n): n = 400 gives ±2 pp (1σ). Daily 45-DTE entries for one underlying over 5 years yield ~1,250 overlapping trades but only ~28 non-overlapping ones; even pooling 20 liquid underlyings with average cross-correlation 0.5 leaves the effective n in the low hundreds. Expect to detect POP differences of 5 pp or more between models; differences of 1–2 pp are not identifiable with a few years of data. For P50, the event is more frequent (~0.6–0.9) and resolves faster, so effective n is larger; for 5% CVaR, you need years of daily one-day forecasts before Z2 has meaningful power.

**Regime splits.** Report every score separately by VIX tercile at entry, by trend regime (above/below 200-day average), by event windows (earnings for single names, FOMC/CPI for indexes), and pre/post 2020 (the 0DTE era changed short-dated dynamics). A model that wins on average but loses in the high-VIX tercile should not be the default for risk numbers.

### Q9. What existing tools show (documented vs undocumented)

| Tool | Numbers shown | Documented model | Undocumented |
|---|---|---|---|
| Tastytrade | POP (≥$0.01 profit at expiry), P50 (reach 50% of max profit before expiry), CVaR (losses in the worst 5% at expiration)\[27\]\[51\] | P50: "Monte-Carlo style simulation… over 10,000 occurrences"; POP via breakeven ITM/OTM probabilities | Which IV, drift, surface dynamics in P50; CVaR simulation details |
| thinkorswim (Schwab) | Probability ITM, OTM, Probability of Touching in Analyze → Probability Analysis and Product Depth; risk profile with IV adjustment | Probability Analysis shows projected price range from implied volatility\[43\]\[52\]\[53\] | Exact touch formula, which IV (per-strike or ATM), drift; community replications approximate it with lognormal barrier math |
| IBKR Probability Lab | Market-implied probability distribution per expiry; user-editable distribution; strategy builder | PD derived from option prices; probabilities use the user's customized distribution | Smoothing/extraction method |
| IBKR Risk Navigator | Scenario P&L grids, Vol Up/Down 15% (relative), coordinated price-vol scenarios, one-day confidence interval (95/99/99.5%) | Vol-coord rule with term-structure response VR(t), VR(0)=1 decreasing; equities: vol up 10·X% for X% down (short-dated), decaying to 2·X% | VaR method (parametric/historical/MC); exact VR(t) shape |
| OptionStrat | Chance of profit "based on the implied move," probability distribution overlay, IV slider\[54\] | IV held constant in the calculator | Distribution, which IV, whether skew is used |
| OptionNet Explorer | Probability between breakevens at expiry, 1σ/2σ zones, probability cone | Pricing model: Black-Scholes only | How the probability is calculated, which IV; no vendor documentation of Monte Carlo |
| Option Alpha | Probability (ITM/OTM, range), POP, EV, Alpha = EV / max loss | BSM N(d₂) with a polynomial approximation; volatility input is 30-day historical volatility, held to expiration | Expected move definition |
| ORATS | Backtests with profit targets/stop losses; scanner returns POP and risk metrics | Backtester entry/exit and slippage methodology documented\[38\]\[55\]\[56\] | Scanner POP distribution model |

## Methods Table by Question

| Q | Method | Estimates | Assumptions | Inputs | Calibration / compute | Evidence of accuracy | Failure modes | Difficulty |
|---|---|---|---|---|---|---|---|---|
| 1 | Flat-IV lognormal | POP at expiry (RN) | GBM, one σ | ATM IV, r, q | None / µs | Baseline; RN bias for index puts | Ignores skew and risk premium | Low |
| 1 | SVI RND CDF | POP at expiry (RN, smile-consistent) | Arbitrage-free SVI/SSVI | Fitted slice, forward | Already fitted / µs | Exact RN; same RN bias | Wing extrapolation; American contamination | Low |
| 1 | FHS (GJR-GARCH) | Physical POP, terminal distribution | Stationary standardized residuals | 30y daily returns | Seconds per fit daily / ~50 ms | FHS standard at CCPs (SPAN 2, ECB) | Residual pool misses unseen crashes | Medium |
| 1 | RN→P transform (power utility, calibration function) | Physical POP | Stable γ or calibration map | RND history + realized | Batch fit / µs | Beats raw RND at 1–6 wk (Bliss–Panigirtzoglou; Shackleton–Taylor–Yu) | γ horizon dependence disputed | Medium |
| 1 | Ross recovery | Physical density | Transition independence | Full state-price matrix | High / unstable | Rejected (Jackwerth–Menner 2020) | Misspecified (BHS) | High — do not build |
| 2 | Closed-form touch | P(touch) (RN) | GBM, constant σ | Strike IV | None / µs | Standard formula | No vol spikes; discrete monitoring | Low |
| 2 | Const-vol MC (Tastytrade-like) | P25/50/75, stop-first | GBM, fixed IVs | IVs | None / ~100 ms | Unvalidated | Understates stop-outs | Low |
| 2 | FHS spot+ATM-vol bootstrap + SSR mapping | Path probabilities, 21-DTE outcomes | Historical joint dynamics; linear SSR | 30y daily returns, IV history | Batch estimation / 0.1–0.3 s | Unvalidated (no published study) | Limited to observed episodes | Medium |
| 2 | Heston / Bates | Path probabilities | Affine SV (+ jumps) | Chain snapshot | 0.1–1 s calibration / 0.3 s+ | Fits mid-maturities; poor short skew | Unstable parameters | High |
| 2 | Local vol MC | Path probabilities | Deterministic σ(S,t) | Arbitrage-free surface | Moderate / seconds with repricing | Wrong smile dynamics (Hagan et al. 2002) | Forward smile flattening | High |
| 3 | SSR-parameterized what-if | Per-contract IV after spot move | Linear in log-spot | Skew, estimated R(T) | Rolling regression / µs | Empirical SSR 1–1.5 for SPX | Large moves, regime shifts | Low |
| 4 | Roll-credit probability | P(roll for credit at t*) | Stage-2 engine + spread model | Surface, quote history | Batch / seconds | None published | Accounting artifact; spreads; term structure | High |
| 5 | Extrinsic vs dividend/carry flag | Early assignment risk state | No-arbitrage bounds | Quotes, dividends, rates | None / µs | Holders often sub-optimal (PSW 2008, BW 2012) | Not a probability; HTB frictions | Low |
| 6 | Full-reval FHS / factor MC | VaR, CVaR 1d–expiry | Joint historical dynamics | History, surfaces | Batch / ~0.1–0.5 s | CCP standard | Unseen crashes; overlap | Medium |
| 6 | TIMS/SPAN grids | Worst-case loss | Fixed scenarios | Surface | None / ms | Regulatory standard | Not a probability; vol shift choice | Low |
| 6 | Delta-gamma | Approx. VaR | Quadratic P&L | Greeks | None / µs | Poor for short options | Non-monotone payoffs | Low (not recommended) |
| 8 | Reliability/Brier/PIT/DM/Acerbi–Szekely | Model calibration | Non-overlapping or HAC | Option history | Batch | Standard forecast evaluation | Low power with overlap | Medium |

## Recommendations

### (a) First release — cheap and honest
1. **RN POP from the SVI CDF**, with flat-IV lognormal POP shown alongside for continuity with Tastytrade (label which is which).
2. **RN P(touch)** for each short strike and each breakeven, closed form with the strike's smile IV.
3. **Constant-vol MC P25/P50/P75 and P(stop first)** with surface frozen in moneyness (sticky moneyness) and with τ = management date, using 20,000 paths and common random numbers; label "risk-neutral, constant-volatility."
4. **Full-revaluation stress grid:** TIMS-style ten-point spot grid × vol {−, 0, +} with IBKR-style asymmetric coupling, plus SPAN's 16 scenarios; show the worst loss.
5. **Early-assignment flag** (High/Watch/Low with dollars at stake).
6. **Greeks:** beta-weighted delta, gamma and vega by expiry bucket (7–14, 15–45, 46–90 DTE), term-damped vega.
7. **What-if with SSR control** defaulting to R = 1.5 index, R = 1 single stocks.

### (b) Second stage — needs the fitted surface and history
1. **Physical POP** from FHS (GJR-GARCH) and from a calibration-function transform of the RND; display both RN and physical with a spread band.
2. **Stage-2 path engine** (FHS joint spot/ATM-vol + SSR mapping + constant-maturity roll) for P25/50/75, P(stop first), outcome mix at 21 DTE, and their conditional P&Ls.
3. **Physical CVaR and VaR** for positions and the portfolio via a factor FHS with full revaluation, horizons 1 day, 5 days and to the management date; 50,000 paths with importance sampling and control variates.
4. **Historical episode replay** with your recorded surfaces (2018, 2020, 2024 from your option history; earlier episodes from spot and VIX only).
5. **Physical EV and return on buying power with a confidence band**; fractional-Kelly sizing hint for defined-risk positions only.
6. **Estimated SSR per underlying and tenor** replacing defaults.

### (c) Research-grade — only if validation shows a gain
1. Heston/Bates or rough-vol calibrated path engines.
2. Regime-switching overlays on the FHS engine.
3. Roll-for-credit probability.
4. Multi-asset jump/copula factor models for portfolio CVaR.

## Tooltip Labels (one sentence each)

- **POP (lognormal):** "Chance the position finishes at least $0.01 profitable at expiry if the underlying is lognormal with one implied volatility for the whole expiry and no risk premium."
- **POP (market-implied):** "Chance of finishing profitable at expiry under the risk-neutral distribution implied by today's fitted smile; for index puts this has historically overstated the chance of large drops."
- **POP (historical-model):** "Chance of finishing profitable at expiry under a GARCH-filtered resampling of historical returns scaled to current volatility; assumes the future resembles the past 30 years."
- **P(touch):** "Chance the underlying trades through this price before expiry under a constant-volatility, risk-neutral model; real paths with jumps can touch more often."
- **P25 / P50 / P75:** "Chance the position can be closed for this share of max profit at any point before [management date], from simulated paths of spot and implied volatility; mark-to-mid, ignores slippage."
- **P(stop first):** "Chance the loss limit is reached before the profit target and before [management date] in the same simulation."
- **Outcome mix at management date:** "Share of simulated paths ending in target, stop, or neither by [date], with the average P&L in each."
- **CVaR 5% (horizon h):** "Average loss in the worst 5% of simulated outcomes over h, using filtered historical scenarios with full repricing; may understate losses in events worse than any in the history."\[57\]\[58\]
- **Stress worst loss (TIMS-style):** "Largest loss across fixed spot moves of ±15% (single stocks) or +6/−8% (broad indexes) with implied volatility shifted as shown; a margin-style stress, not a probability."\[20\]
- **Early-assignment flag:** "High when the option's time value is below the upcoming dividend (calls) or below the interest on the strike (ITM puts); actual assignment depends on the holder's choices."
- **Beta-weighted delta:** "Delta expressed in units of the benchmark using historical beta; betas and correlations change in sell-offs."
- **Physical EV:** "Average P&L under the historical-model distribution, with its 90% interval; zero under the market-implied distribution by construction."

## Numbers Not to Show

- **Ross-recovered "true" probabilities:** empirically rejected (Jackwerth–Menner 2020) and theoretically misspecified (Borovička–Hansen–Scheinkman).\[6\]\[8\]
- **A percentage probability of early assignment:** holder exercise is frequently sub-optimal and friction-driven (Pool–Stoll–Whaley; Barraclough–Whaley; Jensen–Pedersen);\[16\]\[17\]\[44\] there is no honest probability, only a risk flag.
- **Risk-neutral expected value or "edge":** zero by construction under the measure used; any value is an artifact.
- **Max-loss probability for undefined-risk positions from lognormal tails:** the tail beyond the observed range is not identified; show stress losses instead.
- **Roll-for-credit probability in stage (a) or (b):** no validated method, dominated by spread and term-structure assumptions.
- **Single-number Kelly fraction for undefined-risk short premium:** depends on an unidentified tail; full Kelly over-bets under estimation error.
- **Theta/tail-loss ratio as a quality score:** no evidence; it rewards selling more tail.

## Caveats

- Retail-tool model details beyond the quoted vendor documentation are undocumented; where I say "undocumented" I found no official statement.
- The Tastytrade P50 path count conflicts between the vendor's "10,000 occurrences" and your observation and third-party replications of ~1,000.\[35\]\[37\]
- Sources disagree on whether implied risk aversion is horizon-dependent (Bliss–Panigirtzoglou) or approximately constant (arXiv 1910.14522), and on the precise empirical SSR level (1–1.5, ~1.5, 0.9–1.7).\[9\]\[13\]\[59\]
- The magnitudes I give for POP gaps, CVaR Monte Carlo errors and detectable differences are my calculations or judgement, not sourced findings; validate them on your data.
- Option Alpha's POP uses historical rather than implied volatility, so cross-tool POP comparisons are not like-for-like (Option Alpha itself notes comparisons between software are "never apples-to-apples").\[60\]

## Sources

1. [Option-Implied Risk Aversion Estimates, Working Paper 2001-15](https://fraser.stlouisfed.org/files/docs/historical/frbchi/workingpapers/frbchi_workingpaper_2001-15r3.pdf)
2. [Option-Implied Risk Aversion Estimates: Robustness and Patterns](https://www.researchgate.net/publication/228289768_Option-Implied_Risk_Aversion_Estimates_Robustness_and_Patterns)
3. [A multi-horizon comparison of density forecasts for the S&P 500 using index returns and option prices - ScienceDirect](https://www.sciencedirect.com/science/article/abs/pii/S0378426610001834)
4. [Why are Put Options so Expensive? by Oleg Bondarenko :: SSRN](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=375784)
5. [Why Are Put Options So Expensive? - EconPapers - RePEc](https://econpapers.repec.org/RePEc:wsi:qjfxxx:v:04:y:2014:i:03:n:s2010139214500153)
6. [NBER WORKING PAPER SERIES MISSPECIFIED RECOVERY Jaroslav Borovićka](https://www.nber.org/system/files/working_papers/w20209/w20209.pdf)
7. [Positive Eigenfunctions of Markovian Pricing Operators: Hansen-Scheinkman Factorization, Ross Recovery and Long-Term Pricing](https://arxiv.org/pdf/1411.3075)
8. [Does the Ross Recovery Theorem Work Empirically? by Jens Carsten Jackwerth, Marco Menner :: SSRN](https://www.ssrn.com/abstract=2960733)
9. [Skew stickiness Diamond trees and forests Computing the SSR AFV SSR](https://mfe.baruch.cuny.edu/wp-content/uploads/2024/03/SSR-BBQ-2023.pdf)
10. [Skew and implied leverage effect: smile dynamics revisited Vincent Vargas ENS,](https://arxiv.org/pdf/1311.4078)
11. [Variance Dynamics - An empirical journey](https://arxiv.org/pdf/1507.00846)
12. [SPX-VIX Risk Computations Via Perturbed Optimal Transport](https://arxiv.org/pdf/2603.10857)
13. [Skew-stickiness under rough volatility](https://mfe.baruch.cuny.edu/wp-content/uploads/2024/03/SSR-QuantMinds-2023.pdf)
14. [Smile-aware Greek conventions: sticky='strike' / sticky='delta' on VolSurface Greeks · Issue #35 · marwinsteiner/pysvi · GitHub](https://github.com/marwinsteiner/pysvi/issues/35)
15. [Page 1 of 24 Quantitative Strategies](https://www.math.columbia.edu/~smirnov/Derman.pdf)
16. [Failure to Exercise Call Options: An Anomaly and a Trading Game by Veronika Krepely Pool, Hans R. Stoll, Robert Whaley :: SSRN](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=972613)
17. [Early option exercise: Never say never](https://ideas.repec.org/a/eee/jfinec/v121y2016i2p278-299.html)
18. [Early exercise decision in American options with dividends, stochastic](https://arxiv.org/pdf/1612.03031)
19. [Early exercise decision in American options with dividends, ...](https://scaillet.ch/pdfs/cogasc.pdf)
20. [How Portfolio Margin Works and What It Actually Requires](https://www.optionstrading.org/blog/how-portfolio-margin-works/)
21. [Ovation Platform](https://www.theocc.com/getcontentasset/8b47ee4d-b019-4dc0-b9d1-98227c819f15/dfc3d011-8f63-43f6-9ed8-4b444333a1d0/ov_rbh-cpm_user_guide.pdf)
22. [4210\. Margin Requirements](https://www.finra.org/rules-guidance/rulebooks/finra-rules/4210)
23. [Span Methodology](https://www.cmegroup.com/clearing/files/span-methodology.pdf.)
24. [Span Methodology](https://www.cmegroup.com/clearing/files/span-methodology.pdf)
25. [SUBMISSION COVER SHEET](https://www.cmegroup.com/content/dam/cmegroup/market-regulation/rule-filings/2024/10/24-425.pdf)
26. [CME SPAN 2 Margin Framework](https://www.cmegroup.com/clearing/files/cme-span-2-margin-framework.pdf)
27. [tastytrade platform glossary : tastytrade](https://support.tastyworks.com/support/solutions/articles/43000435395-platform-glossary)
28. [Probability of Profit Calculator — Options POP - OptionsMath](https://www.optionsmath.com/probability-of-profit/)
29. [Value-at-Risk and Expected Shortfall for the major digital currencies](https://arxiv.org/pdf/1708.09343)
30. [Closed-Form Transformations from Risk-Neutral to Real-World Distributions](https://www.researchgate.net/publication/222821855_Closed-Form_Transformations_from_Risk-Neutral_to_Real-World_Distributions)
31. [Why are Put Options So Expensive?∗ Oleg Bondarenko† Abstract](https://www3.gmu.edu/schools/vse/seor/studentprojects/graduate/2009Fall/ISG/Investment_Optimization/Resources_files/Bondarenko-Puts.pdf)
32. [ThinkOrSwim's Probability OTM, ITM, of Touching Calculator](https://www.elitetrader.com/et/threads/thinkorswims-probability-otm-itm-of-touching-calculator.244993/page-5)
33. [Understanding probability on thinkorswim](https://www.elitetrader.com/et/threads/understanding-probability-on-thinkorswim.343267/)
34. [How Probability of Profit Is Computed and Checked](https://optionscanner.ai/learn/probability-of-profit-methodology)
35. [tastytrade, Inc.](https://tastytrade.desk.com/customer/en/portal/articles/2615507-p50)
36. [Replicating Tastywork’s Probability of 50% Profit for Option Trades](https://nosparechange.medium.com/replicating-tastyworks-probability-of-50-profit-for-option-trades-5138c0bf31af)
37. [Replicating Tastywork’s Probability of 50% Profit for Option Trades](https://zachlim98.github.io/me/2021-02/tastyworks-p50)
38. [Behind the Scenes of Over 50 Million Options Backtests](https://orats.com/blog/behind-the-scenes-of-over-50-million-options-backtests)
39. [Behind the Scenes of Over 50 Million Options Backtests](https://www.financialservicesreview.com/news/behind-the-scenes-of-over-50-million-options-backtests-nwid-1372.html)
40. [Sticky Strike vs Sticky Delta - Risk Hub](https://riskhub.org/blogs/sticky-strike-vs-sticky-delta)
41. [Dynamics of the Implied Volatility Surface. Theory and Empirical Evidence](https://archivo.alde.es/encuentros.alde.es/anteriores/xiiieea/trabajos/pdf/079.pdf)
42. [Scenario Analysis](https://www.ibkrguides.com/traderworkstation/scenario-analysis.htm)
43. [ThinkorSwim Tutorial: Ultimate Guide for 2026](https://optionstradingiq.com/thinkorswim-tutorial-ultimate-guide-for-2024/)
44. [Early Exercise of Put Options on Stocks - BARRACLOUGH - 2012 - The Journal of Finance - Wiley Online Library](https://onlinelibrary.wiley.com/doi/abs/10.1111/j.1540-6261.2012.01752.x)
45. [Option Investor Rationality Revisited:](https://scholarworks.indianapolis.iu.edu/server/api/core/bitstreams/d7cd12b0-e2b7-4fb4-b208-9d841c751f33/content)
46. [CCP initial margin models in Europe - European Central Bank](https://www.ecb.europa.eu/pub/pdf/scpops/ecb.op314~afc6d2980c.en.pdf)
47. [Portfolio margin](https://en.wikipedia.org/wiki/Portfolio_margin)
48. [Qualifying for Portfolio Margin: Key Requirements](https://longbridge.com/en/academy/options/blog/qualifying-for-portfolio-margin-what-advanced-traders-need-to-know-100494)
49. [Backtesting Value at Risk and Expected Shortfall (Simona Roccioletti (Auth.) ) (Z-Library)](https://www.scribd.com/document/814479837/Backtesting-Value-at-Risk-and-Expected-Shortfall-Simona-Roccioletti-Auth-Z-Library)
50. [Backtesting Value-at-Risk and Expected Shortfall Across Calm ...](https://annalsofappliedsciences.com/index.php/aas/article/download/53/53)
51. [What is the CVaR Metric in Options Trading?](https://optionstradingiq.com/cvar-metric/)
52. [Analyze - thinkorswim Learning Center](https://tos.mx/center/howToTos/thinkManual/Analyze)
53. [Schwab thinkorswim Options Tools Guide: Everything You Should Use](https://optionspilot.app/blog/schwab-thinkorswim-options-tools-guide)
54. [OptionStrat Guide: Visualize Options Strategies Before You Trade — Oyamori](https://oyamori.com/learning/optionstrat-guide/)
55. [ORATS Data API](https://orats.com/data-api)
56. [Methodology](https://docs.orats.io/backtest-api-guide/backtester-methodology.html)
57. [Expected Shortfall (CVaR): Formula & VaR Comparison](https://ryanoconnellfinance.com/expected-shortfall-cvar/)
58. [CVaR (Expected Shortfall): Definition, Formula and Example](https://www.insiderfinance.io/finance-glossary/conditional-value-at-risk-cvar)
59. [Option-based Equity Risk Premiums](https://arxiv.org/pdf/1910.14522)
60. <https://docs.optionalpha.com/technical-documentation/calculations/probability-theory>
