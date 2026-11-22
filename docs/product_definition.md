# Self-3-Trader: initial product definition

Status: draft 1, 2026-11-20, updated through 2026-11-22. Working name; the repo is
`tradertoolbox`. The technical requirements built on this document are in
`docs/technical_requirements.md`, and the work packages in `docs/implementation_plan.md`.

## 1. Purpose

A self-hosted, personal-use application that puts the options trader's analysis tools in one
place and lets them work together: build a trade, see its payoff and probabilities, put it on
a chart, stress it, compare it with the volatility picture, and later scan, monitor and
backtest.

The data comes from the user's own broker API (Tastytrade first). There is no hosted service
and no commercial intent. The code is likely to be open-sourced.

**First replacement targets:** OptionStrat (trade building and visualisation) and the part of
TradingView used for underlying charting. Both are available for side-by-side testing.

## 2. User

One experienced, self-directed options trader. Premium selling at 30-45 DTE first, also
7-14 DTE and up to 90 DTE. 0DTE is secondary. Equity and index options first, futures options
later.

The application is single-user. Usefulness to beginners is welcome but drives no requirement.

## 3. Principles

1. **Read-only first.** No order is sent until the read-only product is mature. Order entry
   stays a possible far-future feature, so the ticket model should not rule it out.
2. **Bring your own credentials.** Each installation uses its owner's broker access. The
   repository never contains market data.
3. **Credentials stay on the backend.** The browser and phone never hold broker secrets.
4. **Features are tiered by the data they need** (section 5). A feature states its tier and
   is hidden or degraded when the data is absent.
5. **One pricing core.** One implementation of pricing, IV, Greeks and forwards serves every
   tool, so the chain, the payoff graph and the simulations agree.
6. **Every probability names its model.** Where the platform's number can be replicated
   (POP, P50, CVaR), the replica is offered beside the application's own model, labelled.
7. **Tools link to each other.** A trade ticket is the shared object: any tool can open it,
   and any tool that produces a trade idea produces a ticket.
8. **Tastytrade only for now.** Broker access sits behind one interface. The list of what the
   application requires from a broker API is written down as features mature, and other
   brokers are judged against that list.

## 4. Deployment

| Part | Where | Notes |
|---|---|---|
| Backend | Self-hosted machine, always on: the development machine at first, a dedicated Linux machine with the data stores later; no container for now | Python. Holds broker credentials, the one streaming connection, the local database, numerics, alerts, the LLM tool interface. It takes over the recording job of `streamer`, so one session serves both and subscriptions are budgeted in one place. It has full access to the T3 store and the historical underlying data; derived quick-access copies can be built when needed |
| Frontend | Laptop browser | Svelte and TypeScript, as in the backtester viewer |
| Phone | Later, reduced functions | The same web application in a container or as an installable web page |
| Network | WireGuard VPN between devices and backend, provided by a separate device | The backend still authenticates its one user from the start; hardening is not an early priority |

## 5. Data tiers

| Tier | Data | Available to |
|---|---|---|
| T1 | Live broker data: chains, quotes, Greeks, open interest, market metrics, positions, and underlying candles (daily for 31 years, 1-hour for about 9 months, 15-minute for about 90 days, 5-minute for about 28 trading days, 1-minute for about 6 trading days). Daily candles also carry an implied-volatility value per underlying since November 2016 (section 6.5). Option contracts have no daily history and only two weeks of continuous 15-minute marks | Anyone with a broker key |
| T2 | History the backend records itself from the day it is installed: daily chain and surface snapshots, open interest, IV series, alerts log | Any installation, growing with time |
| T3 | An external historical store: the Polygon-based 1-minute options store of `bt_dataset`, the `volcalc` lake, and the backtester. A compact IV-history set is derived from it for quick access (section 6.5) | Installations that have such a store |

## 6. Scope by release

Order is a proposal. Each release is usable on its own.

| Release | Content | Tier |
|---|---|---|
| R0 Foundation | Backend skeleton, broker connection, streaming fan-out to the browser, instrument model that fits equity, index and futures options, pricing core, backend login | T1 |
| R1 Trade workbench | Option chain in the Tastytrade style with expected move; ticket builder; payoff graph and table in the OptionStrat style; what-if on price, date and volatility; POP, P50, CVaR (section 6.4, stage a); saved tickets; watchlists; earnings and ex-dividend dates in the chain and ticket; probability of touch; early-assignment flag; a context snapshot stored with each saved ticket; buying power as a Reg-T estimate | T1 |
| R1b Path numbers and stress | Probability of reaching 25, 50 or 75% of maximum profit before a management date, probability of hitting the loss limit first, and the outcome mix at that date; stress grid of fixed price and volatility shocks; smile-dynamics selector for what-if (section 6.4, stage a) | T1 |
| R2 Chart | Underlying chart (section 6.1); the ticket drawn on the chart (strikes, breakevens, expected range, payoff profile) | T1 |
| R3 Volatility | **IV against realised volatility first** (section 6.5), with volatility cones; skew and term structure; IV rank and percentile; expected range and its estimated change over time; bid/ask width analysis; open interest by strike, also on the chart; earnings implied move against past earnings moves; expected-move scorecard per symbol | T1, T2, T3 |
| R4 LLM tools | The backend's functions exposed as read-only tools over MCP; Claude first; tools at task level (position review, roll candidates) rather than raw chain dumps; a daily management digest as an MCP resource | T1 |
| R5 Positions and risk | Read-only positions; delta and gamma risk over time; CVaR per position and portfolio; assignment risk; campaigns (the legs, rolls and assignments of one trade grouped from the transactions, with net credit, total result and days in trade); roll comparator (candidates side by side with credit, breakevens, days added, change in delta, buying power and probabilities); status of each position against the management rules (DTE limit, profit target, loss limit); earnings and ex-dividend dates against open positions; concentration and buying-power view; scenario grid for the whole portfolio | T1, T2 |
| R5b Review | Performance by strategy, tag, DTE, delta and IV rank at entry and by exit reason; decision log per ticket version; fill price against the mid at fill time | T1, T2 |
| R6 Scanners and alerts | Scanners with pre-filter on market metrics and rotating chain sweeps, including IV against realised volatility as a criterion (section 6.5); alerts and notifications, including the management rules of R5; custom markers on watchlists | T1, T2 |
| R7 History | Correlation; simulation of positions and management tactics on history; hand-off of a ticket to the backtester | T3 |
| R7b Probabilities, stage b | Historical-model POP; path numbers from joint resampling of price and implied volatility; CVaR and VaR over 1 day, 5 days and to the management date; replay of historical episodes; validation of every probability against the T3 store (section 6.4) | T1 to T3 |
| Recorder | The backend takes over the `streamer` recording job, as a backend module. Low priority (decided 2026-11-20): until then the old streamer keeps running in parallel on its own session, and the backend uses one session and stays inside the subscription limits. The streamer is retired once the module's output is shown equal | T2 |
| Later | Portfolio optimisation, futures options, phone, order entry, other brokers; estimated GEX (speculative and unreliable, section 9) | |

Decided 2026-11-22 after research A and D (`docs/research/reports/`): R1b, R5b and R7b and
the items from the reports were added; GEX moved to "Later"; IV against realised volatility
has priority over campaigns and the roll comparator, which lean towards backtester territory.

### 6.1 Chart requirements (R2)

These replace the part of TradingView in daily use.

- Candlestick chart, mainly at 1-day and 15-minute resolution. Offered: 1, 5, 15 and 30
  minutes, 1 and 4 hours, 1 day. Weekly and monthly come later (the broker has both since
  1993). Intraday history beyond the broker's depth comes from the local store.
- Intraday charts show the 24-hour feed (decided 2026-11-22: trading hours are being
  extended). The hours outside the regular session get a light background tint, early and
  late hours lightly and the overnight session a little more.
- Where the broker's bars of a resolution do not start at the regular open, the backend
  builds that resolution from finer bars (decided 2026-11-22). Today that applies to 1-hour
  and 4-hour bars, built from 30-minute bars.
- The time axis shows exchange time by default and can be switched to UTC or local time.
  Daily charts always show the trading date. Offsets are taken per instant from the time-zone
  database, because regions change their clocks on different dates.
- Drawings are stored as UTC instants. A drawing made on the 1-day chart is anchored at the
  market open, so it lands on the opening bar of an intraday chart.
- SMA lines of 15, 50, 100 and 200 bars; volume; RSI; ATR on demand.
- Ruler: measure a move between two points in price, percent and time.
- Horizontal lines for price levels, including the short strikes of open positions.
- Vertical lines for moments in time.
- Trendlines.
- **Automatic range marking from a template** (not available in TradingView): the opening
  range or any other defined range of the current day drawn as horizontal lines, and the
  template's critical moments of the day drawn as vertical lines.
- Drawings are saved per symbol.

### 6.2 Trade workbench reference (R1)

OptionStrat's builder and payoff views are the reference. Its optimiser and flow pages are
not in use and are not targets.

### 6.3 Screen layout (decided 2026-11-21 and 2026-11-22 from the mock-ups)

All screens:

- Panels sit close together: small gaps between them and small margins at the window edges.
- Instructions and explanations sit behind a small information icon in the header of their
  panel, the same way everywhere: option chain, probabilities, payoff, chart layers, range
  template. A legend that is needed to read a picture stays visible: the payoff panel keeps
  one legend line above the graph, and shows a different one in table mode (profit, loss,
  the price nearest the current one, the IV shift).
- The window itself does not scroll at laptop size. The top bars and the status bar stay in
  place, and each area that can overflow scrolls on its own. On a narrow window the areas
  stack and the page scrolls as a whole.

Workbench:

- Option chain on the left; ticket and probabilities on the top right; payoff below them.
- The chain fills the height of the window and scrolls inside its panel. Its header, the row
  of the open expiration and the column titles stay visible while the strikes scroll, and it
  opens with the at-the-money line in view. The right side (ticket, probabilities, payoff)
  scrolls separately when it does not fit; the payoff graph takes the height that is left.
- A divider between the chain and the right side is dragged to widen the chain. The default
  columns are delta, bid and ask per side, with strike and IV in the middle. Widening reveals
  open interest, volume, theta and gamma, in that order.
- All listed strikes are shown; there is no "every n-th strike" filter, because strike
  spacing is not uniform across roots.
- The at-the-money position is a thin yellow or orange line between two strike rows and
  takes no row of its own. The expected range is a light background tint on the strike and
  IV cells only; the quantity marks stay outside it.
- In the chain, the instructions and the legend share one information icon in the header, so
  the chain has the full height.
- The symbol box is wide enough for long symbols.
- Several expirations can be open at once, and a ticket can mix expirations.
- The quantity mark of a bought or sold contract (+1, −2) opens a small pop-up that raises
  or lowers the number of contracts, in the chain and in the ticket.
- A ticket row shows the signed quantity (the colour gives the side, with no separate
  buy/sell word), expiration, strike, type, delta, theta, bid, mid and ask. The ticket also
  shows its total extrinsic value.
- A ticket can have more than two breakevens. The breakevens box then takes width from the
  boxes beside it, shows two values to a row without breaking a line, and may use a smaller
  type size.
- The ticket has one editable name. It is filled automatically from the strategy, the symbol
  and the expiration until the user types a name; a control returns it to the automatic
  name. Save stores the ticket under that name and Load recalls a saved ticket.
- The limit price is an editable field with minus and plus buttons. Untouched, it follows the
  market mid. An entered value stays, is marked by an outline, and becomes the entry price of
  the payoff; a "MKT" control lets it follow the market again.
- Stock can be a leg of a ticket. Held positions, stock or options, can be brought into the
  ticket through a control and are marked as held. A held leg shows its purchase price by
  default, with a small switch to the current price; the switch also sets whether its profit
  and loss counts from the purchase price or from now. The ticket's net and limit prices
  cover the new legs only.
- Probabilities are one column with a switch between the models, not two columns.
- The payoff panel can be maximised. It then hides the chain and carries the ticket and
  probability figures itself.
- The line at expiry is drawn thinner than the line of the selected date.
- Moving the pointer over the payoff graph shows a dotted vertical line with the price, the
  probability of the underlying being below or above it at expiry and on the selected date,
  and the profit and loss there.
- A range control limits how far the price axis reaches, which also limits the P/L axis.
  Where the expiry line leaves the graph still rising or falling, a mark says so ("loss
  unlimited", or the value it reaches at a price of zero).

Chart:

- The chart is its own screen and window, not a panel of the workbench.
- Volume is drawn in the price pane, not in a pane of its own.
- The payoff profile at the right edge is a layer that can be switched on and off like an
  indicator. It is scaled to the prices in view, so the price scale is its range control and
  unlimited profit or loss needs no extra control or mark.
- The ticket layers are switched from the legend line above the chart: each legend entry is
  the switch of its layer. There is no separate layers panel.
- The chart takes the height of the window; the side panel scrolls on its own.

### 6.4 Probability and risk numbers (decided 2026-11-22 after research D)

Source: `docs/research/reports/Probability and Risk Methods for a Self-Hosted Short-Premium
Options Analyzer.md`. The report's own finding is that the platform numbers are honest
risk-neutral figures, and that the larger gain comes from adding a real-world estimate beside
them than from a more elaborate risk-neutral model.

| Stage | Number | Model, as named on screen | Data |
|---|---|---|---|
| a | POP, platform replica | Lognormal with the expiry IV and a flat drift | T1 |
| a | POP, market-implied | Risk-neutral distribution of the fitted smile (closed form from SVI) | T1 |
| a | CVaR 5% and expected P/L at expiry, own | Integral over the same distribution. No simulation is needed for numbers at one date, so they carry no sampling noise (own addition; the report simulates them) | T1 |
| a | P50 and CVaR, platform replica | About 1,000 lognormal paths at the IV index | T1 |
| a | Probability of touch, per short strike and breakeven | Constant volatility at the strike's own IV, risk-neutral, closed form | T1 |
| a | Reaching 25, 50 or 75% of maximum profit before the management date; loss limit first; outcome mix with the mean result of each outcome | Constant-volatility simulation, 20,000 paths, labelled as such | T1 |
| a | Stress worst loss | Fixed price grid (±15% for stocks, +6/−8% for broad indexes) with volatility shifts tied to the price move. A margin-style stress, not a probability | T1 |
| a | Early-assignment flag: high, watch or low, with the dollars at stake | Extrinsic value against the dividend (calls) or against the interest on the strike (in-the-money puts) | T1 |
| b | POP, historical model | GARCH-filtered resampling of daily returns | T1 (31 years of daily candles) |
| b | Path numbers with moving volatility | Joint resampling of price and at-the-money IV, the smile moved by a skew-stickiness ratio | T1 for a first version (the broker's daily IV since 2016, section 6.5); T3 for the precise one |
| b | CVaR and VaR for positions and the portfolio | Filtered historical scenarios with full repricing and a market factor | T1 to T3 |
| b | Skew-stickiness ratio estimated per underlying | Regression on recorded IV | T2, T3 |
| c | Stochastic-volatility and jump models; probability of rolling for a credit | Only if validation on T3 shows a gain | T3 |

Rules taken from the report:

- **Not shown:** a percentage probability of early assignment; risk-neutral expected value
  presented as an edge; a Kelly fraction for undefined-risk positions; a theta-to-tail-loss
  score; probabilities from Ross recovery.
- **Stage b numbers appear only after validation** against the T3 store: reliability curve,
  Brier score and PIT test, with entries split by volatility regime. The report estimates that
  a few years of data can separate models that differ by 5 percentage points of POP or more,
  and not by 1 or 2.
- **Path count** is a setting. The replica defaults to about 1,000 paths, as the `tt`
  captures show; own simulations default to 20,000, because 1,000 paths leave only 50 in the
  5% tail. Two separate tests apply once the code exists: repeated runs with different seeds
  show how much a number moves at a given path count (no history needed), and the comparison
  with history shows whether the model is right. More paths cannot repair a wrong model.

Decided 2026-11-22:

- The model switch shows the market-implied model first and the platform replica second.
- POP leads on the ticket, because it can always be computed. A better number may replace
  it once it exists and is validated.
- Management defaults, used by the path numbers, the rule status of R5 and the alerts:

| | Undefined risk | Defined risk (credit spreads, iron condors) |
|---|---|---|
| Entry | About 45 DTE (the nearest listed expiration) | About 45 DTE |
| Profit target | 50% of maximum profit | 50% of maximum profit |
| Management date | About 21 DTE: close, or roll if it can be done for a credit | About 7 DTE: exit. Earlier when the assignment flag turns high. European-style options: no forced exit |
| Loss limit | None by default | None by default |

  With no loss limit set, "loss limit first" is not shown, and the outcome mix has two
  outcomes: target reached before the management date, or not.

Not verified: the report's compute times, the size of the gap between risk-neutral and
real-world POP, and the default skew-stickiness ratio (1.5 for indexes) are its author's
estimates. They are measured on own data before they are relied on.

### 6.5 IV against realised volatility: what data it needs (measured 2026-11-22)

Realised volatility needs only daily underlying candles, which the broker gives for 31 years
for any symbol. Implied volatility has three sources of rising precision:

| Source | What it gives | Limits |
|---|---|---|
| `/market-metrics` (T1) | Current IV index, 30-day IV, HV 30/60/90, IV minus HV, IV rank and percentile, for many symbols in one call | Current values only. Enough for a scanner filter on today's IV minus HV |
| `impVolatility` on daily candles (T1) | A daily IV series per underlying since 2016-11-14 (or since listing), for every symbol tried: SPY, SPX, AAPL, IWM, GDX, SLV, SOFI | Undocumented definition. A hole in 2023 for stocks and ETFs (SPY 2022-12-30 to 2023-08-11; SPX has none). Noisy on less liquid names |
| Own surfaces from option history (T3, the `volcalc` lake) | IV matched to the exact DTE and delta, term structure, skew, and realised volatility over the option's exact remaining life | Per root: option trades, underlying minutes and quotes for validation have to be downloaded and processed. The lake holds SPY, IWM, SLV, GDX and SOFI; more roots are being added |

Comparison of the broker's daily value with the lake's 30-day at-the-money IV (last snapshot
of the day), 770 to 1,367 common days per root:

| Root | Broker minus lake, median (vol points) | Correlation of levels | Days with IV above 20-day RV: broker / lake | Same answer |
|---|---|---|---|---|
| SPY | +3.0 | 0.98 | 86% / 68% | 82% |
| IWM | +2.4 | 0.98 | 78% / 61% | 83% |
| GDX | +2.1 | 0.96 | 61% / 51% | 88% |
| SLV | +2.6 | 0.88 | 62% / 53% | 77% |
| SOFI | +5.3 | 0.62 | 62% / 52% | 83% |

Consequences:

- The display and the scanner criterion can start on broker data alone, for any symbol, with
  ten years of history.
- The broker's value sits 2 to 5 points above an at-the-money IV, probably because it is an
  index over all strikes and so includes the skew (not verified). A raw "IV above RV" test
  therefore depends on which IV is used. The criterion is expressed against the symbol's own
  history of the same series (percentile or z-score of IV minus RV), never across series.
- The conditions worth scanning for (which DTE, which delta, around which events) come from
  the `volcalc` study, not from this series.
- The broker's backtester API cannot supply IV history. Its responses hold only strategy P/L,
  the underlying price and, for a simulated trade, the price and delta of the given legs.
- **IV-history set (T3):** one compact Parquet file per root, derived from the lake's
  surfaces: per day and per intraday snapshot, at-the-money IV at fixed maturities (7, 14,
  30, 45, 60, 90 days), model-free IV, skew measures and realised-volatility windows. It
  serves the displays here, stage b of section 6.4 and the studies of section 11.2.

Still to check: whether the current IV index of `/market-metrics` is the same series as the
candle value; the time of day at which the daily candle value is taken (on 2026-11-20 it
matched the after-hours value, 3.5 points below the last regular-hours hour).

### Features that need evaluation before they get a release

- Probability of rolling for a credit: research D found no published method and calls the
  number easy to misread, because a roll credit adds time and risk without changing expected
  value. It is stage c. The roll comparator of R5 shows the facts of each candidate instead.
- Buying-power effect of a ticket from the broker. The API has
  `POST /margin/accounts/{account}/dry-run`, which creates no order. The docs do not say
  whether the `read` scope is enough. Decided 2026-11-22: not tried in the current phase,
  because most of the account is held at another broker for now and the numbers would not be
  representative. The ticket uses a Reg-T estimate from the margin model of `backtester`.
  The dry-run test stays on the to-do list.
- Bridge to the broker's backtester API (`backtester.vast.tastyworks.com`; documented, scope
  and coverage not stated). Of little use to an installation with T3, but it would give T1
  installations a historical check.
- Strategy optimiser (rank structures for a target price and date, as OptionStrat does).
- Option price charts: the broker gives candles for single contracts; history depth per
  contract has to be measured.

## 7. LLM integration

Jobs, in the user's order: find opportunities from data and natural-language input; evaluate,
comment on and criticise trades and positions; research tasks; natural-language queries
against scanners and what-ifs.

Design rules:

- The LLM calls the application's tools and never computes prices or probabilities itself.
- Tools are read-only.
- First client is Claude through MCP, so no chat UI has to be built at the start. An in-app
  chat and a self-hosted model can follow on the same tool layer.
- A hosted model may see positions and transactions, but only when the user switches the
  account tools on (decided 2026-11-21). With them off, nothing else is limited.

## 8. Not in scope

- A hosted or multi-user service.
- Order entry (until the read-only product is mature).
- A full TradingView replacement: scripting language, community indicators, non-broker data.
- Market-wide options flow and unusual-activity feeds (not reachable within the broker's
  streaming limits).
- Teaching content.
- Shipping or sharing market data.

## 9. Known constraints

Measured facts are in `docs/research/B_broker_api_capability.md`.

- **A backend is required.** The REST API answered 403 to a request carrying a browser
  origin; the same request without it succeeded.
- **The current OAuth grant is read-only** (scope `read`), which enforces principle 1.
- **Streaming limits (Tastytrade):** 5 concurrent sessions, 25,000 subscriptions per session,
  100 candle subscriptions. The SPX chain has 31,158 contracts and SPY 12,326, so chain views
  subscribe by expiration and strike window and scanners sweep in rotation. The recorder
  takes about 7,800 subscriptions.
- **No historical option data from the broker.** A contract has no daily candles, sparse
  trade candles, and two weeks of 15-minute marks. T2 and T3 exist for that reason.
- **15-minute underlying history is about 90 days** from the broker; longer history comes
  from the local underlying store.
- **Positions carry no marks or Greeks**; both come from the stream.
- **GEX is an estimate** from open interest with an assumed dealer side. Open interest updates
  once a day. Research A cites studies on exchange trade records in which market-maker gamma
  was usually positive and its measured effect on volatility small, and one in which the
  "gamma flip" level was ambiguous. Open interest by strike is a fact; signed GEX is shown
  only as a labelled convention.
- **Platform probabilities are convention-bound.** Per `tt/FINDINGS.md`: POP uses the expiry
  IV, the P50 and CVaR simulation uses the IV index, about 1,000 paths (the help centre says
  10,000; 83 captured histograms say 1,000); neither is shown for index options or 0DTE.
- **The Tastytrade account is not the whole account for now.** Most positions are held at
  another broker, so account figures from Tastytrade (positions, margin, buying power)
  are partial. R5 accepts the partial view (decided 2026-11-22).
- **Index options:** no underlying quote (trades only), European exercise, cash settlement,
  forward from put-call parity.
- **Licensing:** market data is for personal, non-professional use; a personal OAuth grant
  covers the owner's account only. The full API terms must be read before publication.
- **Charting library:** Lightweight Charts has no built-in drawing tools, indicators or
  timezone support. The libraries that have all three are closed to this project by licence
  (`docs/research/C_charting.md`). Drawing tools come from the community plugin
  `lightweight-charts-drawing`, confirmed by the prototype of 2026-11-22. Consequences found
  there: future whitespace in a series of its own; a session switch, because the broker's
  intraday feed runs around the clock; a filter for bad prints; drawings stored as UTC
  instants. The time model (zones, daylight saving, sessions, resolutions) was tried there as
  well: section 11 of the charting note.

## 10. Reuse from existing projects

Detail is in `docs/research/E_code_audit.md`.

| Source | What |
|---|---|
| `tt` | REST and DXLink client (the base for the broker module), fake broker for offline tests, payoff and breakeven functions, the POP, P50 and CVaR rules (prose only; no code implements them yet), capture set for validation |
| `volcalc` | Price, IV, Greeks, forward from parity, rates, dividends, clocks, SVI and SSVI surface with density and ITM probability, model-free IV |
| `backtester` | Expected move, margin model with a tastytrade preset, early-assignment rule; viewer tooling, design tokens and chart primitives; strategy simulation for R7 |
| `streamer` | Candle download and day finalisation; the recording job's output contract |
| `bt_dataset` | T3 store and its schema |

The pricing code is extracted into a package in this repo, not pulled in as submodules: the
projects pin conflicting dependencies and use different conventions (year length, rates,
dividends, Greek units).

Built new: the path simulator with POP, P50 and CVaR; the price-by-date P/L surface;
multi-expiry structures; American pricing; the live transport to the browser; the option-chain
grid; chart indicators and drawing tools; login.

## 11. Open decisions

1. Open-source licence.
2. Final name.

Decided on 2026-11-20: chart requirements (6.1); OptionStrat optimiser and flow are not
targets (6.2); the backend replaces `streamer` (section 4); the backend has the T3 store
(section 4); pricing conventions (11.1); the pricing core runs in the backend only, and the
browser receives a P/L grid for what-if; the backend starts as one process, with the broker
stream behind an internal feed interface so that a gateway can be split off later; live data
reaches the browser over one WebSocket with Protocol Buffers messages (QUIC was evaluated and
left for later); several windows can be open at once and follow each other; application state is kept in SQLite and recorded
series in Parquet files; the frontend is a plain Svelte 5 single-page application without
SvelteKit.

Decided on 2026-11-21: the backend machine and the VPN (section 4); a hosted model may see
account data when the user switches it on (section 7); the screen layout (6.3).

Decided on 2026-11-22: the scope additions from research A, with GEX moved to "Later" and IV
against realised volatility given priority (section 6); the probability stages, the order of
the model switch, POP as the leading number, the path count as a setting and the management
defaults (6.4); the what-if default (11.1); Reg-T buying power, with the margin dry-run test
deferred. R5 shows the Tastytrade positions only and accepts that this is a partial view
while most of the account is at another broker. Chart: the 24-hour feed with session tint, the
time-zone choice, daily anchors at the open, the resolution list and the bar-building rule
(6.1).

### 11.1 Pricing conventions (decided 2026-11-20)

`volcalc`'s methods are the default.

| Topic | Default |
|---|---|
| Year length | 365 days (525,600 minutes) |
| Time to expiry | Calendar time to the contract's settlement instant, taken from the broker's `expires-at` and settlement type. A trading-time clock can be added later as an option |
| Rates | Continuous curve built from SOFR and Treasury yields (keyless FRED download); a flat configured rate as fallback |
| Dividends | Discrete cash dividends for stocks and ETFs: the broker's next ex-date and amount, later ones projected from the payment frequency. Indexes use the forward implied from put-call parity |
| Exercise style | European formula on the out-of-the-money side plus an assignment-risk rule. An American pricer comes later, so in-the-money put values of stocks and ETFs are slightly understated until then |
| Greeks | Own values everywhere; the broker's are a cross-check and the fallback when the own inversion fails. Raw units inside, trader units on screen (theta per calendar day, vega per vol point, dollars per position) |
| What-if | Each contract keeps its own IV (confirmed 2026-11-22). One volatility slider shifts all contracts; with several expirations it can be opened into one slider per expiration. Other smile and term-structure behaviour is offered only after the studies of section 11.2 |
| Surface | SVI per expiry as the main surface; free-gamma SSVI as the fallback for thin expiries. Added from `bt_dataset`: an interpolating ATM-variance curve between expiries, wing freeze with quality gates, optional smoothing in time |
| Platform mode | A separate, labelled set used only for the Tastytrade POP, P50 and CVaR replicas: flat 2.93% drift, calendar days over 365, expiry at 16:00 ET, expiry IV for POP, IV index for the simulation |

### 11.2 Studies on historical data (to do)

These decide later defaults. Each runs on the T3 store or the `volcalc` lake.

1. **Smile-aware volatility shift.** When the contracts of a ticket have very different IVs,
   does a shift that follows the smile or skew describe what happens better than the same
   shift for every contract?
2. **Volatility shift across expirations.** The distribution of IV changes by maturity
   (shorter maturities are expected to move more), to weight the common slider for tickets
   with several expirations. Known risk: the weights may fail around binary events such as
   earnings, where they would be most useful. The study reports event windows separately.
3. **Path count and model accuracy** for the path numbers of section 6.4.
4. **Broker IV series against own IV** on more roots, and the two checks left open in 6.5.

To-do outside the studies: the margin dry-run test; the API terms of use; API checks in
regular hours.

## 12. Research tasks before technical requirements

| Task | Question | How |
|---|---|---|
| A Feature landscape | What other tools offer, what traders miss, ranked shortlist | **Done 2026-11-22** in research mode: `docs/research/reports/Options Tools for a Self-Hosted Premium-Selling App.md` (prompt in `docs/research/A_feature_landscape_prompt.md`). Its shortlist is folded into section 6 as proposals. Weak points: forum evidence is thin, six tools were not checked, most prices come from review sites |
| B Broker API capability | Scopes (read-only grant), browser access, event fields, per-contract candle depth, market-metrics content, terms of use | **Done 2026-11-19** except the terms of use and regular-hours checks: `docs/research/B_broker_api_capability.md` |
| C Charting | Lightweight Charts with custom drawing tools against libraries with drawing built in (for example KLineChart); licences | Desk research **done 2026-11-20**, prototype **done 2026-11-22**: `docs/research/C_charting.md` (results in its sections 10 and 11), code in `prototypes/chart/`. Six checks pass in Chromium, Firefox and WebKit, so Lightweight Charts with the `lightweight-charts-drawing` plugin is confirmed. Left over: touch on a real device; whether 4-hour bars are taken from the broker or built in the backend |
| D Probability and risk methods | Models beyond lognormal POP; roll-for-credit feasibility; assignment risk; CVaR for portfolios; what can be validated on the T3 store | **Done 2026-11-22** in research mode: `docs/research/reports/Probability and Risk Methods for a Self-Hosted Short-Premium Options Analyzer.md` (prompt in `docs/research/D_probability_methods_prompt.md`). Proposal in section 6.4. Still to do: checks against the `tt` captures and validation on the T3 store |
| E Architecture | Pricing core placement and language, code sharing with the existing repos, streaming fan-out, local database, MCP layer | Code audit **done 2026-11-20**: `docs/research/E_code_audit.md`. Architecture **decided 2026-11-20 and 2026-11-21**: `docs/research/E_architecture.md` |
| F UI | Layout of the trade workbench and the linked-tool navigation | Mock-ups as Claude Artifacts: first draft 2026-11-20, reviews on 2026-11-21 and 2026-11-22 (decisions in 6.3), fifth draft published 2026-11-22: workbench and chart window at https://claude.ai/artifact/4WMzVB4aDHEP94qirwLdMg |
