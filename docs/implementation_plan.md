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
| WP-0.4 | Stream, feed interface, subscriptions | L | done 2026-11-23 |
| WP-0.5 | Market state and instruments | M | done 2026-11-24 |
| WP-0.6 | Storage, configuration, reference data | M | done 2026-11-25 |
| WP-0.7 | API shell, login, WebSocket | M | done 2026-11-25; SEC-06 open for Linux |
| WP-0.8 | Frontend shell | M | done 2026-11-27 |
| WP-0.9 | R0 live measurements and acceptance | S | in progress: tools and rehearsal 2026-11-27; the day run 2026-11-30 |
| WP-1.1 | Forwards and surface | L | done 2026-11-28: part 1 (numerics, dividends, the fake) 2026-11-27; part 2 (pool, fit service, PRF-05 measured) 2026-11-28. On the synthetic market only: not verified live or on real quotes |
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
a named local recording, and report median, 95th percentile and maximum. Since 2026-11-27 they also
record the CPU time beside the wall time and the machine they ran on (QA-08), so that the headroom
can be judged for the dedicated machine the product moves to later. The chart prototype
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
- *Closes:* BRK-01, BRK-04, BRK-07, BRK-08 (market part), GEN-01 to GEN-03, GEN-09, SEC-05;
  BRK-09 (the data, `rest_status()`; added 2026-11-22).

**WP-0.4 Stream, feed interface, subscriptions** (L; split after the stream client)
- *Read first:* architecture note, sections 1 and 3; code audit, section 2; requirements,
  section 3, "The session and the subscription manager" and "The stream package, the rest"
  (the decisions of 2026-11-23); section 8 of this plan, "From WP-0.4".
- *Build, part 1:* the DXLink session with reconnect, loss and stall detection; the fake
  broker's stream side with the failure scenarios of 3.3; the capture tool for local
  recordings and the replay broker.
- *Build, rework of part 1* (decided 2026-11-23; requirements, section 3): the session without
  a subscription set and without pacing, with a hard cap; the application's own event
  records; `data_time()`; zstd for recordings.
- *Build, part 2:* the feed interface (declarative demand, plain batches, connection state)
  with its in-process implementation; the subscription manager with budget, reserve, priority
  classes, pacing, the refill of a session by class, and use reporting.
- *Done when:* a dropped or silent stream recovers with the full demand restored and the gap
  reported; demand beyond the budget reduces the lowest class first.
- *Closes:* BRK-02, BRK-03, BRK-05, BRK-06, SUB-01 to SUB-03, SUB-07. BRK-06 and SUB-07 are
  closed here for the data (`StreamStatus`, the state items, the use report); WP-0.7
  publishes them and WP-0.8 shows them, as with BRK-09 (decided 2026-11-23).
- *Part 1, done 2026-11-23:* BRK-03 and BRK-05 have their tests, and BRK-06 its data. As
  built, the session restores the subscriptions itself, and the first "done when" sentence
  holds at the session (`tests/broker/test_stream_recovery.py`). After the rework the
  manager restores them, and the sentence is tested on manager and session together. Part 2
  brings SUB-01 to SUB-03 and SUB-07 and the second sentence.
- *Rework and part 2, done 2026-11-23:* the session holds no subscription set and has the
  hard cap; the event records with their conversion (`s3t.broker.tastytrade.convert`);
  `data_time()`; recordings in zstd; the feed interface, the budget and the in-process feed
  (`s3t.subs`). Both "done when" sentences are tests of manager and session together
  (`tests/subs/test_feed_recovery.py`, `tests/subs/test_feed.py`). SUB-01 has its rule test
  (`tests/test_repo.py`). The decisions are in the requirements, section 3; the proposals
  of the package were confirmed on 2026-11-24. One live check of the session ran after the rework (note B, addendum). The
  manager was not run against the live server: WP-0.9.

**WP-0.5 Market state and instruments** (M)
- *Read first:* broker capability note, sections 3 to 5; requirements 4.4; `s3t.subs.feed`
  and the records in `s3t.broker.interface` (the market state is the reader of everything).
- *Build:* the instrument model for stocks, ETFs, indexes and their options, shaped for
  futures; the latest-value store; the dirty set and the flush at the feed's period; the rule
  for a missing bid or ask through to the wire format.
- *Done when:* instruments of SPY, SPX and one futures option root parse into the model, and
  a flush sends only changed rows.
- *Closes:* MKT-01 to MKT-03, MKT-07, GEN-10. MKT-03 and MKT-07 are closed here for the data
  and the wire message (`QuoteRows`); WP-0.7 sends it. GEN-10 is closed for the backend and
  the wire; the stores are WP-0.6.
- *Review of MKT-01 (R), 2026-11-24, by Claude:* each item of its list has its place in the
  model. Per-expiry underlying, exercise style, settlement type, multiplier and tick rules are
  fields of `Expiration`; the settlement instant is `settles_at`. Two limits: an equity chain
  states no exercise style (`Instruments.exercise` reads it), and the style that the broker
  states for /ES weeklies looks wrong (section 8). Seen on the documents of /ES only for
  futures.
- *Done 2026-11-24:* the instrument model (`s3t.broker.instruments`) and the reading of the six
  documents, so that the broker interface returns typed records except for the metrics; the
  market state and the flush (`s3t.market.state`); the registry and the settlement rule; the
  missing-bid rule from the broker's wire to `proto/s3t/wire/v1/market.proto`, checked in both
  languages. SPY, SPX (three roots) and all 54 expirations of the /ES options parsed from the
  live documents (two read-only runs, note B, addendum "WP-0.5, live"). One reading fault was
  found in the first, fixed offline, and confirmed fixed in the second. The proposals of the
  package were confirmed on 2026-11-24, with two changes: the settlement rule for a date
  without a session, and a chain that names what it leaves out (requirements, section 3).

**WP-0.6 Storage, configuration, reference data** (M)
- *Read first:* architecture note, section 4; requirements 4.5 and 4.9.
- *Build:* the SQLite state store with numbered migrations and a backup that works while
  running; the exchange calendar with sessions, holidays and early closes per instrument
  class; the FRED download with cache and the flat-rate fallback.
- *Done when:* migrations apply from empty and from the previous version; the calendar
  answers session questions for a holiday week and both clock changes.
- *Closes:* STO-01, STO-02, STO-07, REF-01, REF-04. STO-01 is closed for the file and its rule; the
  tables come with their packages. REF-01 and REF-04 are closed for the data; WP-0.7 publishes it
  and WP-0.8 shows it. GEN-10 is closed for the stores.
- *Done 2026-11-25:* the state store with its migrations, its backup and the settings (`s3t.store`);
  the exchange calendar (`s3t.refdata.calendar`), which also is the `SessionTimes` of the settlement
  rule; the rates with download, cache, selection and fallback (`s3t.refdata.rates`); holidays and
  early closes in the fake broker; `[calendar]`, `[rates]` and `paths.backup_dir`. Both "done when"
  sentences are tests: `tests/store/test_migrate.py`, `tests/refdata/test_calendar.py`. "From the
  previous version" is shown on a list of the test's own, because there is one real migration, and
  a standing test covers each real one that is added. The calendar placed all 17,193 real SPY bars
  of the chart prototype in a session, and FRED answered one live check (note B, addendum
  "WP-0.6"). The proposals of the package were confirmed on 2026-11-25 (requirements, section 3,
  and the wording of STO-07 in 4.9).

**WP-0.7 API shell, login, WebSocket** (M)
- *Read first:* architecture note, sections 3, 5 and 6; requirements 4.10 and 4.11.
- *Build:* the FastAPI application; login with an argon2 hash and a session cookie; the
  listen address from configuration; the health route; serving the built frontend; the
  WebSocket with Protocol Buffers messages, the JSON development mode, topics with snapshot,
  changes and resubscribe; the `status` and `quotes` topics, with the REST state of BRK-09 in
  `status` and in the health route; the tier declaration that hides or marks features
  (GEN-04); the choice of the live, the fake or the replay broker in the configuration (the
  replay needs the name of a recording), with the mark of GEN-11 in `status`; the stream
  state of BRK-06 and the subscription use of SUB-07 in `status` and in the health route.
- *Decide here:* whether pyright's strict mode applies to `api` (requirements, section 3,
  linting).
- *Done when:* the security tests of 3.6 for routes and the WebSocket pass, and a client that
  reconnects gets fresh snapshots.
- *Closes:* API-01 to API-05, API-08, SEC-01 to SEC-03, SEC-06 to SEC-08, GEN-04, GEN-05
  (structure), QA-02; BRK-06, BRK-09, SUB-07 and GEN-11 (published).
- *Review of API-08 (R), 2026-11-25, by Claude:* the message layer (`s3t.api.channel`) knows a
  carrier of two methods and a codec. Its tests run on a carrier in memory. A rule test keeps the
  web framework out of it and out of the topics. What another carrier would replace is `s3t.api.ws`,
  and on the browser's side the connection module of WP-0.8.
- *Review of GEN-05 (R), 2026-11-25, by Claude:* only the structure. `pricing` is imported by the
  fake broker and by `refdata.rates`; a rule test keeps scipy and the error function out of every
  other module, and numpy and `pricing` out of `api` and `system`. The services that compute come
  with R1.
- *Done 2026-11-25:* the composition (`s3t.system.backend`), the process (`python -m s3t`), login and
  sessions with migration 0002, the guard, five routes, the built frontend, the message layer with
  both codecs, the topics `status` and `quotes`, the tier declaration, the health route, the choice
  of broker, a daily copy of the state file. Both "done when" sentences are tests:
  `tests/api/test_security.py` and `test_quotes.py` ("a client that comes back gets fresh
  snapshots"). The guard, the login, the topics, the message layer and the composition were checked by
  changing them: 48 faults were put in one at a time. The tests missed four, and have a case for each
  since; one made its test hang, and it fails now. The browser suite runs against the real backend in
  three engines; with the origin check switched off, its test of another origin failed in all three.
  **Open:** SEC-06 on Linux; API-05 for the topics of later packages. The proposals of the package
  were reviewed with the owner on 2026-11-26 and 2026-11-27, and decided with three changes
  (requirements, section 3, "The API package").
  Two things of earlier packages were changed: a fault of `Instruments` (a failed read was kept when
  its caller had gone), and `StreamStatus` has `stalled` and three fault counts.

**WP-0.8 Frontend shell** (M)
- *Read first:* code audit, section 3; product definition, 6.3 ("All screens"); the
  backtester viewer's shell, tokens and stores.
- *Build:* the shell with top bars and status bar; hash routing with one address per screen;
  the WebSocket client module and one store per topic; the login screen; the design tokens;
  a status screen showing connection, subscription use and a live quote list; the REST state
  of BRK-09 in the status bar; the "SYNTHETIC DATA" mark of GEN-11 in the status bar and
  every screen header; a Prettier check with its Svelte plugin in `make check`, added first.
- *Done when:* a quote changed in the fake broker appears in the browser, the connection
  loss is shown and recovered from, in three browsers.
- *Closes:* UI-03, UI-05, BRK-06, BRK-09, SUB-07 and GEN-11 (shown); the base for UI-01,
  UI-04, UI-08 and UI-10. Added in the review (2026-11-27): UI-12 for the screens of R0, and
  REF-01, REF-04 and GEN-03 (shown), which WP-0.6 and section 8 had given to WP-0.8.
- *Review of UI-03 (R), 2026-11-27, by Claude:* one module opens the WebSocket,
  `frontend/src/lib/connection.ts`, and only `src/stores/connection.svelte.ts` uses it. A store of
  a topic subscribes there and never sees the socket: `status.svelte.ts` for `status`,
  `quotes.svelte.ts` for `quotes`. The carrier is an interface of two calls, so another carrier
  would replace `webSocket()` and nothing else (API-08, the browser's side).
- *Done 2026-11-27:* the Prettier check, first (the existing files already followed it); the
  connection module with its rules (subscription numbers, resubscription after a reconnect, the
  15 s silence, the wait from 0.5 s to 8 s, `Bye`, a refused handshake, `Refused`, the JSON form
  by `?wire=json`); the stores of `status` and `quotes`; the login screen; hash routing; the
  viewer's tokens with one spacing scale; the shell with top bar and status bar; the status screen
  with the stream, the subscription use, the REST state, the rates, the calendar, the data tiers
  and a live quote list with its stale marks; the information icon as a native popover; the dev
  server's proxy. For the browser suite: one backend per engine, and a test control in
  `backend/tools/e2e_backend.py` (restart the backend, drop the stream, set a quote), with
  `serve(stop=)` and the fake's `fixed_quotes`. Both "done when" sentences are tests in three
  engines (`frontend/e2e/live.spec.ts`): a bid and ask set in the fake show on the screen to the
  cent; a restart of the backend shows the loss in the top bar and the status bar, marks the
  quotes stale, and the app comes back with fresh snapshots. A drop of the broker's stream shows
  its reconnect and gap, and `?wire=json` gives text frames and the same screen. The JSON test
  failed once in Firefox, by a race in the test itself (a frame of the page before); it keeps the
  frames per socket since, and passed nine runs in a row. The test of the loss failed with a fault put in the loss mark, and again
  with one put in the live rule; three faults put in the connection module each failed a unit test. The status screen was looked
  at once, in Chromium at 1440 by 900; that look found an explanation box cut off at the window's
  edge, which is fixed and is now a test. M0 holds on the fake broker: **nothing of this package
  ran against the live broker** (WP-0.9). The proposals are in the requirements, section 3, "The
  frontend shell".
- *Review, 2026-11-27, with the owner:* four proposals decided, two of them changed (requirements,
  section 3, and product definition 6.3). The status bar keeps its order and is compact: short
  texts, each with its whole sentence when pointed at. A row that is not live is greyed as a whole.
  Times are the exchange time of the instrument by default, and the user can choose local time,
  UTC or a zone by its name (UI-12, new; CH-06 widened). JetBrains Mono is the font of prices, a
  named exception for OFL-1.1. Built the same day: the time choice in the top bar, every time with
  its zone, and tests for each in three engines. In WebKit, Enter in the field of a zone went to
  the open list of suggestions, so a listed name applies as soon as it is typed or picked. Later
  the same day the other proposals were decided as proposed, the exchange zone of an instrument
  among them, except one: the accessibility checks came now (`frontend/e2e/a11y.spec.ts`, WCAG 2 A
  and AA, both themes, three engines), with `axe-core` as a named exception. They found three
  faults, all fixed: a panel that scrolls was out of the keyboard's reach, the grey of a stale row
  and the faded old figures of the status bar had too little contrast.

**WP-0.9 R0 live measurements and acceptance** (S)
- *Do, read-only and rate-limited, on a trading day:* how the subscription cap counts; REST
  rate limits; stream behaviour in regular hours; a full day without restart with memory
  recorded; reconnect after a forced drop; throughput of the reader together with IV
  inversion for a subscribed expiration.
- *Write:* the results into the broker capability note and `docs/measurements.md`; changes to
  budgets or limits into the requirements.
- *Closes:* PRF-09, QA-08, and the "to measure" points of the requirements for R0.
- *Decided at the start, 2026-11-27, by the owner:*
  - Nothing goes past a published limit: no probe of the cap, of the change limit or of a sixth
    session, and no burst of REST requests. How the cap counts is not measured, by decision, and
    the budget keeps counting pairs (requirements, section 3, "The live measurements").
  - The REST limits come from the documentation and the response headers.
  - A rehearsal on 2026-11-27, the half day after Thanksgiving; the measured day on Monday
    2026-11-30, with the machine idle.
  - The owner looks at the app during a run.
- *Decided later the same day, by the owner:*
  - The memory bound is observed, not a limit, and 1,024 MB stays.
  - PRF-09 is a run across every session, not a trading day: 25 hours from Monday 08:45 New York,
    with /ES in the demand.
  - Every timing records its CPU time and the machine, for the headroom on another machine.
  - The three proposals of part 1 as proposed:
    - the IV figure is the cost of a stand-in;
    - the stream's rate-error rule stays;
    - QA-08 is closed by review.
- *Split:* part 1 builds the tools and the memory bound and makes the short checks and a rehearsal.
  Part 2 is the day run of 2026-11-30 and its write-up.
- *Part 1, done 2026-11-27:*
  - **The memory bound of PRF-09,** which did not exist: `[process] memory_bound_mb`, a watch in
    `s3t.system.process` and `process` in the health route. Decided by the owner the same day: the
    bound is observed and limits nothing, and 1,024 MB stays.
  - **The day run,** `backend/tools/live_day.py`, with `tools/day_measures.py`. It runs the backend
    with a consumer of about 2,200 pairs (2,008 in the rehearsal, before /ES was added) and
    measures every minute. It forces drops at set times,
    takes a pair out of the demand once, and summarises a run (`--summary`).
  - **`tools/timings.py`:** the calendar, the parse of the chains, and the capacity of the reader by
    replaying a recording at multiples of its pace.
  - **Additions to the checks:** `broker_check.py` (the headers, 50 symbols of metrics, an AM /ES
    option, the chains of /NQ, /CL and /GC) and `stream_check.py` (the field lists before a
    subscription, `--candles DAY`).
  - **Read live** (note B, addendum "WP-0.9"):
    - the extended broker check (18 requests, no 429, no header that states a limit);
    - the stream check in regular hours;
    - a recording of 10 minutes;
    - the rehearsal from 10:00 to 13:30 New York: 2,008 pairs, two forced drops, one removal; no
      loss, no stall, no rate error. Memory 140 to 163 MB.
  - **Read in documents:** the broker's published limits, and the DXLink errors, which name no rate
    or cap error.
  - **Timed at 13:30 New York, idle by the 1-minute load:** the calendar, the chains (15 ms at most
    for SPX, against PRF-05's 50 ms) and the reader (about 49,000 events a second without a loss).
  - The rehearsal's timings were taken under load and are not measurements.
  - **Not done yet:**
    - PRF-09 itself (a full day);
    - the candle check of the early close;
    - the owner's look at the app on the live broker.
- *Part 2, for 2026-11-30:*
  - **The owner** starts the run at about 08:45 New York (15:45 local), for 25 hours (the default):
    `cd backend && uv run python tools/live_day.py --config ../config/s3t.toml`.
    - The machine is idle from 09:30 to 16:00 New York; the night needs no idle machine.
    - The owner looks at the app at the drops, 10:30 and 14:30, and once in the late session.
  - **The follow-up session:**
    - `live_day.py --summary` over the whole run and over 09:30 to 16:00, with the CPU per event
      and the loop's share of a core;
    - `timings.py` again, with CPU time, on an idle machine;
    - `stream_check.py --candles 2026-11-27`;
    - the rows of `docs/measurements.md`;
    - PRF-09 closed, and the status set to done. QA-08 is closed by review, decided 2026-11-27.

Order: WP-0.1 first. Then WP-0.2, WP-0.3 and WP-0.6 are independent of each other. WP-0.4
needs WP-0.3; WP-0.5 needs WP-0.4; WP-0.7 needs WP-0.5 and WP-0.6; WP-0.8 needs WP-0.7;
WP-0.9 closes the release.

### R1 Trade workbench

**WP-1.1 Forwards and surface** (L; split after the numerics. It was sized M; the split was
added on 2026-11-27, when part 1 filled a session)
- *Read first:* `volcalc` `iv/forward.py` and `surface/`; product definition, 11.1. For part 2:
  requirements, section 3, "The surface package" (both parts); `s3t/pricing/surface.py`;
  `s3t/system/process.py` and `s3t/api/topics.py` (`_QuotesView`) as patterns.
- *Build, part 1 (done 2026-11-27):* the forward per expiry from quote mids; the dividend
  schedule from broker data; SVI per expiry with the SSVI fallback, the arbitrage check and a
  fit status; density and cumulative probability; interpolation in time. All in `pricing`, as
  one call that a worker process can run. The fake market with dividends and the settlement
  clock.
- *Build, part 2 (done 2026-11-28):* the process pool (`s3t.system.pool`); the fit service
  (`s3t.analysis.surface`) with its watches, its refresh timer and its own subscriptions; both
  in the backend's composition and in the health route; tests on the fake broker for SPY, SPX
  and AAPL; one test through a real pool.
- *Measure, part 2 (done 2026-11-28):* PRF-05 with a new `tools/timings.py surface`: a fit per
  underlying and per slice in the pool, the steps on the event loop, and the lag of a short
  timer while fits run.
- *Closes:* PRC-05 to PRC-07 and REF-02 (part 1, by tests); PRF-05 (part 2).

**WP-1.2 Chain service** (M)
- *Build:* the chain view by expiration and strike window; own IV and Greeks per flush as one
  array call; expected move per expiration; earnings and ex-dividend dates; index handling;
  stale marks; the grace period for closed views; the `chain` topic and the Arrow snapshot;
  Greeks in trader units per contract (requirements 4.6); a limit on the repeats of the IV
  solver's warning, which it logs on every flush (requirements 4.6, logging).
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
  a trading day with the real broker; the chain's Greek units against Tastytrade's chain.
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
| M1 R0 accepted | WP-0.9 done | The foundation runs across every session without a restart, and the limits are known |
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
| The surface fit is too slow or unstable on thin expirations | WP-1.1. Result, 2026-11-28. Speed: a fit of SPX takes 150 ms in a worker and the event loop is blocked for at most 5.6 ms (measured, synthetic market); a real slice takes about 85 ms, so a real surface takes seconds, in a worker (one slice seen, the surface computed). Stability: the real 0DTE slice of SPXW kept its own SVI in every minute of two days (a one-off). An expiration with few quotes, far-dated or of a small stock, is not answered (section 8) | SSVI only for those expirations; longer refresh timer |
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
  - The set-up proposals (requirements section 3) and the licence additions and exceptions
    under QA-07 were decided with the owner on 2026-11-22. `make audit` was added the same day.
  - The two npm audit exceptions (glob, minimatch) are revisited when
    license-checker-rseidelsohn 6 is stable (only a release candidate on 2026-11-22).
  - The fallback, if more advisories pile up on the npm licence checker: drop it, and let
    `backend/tools/licences.py` read the licence fields from `frontend/node_modules` itself.
    It already applies the policy and uses the checker only to collect. Packages that state
    their licence only in a LICENSE file then fail, and each needs a named exception.
  - Move to TypeScript 7 when svelte-check and openapi-typescript support it.
  - Upgrade Playwright together with the chart prototype and the backtester viewer.
  - In TypeScript, int64 fields arrive as `bigint`. `protobuf-es` 2.17 cannot turn that off: it
    knows `JS_STRING` only (read in its source, 2026-11-24). So product messages use no int64;
    an instant is a `double` of microseconds (decided 2026-11-24, WP-0.5). WP-0.7 follows it.
  - The OpenAPI schema is generated offline and not served (decided 2026-11-27, WP-0.7).
  - There is no CI. QA-04's "before a change is merged" means `make check` before a commit.
  - The protobuf Python code generated by the bundled protoc (35.1) needs a runtime of the
    same major version and no older. `uv.lock` keeps the two together. A `grpcio-tools`
    upgrade means `make gen`.
- From WP-0.2 (2026-11-22):
  - The proposals under the pricing core (requirements 4.6) and the CC0-1.0 allowance that
    numpy needs (QA-07) were decided with the owner on 2026-11-22.
  - REF-05 is closed for the clock. Which instant a contract settles at is a rule in
    `s3t.market.settlement` since WP-0.5 (the broker's `expires-at` is not that instant for
    stocks and ETFs; product definition, 11.1). It has its calendar since WP-0.6.
  - The quoting tick per contract, which widens the IV gate, is to come from the broker's chain
    (WP-1.2). `volcalc`'s tick tables per root were not ported.
  - Two service rules from `volcalc` were not ported into `pricing`. One: no Greeks where there
    is no IV (`enforce_iv_greeks_contract`). Two: PRC-13's fallback to the broker's Greeks. Both
    belong to the chain service (WP-1.2).
  - Index options priced on the parity forward get forward Greeks from `black76`. Delta and
    gamma against the index level need the factor F/S. Decided at WP-1.1 (2026-11-27): the
    factor F/S with the implied yield held fixed, `s3t.pricing.carry`; WP-1.2 applies it.
  - The trading-time clock (`volcalc` `tau_bus`) when it is wanted, on the WP-0.6 calendar.
  - The frozen `volcalc` values match bit for bit with numpy 2.5.4 and scipy 1.18.1. A numpy or
    scipy upgrade can break that; `tests/pricing/test_reference.py` says what to do then.
- From WP-0.3 (2026-11-22):
  - The proposals of the broker package were reviewed with the owner on 2026-11-22 and
    decided (requirements, section 3; the certifi exception under QA-07).
  - BRK-09 was added in the review of the proposals (2026-11-22). The client gives the data
    (`rest_status()`), the topic `status` publishes it (WP-0.7), and WP-0.8 shows it in the status
    bar. How the status bar lays it out is proposed at WP-0.8 (requirements, section 3, "The
    frontend shell"); the mock-ups were not changed.
  - Closed for the broker side only:
    - GEN-02 and SEC-05: the canary test (`tests/broker/test_canary.py`) covers the stream
      token since WP-0.4. The routes and the topics have their own since WP-0.7
      (`tests/api/test_canary_routes.py`). The MCP results follow in R4.
    - GEN-03: the nickname is configured and published since WP-0.7; shown in the top bar since
      WP-0.8.
    - GEN-09: reviewed for the stream at WP-0.4 and for the instruments at WP-0.5. A test
      (`tests/test_repo.py`) holds the keys of the instrument documents inside the broker
      module. The metrics are a record since WP-1.1, with the dividend fields; the same test
      holds their keys. WP-1.2 adds the earnings and the IV per expiration.
  - uvicorn's loggers: done at WP-0.7. The server gets no logging configuration of its own, so they
    reach the one handler of the process, which redacts.
  - The broker's REST limits are not known. The defaults (2 requests per second, burst 4) are
    in `[broker]`. WP-0.9 measures the real limits. The batch of 50 symbols per metrics call
    is untried: the live check asked for two.
  - Whether the quote-token response states an expiry: it does (`expires-at`, seen
    2026-11-23 at WP-0.4). The client reads it. Closed.
  - The expiry instant of a contract (`expires-at`) comes only from the per-contract
    instrument call. It is not needed per contract (WP-0.5): the settlement rule does not use it
    for equity options, and the exercise style is read from one contract per root. The
    allow-list did not grow.
  - The fake broker:
    - has the holidays and early closes of the calendar since WP-0.6;
    - has ES weeklies on Fridays only (live has other weekdays too);
    - carries fewer keys than the live documents. A package that parses more keys adds them
      to the fake. The keys that WP-0.5 reads are in it.
  - For the packages that type the broker's documents (note B, addendum "WP-0.3"):
    - The IV index rank can be above 1, and an entry of the IV list per expiration can lack
      its value (WP-1.1, WP-1.2).
    - SPY's dividend fields in the metrics were 0.0. The dividend schedule of an ETF may need
      another source (WP-1.1). **Corrected at WP-1.1:** that was SPX's document; SPY's amount
      is above zero (note B, addendum "WP-1.1"). No other source is needed for the amount.
  - `../tt` is not a git repository. The ported files name `tt` version 1.6.0 instead of a
    commit.
  - GEN-11 was added in the review of the proposals (2026-11-22). The broker choice and the mark
    in `status` and in the health route are there since WP-0.7; WP-0.8 shows it; R4 puts it in the
    MCP results. WP-0.4 gives the replay broker the recording's date for its "REPLAY" mark.
  - R5, when several accounts are read: account numbers may be shown with their last two
    digits, as in Tastytrade's privacy mode (the owner's suggestion, 2026-11-22), beside the
    nicknames of GEN-03.
  - `config/s3t.toml` was created from the example for the live check. It is git-ignored.
- From WP-0.4 (2026-11-23; part 1, the rework and part 2):
  - The proposals of the package were confirmed by the owner on 2026-11-24 (requirements,
    section 3). The stream file of a recording is one zstd frame with a block flush every
    3 s since then.
  - A reader's queue holds 2,000 batches by default, also the market state's. Whether that
    suits the real rates is judged with the measurements of WP-0.9.
  - For MKT-06 (WP-1.2): the quotes of SPXW options carried no bid or ask time in the
    streamer dumps (note B, addendum of 2026-11-24), so `bid_time` and `ask_time` are `None`
    there. The age of such a quote can only come from the receipt time.
  - The items for WP-0.7 are done. For whoever shows the use: `pending` is 0 while the session is
    down, because nothing is planned for a session that is not there. `granted` less `sent` says
    what is outstanding.
  - **For R2, the candle module:**
    - Two consumers that ask the same candle with different start times count twice, and
      one removal ends both at the broker. The start time is part of the identity of a
      subscription. Decide the rule with the candle module, its only user.
    - A candle subscription is sent again with its original start after a reconnect, so the
      whole history comes again. The candle module may narrow the start.
    - A snapshot ends with a marker: an event with both marks, `snapshot_end` and
      `removed`. It has a time and no prices. The candle module drops events with
      `removed`. The fake sends the same.
    - The marker comes in two kinds, which the record does not tell apart (note B, addendum
      of 2026-11-24). A history that is complete from the start asked for ends at dxFeed's
      "snapshot end". One that was cut at 8,000 bars ends at "snapshot snip", and its time
      is a midnight before the oldest bar. Add a mark if the chart has to know that the
      history was cut.
    - The server states the snip marker again during the day, after an update of the bar in
      progress that carries "transaction pending". So `snapshot_end` comes more than once:
      only the first one after a subscription ends the history. The conversion drops the
      "transaction pending" flag. Add it to the record if the candle module needs it.
    - A candle event without a time is ignored and counted (`ignored_events`; decided
      2026-11-24). The count is in the health route (WP-0.7).
    - `CandlePeriod` has no session and no price attribute (`tho=true`, `price=mark`, the
      alignment `a=s`). Add what CND-03 needs.
  - **For WP-0.9, with `streamer` running:** whether the subscription cap and the change
    limit count per session or per account, and the server's answer to a sixth session (5
    are published).
  - **Not verified live, all for WP-0.9:**
    - The manager against the live server: the refill after a drop, the pacing against the
      real change limit. The live checks ran the session alone.
    - The session in regular hours. A full day. The stall rule on real silence.
    - The server's answer beyond the subscription cap and beyond the change limit. The
      fake's cap answer is invented.
    - Removal in regular hours. In the late session it worked: SPY sent 37 quotes in 32 s
      before the removal and none in the 13 s after it (one run, 2026-11-23).
    - Whether the server states its field lists before the first subscription of a channel.
      The session does not depend on it: it needs the list only before the first data.
  - The stall rule (BRK-03) asks the exchange calendar in the backend (WP-0.7). The tools, which
    run without one, keep the stand-in: weekdays 09:30 to 16:00 New York, without holidays.
  - The fake stream:
    - moves only in the regular session; the live intraday feed runs around the clock on
      weekdays;
    - prices options on a flat 4% rate; since WP-1.1 with the quarterly dividends of SPY and
      AAPL, on the escrowed forward. The rate is a constant of the fake and equals the default
      of `[rates] flat_rate`; a configuration with another flat rate makes the app's model
      forward differ from the fake's;
    - states its Greeks in assumed units: theta per day, vega and rho per percentage point
      (WP-1.9 compares the live units);
    - serves candles in minutes, hours and days only, and needs about a second for the
      first 8,000 daily bars of a symbol;
    - keeps its smile at a quarter of the at-the-money variance or more, so that a 0DTE
      option has a price until it expires (fixed 2026-11-23; before, the fake failed after
      the close of an expiration day).
  - The replay broker does not loop at the end of a recording, and a pair subscribed late
    gets its last recorded event, not a candle history. It has no hard cap. Add any of
    these when a package needs it.
  - A recording is replayed at the interface level, so neither the DXLink decoding nor the
    conversion into records is in a replayed run. The reader's throughput is measured live
    at WP-0.9.
  - `backports.zstd` 1.8.0 is in the lock. The licence check and `make audit` passed with
    it (2026-11-23). It goes with the move to Python 3.14.
  - GEN-02 and SEC-05: the canary test covers the stream token and the recording
    (`tests/broker/test_canary.py`, `test_replay.py`). GEN-09 was reviewed for the stream:
    no dxFeed name crosses the interface, and a test reads a recording for it.
  - `tools/stream_check.py` talks to the session without the feed and reaches into it to
    close its own socket. That is the only way to force a drop from outside, and it stays
    inside the tool. The rule test names the tool as an exception.
  - An untracked `package-lock.json` lies in the repository root. It is not from a package
    of this plan. The owner decides whether it goes.
- From WP-0.5 (2026-11-24):
  - The proposals of the package were confirmed by the owner on 2026-11-24 (requirements,
    section 3, "The market package"). The settlement instant changes the product definition
    11.1. Nothing uses it before WP-1.1.
  - **To be seen live, a few requests (WP-0.9, or earlier if the owner wants):**
    - One future option read alone was refused in the run of 2026-11-23 and parses since the
      second run of 2026-11-24 (note B): `settlement-type` is `Future`, and AM or PM comes from
      `future-option-product.is-am-settled`. Seen with false only; an AM-settled quarterly was
      not read alone. Nothing uses the call yet: the chain has its own AM and PM.
    - Whether all contracts of one equity root have one exercise style (assumed by
      `Instruments.exercise`).
    - Dollars per point and strike factor of products other than /ES.
  - The items for WP-0.6 are done: `SessionTimes` on the calendar, its end (the end of the seventh
    year ahead), and configured closures. Holidays far ahead are rules, not announcements.
  - The items for WP-0.7 are done. The row carries no mark for a value that is not live: the
    `status` says from when on values are (decided 2026-11-27, requirements section 3).
  - **Done at WP-1.1:** the synthetic market priced SPY options to 16:15, the broker's
    `expires-at`, and the settlement rule says 16:00. The fake now prices to the settlement
    instant and quotes until `expires-at`, as the live feed does.
  - **Before the assignment flag (PRB-06):** correct the exercise style of /ES options per root.
    The broker states American for all of them, in the chain and in the document of one weekly
    (E2C). CME and CFTC documents have the weeklies and the end-of-month options as European
    and the quarterlies as American (note B; search excerpts, the current rulebook was not
    read). A further broker call cannot settle it.
  - **For WP-0.9:** watch `MarketStatus.lost`. After a loss no value counts as live until its
    next event, and only a reconnect subscribes again. If losses are seen, the feed subscribes
    again after one (decided 2026-11-24).
  - **For WP-1.2:**
    - No IV after the settlement instant. `tau_cal` stops at one minute, so a quote after the
      close of an expiring option would give a meaningless one.
    - The chain view says which expirations are left out (`OptionChain.left_out`).
    - Of 2.9 million real quotes 29.7% changed sizes only. IV need not be computed for those.
    - The first event after a subscription is the broker's current value, which can be hours
      old, with a fresh receipt time. The age of a value (MKT-06) is not the age of its
      receipt.
    - A price with a size of NaN occurs (10,371 bids and 30,269 asks of 2.9 million). What it
      means was not looked at. The conversion keeps the price.
  - A value that arrives for a pair already released lives until the next sweep (5 s at most).
  - The reading of chains (12,478 and 33,358 contracts live) runs on the event loop and was
    not timed. Judge it at WP-0.9 against the 50 ms of PRF-05.
  - The fake's futures-option ticks have two steps and the live /ES chain four. Left as it is.
- From WP-0.6 (2026-11-25):
  - The proposals of the package were confirmed by the owner on 2026-11-25 (requirements, section
    3, "The storage and reference data package", and the wording of STO-07 in 4.9).
  - The items for WP-0.7 are done. Left: a backend that runs for a year has a calendar that ends a
    year sooner. Seven years are ahead at its start, so a restart now and then is enough.
  - **For WP-0.9:**
    - The feed on 2026-11-27, an early close: whether late ends at 17:00, and the futures at 13:15.
      The 15-minute candles reach back 90 days, so it can be read until February.
    - The feed from 2026-12-06 on: whether the hour from 20:00 to 21:00 keeps its bars, and what a
      holiday evening looks like. The next holidays are 2026-12-25 and 2027-01-01.
    - The start of the calendar and the memory of pandas, on an idle machine.
  - **For R2:** the sessions of CND-02 and the future times of CND-10 are `sessions(day, class)`.
    A 4-hour bar spans two sessions. The futures' holiday hours are not verified, and whether the
    broker counts a holiday's shortened futures session to that day or to the next was not looked
    at.
  - **Versions:** pandas is held below 3.1 until `exchange-calendars` has a release for it.
  - A national day of mourning closes the stock market and shortens the futures day. The calendar
    closes both.
  - **FRED does not answer everything.** The application's client was answered every time (seven
    requests), and `curl` with another start date was not (two requests; note B). If the refresh
    fails in use, look there first. The status has the last error.
  - `exchange-calendars` holds the holidays as code. Its version is in `CalendarStatus`.
- From WP-0.7 (2026-11-25):
  - **Reviewed with the owner** (requirements, section 3, "The API package"). On 2026-11-26: the
    password, the wrong passwords, cffi, `quotes` and the state file as proposed; three changed.
    A session ends 48 hours after its last use, and the time can be raised. Every copy of the state
    file is kept, in a folder per year. The nickname refuses five digits in a row. On 2026-11-27 the
    rest as proposed, migration 0002 among them. A migration file that is in a state file is never
    changed; a correction is a new file.
  - Done at WP-0.8: a page that is open and not touched is logged out after 48 hours. The connection
    module takes `Bye` with `SESSION_ENDED` to the login screen, which says why.
  - **SEC-06 on Linux:** the backend was not run there. A unit file for a system service was not
    written, because it could not be tried. Do both when the target machine is there, or at WP-0.9.
  - **Not verified live:** nothing of this package ran against the real broker. The tests and the
    hand run are on the fake broker and on replays of it. WP-0.9 runs the backend for a day.
  - **For WP-0.8, the connection module** (each item done at WP-0.8, in
    `frontend/src/lib/connection.ts`, `frontend/vite.config.ts` and their tests):
    - The subprotocol is `s3t.v1.protobuf`; `s3t.v1.json` for reading along. A page that speaks an
      unknown one is refused, and reloads. A browser is told no status for a refused WebSocket: ask
      `GET /api/session` (401) whether a login is needed.
    - The numbers of the subscriptions rise and are not used again on a connection. A changed list:
      subscribe under a new number first, then end the old one. Ignore messages of an unknown number.
    - A heartbeat comes every 5 s. A browser is not told when its network has gone, so silence is the
      sign; 15 s would be three missed ones.
    - `Bye` says why the backend ends a connection; the close code 1012 means it stops or restarts.
    - The live rule (UI-05) needs `status` beside every other topic. Without a connection nothing is
      live, whatever the last `status` said.
    - A `Refused` ends the subscription. With `UNAVAILABLE` it is the tier rule: show the reason.
    - The content policy allows no inline script. Inline styles are allowed.
    - `npm run dev` on another port is another origin. Its proxy has to set the `Origin` of the
      backend.
    - The suite's backend is `backend/tools/e2e_backend.py`: the fake broker on a clock that starts in
      a regular session, so its quotes move every second. The hold after wrong passwords is for all
      browsers of the suite: try them in one project only, and fewer than five.
  - **For WP-0.9:**
    - The resident memory was 200 MB after a minute on the fake broker. One look on a loaded machine,
      not a measurement; it is the place to start for PRF-09.
    - A page that stops taking data (a suspended laptop). The backend gives it up after 30 s and
      releases what it asked for; the kernel keeps its socket until TCP gives up. Seen in tests only.
    - Reconnects with browsers open; the copy of the state file on a running day.
  - **For WP-1.3, before tickets are stored:** the state file is the same one whichever broker is
    chosen. Tickets of the synthetic market would lie among the real ones. A state file per source, or
    a mark on what is stored.
  - **For WP-1.8:**
    - Futures in `quotes`: the broker interface needs a call that says what a symbol is.
    - One request per symbol at 2 a second: a list of 100 symbols that were not read before takes
      about 50 s (computed, not measured). The broker has a call for several symbols (not on the
      allow-list, not looked at).
    - A row that is scrolled out of a list and back is empty until its first event (SUB-04).
  - **For R3:** `Features` reports tier T2 as absent until the recorder is there.
  - **For R4:** MCP is to be outside `/api/`, with its own token. `Site` sends everything outside to the
    frontend's files today.
  - **For the phone step:** the `Secure` mark of the cookie, `https` origins, and the host name of the
    proxy in `ServerConfig.origins`.
  - **Not built:** a limit on the number of connections or of requests beyond the login; a list of the
    open sessions (a new password ends them all); a way to set the password in the app.
  - The rule test of GEN-05 looks for scipy, `statistics` and the names of the error and normal
    functions. A second implementation by hand would pass it; it is a guard and not a proof.
  - FastAPI no longer lists the routes of included routers in `app.routes`
    (`fastapi.routing.iter_route_contexts` does). `starlette.testclient` is deprecated with httpx, so
    the tests reach the application over `httpx.ASGITransport` and a WebSocket client of their own
    (`tests/api/conftest.py`).
  - A WebSocket that is refused gets 403 for every reason. A denial with a response of its own makes
    uvicorn 0.54 log an error each time.
  - `json_format` takes an empty JSON list for an empty message. It is ignored, as every message
    without a known body is.
- From WP-0.8 (2026-11-27):
  - **Reviewed with the owner on 2026-11-27** (requirements, section 3, "The frontend shell"):
    every proposal decided. Changed: a compact status bar, a stale row greyed as a whole, times by
    zone with the choice of the user (UI-12, new), JetBrains Mono, and the accessibility checks now.
  - **The exchange zone of an instrument** (UI-12) is the zone of its calendar: New York for stocks,
    ETFs, indexes and their options, Chicago for futures (decided 2026-11-27). The topic `quotes`
    has New York symbols only, so nothing shows a difference yet. **For WP-1.8** (futures in
    `quotes`) and R2: the backend publishes the zone of an instrument; the app does not guess it
    from the symbol.
  - **OFL-1.1 before publication:** the licence asks that every copy of the font carries its
    copyright notice and the licence. Whether the font file's own metadata is enough was not
    checked; else ship the licence text beside it.
  - **Not verified live:** the app never ran against the live broker. The mark of GEN-11 is absent
    on the live broker by a unit test only. WP-0.9 runs the backend for a day: look at the status
    bar and the status screen then, in regular hours.
  - **Seen in a browser only on the fake broker, and some not at all.** Tested without a browser
    (unit tests on fake timers): a stall in the status bar, a hold after a 429, a refused grant, the
    reload when the backend speaks another version, a silent connection, a topic refused with
    `UNAVAILABLE` (GEN-04; the fake has every tier). The replay broker's mark was not seen.
  - **The look** was seen in Chromium at 1440 by 900: the light theme once, and after the review the
    dark theme, connected and during a restart of the backend. Firefox, WebKit and windows below
    1024 by 600 were not looked at. The compact status bar fits one line at 1440 px; at smaller
    widths it was not seen.
  - **Accessibility checks** (plan 3.5) run in the browser suite since the review: every new screen
    or state adds itself to `frontend/e2e/a11y.spec.ts`. A panel that scrolls is a stop of the Tab
    key while it overflows; the chain grid of WP-1.5 needs its own answer for the keyboard.
  - **For WP-1.8:** the symbols of the status screen's quote list are kept in the browser (local
    storage). Watchlists replace them. A row received before a reconnect stays stale until its next
    event, by the live rule; a symbol that rarely trades says "stale" for a long time.
  - The status screen shows two clocks: the browser's and the backend's wall clock (the connection,
    the start) and the time of the data (everything of the market). On the fake broker they are days
    apart; both are written in the zone the user chose (UI-12).
  - **The browser suite** runs one backend per engine (ports 4178, 4180, 4182, each with its control
    on the next port) and one test at a time per engine. A restart of the e2e backend starts the
    fake market's clock again at `fake_start`.
  - `serve()` has a stop event (`s3t.api.server`) and the fake stream the scenario `fixed_quotes`,
    both for the browser suite. The test control is in the tool, not in the application.
  - The information box is a native popover. It needs about Chromium 114, Firefox 125 and Safari 17
    or later (from memory, not looked up); the three engines of the suite have it.
- From WP-0.9 (2026-11-27, part 1). What happened to the items above that were marked "for WP-0.9":
  - **Done:**
    - The batch of 50 symbols per metrics call (from WP-0.3).
    - The session in regular hours, and removal on a ticking symbol in regular hours (from WP-0.4).
    - The manager against the live server: the first fill, and the refill after two forced drops,
      with 2,008 pairs (from WP-0.4).
    - Whether the server states its field lists before the first subscription of a channel: it does
      not (from WP-0.4).
    - The queue of 2,000 batches against real rates: far from full on a thin day. Monday judges it
      on a normal one (from WP-0.4).
    - The AM-settled /ES quarterly read alone; one exercise style per equity root; the dollars per
      point and strike factors of /NQ, /CL and /GC (from WP-0.5).
    - The parse of the chains against the 50 ms of PRF-05: 15 ms at most for SPX (from WP-0.5).
    - The calendar's start and memory: 1.1 s and 67 MB (from WP-0.6).
    - A daily copy of the state file in a running backend (from WP-0.7).
  - **Not measured, by decision of 2026-11-27:**
    - how the cap counts, per session or per account;
    - the server's answer beyond the cap, beyond the change limit, and to a sixth session;
    - the real REST limits.
    The client's defaults stay (requirements, section 3, "The live measurements").
  - **For part 2 (2026-11-30):**
    - PRF-09, the full day;
    - the stall rule on real silence, if any comes;
    - `MarketStatus.lost` over a day;
    - rates on a normal day;
    - the owner's look at the status bar and the status screen on the live broker, with a browser
      open during a forced drop (from WP-0.7 and WP-0.8);
    - the early close of 2026-11-27 from the 15-minute candles: whether the late session ends at
      17:00, and the futures at 13:15 (from WP-0.6);
    - the GEN-11 mark seen absent on the live broker (from WP-0.8).
  - **Carried, with their dates:**
    - The broker's feed from 2026-12-06 on: whether the hour from 20:00 to 21:00 keeps its bars,
      and a holiday evening (2026-12-25, 2027-01-01). For R2, or a check after the first of them
      (from WP-0.6).
    - SEC-06 on Linux and its unit file, when the target machine is there (from WP-0.7).
    - A page that stops taking data, a suspended laptop: seen in tests only (from WP-0.7).
  - **The rate error of the stream is a guess.**
    - The session treats an error as a rate error when its text holds "rate" (`dxlink.py`). Neither
      dxFeed's protocol nor Tastytrade's documents name a text or a code for an excess. The
      broker says only that an excess "fails rather than throttles".
    - Decided 2026-11-27 (owner): leave it. If an `ERROR` with `BAD_ACTION` or `UNKNOWN` is ever
      seen right after a chunk of changes, treat it as a rate error too. The manager stays at 8,000
      changes a minute, below the published 10,000.
  - **The refill after a drop** takes the manager's pace: 2,008 pairs in 14 s. The budget of
    10,000 would take about 67 s at 150 a second (computed). If that is too slow for a view, the
    pace can rise towards the published 10,000 a minute; the recorder class goes first anyway.
  - **The app does not show the process's memory against its bound.** The health route has it.
    Showing it in the status screen needs a field in the wire's status message: with R1's status
    work, or WP-1.9.
  - **The owner's state file was created by the rehearsal** of 2026-11-27, without a password. Set
    one with `tools/set_password.py`, also while the backend runs.
  - **Instruments that trade around the clock** (crypto, 24/7) or nearly so (forex, 24/5) are not
    in the product definition. Whether the broker serves them through this API was not checked.
    If they come:
    - the calendar needs a class without closed hours, or with only the weekend closed;
    - the stall rule needs "open" per class of what is subscribed (today it asks the stock market's
      regular session only, `ExchangeCalendar.market_open`);
    - nothing may assume a quiet night. Nothing does now: the daily copy works while the backend
      runs, the rates refresh every 12 hours, and the calendar reaches seven years ahead.
  - **A whole week** of the backend is observed in normal use after the day run, not measured
    (decided 2026-11-27).
  - The IV stand-in fails for 35% of the contracts, because it prices on the spot without
    dividends. That says nothing of R1. WP-1.2 measures its own flush against PRF-03.
  - On an early close the options of SPY and SPXW are quoted until 13:15 New York, the close plus
    15 minutes. The settlement rule settles them at 13:00, the close, as decided on 2026-11-24.
    WP-1.2 stops their IV at the settlement instant.
  - `tools/timings.py reader` replays at multiples of the pace. Above about 300 times the replay
    itself cannot keep up, so the capacity found (about 49,000 events a second) is a lower bound.

- From WP-1.1 (2026-11-27, part 1):
  - **The proposals** of part 1 and of part 2 were decided by the owner on 2026-11-27, every
    one as proposed (requirements, section 3, "The surface package"). REF-02 and the rows
    "Dividends", "Greeks" and "Surface" of the product definition 11.1 were reworded with them.
  - **Event days between expirations.** Total variance is spread evenly over the days between
    two expirations. An earnings day holds more of it, so a date just before earnings gets too
    much variance and one just after too little. It is the improvement worth making to the
    interpolation in time (agreed 2026-11-27); the earnings date comes with WP-1.2.
  - **Part 2 was open** when part 1 closed: the pool, the fit service and PRF-05. It is built
    since 2026-11-28 (the items "From WP-1.1, part 2" below).
  - **Not verified live, and not on real quotes.** The fit ran on quotes made from known
    smiles only. Whether it is stable on thin real expirations (section 7) is not answered.
    Ways to answer it without a live call: the streamer dumps of SPXW 0DTE quotes, fed to
    `fit_surface` by a one-off script; or a recording that holds the wings.
  - **A live look at a surface** needs about 2,300 subscriptions for SPY (computed), which is
    more than "a few at a time". It is a stated check for the owner to allow, in part 2 or at
    WP-1.9.
  - **The dividend frequency is configuration,** four a year unless a symbol is named. A
    monthly payer that is not named gets a wrong schedule; its parity forward is still right,
    and the flag for an implied dividend off the schedule shows it. The Profile event of the
    stream has an amount, a day and a frequency that the conversion drops; whether the live
    feed fills them needs one Profile subscription to see.
  - **An ex-date that is projected is an estimate.** Whether `dividend-ex-date` moves to a
    declared date before it arrives was not seen. SPY's next one should be 2026-12-18: read the
    metrics once in the weeks before it.
  - **The values that note B lists as SPY's units** (addendum "WP-0.3") may be SPX's: the check
    took the first document for SPY's, and the broker's order is its own. WP-1.2 reads them
    again when it types them.
  - **PRF-03 against PRF-05** (for WP-1.2). PRF-03 allows a flush of 100 ms and the flush runs
    on the event loop; PRF-05 allows the loop no block above 50 ms. One of the two has to give,
    or the flush leaves the loop.
  - **For WP-1.2:**
    - The forward of a fit belongs to the spot of that moment. Between fits, scale it by the
      spot: the ratio F / S moves little in 30 s.
    - `Slice.atm_iv` is there for a slice without a smile too (the quote nearest the money),
      for the expected move.
    - The spot Greeks of P9: `carry.proportional_greeks` for an index, `escrowed_greeks` with
      `carry.implied_pv` for a stock or an ETF on a parity forward. The rate derivative of an
      implied PV is not defined; rho then takes the schedule's or zero.
  - **For WP-1.4:**
    - `Surface.marginal_at(tau, forward)` needs the forward at the date: the model forward to
      it times `Surface.basis_at(tau)`.
    - A probability outside `k_min` to `k_max` of a slice is an extrapolation of the smile.
      Mark it, or do not show it (PRB-13).
    - A `Marginal` names its model: `svi`, `ssvi`, or both for a date between two slices.
    - Probabilities are clipped to 0 and 1. A slice whose butterfly check fails can have a
      negative density; its flag says so, the clip does not.
  - **Not built, by the decision of 2026-11-27:** the wing freeze with quality gates and the
    smoothing of the parameters in time (`bt_dataset`).
  - **Not handled:** the early-exercise premium of American puts in the parity forward of a
    long stock or ETF expiration. With the American pricer.
  - **The SVI fit warns** through the ported IV solver's logger for quotes it cannot invert.
    In a worker process without a handler these go to the terminal: part 2 gives the workers
    a null handler. **Done at part 2**, with a test.
  - **The pytest markers `reference` and `captures`** of sections 3.1 and 3.2 do not exist.
    Tests on real documents are still one-off runs: the metrics parser read the 133 captured
    documents and the recording of 2026-11-27 that way.
  - `tests/pricing` takes about 80 s of the suite's 140 s (seen, not measured on an idle
    machine). Most of it was there before this package.

- From WP-1.1 (2026-11-28, part 2):
  - **The fourteen proposals of part 2 were decided by the owner on 2026-11-28, every one as
    proposed** (requirements, section 3, "The surface package", part 2, the second list): the
    steps in which a set is chosen, the rules that keep it stable, the order of the demand, the
    spot, the filling, the fit on demand, futures products, the last release, how a view reads,
    failures, the worker's signals and its parent, what is left out of a set, the key of a
    slice, the fit's settings.
  - **Nothing takes a watch yet.** The service and the pool are in the composition and idle: no
    process, no consumer, no taker, no task (tests on the composition and on the real process).
    So the day run of WP-0.9 is not changed by part 2 either, and `make e2e` does not reach the
    service. The `chain` topic of WP-1.2 is the first to take a watch.
  - **PRF-05 is measured** (2026-11-28, `docs/measurements.md`): on the synthetic market, whose
    chains are smaller and whose smiles are cleaner than the live ones. WP-1.9 measures it again
    with the R1 figures, on a recording or live.
  - **An adjusted option root is left out of a set and counted** in the health record
    (`left_out`). No real one was seen: the 134 captured chain documents hold standard roots
    only. The chain view of WP-1.2 has to say that such a root has no surface IV.
  - **Not verified live.** The service ran on the synthetic market only. A live look at a
    surface needs about 2,300 subscriptions for SPY (computed) and is the owner's to allow.
  - **The fit on real quotes: a one-off on the streamer dumps** (2026-11-28;
    `docs/measurements.md`). The 0DTE slice of SPXW, at every minute of 2026-11-17 and
    2026-11-20: 780 of 780 fits kept their own SVI on every quoted strike, and all but the first
    minute of each day on the strikes the service would subscribe. That answers the risk for an
    expiration that is short in time and well quoted. It does not answer it for an expiration
    with few quotes, far-dated or of a small stock: the dumps hold none.
  - **On real quotes the SVI solver stops at its limit of 200 evaluations** in 88% of its calls
    (687 of 779, counted on one day), so a real slice takes about 85 ms, whatever its strikes:
    5 times a synthetic one. The slice is `ok`, because the solution passes the arbitrage
    check. How close it lies to the quotes was not looked at: the one-off kept counts and
    timings only. The limit and the tolerances are `volcalc`'s. Worth a look before WP-1.4
    builds probabilities on the smile: the RMSE of real slices, and whether a looser tolerance
    or a better start ends the fit sooner.
  - **A whole real surface was not timed.** At 85 ms a slice, a surface with 45 slices of
    their own SVI would take about 4 s a fit (computed; the count is assumed for SPX, not taken
    from a live chain). It runs in a worker, so PRF-05 holds; while a set fills, the fits come
    one after the other and not every 2 s.
  - **How many subscription changes a set makes over a day is not known.** The rules that keep
    a set stable are built on reasoning, not on a measurement: the synthetic market moves
    little in a test. A day of a live or a recorded market answers it, against the change
    budget of 8,000 a minute (SUB-02).
  - **A futures product is not fitted.** The service reads the chain of a stock, an ETF or an
    index. For `/ES` it needs the futures chain and the price of each future as the given
    forward of its expirations (`SliceInput.forward`), which the pricing side has since part 1.
  - **An index without a last trade has no spot,** and then nothing is chosen for it. Not seen.
    If it shows live, the Summary event's previous close is the stand-in.
  - **A worker of a tool imports the tool** (`spawn` runs the parent's main module again, unless
    it is the `__main__` of a package). It matters for no tool today, because none takes a
    watch but the timing tool.
  - **The workers' memory is outside PRF-09,** whose bound is on the backend's own process.
  - **A fit has no time limit.** A worker that hangs would hold one of the two workers until the
    backend is restarted. The fits are bounded by their iteration limits, so it should not
    arise; if it does, the pool can end its processes and start anew.
  - **The health record has `pool` and `surface`.** The generated types of the frontend changed
    with it. The app shows neither.
  - **The end of the backend with a fit under way** waits for that fit. Tested with the pool
    alone, not with the real process, which has no watch.
  - **The re-reading of the documents on a new day** is tested by setting the day back, not by
    letting a day pass.
  - **For WP-1.2:**
    - Take the watch when the view opens and release it in the view's `finally`, as the quotes
      view does with its demand. The grace period for a closed view is to hold the watch for
      that long.
    - The pairs of the chain view and of the surface overlap, and a pair counts once in the
      budget. How much the two cost together is not measured.
    - A first fit comes a few seconds after the watch, on the cores alone: every slice has its
      forward, and the slices are `sparse` or `none` until the wings are there. The status
      says `filling` until then.
    - `SurfaceFit.expirations` maps the key of a slice to its root, date and settlement
      instant. `SurfaceFit.spot` is the spot the forwards belong to.
  - The backend's suite took 117 s on a quiet machine (seen 2026-11-28, load average 2; not a
    measurement) and about 165 s under load; the tests of the service are about a third of it.
    The slowest is the one with three underlyings at once.
  - **The browser suite fails now and then on a quiet machine, and did so before this part.**
    The tests that restart the backend and wait for the stale rows (`live.spec.ts`, "the loss of
    the connection"; `a11y.spec.ts`, "while the connection is lost") sometimes never see them:
    the row is empty and then live again, within the 10 s of the wait. Seen 2026-11-28, load
    average 2 to 4: with part 2, 2 of 5 full runs failed (Chromium); on the commit of part 1,
    1 of 5 (Chromium and WebKit, the other test). The first test alone passed 24 of 24, and
    under load on 2026-11-27 and 2026-11-28 the suite passed. The numbers are too few to say
    that part 2 changed the rate. The cause is not found. A guess, not checked: the app's first
    attempt to reconnect reaches the old server while it shuts down, and the app drops its rows
    for a snapshot that never comes. It belongs to WP-0.8's code; look at it before WP-1.5
    adds tests of the same kind.

## 9. Starting a package in a fresh session

1. Read `CLAUDE.md`, then this plan's section for the package, then the documents it names
   under "Read first". Do not read the whole documentation set.
2. Check the status table and that the packages it depends on are done.
3. Work to the definition of done in section 1.
4. Before stopping: update the status table, `docs/measurements.md` and section 8, and say
   what was verified and what was not.
