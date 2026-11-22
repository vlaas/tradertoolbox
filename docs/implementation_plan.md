# Self-3-Trader: implementation and testing plan

Status: draft 1, 2026-11-22. Built on `docs/technical_requirements.md` (requirement
identifiers such as `PRC-08` refer to it), `docs/product_definition.md` and
`docs/research/E_architecture.md`.

## 1. How the work is organised

The work is cut into **work packages**. A package is sized to be done in one working session
with a fresh context: it names what to read first, what to build, how it is tested and when
it is done. Packages of one release are numbered with it (`WP-1.3` is the third package of
R1).

Rules that hold for every package:

1. **Replay first.** Everything is built and tested against the fake broker and synthetic
   market data (section 3.2). The live broker is used for stated checks only, read-only and
   rate-limited.
2. **A vertical slice early.** R0 ends with a live quote travelling from the broker to a
   browser window through every layer, so that each later package adds to a working system.
3. **Measure the risky things first.** Each release starts with the measurements that could
   change its design (section 7).
4. **Requirements are closed explicitly.** A package lists the requirements it closes; it is
   not done until each has its test, measurement or review recorded.
5. **Decisions go into the documents.** A decision taken during a package is written into
   the product definition or the technical requirements with its date, not only into code.

### Definition of done for a package

- The listed requirements are met and their verification exists.
- `make check` passes: lint, type checks, unit and integration tests, generated-code checks,
  licence check.
- New measurements are added to `docs/measurements.md` with date, machine load and method.
- The status table in section 2 is updated, and anything found but not done is added to
  section 8.
- The owner is offered a commit.

Sizes are relative: **S** fits easily in one session, **M** fills one, **L** may need two and
names its split point.

## 2. Status

| Package | Title | Size | Status |
|---|---|---|---|
| WP-0.1 | Repository skeleton and tooling | S | done 2026-11-22 |
| WP-0.2 | Pricing core: extraction | M | done 2026-11-22 |
| WP-0.3 | Broker interface and REST client | M | done 2026-11-22 |
| WP-0.4 | Stream, feed interface, subscriptions | L | not started |
| WP-0.5 | Market state and instruments | M | not started |
| WP-0.6 | Storage, configuration, reference data | M | not started |
| WP-0.7 | API shell, login, WebSocket | M | not started |
| WP-0.8 | Frontend shell | M | not started |
| WP-0.9 | R0 live measurements and acceptance | S | not started |
| WP-1.1 | Forwards and surface | M | not started |
| WP-1.2 | Chain service | M | not started |
| WP-1.3 | Ticket model and evaluation | L | not started |
| WP-1.4 | Probabilities, stage a | L | not started |
| WP-1.5 | Option chain grid | L | not started |
| WP-1.6 | Ticket and probability panels | M | not started |
| WP-1.7 | Payoff panel | M | not started |
| WP-1.8 | Symbols, watchlists, windows | S | not started |
| WP-1.9 | R1 acceptance | M | not started |

Later releases are planned in section 5 at a coarser grain and get their packages when the
release before them is accepted.

## 3. Testing plan

### 3.1 Layers

| Layer | What is tested | Tools | Runs |
|---|---|---|---|
| Unit | Pure functions: pricing, payoff, probabilities, time model, bar building, stores | pytest, hypothesis; vitest | Every `make check` |
| Component | One backend module against the fake broker or a temporary database | pytest with asyncio | Every `make check` |
| Service | Services and routes against the replay broker, with fixed inputs and expected outputs | pytest, HTTP and WebSocket test clients | Every `make check` |
| End to end | The app in a browser against the backend in replay mode | Playwright: Chromium, Firefox, WebKit | `make e2e`, before a package is closed |
| Numerical reference | Agreement with frozen values, an independent library and the platform captures | pytest, marked `reference` | Every `make check`; the capture set only where present |
| Performance | The PRF targets | Scripts that write to `docs/measurements.md` | At the packages named in section 4 |
| Live broker | Behaviour that only the real service shows | Scripts, read-only, rate-limited, run by hand | At WP-0.9, WP-1.9 and release acceptance |
| Side by side | Agreement with OptionStrat and TradingView | Checklists in `docs/acceptance/` | Release acceptance of R1 and R2 |

### 3.2 Test data

The repository holds no market data (GEN-07), so the tests cannot depend on recordings.

- **Synthetic market.** A deterministic generator, committed as code, produces instruments,
  chains, quotes, Greeks, candles and market metrics from a fixed smile and seeded paths. The
  fake broker serves it over the same REST and DXLink shapes as the real one. Every test in
  `make check` uses this source only.
- **Local recordings.** A capture tool stores real sessions in a git-ignored data directory
  named by configuration. The replay broker can serve them. They are used for performance
  runs and for looking at real behaviour, never by `make check`.
- **Platform captures.** The POP, P50 and CVaR captures stay in the `tt` repository. Tests
  that need them are marked `captures`, take the path from configuration and are skipped,
  visibly, when it is absent.
- **Frozen references.** Reference values for the pricing core come with the extracted
  code. They are numbers computed from formulas, not market data, and are committed.

A test run prints how many tests were skipped for missing local data, so a green run is never
mistaken for a full one.

### 3.3 The fake broker

Taken from `tt/tests/fake_tt.py` and extended. It must be able to play these on request:

- token expiry during a session; HTTP 429 with a retry time;
- a dropped stream; a stream that stays connected and falls silent;
- a subscription request beyond the cap; a missing bid or ask; an index without a quote;
- candle history with a gap, a suspect bar and a clock change;
- delayed and out-of-order messages.

### 3.4 Numerical tests

| Subject | Method | Tolerance |
|---|---|---|
| Price, IV, Greeks | Ported tests; frozen `volcalc` values; an independent library as a development-only cross-check | As in the source tests |
| Properties | Put-call parity; monotonicity in strike, volatility and time; bounds; IV round trip | Exact within floating point, or stated per property |
| Surface | Fit recovers a known SVI; butterfly-arbitrage check fires on a constructed case; density integrates to one | Stated per test |
| Payoff | Hand-computed cases for each named structure, with stock and mixed expirations; unlimited flags | Exact to the cent |
| Integrals against simulation | POP, expected P/L and CVaR at expiry: the integral equals a large seeded simulation | Within the simulation's own error |
| Simulation | Same seed, same result; error shrinks with the square root of the path count; common random numbers reduce the difference error | Stated per test |
| Platform replicas | The `tt` capture set | PRB-02 and PRB-03 |
| Time model | The cases of the chart prototype's sixth check, ported: zones, clock changes, the weekday clock change, sessions | Exact |

### 3.5 Frontend tests

- **Unit:** stores, the WebSocket client's reconnect logic, formatting, the time model.
- **End to end:** each workbench and chart behaviour of section 4.13 and 4.14 of the
  requirements as a Playwright test against synthetic data, in three browsers. The prototype's
  checks are the starting point for the chart.
- **Layout assertions:** no page scroll at laptop size, panes scroll on their own, pinned
  rows stay pinned, pop-ups are not clipped, no text overflows its box at the smallest
  supported width.
- **Accessibility:** the automated checks the backtester viewer already uses.

### 3.6 Security tests

- Every route, enumerated from the application, refuses an unauthenticated request; a new
  route without a test fails the suite.
- The WebSocket refuses a wrong origin and a missing session.
- A canary value placed in the environment file never appears in logs, responses, stored
  files or tool results.
- The broker client can only issue requests from an allow-list of methods and paths; the test
  fails if a path that changes account state is added.
- The MCP server lists no tool outside its read-only registry, and the account tools are
  absent when the setting is off.

### 3.7 Performance protocol

Timing runs are made on an idle machine, record the load average, use the synthetic market or
a named local recording, and report median, 95th percentile and maximum. The chart prototype
showed that a loaded machine inflates results several times over.

### 3.8 What is not automated

Touch on a real device; the look of a screen; agreement with the reference tools; behaviour of
the real broker in regular hours. Each has a checklist and a named point in the plan.

## 4. R0 and R1 packages

### R0 Foundation

**WP-0.1 Repository skeleton and tooling** (S)
- *Read first:* architecture note, section 8; requirements, sections 3 and 4.19.
- *Build:* `backend/` (Python 3.13, uv, ruff, pyright, pytest, hypothesis) and `frontend/`
  (Svelte 5, TypeScript, Vite, vitest, Playwright) as empty but working projects; `proto/`
  with one message and the generation step for both sides; OpenAPI-to-TypeScript generation;
  `make check` and `make e2e`; the licence check; configuration loading with a documented
  example file.
- *Decide here:* the Protocol Buffers tools for Python and TypeScript. Decided 2026-11-22:
  protoc from `grpcio-tools` with the official Python runtime, and `protoc-gen-es` with
  `@bufbuild/protobuf` (requirements, section 3).
- *Done when:* `make check` is green on the skeleton and fails when generated code is stale.
- *Closes:* QA-04, QA-05, QA-07, GEN-07, GEN-08.

**WP-0.2 Pricing core: extraction** (M)
- *Read first:* code audit, section 1; architecture note, "Extraction of the pricing code";
  product definition, 11.1; then the `volcalc` files named there.
- *Build:* `pricing` with price, IV, Greeks (adding vanna, charm, volga), Black-76, clocks,
  the rate curve and the dividend schedule; the unit conversion at the service edge; each file
  records its source repository and commit.
- *Tests:* the ported tests, the frozen references, property tests.
- *Done when:* the reference values match and no module of `pricing` imports I/O.
- *Closes:* PRC-01 to PRC-04, REF-05, QA-01.

**WP-0.3 Broker interface and REST client** (M)
- *Read first:* broker capability note; code audit, section 2; `tt/tt_capture.py` and
  `tt/tests/fake_tt.py`.
- *Build:* the broker interface; the Tastytrade REST client with token renewal, rate limiter
  and back-off; instruments, chains, market metrics, quote token; the allow-list of calls;
  log redaction; the fake broker's REST side on the synthetic market.
- *Done when:* the client passes against the fake broker including token expiry and 429, and
  the security tests of 3.6 for secrets and the allow-list pass.
- *Closes:* BRK-01, BRK-04, BRK-07, BRK-08 (market part), GEN-01 to GEN-03, GEN-09, SEC-05.

**WP-0.4 Stream, feed interface, subscriptions** (L; split after the stream client)
- *Read first:* architecture note, sections 1 and 3; code audit, section 2.
- *Build, part 1:* the DXLink session with reconnect, resubscribe and stall detection; the
  fake broker's stream side with the failure scenarios of 3.3; the capture tool for local
  recordings and the replay broker.
- *Build, part 2:* the feed interface (declarative demand, plain batches, connection state)
  with its in-process implementation; the subscription manager with budget, reserve, priority
  classes and use reporting.
- *Done when:* a dropped or silent stream recovers with the full demand restored and the gap
  reported; demand beyond the budget reduces the lowest class first.
- *Closes:* BRK-02, BRK-03, BRK-05, BRK-06, SUB-01 to SUB-03, SUB-07.

**WP-0.5 Market state and instruments** (M)
- *Read first:* broker capability note, sections 3 to 5; requirements 4.4.
- *Build:* the instrument model for stocks, ETFs, indexes and their options, shaped for
  futures; the latest-value store; the dirty set and the flush at the feed's period; the rule
  for a missing bid or ask through to the wire format.
- *Done when:* instruments of SPY, SPX and one futures option root parse into the model, and
  a flush sends only changed rows.
- *Closes:* MKT-01 to MKT-03, MKT-07, GEN-10.

**WP-0.6 Storage, configuration, reference data** (M)
- *Read first:* architecture note, section 4; requirements 4.5 and 4.9.
- *Build:* the SQLite state store with numbered migrations and a backup that works while
  running; the exchange calendar with sessions, holidays and early closes per instrument
  class; the FRED download with cache and the flat-rate fallback.
- *Done when:* migrations apply from empty and from the previous version; the calendar
  answers session questions for a holiday week and both clock changes.
- *Closes:* STO-01, STO-02, STO-07, REF-01, REF-04.

**WP-0.7 API shell, login, WebSocket** (M)
- *Read first:* architecture note, sections 3, 5 and 6; requirements 4.10 and 4.11.
- *Build:* the FastAPI application; login with an argon2 hash and a session cookie; the
  listen address from configuration; the health route; serving the built frontend; the
  WebSocket with Protocol Buffers messages, the JSON development mode, topics with snapshot,
  changes and resubscribe; the `status` and `quotes` topics; the tier declaration that hides
  or marks features (GEN-04).
- *Done when:* the security tests of 3.6 for routes and the WebSocket pass, and a client that
  reconnects gets fresh snapshots.
- *Closes:* API-01 to API-05, API-08, SEC-01 to SEC-03, SEC-06 to SEC-08, GEN-04, GEN-05
  (structure), QA-02.

**WP-0.8 Frontend shell** (M)
- *Read first:* code audit, section 3; product definition, 6.3 ("All screens"); the
  backtester viewer's shell, tokens and stores.
- *Build:* the shell with top bars and status bar; hash routing with one address per screen;
  the WebSocket client module and one store per topic; the login screen; the design tokens;
  a status screen showing connection, subscription use and a live quote list.
- *Done when:* a quote changed in the fake broker appears in the browser, the connection
  loss is shown and recovered from, in three browsers.
- *Closes:* UI-03, UI-05; the base for UI-01, UI-04, UI-08 and UI-10.

**WP-0.9 R0 live measurements and acceptance** (S)
- *Do, read-only and rate-limited, on a trading day:* how the subscription cap counts; REST
  rate limits; stream behaviour in regular hours; a full day without restart with memory
  recorded; reconnect after a forced drop; throughput of the reader together with IV
  inversion for a subscribed expiration.
- *Write:* the results into the broker capability note and `docs/measurements.md`; changes to
  budgets or limits into the requirements.
- *Closes:* PRF-09, QA-08, and the "to measure" points of the requirements for R0.

Order: WP-0.1 first. Then WP-0.2, WP-0.3 and WP-0.6 are independent of each other. WP-0.4
needs WP-0.3; WP-0.5 needs WP-0.4; WP-0.7 needs WP-0.5 and WP-0.6; WP-0.8 needs WP-0.7;
WP-0.9 closes the release.

### R1 Trade workbench

**WP-1.1 Forwards and surface** (M)
- *Read first:* `volcalc` `iv/forward.py` and `surface/`; product definition, 11.1.
- *Build:* the forward per expiry from quote mids; the dividend schedule from broker data;
  SVI per expiry with the SSVI fallback, the arbitrage check and a fit status; density and
  cumulative probability; interpolation in time; the fit service in the process pool with its
  refresh timer.
- *Closes:* PRC-05 to PRC-07, REF-02, PRF-05.

**WP-1.2 Chain service** (M)
- *Build:* the chain view by expiration and strike window; own IV and Greeks per flush as one
  array call; expected move per expiration; earnings and ex-dividend dates; index handling;
  stale marks; the grace period for closed views; the `chain` topic and the Arrow snapshot.
- *Measure:* PRF-03 on the synthetic market and on a local recording.
- *Closes:* MKT-04 to MKT-06, REF-03, SUB-04, SUB-05, API-06, PRC-13, PRF-03.

**WP-1.3 Ticket model and evaluation** (L; split after the payoff functions)
- *Read first:* product definition, 6.3 (ticket items); the mock-up logic in
  `docs/mockups/project/Main.dc.html` as a worked example of the rules, not as code to port.
- *Build, part 1:* the ticket with option legs, stock legs and held legs with purchase price
  and basis; payoff, breakevens, maximum profit and loss with the unlimited flags; the P/L
  grid over price and date; what-if with one volatility shift or one per expiration.
- *Build, part 2:* totals and Greeks; the Reg-T estimate; the strategy name; the limit price
  rule (follows the mid, or entered and then the entry price); storage with the context
  snapshot; save, load and list; the `ticket` topic; the shared selection across windows.
- *Measure:* PRF-01.
- *Closes:* PRC-08 to PRC-11, PRB-12, STO-05, API-07, WB-12 to WB-15 (backend part), PRF-01.

**WP-1.4 Probabilities, stage a** (L; split after the platform mode)
- *Read first:* product definition, 6.4; `tt/FINDINGS.md`; the research D report, sections Q1,
  Q2 and Q5.
- *Build, part 1:* the platform mode and the POP, P50 and CVaR replicas, checked on the
  capture set.
- *Build, part 2:* market-implied POP; expected P/L and CVaR at expiry as integrals; the
  probability below and above a price at an expiration and at an earlier date; probability of
  touch; the assignment flag; the model name and assumptions on every result; the guard for
  numbers that are not shown.
- *Closes:* PRC-12, PRB-01 to PRB-06, PRB-13, PRB-15, GEN-06.

**WP-1.5 Option chain grid** (L; split after the static grid)
- *Read first:* product definition, 6.3; the workbench mock-up; code audit, section 3
  (`DataTable` verdict and the recorded lessons).
- *Build, part 1:* the virtual grid keyed by strike with calls, strike and puts; several open
  expirations; pinned header, expiration row and column titles; opening at the money; the
  divider with the extra columns; the at-the-money line and the range tint.
- *Build, part 2:* streaming cell updates; click to buy or sell; the quantity control; the
  information icon component; stale and missing values.
- *Measure:* PRF-08.
- *Closes:* WB-01 to WB-04, UI-08, UI-09, UI-11, PRF-08.

**WP-1.6 Ticket and probability panels** (M)
- *Build:* the ticket rows and totals with the breakevens rule; the name with save and load;
  the limit control; stock and held legs with the cost and now switch; event dates; the
  probabilities column with the model switch and its notes.
- *Closes:* WB-05, WB-06, WB-08, WB-09 (tickets), WB-12 to WB-15 (frontend part), UI-06,
  UI-07.

**WP-1.7 Payoff panel** (M)
- *Build:* the graph with the three lines, the expected-move band, strikes and breakevens;
  the pointer line with probabilities; the range control and the edge marks; the table with
  its own legend; date and volatility controls; maximise; interpolation of the grid between
  requests.
- *Decide here:* ECharts or an own canvas for this graph, by whether ECharts carries the
  pointer line and edge marks without workarounds.
- *Measure:* PRF-02.
- *Closes:* WB-07, WB-10, WB-16, WB-17, PRF-02.

**WP-1.8 Symbols, watchlists, windows** (S)
- *Build:* symbol search; watchlists with the `quotes` topic; each screen in its own window
  following the shared symbol and ticket.
- *Closes:* UI-01, UI-02, UI-04, WB-09 (watchlists).

**WP-1.9 R1 acceptance** (M)
- *Do:* the OptionStrat checklist over the named structures; the end-to-end suite in three
  browsers; the layout assertions; the PRF measurements of R1 on an idle machine; a trial on
  a trading day with the real broker.
- *Write:* `docs/acceptance/R1.md` with inputs, results and the differences found.
- *Closes:* WB-11, QA-03, QA-06 (R1).

Order: WP-1.1 and WP-1.5 part 1 can start at once. WP-1.2 needs WP-1.1. WP-1.3 needs only
the pricing core. WP-1.4 needs WP-1.1 and WP-1.3. WP-1.5 part 2 needs WP-1.2. WP-1.6 needs
WP-1.3 and WP-1.4. WP-1.7 needs WP-1.3. WP-1.8 is independent. WP-1.9 closes the release.

## 5. Later releases

Each gets its packages when the release before it is accepted. The entry in "First" is what
the release starts with.

| Release | Content of the plan | First |
|---|---|---|
| R1b Path numbers and stress | The seeded path simulator with a path-count setting, shared random numbers and an error estimate; management defaults by strategy class; the stress grid; the smile-dynamics selector; their panels (PRB-07 to PRB-11, PRF-04) | Measure PRF-04 with a bare simulator before building around it |
| R2 Chart | Candles in the backend: download, cache, the bar-building rule, sessions, suspect bars, future whitespace (CND-01 to CND-10). The chart behind an own interface with the prototype's time model and its checks ported (CH-01, CH-02, CH-06 to CH-09). Drawings with storage and undo (CH-03 to CH-05, STO-06). Ticket layers and range templates (CH-10 to CH-12). Acceptance against the TradingView checklist and touch on a real device (CH-14) | The candle backend, because the chart depends on its bar and session rules |
| R3 Volatility | IV against realised volatility from the broker's daily series (VOL-01 to VOL-03, CND-11), then the daily snapshot recorder (VOL-09, STO-03), skew and term structure, expected range, bid/ask width, the open-interest sweep, earnings moves, and the IV-history set from the lake (STO-04, STO-08) | The two open checks on the broker's IV series |
| R4 LLM tools | The MCP server over the existing services, its token, the account switch, the tool tests (MCP-01 to MCP-05, MCP-07, SEC-04) | Confirm which Claude clients can reach a host on the VPN |
| R5 Positions and risk, R5b Review | Account reads, marks and Greeks, campaigns, the roll comparator, rule status, the portfolio view; then the review screens (POS, REV, MCP-06, CH-13) | How far back the broker's transactions reach |
| R6 Scanners and alerts | The two-stage scanner within the background budget, the IV-against-realised criterion, alert evaluation and delivery (SCN) | The rotation time for a watchlist of realistic size |
| R7 History, R7b Probabilities stage b | Correlation, simulation on history, the backtester hand-off; then the stage b models, each with its recorded validation (PRB-14) | The studies of the product definition, section 11.2 |
| Recorder | The recorder module, run beside the streamer until the outputs are shown equal (REC) | A byte-level comparison tool for the two outputs |

## 6. Milestones

| Milestone | Reached when | What it proves |
|---|---|---|
| M0 Live path | WP-0.8 done | A quote travels from the broker to a browser through every layer |
| M1 R0 accepted | WP-0.9 done | The foundation holds for a trading day and the limits are known |
| M2 First ticket | WP-1.3 and WP-1.7 done | A ticket can be built by hand and its payoff seen |
| M3 R1 accepted | WP-1.9 done | The workbench replaces OptionStrat for daily use |
| M4 R2 accepted | R2 acceptance | The chart replaces the TradingView subset |

## 7. Risks and the measurement that answers each

| Risk | Answered by | If it goes badly |
|---|---|---|
| The stream reader cannot keep up once IV is inverted on every flush | WP-0.9, WP-1.2 | Invert on a slower timer, or only for rows in view |
| The subscription cap counts symbol-event pairs | WP-0.9 | Halve the chain budgets; fewer event types per contract |
| Regular-hours message rates differ much from the after-hours probe | WP-0.9 | Raise the aggregation period; revisit the flush design |
| The Protocol Buffers tool chain is awkward in one of the two languages | WP-0.1. Result, 2026-11-22: not awkward in either language. Bytes and canonical JSON written by Python decode and re-encode identically in TypeScript, in Node and in the three browsers | JSON messages with the same schema discipline, as the architecture note allows |
| The chain grid drops frames with streaming cells | WP-1.5 | Update only visible rows; coarser update batches |
| The surface fit is too slow or unstable on thin expirations | WP-1.1 | SSVI only for those expirations; longer refresh timer |
| The platform replicas drift from the platform | WP-1.4, and a recurring capture check | Mark the replica as approximate with the measured error |
| The drawing plugin breaks its interface again (it did once) | The own interface module of R2 and the pinned version | Stay on the pinned version; the fallback of the charting note |
| Scope grows inside a release | The requirement list per package | Move the item to section 8, not into the package |

## 8. Carried items

Found but not planned into a package yet.

- Read the broker's API terms of use before publication.
- The margin dry-run test (deferred, product definition 6.4).
- Whether built bars are cut at the regular close.
- The open-source licence and the final name.
- The studies of the product definition, section 11.2.
- An American pricer for in-the-money values of stock and ETF options.
- From WP-0.1 (2026-11-22):
  - The owner has not yet confirmed the set-up proposals in requirements section 3 (version
    rule, linting, committed generated code, configuration, the package name `s3t`) or the
    licence additions and exceptions marked "proposed" under QA-07.
  - Move to TypeScript 7 when svelte-check and openapi-typescript support it.
  - Upgrade Playwright together with the chart prototype and the backtester viewer.
  - In TypeScript, int64 fields arrive as `bigint`. Choose per field, when the wire messages
    are designed (WP-0.5, WP-0.7), whether to keep `bigint` or to mark the field as a JS
    number.
  - The OpenAPI schema is generated offline and not served. WP-0.7 decides whether an
    authenticated copy is served.
  - There is no CI. QA-04's "before a change is merged" means `make check` before a commit.
  - The protobuf Python code generated by the bundled protoc (35.1) needs a runtime of the
    same major version and no older. `uv.lock` keeps the two together. A `grpcio-tools`
    upgrade means `make gen`.
- From WP-0.2 (2026-11-22):
  - The owner has not yet confirmed the proposals under the pricing core in the requirements
    (4.6): the escrowed dividend model, the raw and trader units, logging in `pricing`, mpmath
    as the cross-check. numpy's licence passes only with the proposed CC0-1.0 allowance (QA-07).
  - REF-05 is closed for the clock. Which instant a contract settles at (PM or AM settlement,
    early closes) is still to be taken from the broker's `expires-at` and settlement type and
    the calendar: WP-0.5 with WP-0.6.
  - The FRED download and the as-of selection of published rates (`volcalc` `build_rates`,
    `rates_asof`) go to WP-0.6. `pricing` has the conversion and the curve only.
  - The quoting tick per contract, which widens the IV gate, is to come from the broker's chain
    (WP-1.2). `volcalc`'s tick tables per root were not ported.
  - Two service rules from `volcalc` were not ported into `pricing`. One: no Greeks where there
    is no IV (`enforce_iv_greeks_contract`). Two: PRC-13's fallback to the broker's Greeks. Both
    belong to the chain service (WP-1.2).
  - Index options priced on the parity forward get forward Greeks from `black76`. Delta and
    gamma against the index level need the factor F/S. Decide it with the forward (WP-1.1).
  - The trading-time clock (`volcalc` `tau_bus`) when it is wanted, on the WP-0.6 calendar.
  - The frozen `volcalc` values match bit for bit with numpy 2.5.4 and scipy 1.18.1. A numpy or
    scipy upgrade can break that; `tests/pricing/test_reference.py` says what to do then.
- From WP-0.3 (2026-11-22):
  - The owner has not yet confirmed the proposals of the broker package. They are listed in
    the requirements, section 3, and include the certifi exception under QA-07.
  - Closed for the broker side only:
    - GEN-02 and SEC-05: the canary test (`tests/broker/test_canary.py`) is to be extended to
      the stream token in WP-0.4, the routes in WP-0.7 and the MCP results in R4.
    - GEN-03: the nickname in the browser comes with WP-0.7 and WP-0.8.
    - GEN-09: to be reviewed again when WP-0.4 has added the stream methods and WP-0.5 has
      typed the instruments.
  - uvicorn's loggers do not propagate to the root logger. WP-0.7 calls
    `s3t.secrets.install_redaction` on their handlers.
  - The broker's REST limits are not known. The defaults (2 requests per second, burst 4) are
    in `[broker]`. WP-0.9 measures the real limits. The batch of 50 symbols per metrics call
    is untried: the live check asked for two.
  - Whether the quote-token response states an expiry was not seen. WP-0.4 checks it when the
    stream uses the token. Until then the 24 h measured in section 1 of note B applies.
  - The expiry instant of a contract (`expires-at`) comes only from the per-contract
    instrument call. A chain has many contracts. If WP-0.5 or WP-1.2 needs it per contract,
    the bulk instrument call (`/instruments/equity-options?symbol[]=`) has to go on the
    allow-list, by review.
  - The fake broker:
    - has no holidays (WP-0.6 brings the calendar);
    - has ES weeklies on Fridays only (live has other weekdays too);
    - carries fewer keys than the live documents. A package that parses more keys adds them
      to the fake (WP-0.5).
  - For the packages that type the broker's documents (note B, addendum "WP-0.3"):
    - The live futures list is not in date order (WP-0.5).
    - The IV index rank can be above 1, and an entry of the IV list per expiration can lack
      its value (WP-1.1, WP-1.2).
    - SPY's dividend fields in the metrics were 0.0. The dividend schedule of an ETF may need
      another source (WP-1.1).
  - `.env` has mode 0644: group and others can read it. The backend warns about this. The
    owner should run `chmod 600 .env`.
  - `../tt` is not a git repository. The ported files name `tt` version 1.6.0 instead of a
    commit.
  - `config/s3t.toml` was created from the example for the live check. It is git-ignored.

## 9. Starting a package in a fresh session

1. Read `CLAUDE.md`, then this plan's section for the package, then the documents it names
   under "Read first". Do not read the whole documentation set.
2. Check the status table and that the packages it depends on are done.
3. Work to the definition of done in section 1.
4. Before stopping: update the status table, `docs/measurements.md` and section 8, and say
   what was verified and what was not.
