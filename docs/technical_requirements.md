# Self-3-Trader: technical requirements and technology selection

Status: draft 1, 2026-11-22. Built on `docs/product_definition.md` (what and why),
`docs/research/E_architecture.md` (the architecture decisions with their reasons) and the
research notes B, C and D. Where this document and an older note disagree, this document
holds.

The implementation and testing plan is `docs/implementation_plan.md`.

## 1. How to read this

Each requirement has an identifier, a release and a way to verify it.

- **Release** is the first release that must meet it (product definition, section 6). R0
  requirements hold for everything after.
- **Verify:** T = automated test; M = measurement that is recorded; S = side-by-side
  comparison with a reference tool; R = review of code or configuration.
- "The backend" is the one server process. "The app" is the browser application.
- Figures marked *target* are goals to be measured, not measured facts. Figures marked
  *measured* come from the probes and the chart prototype.

## 2. System

One backend process on a home machine and one single-page web application. The backend holds
the broker credentials, the one streaming session, the stores and all numerics. The browser
draws. A language model reaches the same functions through MCP.

```
 Tastytrade REST ──┐                       ┌── REST (JSON, Arrow for bulk) ──┐
                   ├─ broker ─ market ─────┤                                 ├─ browser
 Tastytrade DXLink ┘  module   state       ├── WebSocket (Protocol Buffers) ─┘
                         │        │        └── MCP over HTTP (read-only) ───── Claude
                    feed and    analysis ── pricing core (pure library)
                  subscriptions    │
                                 store ── SQLite (state), Parquet (series), T3 (read-only)
```

Backend modules: `pricing`, `broker`, `subs`, `market`, `refdata`, `candles`, `analysis`,
`store`, `api`, `mcp`; later `alerts`, `scanner`, `recorder`.

## 3. Technology selection

Versions are pinned at set-up from the then-current stable releases unless a version is given.
Every dependency must carry a permissive licence or be a named exception; QA-07 checks it (the
policy is under QA-07).

| Area | Choice | Decided by |
|---|---|---|
| Backend language | Python 3.13, one locked dependency set with uv | Architecture note, section 2 |
| Numerics | numpy and scipy; no numba at the start | Same |
| Web framework | FastAPI on uvicorn, one asyncio process; a process pool for heavy numerics | Architecture note, section 1 |
| Broker access | httpx (REST), websockets (DXLink); own client based on `tt` | Code audit |
| Data models | pydantic for service inputs and outputs | Architecture note, section 5 |
| State store | SQLite in WAL mode, numbered migration files | Architecture note, section 4 |
| Series store | Parquet files read with DuckDB and polars; pyarrow | Same |
| Live transport | One WebSocket; messages defined in Protocol Buffers with generated Python and TypeScript code | Architecture note, section 3 |
| Protocol Buffers tools | `protoc` from the `grpcio-tools` wheel (a pinned development dependency, no system install) generates both sides. Python: the official `protobuf` runtime with `.pyi` stubs. TypeScript: `protoc-gen-es` with the `@bufbuild/protobuf` runtime. Both implement the canonical proto3 JSON mapping, which API-04's JSON switch relies on. **Decided 2026-11-22** | WP-0.1 |
| Bulk transport | Arrow over REST | Same |
| LLM interface | MCP Python SDK, served over HTTP inside the backend | Architecture note, section 5 |
| Calendars | exchange-calendars for sessions and holidays; the system time-zone database | This document |
| Login | argon2-cffi | Architecture note, section 6 |
| Frontend | Svelte 5, TypeScript and Vite as a single-page application with hash routing | Architecture note, section 7 |
| Price chart | Lightweight Charts 5.3.0 with `lightweight-charts-drawing` 0.11.1, both pinned and wrapped behind an own interface | Charting note, sections 10 and 11 (prototype) |
| Other charts | ECharts: payoff, skew, term structure, histograms | Architecture note, section 7 |
| Chain grid | Written new, on a virtual-row library | Same |
| Backend tools | ruff (lint and format), pyright, pytest, hypothesis, pip-licenses; mpmath for the pricing cross-check (proposed 2026-11-22, WP-0.2) | Architecture note, section 8 |
| Frontend tools | vitest, Playwright (Chromium, Firefox, WebKit), openapi-typescript, svelte-check, license-checker-rseidelsohn | Same; the prototype |

Not chosen yet: the library for GARCH fitting (stage b of the probabilities), and the reverse
proxy for TLS at the phone step.

Proposed at set-up (WP-0.1, 2026-11-22), not yet confirmed by the owner:

- **Version rule.** Exact pins of the newest stable release that is at least two weeks old.
  Two exceptions: TypeScript stays on 5.9, because svelte-check and openapi-typescript do not
  support TypeScript 7 (the current stable release). Playwright stays on 1.64, the version of
  the chart prototype and the backtester viewer, so one set of browsers serves all three.
- **Linting.** Python: `ruff check` and `ruff format --check`; pyright in its standard mode,
  as `volcalc` uses, so that ported code passes unchanged. Frontend: `svelte-check
  --fail-on-warnings` and strict TypeScript settings. ESLint and Prettier are not used.
- **Generated code is committed.** `make check` regenerates it into a temporary directory and
  fails on any stale, missing or orphaned file (QA-05).
- **Configuration** is one TOML file (`config/s3t.example.toml` documents it). Strict types;
  unknown keys are refused. The backend package is named `s3t` until the final name is chosen.

The broker package (WP-0.3). Decided 2026-11-22:

- **Environment file** (GEN-02). The variables are `TASTY_CLIENT_SECRET`,
  `TASTY_REFRESH_TOKEN` and the optional `TASTY_IS_TEST`, the names the chart prototype
  already uses. The sandbox switch stays in the environment file with the credentials,
  because sandbox credentials only work on the sandbox host. The backend warns, and does not
  refuse to start, when group or others can read the file.
- **Request limit** (BRK-04). A token bucket at 2 requests per second with a burst of 4,
  set in the optional `[broker]` section of the configuration. A 429 holds every request,
  not only the refused one, until `Retry-After`. A stated wait over 60 s fails the call at
  once, and the requests stay held until then. Without the header, and for 502, 503, 504 and
  network errors, the waits are exponential: from 1 s, doubling, with full jitter, at most
  30 s, 5 attempts in all. The broker's real limits are measured at WP-0.9, and the defaults
  change then if needed.
- **Token renewal** (BRK-01, BRK-07). The token is renewed when a call needs it and less than
  60 s of its life is left, not by a timer, so it lapses during a quiet spell. Only one
  renewal runs at a time. A 401 on a read leads to one renewal and one retry; a second 401
  is an error. A refused renewal (400, 401 or 403 from the token endpoint) is not retried:
  the client makes no further call until a restart with a new grant. A grant with more than
  read scope is refused the same way, and its token is dropped unused.
- **Redaction** (SEC-05, GEN-03). A filter on every log handler masks every registered
  secret, `Bearer` values, JWT-shaped strings, and the secret fields of JSON bodies and query
  strings. It masks too much rather than too little. The registered secrets are the
  environment secrets, the access, id and quote tokens, and the account number taken from
  the token's scope. Tokens issued at run time are kept as the last four of each kind, so a
  quote token that lives 24 hours stays masked however many access tokens follow it. Account
  numbers in `/accounts/<number>` and `accounts:<number>:` are masked in full as `<acct>`.
  Where R5 has to tell several accounts apart, the owner suggests showing the last two
  digits, as Tastytrade's privacy mode does; that is decided at R5.
- **Return types** (GEN-09). The streaming token is typed. Instruments, chains and market
  metrics are returned as the broker's JSON until the package that first uses each one types
  it: WP-0.5 for instruments and chains, WP-1.1 for the dividend fields, WP-1.2 for earnings
  and the IV per expiration. A package may type a document earlier when waiting would
  significantly increase the work to redo or lead to worse code. Checked at decision time:
  waiting costs about ten test assertions; typing now would mean guessing at the model.
- **Broker keys stay in the broker module** (GEN-09). Once a document is typed, the
  interface returns only the typed record, and the broker's JSON stays inside
  `s3t.broker.tastytrade`. Until then, nothing outside `s3t.broker` reads broker keys, except
  the package that is typing the document. A review rule.
- **The fake broker** lives in the backend package (`s3t.broker.fake`), not under `tests/`,
  so that the app can run on it for the end-to-end tests and for work without the broker.
  Its contents are listed in its module docstring and grow with the packages that need more:
  quotes, Greeks and candles at WP-0.4, keys at WP-0.5, holidays at WP-0.6. It uses real
  tickers (SPY, SPX, AAPL, /ES) with invented values, so GEN-11 marks its data.

Proposed 2026-11-22 (WP-0.3), not yet confirmed by the owner:

- **The REST state of BRK-09** as the client gives it (`RestStatus`). The token state is one
  of: none yet, valid, due (inside the 60 s margin), lapsed (expired while nothing was
  called), renewing, refused with its reason. With it come the expiry, the instant from which
  the next call renews, the last renewal and the number of renewals. The limit is shown as
  the configured rate and burst, beside the requests sent in the last 60 s. A 429 shows its
  hold end and the count of 429 answers so far.
- **GEN-11 for the replay broker** (WP-0.4). The mark reads "REPLAY", with the date of the
  recording.

## 4. Requirements

### 4.1 General

| ID | Requirement | Release | Verify |
|---|---|---|---|
| GEN-01 | The backend sends no order and calls no broker endpoint that changes account state. The broker client has no code path for such calls. | R0 | R, T |
| GEN-02 | Broker secrets exist only in the backend's environment file. They never reach the browser, the logs, the stores or an MCP result. | R0 | T, R |
| GEN-03 | The account number is masked in logs and never sent to the browser; the app shows a nickname. | R0 | T |
| GEN-04 | Every feature declares the data tier it needs (T1, T2, T3). When the tier is absent the feature is hidden or shown as unavailable with the reason; it never fails silently. | R0 | T |
| GEN-05 | One implementation of pricing, IV, Greeks and forwards serves the chain, the payoff views, the simulations, the REST routes and the MCP tools. | R0 | R, T |
| GEN-06 | Every probability and risk number carries the name of its model and its assumptions, on screen and in tool results. | R1 | T, R |
| GEN-07 | The repository contains no market data and no credentials. Downloaded data directories and environment files are git-ignored. | R0 | R, T |
| GEN-08 | Machine-specific values (listen address, host name, paths to the stores, the T3 location) are configuration, not code. | R0 | R |
| GEN-09 | The broker is reached through one interface. Everything the application asks of a broker is listed in that interface, so another broker can be judged against it. Broker-specific keys and shapes stay inside the broker module (decided 2026-11-22, section 3). | R0 | R |
| GEN-10 | All timestamps inside the backend, in the stores and on the wire are UTC instants. Conversion to a zone happens only at display. | R0 | T |
| GEN-11 | When the backend runs on the fake broker, the app says so everywhere: a permanent "SYNTHETIC DATA" mark in the status bar and in the header of every screen, and in every MCP result. The choice between the live and the fake broker is configuration, live by default, and logged at start. Decided 2026-11-22 (owner, WP-0.3 review). | R0 | T |

### 4.2 Broker connection

| ID | Requirement | Release | Verify |
|---|---|---|---|
| BRK-01 | The backend authenticates with OAuth2 using a refresh token and renews the access token before it expires (15 minutes). | R0 | T |
| BRK-02 | The backend holds one DXLink session. It reconnects after a drop, resubscribes the full demand and reports the gap. | R0 | T |
| BRK-03 | A stalled stream (connected, no data for a configured time while the market is open) is detected and treated as a drop. | R0 | T |
| BRK-04 | REST calls respect the broker's rate limits: a limiter in the client, and exponential back-off on HTTP 429. | R0 | T |
| BRK-05 | A replay implementation of the broker interface serves a recorded session, for tests and for work outside market hours. | R0 | T |
| BRK-06 | The connection state (connected, resubscribing, stalled, gap) is published to the app and shown. | R0 | T |
| BRK-07 | The backend works with a grant of scope `read` only. | R0 | M |
| BRK-08 | The client reads: instruments and option chains, market metrics, quote tokens, candles, and for the account: positions, balances and transactions. | R0 (market), R5 (account) | T |
| BRK-09 | The REST state is published to the app and shown in the status bar: the access token's state, with the time from which the next call renews it and a renewal under way or refused; the configured request rate beside the requests of the last minute; a hold after a 429. Decided 2026-11-22 (owner, WP-0.3 review). | R0 | T |

### 4.3 Feed and subscriptions

| ID | Requirement | Release | Verify |
|---|---|---|---|
| SUB-01 | The rest of the backend reaches the stream only through the feed interface: declarative demand (the full wanted set with a priority), data as plain batches, connection state in the stream. | R0 | R, T |
| SUB-02 | The subscription manager is the only caller of subscribe and unsubscribe. It keeps the session inside 25,000 subscriptions, 100 candle subscriptions and the change-rate limit, with a configured reserve. | R0 | T |
| SUB-03 | Demand has priority classes: recorder, open positions and alerts, visible views, background work. When the budget is short, lower classes are reduced first and the consumer is told. | R0 | T |
| SUB-04 | A closed view keeps its subscriptions for a grace period before release. | R1 | T |
| SUB-05 | A chain view subscribes by expiration and strike window, never a whole chain of a large root. | R1 | T |
| SUB-06 | Open interest is collected by a daily sweep and not held as a standing subscription. | R3 | T |
| SUB-07 | The manager reports its use (per class and in total) to the app. | R0 | T |

To measure in R0: whether the cap counts symbols or symbol-event pairs.

### 4.4 Market state and instruments

| ID | Requirement | Release | Verify |
|---|---|---|---|
| MKT-01 | The instrument model covers stocks, ETFs, indexes and their options, and is shaped to take futures and futures options: per-expiry underlying, exercise style, settlement type, settlement instant, multiplier, tick rules. | R0 | T, R |
| MKT-02 | The backend keeps the latest value per symbol and event type in memory, with the receipt time. | R0 | T |
| MKT-03 | Changed symbols are flushed to the app at the feed's aggregation period. A flush sends only the rows that changed. | R0 | T |
| MKT-04 | Own IV and Greeks are recomputed for changed contracts in each flush, as one array operation. | R1 | T, M |
| MKT-05 | An index has no usable quote: its price comes from trades, and its forward from put-call parity. | R1 | T |
| MKT-06 | A quote is marked stale when it is older than a configured age, and stale values are shown as such. | R1 | T |
| MKT-07 | A missing bid or ask is carried as missing, never as zero, through the wire format. | R0 | T |

### 4.5 Reference data and time

| ID | Requirement | Release | Verify |
|---|---|---|---|
| REF-01 | A continuous rate curve is built from SOFR and Treasury yields downloaded from FRED without a key; a flat configured rate is the fallback, and the app shows which is in use. | R0 | T |
| REF-02 | Dividends are discrete: the broker's next ex-date and amount, later ones projected from the payment frequency. | R1 | T |
| REF-03 | Earnings and ex-dividend dates come from the broker's market metrics. | R1 | T |
| REF-04 | Sessions (overnight, early, regular, late), holidays and early closes come from an exchange calendar, per instrument class. | R0 | T |
| REF-05 | Time to expiry is calendar time to the contract's settlement instant, in a 365-day year. | R0 | T |
| REF-06 | Zone offsets are taken per instant from the time-zone database, so each region's own daylight-saving dates apply. | R2 | T |

### 4.6 Pricing core

The conventions are those of the product definition, section 11.1.

| ID | Requirement | Release | Verify |
|---|---|---|---|
| PRC-01 | `pricing` is a pure library: no I/O, no global state, array inputs and outputs. | R0 | R |
| PRC-02 | It provides price, IV inversion, delta, gamma, theta, vega, rho, vanna, charm and volga for European options under Black-Scholes with discrete dividends and under Black-76. | R0 | T |
| PRC-03 | Code taken from `volcalc`, `backtester` and `tt` records its source repository and commit, and its tests are ported with it. | R0 | R |
| PRC-04 | Units inside are raw; the service layer converts to trader units: theta per calendar day, vega per volatility point, dollars per position. | R0 | T |
| PRC-05 | The forward per expiry comes from put-call parity over quoted strikes. | R1 | T |
| PRC-06 | A volatility surface is fitted per snapshot: SVI per expiry, free-gamma SSVI as fallback for thin expiries, with a check for butterfly arbitrage and a fit status per slice. | R1 | T |
| PRC-07 | The surface gives a risk-neutral density and cumulative probability per expiry in closed form. | R1 | T |
| PRC-08 | Payoff, breakevens, maximum profit and maximum loss are computed for any ticket, including stock legs and legs of several expirations, with the first expiry as horizon. Unlimited profit or loss is reported as such, not as a number. | R1 | T |
| PRC-09 | A P/L grid over price and date is computed for a ticket in one call. | R1 | T, M |
| PRC-10 | In-the-money values of American options use the European formula plus the assignment flag until an American pricer exists; the app says so where it matters. | R1 | R |
| PRC-11 | Buying power is a Reg-T estimate from the margin model taken from `backtester`, labelled as an estimate. | R1 | T |
| PRC-12 | A platform mode holds the conventions of the Tastytrade replicas and is used by nothing else. | R1 | T |
| PRC-13 | Own Greeks are used everywhere; the broker's are a cross-check and the fallback when the own inversion fails, and a fallback value is marked. | R1 | T |

Proposed in WP-0.2 (2026-11-22), not yet confirmed by the owner:

- **Discrete dividends (PRC-02)** use `volcalc`'s escrowed model: F = (S − PV(div)) e^{rτ},
  priced with Black-76 on that forward. The spot Greeks follow by the chain rule and hold the
  spot fixed while time passes. The Black-76 Greeks hold the forward fixed. One rate discounts
  the strike and the dividends.
- **Raw units (PRC-04).** Values are per unit of the underlying. Theta and charm are per year of
  the calendar clock. Vega, vanna and volga are per unit of σ, rho per unit of r. The list is in
  `s3t/pricing/__init__.py`.
- **Trader units (PRC-04)**, in `s3t/analysis/units.py`. Everything is per position: multiplier
  times signed quantity. Value in dollars; delta in share equivalents; gamma per one-dollar
  move; theta and charm per calendar day; vega, vanna and rho per point; volga per volatility
  point squared.
- **PRC-01 and logging.** The standard-library logger is allowed in `pricing`. It does no I/O
  without a handler, and only the application attaches one.
- **The rate download is not in `pricing`.** The FRED download and the stored rate history
  belong to `refdata` (WP-0.6, REF-01). The extraction table of the architecture note puts the
  download with the rate curve, which PRC-01 does not allow.
- **The independent cross-check (QA-01, plan 3.4)** is mpmath at 40 digits, a development
  dependency. It replaces `volcalc`'s py-vollib, which brings numba and pandas.

### 4.7 Probability and risk

The numbers and their stages are those of the product definition, section 6.4.

| ID | Requirement | Release | Verify |
|---|---|---|---|
| PRB-01 | POP is offered from two models through one switch: market-implied (from the fitted smile) first, platform replica second. POP is the leading number on the ticket. | R1 | T |
| PRB-02 | The platform replica of POP matches the `tt` capture set as well as the rules in `tt/FINDINGS.md` do today (86 of 87 captures). | R1 | T |
| PRB-03 | The platform replicas of P50 and CVaR stay within the tolerances of `tt/FINDINGS.md` on the capture set. | R1 | T |
| PRB-04 | Numbers at one date (POP, expected P/L, CVaR at expiry under the own model) are integrals over the distribution, not simulations. | R1 | T |
| PRB-05 | Probability of touch is given per short strike and breakeven, closed form, with the strike's own IV. | R1 | T |
| PRB-06 | The early-assignment flag has three states with the dollars at stake. No percentage is shown. | R1 | T |
| PRB-07 | Path numbers (reaching 25, 50 or 75% of maximum profit before the management date; the outcome mix with the mean result per outcome) come from a simulation whose path count is a setting, default 20,000. | R1b | T, M |
| PRB-08 | The management date and targets default by strategy class as in section 6.4. "Loss limit first" appears only when the user has set a loss limit. | R1b | T |
| PRB-09 | A simulation is seeded: the same inputs give the same result, and two tickets compared side by side share their random numbers. | R1b | T |
| PRB-10 | The sampling error of a simulated number is estimated and shown when it exceeds a configured size. | R1b | T |
| PRB-11 | The stress grid revalues the ticket fully over fixed price points with volatility shifts tied to the price move, and reports the worst loss. | R1b | T |
| PRB-12 | What-if keeps each contract's IV and applies one volatility shift; with several expirations the shift can be set per expiration. | R1 | T, S |
| PRB-13 | A number listed as "not shown" in section 6.4 is not computed for display under any setting. | R1 | R |
| PRB-15 | The probability of the underlying being below or above a given price is available at each expiration, from the selected model, and at any earlier date, from the surface interpolated in time. | R1 | T |
| PRB-14 | A stage b number is released only with a recorded validation against the T3 store: reliability curve, Brier score and PIT test, split by volatility regime. | R7b | M, R |

### 4.8 Candles and the time model

| ID | Requirement | Release | Verify |
|---|---|---|---|
| CND-01 | Offered resolutions: 1, 5, 15 and 30 minutes, 1 and 4 hours, 1 day. Weekly and monthly later. | R2 | T |
| CND-02 | Intraday candles cover the 24-hour feed. Each bar carries its session, taken from the exchange clock. | R2 | T |
| CND-03 | **Bar building rule:** when the broker's bars of a resolution do not start at the regular open, the backend builds that resolution from the finest aligned broker bars. Today that is 1 hour and 4 hours, built from 30-minute bars. Broker bars are used as they are where they align. | R2 | T |
| CND-04 | A built bar is marked as built, and its values equal the aggregation of its source bars (open of the first, high and low over all, close of the last, volume summed). | R2 | T |
| CND-05 | A daily bar's instant is the regular open of its session. | R2 | T |
| CND-06 | The backend keeps the intraday bars it has downloaded (a candle cache, T2), so the history of a symbol grows with use beyond the broker's depth. | R2 | T |
| CND-07 | Where the T3 underlying store has a symbol, history beyond the broker's depth is read from it. | R2 | T |
| CND-08 | A bar whose range is implausible against its neighbours is flagged as suspect; the chart can hide or mark suspect bars. The rule and its threshold are configuration. | R2 | T |
| CND-09 | Live candles are sent as last-bar updates and new bars on the `candles` topic, within the 100 candle subscriptions of the session. | R2 | T |
| CND-10 | Whitespace times for the future are generated by the backend from the session calendar, including holidays and early closes. | R2 | T |
| CND-11 | The daily candle's implied-volatility value is stored as a series per underlying, with its gaps as gaps. | R3 | T |

Measured depth of broker candles (SPY): 1 minute about 6 trading days, 5 minutes about 28
trading days, 15 and 30 minutes about 90 days, 1 and 4 hours about 9 months, daily since 1995.
Bars built from 30-minute bars therefore reach back 90 days on broker data alone.

Open detail: whether built bars are also cut at the regular close, so that no bar straddles
16:00.

### 4.9 Storage

| ID | Requirement | Release | Verify |
|---|---|---|---|
| STO-01 | Application state lives in one SQLite file in WAL mode: login, settings, tickets, watchlists, drawings, range templates, alerts, instrument cache. | R0 | T |
| STO-02 | The schema changes only through numbered migration files, applied at start-up, with the version recorded. | R0 | T |
| STO-03 | Recorded series are Parquet files partitioned by date and underlying. Files written during the day are compacted into one per day after the close. | R3 | T |
| STO-04 | The T3 store and the `volcalc` lake are read-only and optional; their paths are configuration. | R3 | T, R |
| STO-05 | A ticket is stored in the backend and addressed by id. Saving a ticket stores a context snapshot with it: underlying price, IV figures, the legs' quotes and Greeks, the time. | R1 | T |
| STO-06 | Drawings are stored per symbol with anchors as UTC instants and prices, in a versioned record. | R2 | T |
| STO-07 | The state file can be backed up by copying while the backend runs. | R0 | T |
| STO-08 | The IV-history set is one compact Parquet file per root, derived from the lake's surfaces: at-the-money IV at fixed maturities, model-free IV, skew measures and realised-volatility windows, per day and per intraday snapshot. | R3 | T |

### 4.10 API and transport

| ID | Requirement | Release | Verify |
|---|---|---|---|
| API-01 | Each service is a plain function with typed input and output. REST routes and MCP tools are thin wrappers over the same functions. | R0 | R, T |
| API-02 | TypeScript types for REST are generated from the OpenAPI schema; hand-written copies are not allowed. | R0 | T |
| API-03 | Live data uses one WebSocket. The client subscribes to topics; the server sends a snapshot, then changes. After a reconnect the client resends its topics and gets fresh snapshots. | R0 | T |
| API-04 | WebSocket messages are defined in `.proto` files with generated code on both sides. A development switch sends the same messages as readable JSON. | R0 | T |
| API-05 | Topics: `chain`, `quotes`, `ticket`, `candles`, `status`; later `positions`, `alerts`. | R0 to R6 | T |
| API-06 | Bulk snapshots (candle history, a whole expiry) travel as Arrow over REST. | R1 | T |
| API-07 | The selected symbol and ticket are shared through the backend, so several windows follow each other. | R1 | T |
| API-08 | The message layer does not depend on the carrier, so the WebSocket can be replaced later. | R0 | R |

### 4.11 Security and deployment

| ID | Requirement | Release | Verify |
|---|---|---|---|
| SEC-01 | One user. The password is stored as an argon2 hash. A session is a cookie with HttpOnly and SameSite=Strict. | R0 | T |
| SEC-02 | Every REST route, the WebSocket and the MCP endpoint require authentication. The WebSocket also checks the origin. | R0 | T |
| SEC-03 | The backend listens on one configured address, never on all interfaces. | R0 | T |
| SEC-04 | MCP uses its own bearer token, separate from the browser session, and can be revoked without changing the password. | R4 | T |
| SEC-05 | Logs redact tokens. | R0 | T |
| SEC-06 | The backend runs on macOS (development) and Linux (target) as an ordinary user process or system service, without a container. | R0 | M |
| SEC-07 | The backend serves the built frontend itself. | R0 | T |
| SEC-08 | A health route reports broker connection, stream state, subscription use, store versions and the reference data in use. | R0 | T |

### 4.12 Frontend, general

| ID | Requirement | Release | Verify |
|---|---|---|---|
| UI-01 | Each screen has an address (hash route) and can run in its own browser window. | R1 | T |
| UI-02 | A ticket is addressed by id in the URL; any screen can open it. | R1 | T |
| UI-03 | One WebSocket client module, one store per topic. | R0 | R |
| UI-04 | The app works in current Chromium, Firefox and WebKit browsers on a laptop. Phone layouts are later. | R1 | T |
| UI-05 | Loss of the connection is visible on every screen, and live values are marked stale until it returns. | R0 | T |
| UI-06 | Colours come from the design tokens of the backtester viewer; a side is shown by colour and sign, not by words alone, and never by colour alone. | R1 | R |
| UI-07 | Numbers use tabular figures and fixed decimals per column. | R1 | R |
| UI-08 | At laptop size the window does not scroll: the top bars and the status bar stay in place and each area that can overflow scrolls on its own. Below a stated width the areas stack and the page scrolls as a whole. | R1 | T |
| UI-09 | Pop-ups and menus are drawn above the scrolling areas and are never cut off by them. | R1 | T |
| UI-10 | Gaps between panels and margins at the window edges are small and come from one spacing scale. | R1 | R |
| UI-11 | Instructions and explanations open from an information icon in the header of their panel, one component used everywhere. A legend needed to read a view stays visible and follows the view (graph or table). | R1 | T, R |

### 4.13 Trade workbench

The layout is that of the product definition, section 6.3.

| ID | Requirement | Release | Verify |
|---|---|---|---|
| WB-01 | The option chain lists all strikes of an expiration in a virtual grid; several expirations can be open; the chain opens at the expiration nearest 45 DTE. It fills the height of the window and scrolls inside its panel, with its header, the row of the open expiration and the column titles staying in view, and it opens with the at-the-money line in view. | R1 | T |
| WB-02 | Default columns are delta, bid and ask per side with strike and IV in the middle. Dragging the divider reveals open interest, volume, theta and gamma, in that order. | R1 | T |
| WB-03 | The at-the-money position is a line between two rows; the expected range is a tint on the strike and IV cells. Instructions and the legend open from one information icon and take no permanent space. | R1 | T |
| WB-04 | A click on a bid sells one contract and on an ask buys one. The quantity mark opens a control to raise, lower or remove, in the chain and in the ticket. | R1 | T |
| WB-05 | A ticket row shows signed quantity, expiration, strike, type, delta, theta, bid, mid and ask. The ticket shows its totals: price at natural and mid, maximum profit and loss, breakevens, buying power, extrinsic value and position Greeks. All breakevens are shown, however many: two to a row, never broken across lines, with the box widening and the type size shrinking as needed. | R1 | T |
| WB-06 | A ticket can mix expirations. | R1 | T |
| WB-07 | The payoff panel shows a graph and a table over price and date, with date and volatility controls, and can be maximised to carry the ticket and probability figures. | R1 | T, S |
| WB-08 | Earnings and ex-dividend dates that fall before an expiration are shown on that expiration and on the ticket. | R1 | T |
| WB-09 | A ticket has one name, generated from strategy, symbol and expiration until the user edits it, with a way back to the generated name. Save stores the ticket under its name; Load lists the saved tickets of the symbol and reopens one. Watchlists can be created and edited. | R1 | T |
| WB-10 | Changing a leg, the date, the volatility shift or the model updates the panel without a full reload; the browser interpolates the grid between requests. | R1 | T, M |
| WB-11 | Payoff values agree with OptionStrat for the same legs and inputs within a stated tolerance, on a checklist of structures. | R1 | S |
| WB-12 | The limit price is editable by typing and by one-cent steps. Untouched, it follows the market mid of the new legs. An entered value stays fixed, is visibly marked as entered, and is the entry price for the payoff and the ticket totals. One control returns it to following the market. | R1 | T |
| WB-13 | Stock can be a leg, in shares, and takes part in payoff, Greeks, breakevens and probabilities. | R1 | T |
| WB-14 | A held position (stock or option) can be added to a ticket and is marked as held. In R1 it is entered by hand with its purchase price; from R5 it can be picked from the account's positions. | R1, R5 | T |
| WB-15 | A held leg shows its purchase price by default, with a switch to the current price. The switch also sets the basis of its profit and loss: from the purchase price, or from now. Net, natural and limit prices cover the new legs only. | R1 | T |
| WB-16 | Pointing at a price in the payoff graph shows the price, the probability of the underlying being below and above it at expiry and on the selected date, and the P/L there. | R1 | T |
| WB-17 | A range control sets how far the price axis of the payoff graph and table reaches. Where the expiry line leaves the graph still rising or falling, a mark states it: unlimited, or the value reached at a price of zero. | R1 | T |

### 4.14 Chart

Requirements are those of the product definition, section 6.1; the time model is that of the
charting note, section 11.

| ID | Requirement | Release | Verify |
|---|---|---|---|
| CH-01 | Candles with volume in the price pane; SMA 15, 50, 100 and 200; RSI and ATR in panes of their own, each switchable. | R2 | T |
| CH-02 | The chart library and the drawing plugin are used only through an own interface module, so either can be replaced. | R2 | R |
| CH-03 | Drawing tools: trendline, horizontal line, vertical line, ruler (price, percent, bars, elapsed time). Select, move, delete and undo. | R2 | T |
| CH-04 | The ruler's elapsed time is computed from the instants, not from chart times. | R2 | T |
| CH-05 | Drawings are saved per symbol and shown on every resolution. A drawing made on the daily chart is anchored at the regular open. | R2 | T |
| CH-06 | The time axis shows exchange time by default and can be switched to UTC or local time. Daily charts always show the trading date. | R2 | T |
| CH-07 | Chart times rise strictly in every zone. Where a zone sets its clocks back while bars keep coming, the earlier offset is carried until the data has a gap. | R2 | T |
| CH-08 | Sessions outside regular hours are tinted: early and late hours lightly, overnight a little more. | R2 | T |
| CH-09 | Future time exists on the axis as whitespace in a series of its own, far enough for the longest expiration of the shown ticket. | R2 | T |
| CH-10 | The ticket is drawn on the chart as switchable layers: strikes, breakevens, expected-range cone with the expiry line, payoff profile at the right edge. Layers take no pointer input. The profile is scaled to the prices in view. The layers are switched from the chart's legend line. | R2 | T |
| CH-11 | Strike lines take part in the autoscale only while the latest bar is on screen, and this can be switched off. | R2 | T |
| CH-12 | Range templates draw a defined intraday range and its critical moments automatically for the current day. | R2 | T |
| CH-13 | Horizontal lines at the short strikes of open positions. | R5 | T |
| CH-14 | Functions in daily use in TradingView are present, on a checklist. | R2 | S |
| CH-15 | The library's attribution requirement is met. | R2 | R |

### 4.15 Volatility tools

| ID | Requirement | Release | Verify |
|---|---|---|---|
| VOL-01 | IV against realised volatility per symbol: the IV series, realised volatility over chosen windows, their difference, and where today's difference stands in the symbol's own history. | R3 | T |
| VOL-02 | The comparison is always made within one IV series. The broker's series and own IV are never mixed in one percentile. | R3 | T, R |
| VOL-03 | With broker data alone the IV series is the daily candle value since 2016. Where the IV-history set exists, IV matched to DTE and delta is offered as well. | R3 | T |
| VOL-04 | Skew and term structure from the fitted surface; IV rank and percentile. | R3 | T |
| VOL-05 | Expected range and its estimated change over time. | R3 | T |
| VOL-06 | Bid/ask width per expiration and strike. | R3 | T |
| VOL-07 | Open interest by strike, from the daily sweep, labelled with its date. | R3 | T |
| VOL-08 | Earnings implied move against past realised earnings moves. | R3 | T |
| VOL-09 | The backend records a daily chain and surface snapshot for the symbols on the watchlists (T2). | R3 | T |

### 4.16 LLM tools

| ID | Requirement | Release | Verify |
|---|---|---|---|
| MCP-01 | Tools are read-only. No write tool is registered at all. | R4 | T, R |
| MCP-02 | Two tool sets, market and account. The account set is offered only when the user has switched it on; it is off by default. | R4 | T |
| MCP-03 | Tools work at task level (evaluate a ticket, chain slice, skew and term structure, expected move, IV against realised volatility, watchlists); none returns a whole chain. | R4 | T |
| MCP-04 | Every number in a result carries its unit, its as-of time, its data tier and, for a probability, its model. | R4 | T |
| MCP-05 | A tool that produces a trade idea returns a ticket id that the app can open. | R4 | T |
| MCP-06 | A daily digest is offered as an MCP resource. | R5 | T |
| MCP-07 | A tool result equals the REST result for the same inputs. | R4 | T |

### 4.17 Later releases

Stated at the level known today; each is detailed before its release starts.

| ID | Requirement | Release | Verify |
|---|---|---|---|
| POS-01 | Positions, balances and transactions are read from Tastytrade and shown as they are; the view states that it covers this account only. | R5 | T |
| POS-02 | Position marks and Greeks come from the stream and the own pricing core. | R5 | T |
| POS-03 | Transactions are grouped into campaigns: the legs, rolls and assignments of one trade, with net credit, total result and days in trade. Grouping can be corrected by hand. | R5 | T |
| POS-04 | Roll comparator: candidates side by side with credit, breakevens, days added and the changes in delta, buying power and probabilities. | R5 | T |
| POS-05 | Each position shows its status against the management rules. | R5 | T |
| POS-06 | Portfolio view: beta-weighted delta, theta, concentration by underlying and expiry week, buying-power use, and a scenario grid. | R5 | T |
| REV-01 | Performance by strategy, tag, DTE, delta and IV rank at entry, and by exit reason. | R5b | T |
| SCN-01 | A scanner filters on market metrics first, then sweeps chains in rotation within the background budget, and shows the age of each result. | R6 | T |
| SCN-02 | IV against realised volatility is a scanner criterion. | R6 | T |
| SCN-03 | Alerts are evaluated in the backend and delivered to the app; the management rules of R5 are alert conditions. | R6 | T |
| REC-01 | The recorder module reproduces the `streamer` output contract exactly, and replaces the streamer only after its output is shown equal on parallel runs. | Recorder | T, M |
| REC-02 | A full recorder queue is logged and counted and never blocks forwarding. | Recorder | T |

### 4.18 Performance

Targets unless marked measured. Each is measured in the release named and recorded.

| ID | Requirement | Release | Verify |
|---|---|---|---|
| PRF-01 | A P/L grid for a four-leg ticket (200 prices, 60 dates) is computed in under 50 ms of server time. | R1 | M |
| PRF-02 | From a changed control to the redrawn payoff panel: under 200 ms on the LAN. | R1 | M |
| PRF-03 | A flush that recomputes IV and Greeks for 1,000 changed contracts takes under 100 ms and never delays the stream reader. | R1 | M |
| PRF-04 | A path simulation with 20,000 paths, 45 daily steps and four legs finishes in under 1 s. | R1b | M |
| PRF-05 | A surface fit for one underlying runs in the process pool; the event loop is never blocked for more than 50 ms by numerics. | R1 | M |
| PRF-06 | The chart shows 8,000 bars with four moving averages and two indicator panes in under 300 ms after the data has arrived. Measured in the prototype: 50 to 145 ms across three browsers. | R2 | M |
| PRF-07 | A live candle update costs under 10 ms in the browser and delays no frame at two updates a second. Measured in the prototype: 1 to 5 ms, no late frame. | R2 | M |
| PRF-08 | The chain grid scrolls and updates without late frames at one update a second for 400 visible-or-buffered rows. | R1 | M |
| PRF-09 | The backend runs a full trading day without restart and without growth of memory beyond a configured bound. | R0 | M |

### 4.19 Quality

| ID | Requirement | Release | Verify |
|---|---|---|---|
| QA-01 | The pricing core has unit tests, property tests (parity, monotonicity, bounds) and the frozen reference values from `volcalc`. | R0 | T |
| QA-02 | Services and routes are tested against the replay broker, so results are repeatable. | R0 | T |
| QA-03 | The app is tested with Playwright against the backend in replay mode, in Chromium, Firefox and WebKit. | R1 | T |
| QA-04 | Type checks (pyright, TypeScript) and linting pass before a change is merged. | R0 | T |
| QA-05 | Generated code (OpenAPI types, Protocol Buffers) is checked to be current. | R0 | T |
| QA-06 | R1 and R2 are accepted against side-by-side checklists with OptionStrat and TradingView. | R1, R2 | S |
| QA-07 | A licence check runs over all dependencies, runtime and development, of both projects. It uses an allow-list of permissive licences, and anything else must be a named exception with its reason. Decided 2026-11-22. The policy is under this table. | R0 | T |
| QA-08 | Timing checks are run on an idle machine and record the machine load with the result. | R0 | R |

Licence policy of QA-07, implemented in `backend/tools/licences.py`:

- The check evaluates the SPDX expression of each package. With AND, every part must be
  allowed; with OR, one part is enough. The collecting tools are not trusted with this: the
  npm checker passes `MIT AND CC-BY-3.0` because one part is allowed.
- Allowed, decided 2026-11-22: MIT, Apache-2.0, BSD-2-Clause, BSD-3-Clause, ISC, 0BSD, Zlib and
  PSF-2.0, with the metadata spellings Python packages use for them.
- Allowed, proposed 2026-11-22 (found at set-up, not yet confirmed): Python-2.0 (the PSF
  licence under another identifier), BlueOak-1.0.0 (a permissive licence; 12 npm development
  packages) and CC0-1.0 (a public-domain dedication). numpy, added in WP-0.2, needs CC0-1.0:
  its expression is `BSD-3-Clause AND 0BSD AND MIT AND Zlib AND CC0-1.0`.
- Named exceptions, each for the licence exactly as reported, so a change of licence ends the
  exception:

| Package | Licence | Reason | Status |
|---|---|---|---|
| hypothesis | MPL-2.0 | Development only, used unmodified | Decided 2026-11-22 |
| lightningcss and its platform binaries | MPL-2.0 | A build tool of Vite, used unmodified, and not part of the built app | Proposed 2026-11-22 |
| spdx-exceptions, spdx-ranges | CC-BY-3.0 | Licence-list data inside the licence checker itself; development only | Proposed 2026-11-22 |
| certifi | MPL-2.0 (reported as `Mozilla Public License 2.0 (MPL 2.0)`) | The CA certificate bundle of httpx, data used unmodified; a runtime dependency | Decided 2026-11-22 (WP-0.3) |

## 5. Open points

To measure:

- Whether the subscription cap counts symbols or symbol-event pairs (R0).
- REST rate limits and stream behaviour in regular hours (R0).
- Throughput of the stream reader together with per-flush IV inversion (R0).
- The grid round trip and the simulation time against PRF-01, PRF-02 and PRF-04 (R1, R1b).
- Touch on a real tablet or phone (before the phone step).
- Whether the IV index of the market metrics is the same series as the candle value, and at
  what time of day the daily value is taken (R3).

To decide:

- Whether built bars are cut at the regular close (CND).
- The open-source licence and the final name.

To do, outside the releases: read the broker's API terms of use before publication; the
margin dry-run test; the studies of the product definition, section 11.2.

## 6. Requirements by release

| Release | Requirement groups that start there |
|---|---|
| R0 Foundation | GEN, BRK, SUB-01 to 03 and 07, MKT-01 to 03 and 07, REF-01, 04 and 05, PRC-01 to 04, STO-01, 02 and 07, API-01 to 05 and 08, SEC (except 04), UI-03 and 05, PRF-09, QA (except 03 and 06) |
| R1 Trade workbench | SUB-04 and 05, MKT-04 to 06, REF-02 and 03, PRC-05 to 13, PRB-01 to 06, 12, 13 and 15, STO-05, API-06 and 07, UI, WB, PRF-01 to 03, 05 and 08, QA-03 and 06 |
| R1b Path numbers and stress | PRB-07 to 11, PRF-04 |
| R2 Chart | REF-06, CND-01 to 10, STO-06, CH (except 13), PRF-06 and 07 |
| R3 Volatility | SUB-06, CND-11, STO-03, 04 and 08, VOL |
| R4 LLM tools | SEC-04, MCP (except 06) |
| R5 Positions and risk, R5b Review | BRK-08 (account), POS, REV, MCP-06, CH-13 |
| R6 Scanners and alerts | SCN |
| R7b Probabilities, stage b | PRB-14 |
| Recorder | REC |
