# Research D: prompt for Claude research mode

Paste everything below the line into a research-mode conversation. The prompt is
self-contained; it needs no access to the local repos.

---

I am building a self-hosted, single-user options-analysis application for my own trading. I
need a survey of probability and risk methods for option positions, with a recommendation of
what to implement and in what order. Please research the academic and practitioner literature
and the documentation of existing tools, and cite sources for every claim that is not
elementary.

## Context

- **Trader:** experienced, sells premium. Main horizon 30-45 DTE, also 7-14 DTE and up to
  90 DTE; 0DTE is secondary. Typical structures: short puts and calls, strangles, vertical
  spreads, iron condors, later calendars and diagonals. Positions are usually managed before
  expiry: closed at a share of maximum profit, rolled, or closed at a loss limit.
- **Underlyings:** US equity and ETF options (American exercise, discrete dividends) and
  index options (European, cash-settled). Futures options later.
- **Data available live:** the full option chain with quotes, so a volatility surface is
  fitted per snapshot (SVI per expiry, SSVI as fallback) and gives a risk-neutral density per
  expiry. Daily underlying history for 30 years. Underlying 1-minute history and a 1-minute
  option history for a set of liquid underlyings over several years for validation.
- **Compute:** one home server, Python with numpy and scipy. An interactive answer should
  take well under a second for a four-leg position; a batch answer may take seconds.
- **What the broker platform shows** (Tastytrade), which I can already reproduce or nearly so:
  POP as a lognormal probability of profit at expiry with one flat IV per expiry; P50
  (probability of reaching 50% of maximum profit before expiry) and CVaR (mean loss in the
  worst 5% at expiry) from a simulation of about 1,000 lognormal paths with constant
  volatility. These are the baseline. I want to know what is better and how much better.

## Questions

1. **Probability of profit at expiry.** Compare lognormal with flat IV, the risk-neutral
   density implied by the fitted smile, and real-world (physical-measure) estimates:
   historical or bootstrapped return distributions, GARCH-type filtered simulation, and
   methods that convert a risk-neutral density to a real-world one (risk-premium adjustment,
   Ross recovery and its critiques, empirical calibration). What does the evidence say about
   the size and sign of the difference for out-of-the-money index and single-stock options,
   and about which estimate is best calibrated out of sample?

2. **Probabilities before expiry (path-dependent).** Probability of touching a strike or a
   breakeven, of reaching a profit target (25%, 50%, 75% of maximum profit) before expiry or
   before a management date (for example 21 DTE), and of hitting a loss limit first (for
   example two times the credit). These depend on the option's value along the path, so they
   need a joint model of the underlying and implied volatility. Which model classes are used
   for this (constant volatility, local volatility, Heston and other stochastic volatility,
   SABR, jump diffusion, regime switching, empirical spot-volatility regression such as
   skew-stickiness ratios, historical block bootstrap of joint spot and IV moves)? For each:
   what it captures, calibration effort from one chain snapshot plus history, compute cost,
   and known failure modes. Is there published evidence on how well any of them predicts
   "reached 50% of maximum profit" frequencies for short premium positions?

3. **Smile dynamics for what-if.** When the underlying is moved by x% in a scenario, how
   should implied volatility per contract respond? Compare sticky strike, sticky delta or
   moneyness, sticky local volatility, and empirically fitted spot-volatility betas, by
   underlying type and horizon. What is a defensible default and what are sensible user
   controls?

4. **Probability of rolling for a credit.** A trader with a tested short option wants to know
   the chance that, at a future date, the position can be rolled out in time (same or better
   strike) for a net credit. Is there any literature or tool that estimates this? If not, how
   would one define it precisely and compute it from a joint spot and surface model, and what
   would make the number misleading?

5. **Early assignment.** Methods to estimate the probability or risk of early assignment for
   short American calls (dividend-driven) and short in-the-money puts (carry-driven): the
   exact optimal-exercise conditions, simple practitioner rules (extrinsic value against the
   dividend, extrinsic value against carry), and empirical evidence on how often holders
   exercise optimally or early. What is a good screen-level indicator?

6. **Tail risk for a position and a portfolio.** VaR and CVaR (expected shortfall) for a
   portfolio of option positions across several correlated underlyings, with horizons from one
   day to expiry. Compare delta-gamma approximations, full revaluation under historical
   simulation, filtered historical simulation, Monte Carlo with a factor model, and stress
   scenarios (fixed shocks of spot and volatility, historical episodes). How should the
   volatility shock be tied to the spot shock? How do broker portfolio-margin methods (OCC
   TIMS, SPAN for futures options) define their scenarios, and can they serve as a stress
   template? How many paths or scenarios are needed for a stable 5% CVaR, and which
   variance-reduction methods matter here?

7. **Other risk and return measures worth showing.** For example expected value under a
   real-world density, return on buying power, Kelly-type sizing for negatively skewed
   payoffs, probability-weighted maximum loss, theta against tail-loss ratios, beta-weighted
   delta, gamma and vega exposure by expiry bucket. Which are common in professional tools
   and which have evidence of being useful? Which are known to mislead?

8. **Validation.** Given a multi-year 1-minute option history, how should each probability be
   validated: calibration curves and reliability diagrams, Brier score, probability integral
   transform, coverage tests for CVaR (for example the Acerbi-Szekely tests), overlapping
   sample corrections, regime splits. What sample sizes are needed before a difference
   between two models is meaningful?

9. **What existing tools do.** For OptionStrat, Tastytrade, thinkorswim (Analyze tab and
   probability of touching), Interactive Brokers (Probability Lab, Risk Navigator), ORATS,
   OptionNet Explorer, Option Alpha and similar: which probabilities and risk numbers they
   show and, where documented, which model is behind each.

## Output wanted

- A table of methods by question: what it estimates, model assumptions, inputs needed,
  calibration and compute cost, evidence of accuracy, failure modes, and difficulty to
  implement (low, medium, high).
- A recommended set in three stages: (a) first release, cheap and honest; (b) second stage,
  needs the fitted surface and history; (c) research-grade, only if validation shows a gain.
- For each number that would be shown to the user, one sentence stating what it assumes, in
  wording suitable for a label or tooltip.
- A list of numbers that should not be shown because no honest estimate exists, with the
  reason.
- A validation plan against the option history.
- Clear separation between what the sources establish and what is your own judgement, and a
  note wherever sources disagree.
