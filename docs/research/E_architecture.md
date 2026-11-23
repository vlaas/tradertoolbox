# Research E, part 2: architecture proposal

Draft 1, 2026-11-20. A proposal for review, not a decision. It builds on the code audit
(`E_code_audit.md`), the API probe (`B_broker_api_capability.md`) and the pricing conventions
decided in `docs/product_definition.md` section 11.1.

Nothing here has been prototyped. Timing figures are estimates unless a source is named.

## 1. Shape

One backend process on the home server and one single-page web application in the browser.

```
 Tastytrade REST ──┐                       ┌── REST (JSON, Arrow for bulk) ──┐
                   ├─ broker ─ market ─────┤                                 ├─ browser
 Tastytrade DXLink ┘  module   state       ├── WebSocket (live updates) ─────┘
                         │        │        └── MCP (read-only tools) ───────── Claude
                 subscription   analysis ── pricing core (pure library)
                    manager        │
                                 store ── SQLite (state), Parquet (series), T3 store (read)
```

| Module | Job |
|---|---|
| `pricing` | Pure library, no I/O: price, IV, Greeks, forward, clocks, rate curve, dividend schedule, SVI and SSVI, density, payoff, path simulation, margin, expected move |
| `broker` | An interface plus the Tastytrade implementation: token refresh, REST, one DXLink session with reconnect, resubscribe and stall detection. A second implementation replays a recorded session for tests and for work outside market hours |
| `subs` | Subscription manager: the only caller of DXLink subscribe. Counts demand per consumer, enforces the budget and the change rate |
| `market` | In-memory latest value per symbol and event; instrument registry; own IV and Greeks per contract |
| `refdata` | Rates (FRED), dividends (broker market metrics), exchange calendar and sessions |
| `analysis` | The services: chain view, ticket evaluation, surface fit, expected range, risk. Typed inputs and outputs |
| `store` | Persistence |
| `api` | REST and WebSocket routes, login |
| `mcp` | Tool definitions over the same services |
| `alerts`, `scanner`, `recorder` | Later releases |

**Decided 2026-11-20: one asyncio process to start** (FastAPI on uvicorn) that also serves
the built frontend, as the backtester viewer does. Heavy numerics (surface fits, simulations)
run in a process pool so they cannot stall the stream reader.

### Later split (not decided; current preference is option 3)

A restart of the process that holds the stream drops every subscription, which is harmless
for interactive use and a gap for recording. Three ways to split were considered:

| Option | Layout | For | Against |
|---|---|---|---|
| 1 | The main process gains a gateway interface; today's streamer connects to it | The main process only grows | A main-process restart breaks everything |
| 2 | A separate gateway; the main process and the streamer both connect to it | The gateway is small and stable and rarely changes | Three processes |
| 3 | A gateway that is also the recorder; the main process connects to it | Fewer processes; still small and stable | A disk stall could stall forwarding |

The risk of option 3 is contained by design: the reader hands each message to a bounded
queue for the disk writer, which runs on its own thread, and forwards it independently. A
full writer queue is logged and counted; it never blocks forwarding.

### Feed interface (built from the start)

So that option 2 or 3 is a small step later, the rest of the backend reaches the stream only
through one interface, with an in-process implementation now and a socket implementation
after the split.

- **Demand is declarative.** A consumer states the full set of symbol and event pairs it
  wants, with a priority. Either side can resend the set after a reconnect; there is no
  call-by-call subscription history to replay.
- **Data crosses as plain batches:** symbol, event type, fields as received, receipt
  timestamp, sequence number. No object references and no callbacks. (Changed 2026-11-23:
  the fields are the application's own records, not "as received". The broker module
  converts them. See the technical requirements, section 3.)
- **Connection state is part of the stream:** connected, resubscribed, stalled, gap from
  sequence number to sequence number.
- **REST stays outside the interface.** Each process refreshes its own access token, so the
  gateway carries the stream only.
- (Added 2026-11-23, WP-0.4.) Asking and reading are separate: a consumer states demand
  with one priority class, and a reader gets every batch of the event types it names. The
  interface is `s3t.subs.feed`; the decisions are in the technical requirements, section 3.

## 2. Pricing core placement (open decision 2)

**Decided 2026-11-20: backend only, in Python with numpy and scipy.** Broker quotes already
arrive about once a second, so a round trip through the backend does not change the picture.
0DTE, where delay matters most, is secondary and its entries and exits are likely left to a
trading bot.

- It keeps principle 5: one implementation, so the chain, the payoff graph, the simulation
  and the LLM tools return the same numbers.
- The surface fit and the simulation need scipy and the market state, which live in the
  backend.
- Interactive what-if does not need pricing in the browser. The backend returns a P/L grid
  over price and date for the ticket; the browser draws and interpolates it. A change of an
  input (legs, volatility shift, model) requests a new grid.

Estimated cost of one grid: 4 legs, 200 prices and 60 dates are 48,000 European evaluations,
a few milliseconds in numpy. A P50 simulation with 10,000 paths, 45 daily steps and 4 legs is
1.8 million evaluations, some tenths of a second. Network round trip on the VPN adds tens of
milliseconds. The grid round trip is measured in the R0 prototype; it is not a gate.

Rejected: a second implementation in TypeScript or WebAssembly. Two cores would have to be
kept equal by tests for no gain that the estimate shows.

No numba at the start. Python 3.13, one locked dependency set (uv).

## 3. Live data path

1. The DXLink reader task decodes a message and updates `market` state. (The recorder, when
   it exists, writes the raw message first.)
2. Changed symbols go to a dirty set.
3. A flush matched to the feed's aggregation period (the probe was granted 0.5 s; quotes
   arrive about once a second) recomputes own IV and Greeks for dirty contracts in one array
   call, and sends each browser subscription the rows that changed.

The forward per expiry comes from put-call parity over the quoted strikes and is refreshed on
a slower timer. A full surface (about 30 ms per SVI slice, from `volcalc`'s docs) is fitted in
the process pool on demand and on a timer of 30 to 60 s for underlyings in view.

**Browser transport, decided 2026-11-20: one WebSocket.** The client sends subscribe and
unsubscribe messages for topics; the server sends a snapshot, then changes. After a reconnect
the client resends its topic list and gets fresh snapshots.

| Topic | Content |
|---|---|
| `chain` (underlying, expiry, strike window) | Bid, ask, own IV and Greeks, volume, open interest per row |
| `quotes` (symbols) | Watchlist rows |
| `ticket` (id) | Leg marks, position Greeks, P/L |
| `candles` (symbol, period) | Last bar updates and new bars |
| `positions`, `alerts` | Later releases |

Bulk snapshots (candle history, a whole expiry) travel as Arrow over REST, as in the viewer.
Server-sent events and polling were considered; the WebSocket fits better because it carries
demand and data on one connection and lets the backend push alerts.

**QUIC (WebTransport) was evaluated on 2026-11-20 and not adopted for now** (confirmed by the
owner the same day). Desk research from web sources, nothing run.

- A browser reaches QUIC through WebTransport. It has been in all major browsers since March
  2026 (Safari 26.4 was last), so support is recent; implementations still differ in details.
- Its gains are independent streams without head-of-line blocking, unreliable datagrams,
  faster connection set-up and connection migration. They pay off with packet loss, many
  parallel streams or data that may be dropped. This application sends one user about one
  update a second over a VPN.
- Costs: uvicorn has no HTTP/3; the Python options are aioquic directly, Hypercorn's optional
  HTTP/3 mode (WebTransport support not confirmed) or young 0.x libraries. QUIC always needs
  TLS, so certificates would be required from the first day instead of at the phone step.
  A WebSocket fallback is the usual advice, which means two transports.
- WebSocket over HTTP/3 (RFC 9220) is not in any stable browser.

The door stays open: the message layer (below) does not depend on the carrier, and the
browser's connection module hides it. One WebTransport stream can replace the WebSocket
later. The phone step, which brings TLS and a lossy network, is the point to look again.

**Message format, decided 2026-11-20: Protocol Buffers.** WebSocket messages are defined in `.proto`
files, with generated code for Python and TypeScript.

- One schema language serves the browser channel now and the gateway socket after the split.
- Both ends get generated types and structural validation of every message.
- The same messages have a standard JSON form, so a development switch can send readable
  JSON while normal use sends binary.
- Bandwidth is not the reason at the laptop (the JSON estimate is tens of kilobytes per
  second, and WebSocket compression exists); it helps the phone step.

Costs: a code-generation step in both builds, and a second schema system beside the OpenAPI
schema of the REST routes. The WebSocket set is kept small (live rows, subscription control,
connection state) so few types exist in both. Row updates are sent as columns of packed
numbers; a missing bid or ask needs an explicit rule (NaN or an optional field), because a
plain numeric field cannot tell zero from absent. Tool choice and versions are to be checked
at set-up.

### Subscription budget

Published limits: 25,000 subscriptions per session, 10,000 changes per minute, 100 candles.

| Use | Size (from the probe's chain sizes) |
|---|---|
| One SPY expiry, all strikes, Quote and Greeks | about 385 contracts, 770 subscriptions |
| One SPXW expiry, all strikes, Quote and Greeks | about 523 contracts, 1,050 subscriptions |
| SPY surface, all 32 expirations, Quote only | 12,326 |
| SPX surface, all expirations, Quote only | 31,158: over the cap |
| Recorder, when it moves in | about 7,800 |

Consequences:

- Whole expirations are affordable in the chain view, so the strike window is a display
  matter more than a budget matter.
- Surfaces subscribe a moneyness window and the expirations inside a DTE limit (90 DTE covers
  the stated trading range), or sweep in batches and release.
- Open interest changes once a day, so it is collected by a daily sweep, not held.
- Demand has priority classes: recorder, then open positions and alerts, then visible views,
  then background work (surface snapshots, scanners). A closed view keeps its subscriptions
  for a grace period to avoid churn.

**To measure:** whether the cap counts symbols or symbol-event pairs; REST rate limits;
behaviour in regular hours.

## 4. Storage

Decided 2026-11-20.

| Data | Storage | Reason |
|---|---|---|
| Application state: login, tickets, watchlists, drawings, range templates, alerts, settings, instrument cache | SQLite in WAL mode, numbered migration files | Small transactional writes, one file, no service to run |
| T2 series: daily chain and surface snapshots, IV series, open interest, alert log | Parquet files partitioned by date and underlying, read with DuckDB and polars in-process | The format every existing repo already reads; append-only; no writer lock between processes |
| Recorder raw output | JSONL, the streamer's contract | Unchanged for the downstream pipeline |
| T3 | Read-only over the existing store, path in configuration, optional | |

Series written during the day are compacted into one file per day at the close, as the
streamer's day finalisation does.

Rejected: one DuckDB database file for everything (one writing process at a time; file format
tied to the DuckDB version), a database server (an extra service for one user), and SQLite
for everything (poor fit for analytical reads; the other repos cannot read it directly).

## 5. API and LLM layer

- Each service in `analysis` is a plain function with typed input and output models. REST
  routes and MCP tools are thin wrappers over the same functions, so the UI and the LLM get
  identical numbers.
- TypeScript types are generated from the OpenAPI schema, as in the viewer.
- MCP runs inside the backend over HTTP, with its own bearer token. Tools are read-only.
- Tools come in two sets, market and account. Decided 2026-11-21: a hosted Claude may see
  positions and transactions through the account set, and the user switches that set on or
  off in the application's settings (off by default). With it off the account tools are not
  offered to the model; nothing else in the application or in the market tool set is
  limited.
- First tools: quote and market metrics, chain slice, evaluate ticket, skew and term
  structure, expected move, watchlists.

Reach, from memory and to be verified at R4: Claude Code and Claude Desktop on the laptop can
connect to an MCP server on the VPN. Connectors of the claude.ai web and phone clients call
from Anthropic's servers and cannot reach a VPN-only host; that would need a public endpoint,
which is not planned.

## 6. Login, secrets, network

- One user. Password stored as an argon2 hash; session cookie (HttpOnly, SameSite=Strict);
  the WebSocket uses the same cookie and checks the origin.
- The backend listens on one configured address only, never on all interfaces. The address
  and the host name are configuration, not code.
- Decided 2026-11-21, machine: during development the backend runs on the development
  machine (macOS) and listens on its LAN address; a test host name resolves to it. Both
  values are local configuration and are not written here. Later it moves to a dedicated Linux machine that has the data stores, as a
  system service. No container for now. The T3 store may be absent on the development
  machine, which the tier design already allows.
- Decided 2026-11-21, VPN: WireGuard, provided by a separate device. Clients on the VPN reach
  the backend machine; the backend needs no VPN software of its own.
- Broker secrets stay in an environment file readable by the backend user only. Logs redact
  tokens and mask the account number; the frontend shows an account nickname.
- TLS is not needed on the VPN for the laptop. An installable web page on the phone needs a
  secure context. WireGuard issues no certificates, so at the phone step a reverse proxy gets
  a certificate for the host name; a public certificate authority can issue one for a name
  that resolves to a private address through a DNS challenge, without exposing the host.
  Not verified here; check at the phone step.

## 7. Frontend

- Decided 2026-11-20: Svelte 5, TypeScript and Vite as a single-page application with hash
  routing, without SvelteKit: there is no server rendering need, and the viewer's tooling
  lifts as it is. Routing and per-screen loading are written by hand.
- Decided 2026-11-20: several browser windows are used at once (for example the chart on one
  monitor and the workbench on another). The selected ticket and symbol are synchronised
  through the backend so windows follow each other, and each screen can run in its own
  window.
- ECharts for payoff, skew, term structure and histograms. The price chart library is task C.
- One WebSocket client module; one store per topic; `$state.raw` for large arrays.
- The option-chain grid is written new: virtual rows keyed by strike, per-cell updates.
- The ticket is stored in the backend and addressed by id in the URL, so every tool and every
  MCP tool can open the same ticket.

## 8. Repository layout

```
backend/    pyproject.toml, uv.lock
  src/<pkg>/  pricing/ broker/ subs/ market/ refdata/ analysis/ store/ api/ mcp/
  tests/
frontend/   package.json, src/
docs/
```

Core dependencies: numpy, scipy, polars, pyarrow, duckdb, fastapi, uvicorn, httpx,
websockets, pydantic, the MCP Python SDK, exchange-calendars, argon2-cffi. Tools: uv, ruff,
pyright, pytest, hypothesis; Vite and Playwright.

### Extraction of the pricing code

| From | To `pricing` | Change |
|---|---|---|
| `volcalc` `iv/bsm.py`, `black76.py`, `clocks.py` | price, IV, Greeks, clocks | None expected; add vanna, charm, volga |
| `volcalc` `iv/forward.py` | forward, dividend schedule | Remove the `vrp.config` import; take quote mids |
| `volcalc` `refdata/rates.py` | rate curve | Conversions and the curve only. The FRED download goes to `refdata` (WP-0.6), because PRC-01 allows no I/O in `pricing` (corrected 2026-11-22) |
| `volcalc` `surface/svi.py`, `ssvi.py`, `fixed_delta.py`, `mfiv.py` | surface, density, model-free IV | Add the three `bt_dataset` items of section 11.1 |
| `backtester` `lib/expected_move.py`, `broker/margin_model.py` | expected move, margin | Detach from the position book |
| `tt` payoff functions | payoff, breakevens | Rewrite on arrays; several expiries |
| New | path simulation, POP, P50, CVaR, P/L grid, platform mode | |

Each extracted file records its source repo and commit. The source tests are ported with it.

## 9. Testing

| Layer | Method |
|---|---|
| Pricing | Ported unit tests; property tests (parity, monotonicity, bounds); `volcalc`'s frozen reference values; an independent library as a cross-check in development |
| Platform mode | The `tt` capture set: POP must match the 86 of 87 captures it matches today; P50 and CVaR within the tolerances in `tt/FINDINGS.md` |
| Broker | The fake server from `tt`, extended with candles, unsubscribe, disconnects and stalls |
| Services and API | Replay broker with a recorded session, so results are deterministic |
| MCP | Each tool called through a test client against the replay broker |
| Frontend | Playwright against the backend in replay mode; the viewer's accessibility and offline checks |
| Against the references | Manual side-by-side checklists with OptionStrat and TradingView for R1 and R2 |

## 10. Risks

- **Scope** stays the main risk; the release order in the product definition is the control.
- **Stream throughput in Python.** The streamer handles about 7,500 subscriptions today, so
  the order of magnitude is proven, but not together with per-flush IV inversion. Measure
  in R0.
- **Surface refresh cost.** A full refit is about 1 s of CPU per underlying at 30 ms per
  slice, hence the process pool and the timer.
- **Regular-hours behaviour and rate limits of the API** are not measured yet.

## 11. Decisions asked

1. Pricing core in the backend only (section 2). **Decided 2026-11-20.**
2. One process now, with the stream behind a feed interface so a gateway split is a small
   step later (section 1). **Decided 2026-11-20**; the form of the split is left open, with
   a preference for a gateway that is also the recorder.
3. One WebSocket for live data, Arrow over REST for bulk snapshots, Protocol Buffers as the
   message format (section 3). **Decided 2026-11-20.**
4. SQLite for state and Parquet for series (section 4). **Decided 2026-11-20.**
5. Plain Svelte single-page application (section 7). **Decided 2026-11-20.**

Detail questions, answered 2026-11-21:

- Machine: the development machine now, a dedicated Linux machine with the data stores
  later; no container for now (section 6).
- VPN: WireGuard from a separate device (section 6).
- A hosted Claude may see positions and transactions when the user switches the account tool
  set on (section 5).
