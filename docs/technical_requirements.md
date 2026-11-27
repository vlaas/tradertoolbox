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
`store`, `system`, `api`, `mcp`; later `alerts`, `scanner`, `recorder`. (`system` was added with
WP-0.7; decided 2026-11-27.)

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
| Backend tools | ruff (lint and format), pyright, pytest, hypothesis, pip-licenses, pip-audit; mpmath for the pricing cross-check (decided 2026-11-22, WP-0.2) | Architecture note, section 8 |
| Frontend tools | vitest, Playwright (Chromium, Firefox, WebKit), openapi-typescript, svelte-check, license-checker-rseidelsohn; Prettier with `prettier-plugin-svelte` (WP-0.8) | Same; the prototype |

Not chosen yet: the library for GARCH fitting (stage b of the probabilities), and the reverse
proxy for TLS at the phone step.

Set-up (WP-0.1). Decided 2026-11-22:

- **Version rule.**
  - Direct dependencies are pinned exactly. The versions are chosen to work together; they
    need not be the newest.
  - No package, direct or indirect, is younger than two weeks when the lock is made.
    `[tool.uv] exclude-newer = "14 days"` in `backend/pyproject.toml` and
    `min-release-age=14` in `frontend/.npmrc` apply this to the whole lock (tested
    2026-11-22: a lock made a week earlier still passes `uv sync --locked`). An urgent fix
    that is newer needs a named exception for that one package.
  - An older version is acceptable while it is maintained and has no known exploitable
    vulnerability. A version that becomes very stale is a signal to plan a change, not a
    fault in itself.
  - Two exceptions: TypeScript stays on 5.9, because svelte-check and openapi-typescript do
    not support TypeScript 7. Playwright stays on 1.64, the version of the chart prototype
    and the backtester viewer, so one set of browsers serves all three. Both are in plan
    section 8, to be revisited when the tools allow.
  - The npm lock is re-resolved without `node_modules`. Otherwise npm writes it from the
    installed tree and drops the packages of the other platforms (seen 2026-11-22).
- **Vulnerability audit.** `make audit` (`backend/tools/audit.py`) checks every package of
  both lock files: pip-audit over `uv.lock`, `npm audit` over `package-lock.json`. It needs
  the network, so it is not part of `make check`. It runs before a commit that changes
  dependencies, and at each release acceptance. A known vulnerability judged not exploitable
  here is a named exception by advisory, with its reason, printed on every run; any other
  advisory fails the audit. First run 2026-11-22: Python clean. npm had two moderate
  advisories, both inside the licence checker only, a development tool: GHSA-3g3m-2fjr-6pw4
  (glob) and GHSA-g3hv-75wg-m249 (minimatch). They are named exceptions. Their fixes need
  major versions that the checker does not accept yet.
- **Linting.**
  - Python: `ruff check` with the families E, F, I, UP, B and ASYNC, and `ruff format
    --check`. ASYNC had no findings when it was added; it guards the stream reader of WP-0.4.
    Bandit's S family (17 findings in code and tools, none real) and the style families
    (RUF, SIM, PT) were measured and left out.
  - pyright in its standard mode, as `volcalc` uses, so that ported code passes unchanged.
    Strict mode gave 671 errors (474 in the ported `pricing`, where numpy and scipy are not
    fully typed). Strict for `api` and `system` since WP-0.7 (decided 2026-11-27, below).
  - Frontend: `svelte-check --fail-on-warnings` and strict TypeScript settings. A Prettier
    check with its Svelte plugin (`prettier` 3.10.1, `prettier-plugin-svelte` 4.1.1, both MIT) runs
    in `make check` since WP-0.8 (2026-11-27): no semicolons, single quotes, width 100, trailing
    commas, the style the code had. Generated code is left out. No ESLint unless a need shows.
- **Generated code is committed** (QA-05). `make check` regenerates it into a temporary
  directory and fails on any stale, missing or orphaned file. The list of generated trees is
  the root `.gitattributes`, where each is marked `linguist-generated`. A test keeps it equal
  to the trees `backend/tools/generate.py` owns.
- **Configuration** is one TOML file (`config/s3t.example.toml` documents it). Strict types;
  unknown keys are refused; errors name the key, never the value.
- **The package name `s3t` is permanent** as the internal name: the Python package, the
  `S3T_CONFIG` variable, the configuration file and the frontend package. The final product
  name, still open, appears only where people see it: the window title, the screens and the
  documents.

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
- **The REST state of BRK-09** as the client gives it (`RestStatus`). The token state is one
  of: none yet, valid, due (inside the 60 s margin), lapsed (expired while nothing was
  called), renewing, refused with its reason. With it come the expiry, the instant from which
  the next call renews, the last renewal and the number of renewals. The limit is shown as
  the configured rate and burst, beside the requests sent in the last 60 s. A 429 shows its
  hold end and the count of 429 answers so far. In the status bar it may be terse and
  technical. An information icon explains the numbers where that is needed (product
  definition 6.3).

The session and the subscription manager (BRK-02, SUB-02). **Decided 2026-11-23 (owner,
WP-0.4 review):**

- **The manager holds the content.** It alone knows which pairs are subscribed and in which
  order. It keeps the budget, removes duplicates, reduces by class, paces the changes and
  counts them. Nothing else calls the session; every other part asks the manager for data.
- **The session does the technical work.** It connects, authorizes, opens the channels,
  keeps the connection alive and renews the quote token. It detects a loss or a stall, waits
  and reconnects without being asked. It reports its phase, the gap, and a rate error of the
  server.
- **The session keeps no subscription set and never resubscribes by itself.** `subscribe`
  and `unsubscribe` mean "send this now on the current connection", in messages of 100. A
  call while the session is not connected is refused, not queued.
- **After a reconnect the session is open and empty, and says so.** The manager sends the
  granted set again, the highest class first, by the same rule as any other change: the
  difference between what is granted and what the current connection was sent. A new
  session object is filled the same way.
- **A hard cap in the session** is a backstop. The session counts the pairs sent on the
  current connection and refuses to go beyond a configured figure. The manager never
  reaches it in correct operation.
- **Items go to a sink.** `broker.stream(sink)` gives every item to one synchronous
  function: event batches, phase changes, gaps and notices, numbered by the stream's own
  counter. The session holds no queue; the feed owns the one queueing rule (SUB-01).
- **The broker states its source and the time of its data.** The source is live, fake or
  replay with the date of the recording (GEN-11). The time of the data is the wall clock on
  a live or fake broker and the position in the recording on a replay. Time to expiry and
  the age of a quote are measured against it, never against the system clock.
- **Broker keys stay strictly inside the broker module, for the stream too** (GEN-09). No
  dxFeed field name and no value "as received" crosses the interface. The broker module
  converts every event into the application's own record. This replaces "fields as
  received" in the architecture note, section 3.
- **Recordings** (BRK-05) are at the interface level, not the wire level: a directory under
  `<data_dir>/recordings/` with `meta.json`, `rest.jsonl` and the stream file, with a format
  version (`s3t.broker.recording`). The quote token is never written. A thread writes; a
  full queue loses the item, counts it and marks it in the file.
- **The time of the data is `data_time()`** on the broker, in place of `now()`.
- **The event records** (GEN-09, SUB-01). One record type per event, defined in
  `s3t.broker.interface`, with the application's names:
  - Quote: symbol, bid, ask, bid size, ask size, bid time, ask time.
  - Trade: symbol, price, size, time, day volume.
  - Summary: symbol, day open, high, low and close, previous close, open interest.
  - Greeks: symbol, time, price, IV, delta, gamma, theta, rho, vega. The units are the
    broker's until WP-1.9 has compared them.
  - Profile: symbol, description, trading status, halt start and end, 52-week high and low.
  - Candle: symbol, period, time, open, high, low, close, volume, VWAP, IV, open interest,
    and two marks: end of the snapshot, bar removed.
  - Numbers are floats and a missing number is NaN, never zero and never text. Times are
    UTC. A batch stays columnar: one event type, the receipt time taken before decoding,
    and the rows. A field is added when a package needs it.
- **Symbols.** The stream symbol is the broker's (`streamer-symbol` of the chain document).
  Outside the broker module it is an opaque key: taken from the broker, given back to it,
  never parsed or composed. A candle subscription names the symbol and the period as
  separate values; the broker module builds dxFeed's candle symbol. WP-0.5 puts the
  instrument model on top.
- **The REST documents** stay under the rule of 2026-11-22 above: the broker's JSON until
  the package that first uses a document types it.
- **Compression of the stream file:** zstd, through `backports.zstd` (PSF-2.0; the module of
  Python 3.14's standard library for 3.13, so the dependency goes with the move to 3.14).
  The file is `stream.jsonl.zst`. The writer flushes every few seconds, so a recording
  that was cut off is readable up to there. (Changed 2026-11-24: the flush is a block
  flush inside one frame, not the end of a frame. See below.)
- The code of part 1 was written before these decisions. It was reworked on 2026-11-23,
  with part 2.

The stream package, the rest. **Decided 2026-11-23 (owner, WP-0.4 review):**

- **Dependency.** `websockets` 17.4 (BSD-3-Clause), the library section 3 already names.
- **Events.** Quote, Trade, Summary, Greeks, Profile and Candle, one channel each, in the
  compact format with the field lists of the official SDK. Aggregation period 0.5 s
  (`[stream]`; granted live on 2026-11-23).
- **Pacing** (SUB-02) is the manager's, by the decision above. Removals go first. The
  figures: chunks of 100, 150 changes a second, at most 8,000 in any 60 s (the published
  10,000 less a reserve of 2,000, `[subscriptions]`). On "rate is too high" every change
  waits 5 s (longer when it repeats), the rate is halved, and what was sent on that channel
  in the 2 s before is sent again. The rate recovers after 10 minutes without such an error.
- **Reconnect** (BRK-02). After any loss: waits from 1 s, doubling, at most 30 s, with
  jitter, without an attempt limit. A new quote token is fetched when the server refuses the
  held one, or when it has less than 5 minutes left.
- **Loss and stall** (BRK-03). No message at all for 60 s is a loss at any hour (the
  keepalive runs every 30 s). No data for `stall_after` (60 s, `[stream]`) while the market
  is open and something is subscribed is a stall: the phase `stalled`, then a loss. Each
  stall in a row doubles the wait, up to 15 minutes; data that keeps coming resets it. "The
  market is open" is a rule passed to the session. The backend passes the exchange calendar
  (WP-0.7). Without one, in the tools, it is weekdays 09:30 to 16:00 New York.
- **Gap** (BRK-02, BRK-06). When a session is open again, a gap item goes out before any of
  its data: the number and the receipt time of the last batch before the loss, and the time
  the new session opened. The session is then `connected`. "Resubscribing" (BRK-06) is a
  state of the manager: from the opening of a session until everything granted was sent.
  The feed publishes both. DXLink has no sequence of its own, so "from sequence number to
  sequence number" means the stream's counter.
- **Budget figures** (SUB-02).
  - Subscriptions: the manager grants up to 10,000. The session's hard cap is 11,000. Both
    are configuration: `[subscriptions] limit` is the hard cap, and `reserve` (1,000) is
    taken off it for the manager. Nothing draws on the reserve; it is the distance between
    what the manager grants and where the session refuses.
  - The figures are well below the published 25,000 on purpose. The broker has objected to
    high subscription use on this account before, and its own app and the owner's
    `streamer` (always running, 0DTE SPX) use the same account. They can be raised later.
  - Candles: 100 less a reserve of 5. Changes: 10,000 a minute less a reserve of 2,000.
  - The budget counts symbol and event pairs until WP-0.9 has measured how the broker
    counts, and whether it counts per session (as published) or per account. (2026-11-27: not
    measured, by decision; the budget keeps counting pairs. See "The live measurements" below.)
- **Reduction inside a class** (SUB-03). The consumer that registered first keeps its pairs.
  A consumer gives its pairs in order of importance and is cut from the end.
- **Replay** (BRK-05). REST reads return the recorded document or "not found". The stream
  emits the recorded items for the pairs subscribed now, at the recorded pace, a multiple of
  it, or as fast as they are taken. A pair subscribed late gets its last recorded event. At
  the end the stream is `disconnected` with the reason "end of the recording".
- **Tests of replay** (QA-02, plan 3.1 and 3.2). `make check` holds no market data, so it
  tests the replay broker on a recording made from the fake broker in a temporary directory.

The event records and the feed, as built (WP-0.4, rework and part 2). **Decided 2026-11-23
(owner, review of the WP-0.4 plan).** The signatures are in `s3t.broker.interface` and
`s3t.subs.feed`.

- **A batch is columnar in the strict sense.** `EventBatch.columns` holds one sequence per
  field of the event's record, in the order of the record's fields, all of one length.
  `rows()` gives records, for tests and tools.
- **A time in a record** is a `datetime` in UTC, and `None` where the broker gives none
  (dxFeed sends 0). A missing number is NaN, as decided before. The time of a candle is the
  exception: it is always present (decided 2026-11-24, below).
- **Values have the application's names.** The event types are `quote`, `trade`, `summary`,
  `greeks`, `profile`, `candle`. The trading status is `active`, `halted` or `unknown`. The
  wire names and the whole mapping are in `s3t.broker.tastytrade.convert`.
- **The candle period is a value,** `CandlePeriod(count, unit)`, with the units minute,
  hour, day, week and month. It has no session and no price attribute until R2 needs one.
  Its text form is `15m`, `1h`, `1d`, `1w`, `1mo`.
- **The session refuses by an error** (`StreamRefused`) with a reason: `not_connected`,
  `limit`, `candle_limit`. The whole call is refused before anything is sent. A connection
  lost during the sending gives the same error with `not_connected`.
- **`subscribe` and `unsubscribe` are `async`** and return when their messages were handed
  to the connection. The session opens and sets up all six channels before it says
  `connected` (seen live 2026-11-23: 1.15 s from `connecting` to `connected`).
- **The stall clock** (BRK-03) starts when the first pair is sent on a connection. A session
  that holds nothing cannot stall.
- **The feed separates demand from reading** (SUB-01). A consumer has a name and one
  priority class and states the full set it wants, the most important pair first. A reader
  gets every batch of the event types it names, whoever asked for the pairs.
- **The stream's items pass through the feed unchanged,** with the stream's numbers. The
  feed's own items carry a time and no number: `FillState` (`resubscribing`,
  `resubscribed`; BRK-06), `Reduction` (SUB-03) and `Lost`.
- **A consumer is told of a reduction** by a `Reduction` item, which every reader gets, and
  by the status. `Demand.set()` returns nothing. The grant is computed 0.05 s after the
  first change, so quick changes cost one computation.
- **A reader's queue is bounded:** 2,000 batches by default, set per reader. A full queue
  loses the newest batch, counts it and tells the reader with a `Lost` item. States, gaps,
  notices and the feed's own items are never lost.
- **The budget** (`s3t.subs.budget`, pure). Classes in order; inside a class the consumers
  in the order they registered; each consumer's pairs in its order. A pair that is granted
  already costs nothing and counts for the class that got it first.
- **The order of sending:** removals first, then additions, the highest class first, per
  event type in chunks of 100.
- **The lines of a recording** (format 1; the stream lines are the same in format 2, below). A
  batch line holds the columns. NaN is `null`, an
  instant is microseconds since 1970, a candle period is text. The line with the field
  names of an event type stays, so a field added later leaves older files readable.
- **The replay broker follows the session's contract.** It refuses a call while it is not
  connected. After a recorded reconnect it holds nothing and is filled again by the
  manager. `ReplayBroker(paused=True)` stops after the first `connected` until `resume()`.
- **Not solved:** two consumers that ask the same candle with different start times. The
  start time is part of the identity of a subscription, so they count twice, and one removal
  ends both at the broker. Carried to R2 (plan, section 8).

**Decided 2026-11-24 (owner):**

- **`Candle.time` is never `None`.** A candle event without a time is ignored by the broker
  module, and the session counts it (`ignored_events`). In six days of the owner's streamer
  dumps all 423,183 candle events had a time, the markers included (note B, addendum of
  2026-11-24).
- **The end of a snapshot is either of two dxFeed flags.** `snapshot_end` is set for
  "snapshot end" (0x8, seen live on a history with a start) and for "snapshot snip" (0x10,
  in the streamer dumps on a history cut at 8,000 bars).
- **The stream file of a recording is one zstd frame with a block flush every 3 s,** and
  when the writer falls idle. It replaces the frame end of the decision of 2026-11-23. A
  frame every 3 s made the file larger than gzip did on the synthetic market: 6.9 times
  smaller than the lines, against 8.4 for gzip level 5. The block flush gives 9.9
  (measured, `docs/measurements.md`). A recording that was cut off is still readable up to
  the last flush. The reader reads the file as a stream.
- **A third figure per class in the use report:** `counted`, the pairs that count against
  the budget for the class. `granted` says how many of the class's pairs are held, by this
  class or a higher one. The counts of all classes add up to the total.
- **`meta.json` is written when a recording starts,** with `complete` false, and again at
  the end. Without it a recording that was cut off could not be opened at all.
- **The manager's worker does not end by an error.** An unexpected error is logged with its
  trace, counted (`failures`) and tried again after 5 s.
- **A hard-cap refusal** is counted (`cap_refusals`) and not tried again until the demand
  changes or a new session opens. Both counts are attributes of the feed, not in
  `FeedStatus`. WP-0.7 publishes them.
- **The replay broker has no hard cap.** Its status shows 0 for the limits.
- **The synthetic smile** is kept at a quarter of the at-the-money variance or more. The raw
  slice has no value in the last half hour before an expiry, and the fake failed there.
- **pyright** also searches `tests`, so that the feed tests can use the rig of the broker
  tests.

Nothing of WP-0.4 is left as a proposal.

The market package (WP-0.5). **Decided 2026-11-24 (owner, WP-0.5 review).** The facts behind it
are in note B, addenda "WP-0.5" and "WP-0.5, live". The signatures are in
`s3t.broker.instruments`, `s3t.market` and `s3t.api.rows`.

- **The instrument model** (MKT-01, GEN-09) is part of the broker interface, like the event
  records: `s3t.broker.instruments`. Records are frozen. A missing value is None, never zero
  and never NaN. An expiration date is a calendar date and not an instant.
- **The broker interface returns the records** for instruments and chains. A key that is
  missing or unreadable is a `BrokerProtocolError` that names the key and not the value. One
  expiration that cannot be read is left out, logged, and named in the chain with the reason
  (`left_out`), so that a view says what is missing. A chain of which none can be read is an
  error.
- **An option chain has one form** for equities and futures: one `Expiration` per root and
  date, sorted by date, AM before PM, then root. Strikes are parallel tuples, with no object
  per contract. Each expiration names its own underlying.
- **The exercise style** of an equity or index root is not in the chain. It is read from one
  contract per root and cached. It is not derived from the kind of the underlying. For a
  futures option it is what the broker states, which looks wrong for the weeklies and
  end-of-month options of /ES (note B). It is corrected per root before the assignment flag
  (PRB-06) uses it. Until then it has little effect: prices use the European formula.
- **Dollars per point of a futures option** are `notional-value / display-factor`. An
  expiration whose `strike-factor` is not 1 is left out, as one that cannot be read.
- **The settlement instant** (REF-05, MKT-01): PM-settled equity and index options settle at
  the close of the regular session on the expiration date, AM-settled ones at its open, from
  the calendar (WP-0.6). A date without a session settles with the session before it, at most
  four days back. A date that the calendar does not cover has no instant; the calendar must
  reach the last listed expiration. Futures options settle at the broker's `expires_at`. No IV
  is computed after the settlement instant (WP-1.2). This changes the product definition 11.1.
- **The latest value** (MKT-02) is the last one received, per pair, with its receipt time. An
  event replaces the whole value: a bid that has gone missing is missing.
- **What changed** (MKT-03): a pair counts as changed when an event arrived for it or its value
  was dropped. Values are not compared.
- **The flush** comes at the feed's aggregation period, and not at all when nothing changed.
  Each taker has its own sets of changed pairs. A late taker gets the union in one flush.
- **A pair that is no longer granted** has its value dropped at the next flush after the grant
  changed, and the pair counts as changed. The feed interface has `granted()` for it. A row that
  scrolls out of a view and back is blank until its first event; the grace period of SUB-04
  softens that.
- **After a reconnect, and after lost batches, the values stay.** `live_since` says from which
  receipt time on a value is confirmed. After a loss a quiet contract is not confirmed until
  its next event, because only a reconnect subscribes again. If WP-0.9 sees losses, the feed
  subscribes again after one.
- **The market state does not read candles.** Their history arrives newest first. The candle
  module (R2) is their reader.
- **A missing bid or ask** (MKT-07): dxFeed sends it as a price of 0 with a size of NaN. The
  broker module makes it NaN. A price of 0 with a size is kept, and so is a price without a
  size. A number that is not finite is missing.
- **The wire** (MKT-07, GEN-10). Numbers in columns are packed `double`; a missing one is NaN,
  and zero is a value. All columns of a message have one length. An instant is a `double` of
  microseconds since 1970 in UTC, exact until 2255, and NaN when missing. No int64 in product
  messages: `protobuf-es` gives `bigint` for it and cannot be told otherwise. Symbols on the
  wire are the application's.
- **One wire message now,** `QuoteRows`. The envelope and the topics are WP-0.7.
- **The recording format is version 2:** results of REST reads are the typed records. Version 1
  recordings cannot be read; none existed. An item that cannot be encoded is counted as lost
  and does not end the writer. A writer that failed does not mark the recording complete.
- **Linting:** four ruff rules on time zones are added to the families above: DTZ003, DTZ005,
  DTZ006, DTZ011 (GEN-10).
- **The generator** writes the fixtures in a process of its own. One process holds one version
  of a `.proto` file, and the application may have loaded the committed one.

The storage and reference data package (WP-0.6). **Decided 2026-11-25 (owner, WP-0.6 review).** The
facts behind it are in note B, addendum "WP-0.6". The signatures are in `s3t.store`,
`s3t.refdata` and `s3t.instants`.

- **Instants in the store** (GEN-10) are INTEGER microseconds since 1970 in UTC, in columns whose
  names end in `_at_us`. One module converts for the store, the recordings and the wire. Tables
  are STRICT. A calendar date is ISO text.
- **Migrations** (STO-02) are SQL files `NNNN_name.sql` without a gap. Each runs in one transaction
  together with its record: version, name, checksum, instant. A script cannot end its transaction,
  set a pragma or attach a file; an authorizer denies it. Foreign keys are off during a migration
  and checked before its commit.
- **A file that cannot be used is refused before anything in it is changed:** one of another
  program, one of a newer version, one whose applied migration is not the file here now. There are
  no down-migrations. The way back is the copy that is made before a migration of a file with
  content.
- **Tables come with their packages** (STO-01). Now there is `setting` only: key, JSON value,
  instant. WP-0.7 adds the login, WP-1.3 the tickets, and so on. Only `s3t.store` opens a SQLite
  file; a test of the repository holds it.
- **One thread owns the connection.** A function on the tables takes the connection and runs in
  one transaction, for reading or for writing. The write-ahead log is synced at every commit
  (`synchronous=FULL`), because writes are rare. The file and its copies are readable by the owner
  only.
- **The backup** (STO-07) is SQLite's online backup: one consistent state, in one file that needs no
  other, checked before it gets its name. It is a command, `tools/state_backup.py`. Nothing is
  overwritten and nothing is deleted. There is no schedule; a daily copy fits the process of
  WP-0.7 (decided 2026-11-26, below; it deletes nothing either). The wording of STO-07 was changed
  for it (4.9).
- **The calendar** (REF-04) has the trading days and regular sessions of `exchange-calendars`, XNYS
  for stocks, ETFs and indexes and the days of CMES for futures. One module imports the library.
  It covers 1990 to the end of the seventh year ahead. A question about a day outside is refused,
  except by `market_open`, which the stall rule asks and which answers "closed".
- **Sessions by class,** New York time. Stocks and ETFs: overnight from 20:00 the evening before to
  04:00, early to 09:30, regular, late for four hours after the close (20:00; 17:00 on an early
  close). Indexes: regular only. Futures: from 18:00 the evening before to 17:00, to 13:15 when the
  stock market closes early, to 13:00 when it is closed. The overnight session belongs to the
  trading day after it (measured, note B, addendum "WP-0.6"). **Not verified:** the end of late on
  an early close, and the futures' holidays and early closes.
- **Options have no class.** They settle by the regular session. Their own quoting hours differ by
  symbol: ETF options to 16:15, and since 2026-10-19 longer hours at Cboe for some symbols (search
  excerpts). Nothing needs them before WP-1.2.
- **Configured closures:** `[calendar] closures`. A closure is announced about ten days ahead, and
  the version rule takes no package younger than 14 days. A configured closure closes every class.
- **The 23-hour trading** that NYSE Arca starts on 2026-12-06, and Nasdaq probably with it (overnight
  from 21:00; search excerpts), changes no class. The feed has bars from 20:00 already.
- **The rates** (REF-01) are kept as FRED sent them, one CSV file per series in `data_dir/rates`,
  with the history from 2018. A download replaces a file only if it can be read and loses nothing.
  A complete download is repeated after 12 hours. A failed one is tried again after 10 minutes,
  doubling to 2 hours, for the missing series only.
- **The curve belongs to the time of the data,** so a replay gets the rates of its day. A value of
  day d counts from d+1 (`volcalc`). For a replay that can be some hours early, because Treasury
  values come out in the afternoon of the next business day (from memory). A series older than
  10 days is left out. DTB3 stands in for DGS3MO when that has no usable value.
- **The flat rate** is used when no series has a usable value, and the status says why. Its default
  is 0.04, the rate of the fake broker. `[rates] download = false` switches the network off.
- **The fake broker** has the holidays and early closes of the calendar. An expiration on a closed
  Friday moves to the trading day before. Its session expects data when the calendar has the market
  open, so that the silence of a holiday is not a stall. Assumed: the weekly options of /ES expire
  at 15:00 Chicago on an early close too.
- **pandas** comes with `exchange-calendars` and is held below 3.1 (`constraint-dependencies`): the
  library uses a call that pandas 3.1 deprecates. The schedules are the same on 2.3, 3.0 and 3.1
  (compared 2026-11-25). It is a runtime dependency that nothing else uses. WP-0.2 had refused a
  development library that brings it.

The API package (WP-0.7). The signatures are in `s3t.system` and `s3t.api`, the messages in
`proto/s3t/wire/v1/channel.proto` and `status.proto`.

**Decided 2026-11-26 (owner, WP-0.7 review):**

- **The password** is set by `tools/set_password.py` only, with 12 to 1,024 characters. It ends
  every session. There is no set-up page and no form in the app. argon2id with 64 MiB and three
  passes, one check at a time, in a thread.
- **Wrong passwords.** Five are free; after that each one holds the login, from 1 s doubling to 60 s,
  for everybody. The count is not kept over a restart.
- **A session ends 48 hours after its last use** (`[login] session_hours`, 1 to 8,760, to raise it).
  Changed from the proposal of a fixed 30 days. Use is what the browser does: a request to the API,
  the opening of the WebSocket, a message it sends there. What the backend sends to an open page is
  no use, so a page that nobody touches is logged out too. The end is moved at most once a minute,
  because each move is a write to the state file. It also ends at a logout, and with a new password.
  A session is 256 random bits in a cookie; the state file has their SHA-256.
- **Copies of the state file.** The running backend makes one a day (`[store] daily_copy`), and
  every copy is kept: nothing is deleted, as decided on 2026-11-25. Changed from the proposal of
  keeping 14. All copies lie in a folder per year below `paths.backup_dir`, the daily ones, those of
  `tools/state_backup.py` and those made before a migration. There are a few a day at most, so one
  level is enough. A second copy of a day is not made after a restart.
- **cffi** (MIT-0, through argon2-cffi) is a named exception, and MIT-0 is not on the allowed list.
- **The account's nickname** refuses five digits in a row, which an account number has; "Roth 2025"
  is a name. Changed from the proposal, which refused four digits anywhere.
- **Topic `quotes`.** Stocks, ETFs and indexes, at most 100 symbols. Futures come with a call of the
  broker interface that says what a symbol is (WP-1.8); nothing outside the broker module may read a
  symbol.
- **One state file for every broker** stays for now. Whether the fake and the replay get their own,
  or a mark on what is stored, is decided before WP-1.3 stores tickets.

**Decided 2026-11-27 (owner, WP-0.7 review),** as proposed on 2026-11-25:

- **Two new packages of modules.** `s3t.system` is the process as a whole: the composition, the
  login, the features and the health service. `s3t.api` is the edge. A service knows neither the
  API nor MCP (API-01). Tests of the repository hold it, and that only one module carries messages
  (API-08) and that nothing outside `pricing` has the tools for a second pricing (GEN-05).
- **Start and end.** The socket is opened first and listens at once, so a second backend stops
  before it opens a broker session or the state file. Everything is closed in the opposite order on
  SIGINT or SIGTERM. What keeps the backend from starting is one line and exit status 1.
- **What is public** (SEC-02): `POST /api/login` and the files of the built frontend. Everything
  else under `/api/` is closed by one guard, also a route that nobody thought of. The OpenAPI
  schema is not served; nothing reads it at run time.
- **Origin.** The WebSocket, and every request with a method that could change something, must state
  the origin of the listen address or of the host name, with the port. SameSite=Strict is not enough:
  a page on another port of the same host is the same site. Without the check it opened the WebSocket
  in all three browsers (measured 2026-11-25, `frontend/e2e/backend.spec.ts`).
- **Migration 0002** brings the tables `login` and `session`.
- **The cookie** lives as long as a browser keeps one (400 days; with the decision of 2026-11-26 on
  the end of a session), because the backend decides when a session ends, and a cookie of a session
  that has ended opens nothing. HttpOnly, SameSite=Strict, the path `/api`, the port in its name
  (cookies are shared between the ports of a host). No `Secure` mark before the phone step brings
  TLS.
- **The message layer** (API-03, API-08). `ClientMessage` subscribes and unsubscribes under numbers
  that the client chooses; they rise and are used once. `ServerMessage` has the number, a snapshot
  mark and a body. To change a list, subscribe anew and then end the old subscription. A slow client
  gets fewer messages and never old ones; after 30 s without taking any it is given up. What cannot
  be read ends the connection; a topic that fails ends itself with a message. A heartbeat goes out
  every 5 s with the time of the data.
- **The JSON form** (API-04) is chosen by the client per connection, with the WebSocket subprotocol
  `s3t.v1.json` in place of `s3t.v1.protobuf`. No setting of the server. The version is in the name,
  so a page that speaks another one is refused before it is open.
- **Topic `status`.** The whole state in every message: at once when a person would call it a change,
  otherwise every 5 s while the running figures move. It holds the mark of GEN-11, the nickname
  (GEN-03), the stream, the use per class and in total, the REST state, and `live_since_us`.
- **Live or not** (UI-05). A row has no mark. A quote or a trade is live when it was received at
  `live_since_us` of the `status` or later; when that is unset, nothing is live. `prev_close` is a
  value of the day and is not judged by it.
- **A stall is a mark** (BRK-06). The phase `stalled` lasts no time, so a state that is published
  could never show it. `StreamStatus.stalled` holds from a stall until data comes again.
- **Tiers** (GEN-04). A feature is declared once with the tiers it needs and with what the app does
  without them (hidden or unavailable). Every route and topic names its feature or is listed as one of
  the process itself; a test holds it. T1 is absent when the broker refused the grant; T2 until R3
  records; T3 without its directory. One feature now: `quotes`.
- **The health route** gives the status records of the parts as they are, and the counts that should
  be zero. It needs a session like every other route.
- **Rates.** Only the live broker downloads. The fake uses the flat rate and reads no file. A replay
  reads the files, so that a run can be repeated.
- **pyright** is strict for `api`, `system` and `__main__`: 4 findings when it was switched on, all
  fixed. It finds `s3t` in its source now (`extraPaths`), and no longer as an installed package.
- **A content policy** lets the page load code and data from this backend only.
- **What `quotes` sends.** The snapshot comes at once, with empty rows for what is not known yet.
  The instruments are read one at a time, because the request limit is shared with the token that a
  reconnect needs. A symbol without data has a note: unknown, cut by the budget (SUB-03), or the
  broker could not be asked (tried again after 30 s).
- **The choice of broker** (GEN-11): `[broker] source`, `recording`, `fake_start`,
  `account_nickname`. `fake_start` sets the clock of the synthetic market, which moves in the
  regular session only.
- **Dependencies:** uvicorn 0.54.0 (BSD-3-Clause) and argon2-cffi 25.1.0 (MIT). With them click,
  argon2-cffi-bindings, cffi and pycparser.

The frontend shell (WP-0.8). The code is in `frontend/src`; its parts are named in the plan,
WP-0.8.

**Decided 2026-11-27 (owner, WP-0.8 review):**

- **The status bar** (BRK-09's layout, left open at WP-0.3) is compact and easy to grasp: short
  texts, abbreviated where they can be, in this order: the browser's connection ("Backend ok",
  "Backend lost · retry 3 s"), the stream ("Stream ok · 0.5 s", resubscribing, stalled, down, and
  "gap 2 s" for ten minutes after a gap), the subscriptions ("Subs 9/10,000", with what is pending
  or lost), REST ("REST 5/min ≤2/s · token renews 10:14", and "held 10:00 (429)"), the mark of
  GEN-11; then the time of the data, "Read-only" and an information icon that explains the
  figures. Each part shows its whole sentence when pointed at (its `title`). A part is a dot and
  words, so no state is told by colour alone. Every figure is also on the status screen. The
  mock-ups are not changed.
- **Live values** (UI-05). A row whose values are not all live is greyed as a whole (the token
  `--stale-row`) and says "stale" in its last column. The top bar of every screen says that the
  connection is lost, with the reason and the wait. Live is the rule of 2026-11-27 (above), and
  only while the connection is open and the `status` came on it.
- **Times** (UI-12). The default is the exchange time of the instrument; the user can choose local
  time, UTC or a zone by its name, in the top bar. Every time names its zone: ET and CT for the
  two exchange zones, else the browser's short name (UTC, EET). The choice is kept in the browser.
  The backend's times that belong to no instrument (the token, the stream, the time of the data)
  are in the market's zone, New York, in exchange time. Every symbol of the topic `quotes` is of
  New York (stocks, ETFs, indexes); the zone of an instrument has to come from the backend when
  futures come (plan section 8).
- **The font of prices** is JetBrains Mono, as in the mock-ups: OFL-1.1, a named exception under
  QA-07 (`@fontsource/jetbrains-mono` 5.3.0).

**Decided 2026-11-27 (owner, WP-0.8 review), as proposed:**

- **The login is a state, not an address.** Without a session the login screen stands in for the
  screen of the address, and the address stays. A session that ends while a page is open (`Bye`
  with `SESSION_ENDED`) takes it to the login screen with the reason.
- **Addresses.** `#/status` is the only screen of R0 and the default; an address of no screen is
  replaced by it without a step in the history. The top bar lists only the screens that exist.
- **The connection** (`lib/connection.ts`). After a loss the next attempt waits 0.5 s, doubling to
  8 s, and from 0.5 s again once a connection brought a message. Silence for 15 s is a loss (three
  missed heartbeats). A refused handshake asks `GET /api/session`: none means the login screen; no
  answer means trying again; a session that is there twice in a row means another version of the
  messages, and the page reloads, at most once a minute. Close 1012 reads "the backend restarts".
  The numbers of the subscriptions rise over the life of the page, not only of a connection.
- **The JSON form** (API-04) is `?wire=json` in the page's address.
- **The mark of GEN-11** sits in the top bar, which is the header of every screen window (as in the
  mock-ups), and in the status bar. Its text is the backend's.
- **The exchange zone of an instrument** (UI-12) is the zone of the calendar its sessions come
  from: New York (XNYS) for stocks, ETFs, indexes and their options; Chicago (CMES) for futures and
  their options. It is not the home of the exchange: Cboe lists VIX and SPXW and states its hours
  in Chicago time (from memory, not looked up), and both are New York's here. The backend publishes
  the zone of an instrument when one of another zone comes (WP-1.8, R2); the app does not guess it
  from the symbol.
- **The status screen** lists SPY, SPX and AAPL, which the fake and the live broker know. The list
  can be edited and is kept in the browser. The health and the features routes are asked at each
  connection and every 30 s, for the rates (REF-01), the calendar (REF-04), the state file and the
  data tiers (GEN-04).
- **Layout** (UI-08 base). The window does not scroll at 1024 by 600 and above; below it the areas
  stack and the page scrolls. The spacing scale is 2, 4, 6, 10 and 16 px, with 6 px between panels
  and at the window's edges (UI-10).
- **The information icon** (UI-11 base) is one component on a native popover, so its box is drawn
  above every scrolling area (UI-09 base). It opens towards the side of the window with room.
- **Theme.** The viewer's tokens as they are, dark by default and light by the system's setting,
  with no switch yet.
- **The dev server.** `S3T_BACKEND=http://<address>:<port> npm run dev` sends `/api` and the
  WebSocket to that backend and states the backend's origin (SEC-02). No address is in the code.
- **The browser suite** runs one backend per engine, and one test at a time in each, so that a
  restart in one engine breaks no test of another. Its tool (`backend/tools/e2e_backend.py`) has a
  control on the next port for the tests: restart the backend, drop the stream, set a quote. It is
  not part of the application.

**Decided 2026-11-27 (owner, WP-0.8 review), changed from the proposal:**

- **Accessibility checks** (plan 3.5) now, not with the first screen of R1: the viewer's checks,
  WCAG 2 A and AA with a contrast of 4.5:1, in both themes, on the login screen, the status screen
  with the explanation of its status bar open, and the status screen while the connection is lost
  (`frontend/e2e/a11y.spec.ts`). `@axe-core/playwright` 4.13.0 and `axe-core` 4.13.0, the viewer's
  version, are MPL-2.0 and a named exception under QA-07. Their first run found three faults, all
  fixed: a panel that scrolls could not be reached by the keyboard (it is a stop of the Tab key
  while it overflows), the grey of a stale row left too little contrast (a tint of 10%,
  `--stale-row`, in place of the viewer's 25%), and the status bar's figures of a connection that
  has gone were faded (they are in italics).

The live measurements (WP-0.9). The facts are in note B, addendum "WP-0.9", and the figures in
`docs/measurements.md`. The tools are `backend/tools/live_day.py`, `timings.py`, and the additions
to `broker_check.py` and `stream_check.py`.

**Decided 2026-11-27 (owner, WP-0.9 plan):**

- **No probe past a published limit.** No subscription beyond the cap, no change rate beyond the
  limit, no sixth session, no burst of REST requests. The account is the one the broker objected to
  before, and the owner's `streamer` runs on it. So these are **not measured, by decision**:
  - how the cap counts (symbols, or symbols and event types);
  - whether it counts per session or per account;
  - what the server answers beyond the cap or the change limit, and to a sixth session.
- **The budget keeps counting symbol and event pairs,** the safe reading of a unit the broker does
  not define (note B). The application grants at most 10,000, and the `streamer` holds about 7,500.
  So even a cap of 25,000 per account is not reached by the two together. The broker's own app was
  not counted.
- **The REST limits are read from the documentation and the response headers.**
  - The broker publishes no numbers for REST. No response states a limit, and a 429 carries no
    header for it (note B).
  - The client's default of 2 requests a second with a burst of 4 stays (BRK-04). No 429 has been
    seen at it.
- **PRF-09 is a run across every session, not a trading day.** Instruments trade at different
  hours. Futures run from Sunday 18:00 to Friday 17:00 New York with a daily break, and stocks have
  overnight, early, regular and late sessions, with 23-hour trading from 2026-12-06. Crypto (24/7)
  and forex (24/5) may come later.
  - At R0 it is measured over 25 hours: from Monday 2026-11-30, 08:45 New York, to Tuesday 09:45.
    That crosses every session of stocks and futures, the futures' daily break, the 24-hour life of
    the stream's quote token, two refreshes of the rates and a daily copy of the state file.
  - The demand of the run adds the front /ES future and /ES options near the money, so that the
    night has data.
  - The machine is idle from 09:30 to 16:00 New York for the timings; memory and states need no
    idle machine.
  - A whole week is observed in normal use afterwards, not as a measured run.
  - A rehearsal ran on 2026-11-27, the half day after Thanksgiving.
- **Every timing records its CPU time and the machine** (QA-08), beside the wall time and the load.
  The product moves to a dedicated machine later, which may have another capacity, so the headroom
  has to be judged from the CPU time. The event loop is one thread, so its headroom is the share of
  one core it uses. The owner expects the machine not to be the bottleneck; if it is, the code needs
  optimising.
- **The memory bound of PRF-09 is observed, not a limit.** It is the setting
  `[process] memory_bound_mb`, 1,024 MB, which is enough; it is not set again from the day run.
  - A watch in the backend (`s3t.system.process`) reads the peak resident memory once a minute and
    logs one warning when it goes above the bound.
  - The health route reports the peak, the bound and since when it was exceeded.
  - Nothing else happens. The backend does not restart or refuse anything, and the app does not
    show it yet.

**Proposed 2026-11-27 (WP-0.9) and decided the same day (owner):**

- **The IV cost measured at R0 is that of a stand-in:** `escrowed_iv` on the event loop, at every
  flush, for the options whose quote changed. The design of the flush is WP-1.2's. The capacity of
  the reader is measured by replaying a recording at multiples of its pace, without the DXLink
  decoding. A replay as fast as the loop runs measures nothing: it outruns every reader and ends
  before the first flush.
- **The rate error of the stream is a guess.** The session takes an error as a rate error when its
  text contains "rate" (`dxlink.py`). No document names that text, and none says how an excess is
  reported. The manager paces at 8,000 changes a minute, so it should not arise.
  - The session is left as it is.
  - If an `ERROR` of the code `BAD_ACTION` or `UNKNOWN` is ever seen right after a chunk of
    changes, it is treated as a rate error too (plan, section 8).
- **QA-08 is closed by review:** every timing row of `docs/measurements.md` since WP-0.3 records
  the load average, and each package that took no timing says so.

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
| GEN-11 | When the backend does not run on the live broker, the app says so everywhere: a permanent mark in the status bar and in the header of every screen, and in every MCP result. On the fake broker it reads "SYNTHETIC DATA"; on the replay broker "REPLAY" with the date of the recording. The choice of broker is configuration, live by default, and logged at start. Decided 2026-11-22 (owner, WP-0.3 review). | R0 | T |

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
| SUB-02 | The subscription manager is the only caller of subscribe and unsubscribe. It keeps the session inside the configured subscription limit (10,000 granted and a hard cap of 11,000 by default, decided 2026-11-23; 25,000 are published), 100 candle subscriptions and the change-rate limit, with a configured reserve. | R0 | T |
| SUB-03 | Demand has priority classes: recorder, open positions and alerts, visible views, background work. When the budget is short, lower classes are reduced first and the consumer is told. | R0 | T |
| SUB-04 | A closed view keeps its subscriptions for a grace period before release. | R1 | T |
| SUB-05 | A chain view subscribes by expiration and strike window, never a whole chain of a large root. | R1 | T |
| SUB-06 | Open interest is collected by a daily sweep and not held as a standing subscription. | R3 | T |
| SUB-07 | The manager reports its use (per class and in total) to the app. | R0 | T |

To measure in R0: whether the cap counts symbols or symbol-event pairs. (Not measured, by
decision of 2026-11-27: section 3, "The live measurements". The budget counts pairs.)

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

Decided 2026-11-22 (WP-0.2, reviewed with the owner):

- **Discrete dividends (PRC-02)** use `volcalc`'s escrowed model: F = (S − PV(div)) e^{rτ},
  priced with Black-76 on that forward. It applies to stock and ETF options wherever the
  forward is built from the spot and the dividend schedule. A continuous yield was rejected:
  it is wrong around the ex-dates that fall inside short options. Early exercise waits for
  the American pricer (plan, section 8).
- **What the Greeks hold fixed.** The spot Greeks follow by the chain rule and hold the spot
  fixed while time passes, so theta and charm include the carry, as platforms show them for
  equity options. The Black-76 Greeks (indexes on the parity forward, futures options) hold
  the forward fixed.
- **One rate** discounts the strike and the dividends.
- **Raw units (PRC-04).** Values are per unit of the underlying. Theta and charm are per year of
  the calendar clock. Vega, vanna and volga are per unit of σ, rho per unit of r. The list is in
  `s3t/pricing/__init__.py`.
- **Trader units per position (PRC-04)**, in `s3t/analysis/units.py`: multiplier times signed
  quantity. Value in dollars; delta in share equivalents; gamma per one-dollar move; theta
  and charm per calendar day; vega, vanna and rho per point; volga per volatility point
  squared. A key without a defined unit is refused.
- **Trader units per contract**, for the option chain (WP-1.2, WP-1.5): the same conversion
  with multiplier and quantity 1. Delta as a fraction (0.30); theta per share in dollars a
  calendar day; vega per share in dollars a vol point. To be checked against Tastytrade's
  chain at the R1 acceptance (WP-1.9).
- **PRC-01 and logging.** The standard-library logger is allowed in `pricing`. It does no I/O
  without a handler, and only the application attaches one. The ported solver keeps its
  three log lines. The service layer that runs it on every flush (WP-1.2) limits a repeated
  warning.
- **The rate download is not in `pricing`.** The FRED download and the stored rate history
  belong to `refdata` (WP-0.6, REF-01); `pricing` has the conversions and the curve only. The
  architecture note's extraction table was corrected to match.
- **The independent cross-check (QA-01, plan 3.4)** is mpmath at 40 digits, a development
  dependency. The reference prices are written fresh in mpmath, and every reference Greek is
  mpmath's numerical derivative of that price. It replaces `volcalc`'s py-vollib, which brings
  numba and pandas.

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
| STO-07 | A consistent copy of the state file can be taken while the backend runs. | R0 | T |
| STO-08 | The IV-history set is one compact Parquet file per root, derived from the lake's surfaces: at-the-money IV at fixed maturities, model-free IV, skew measures and realised-volatility windows, per day and per intraday snapshot. | R3 | T |

Decided 2026-11-25 (owner, WP-0.6 review):

- **STO-07** was reworded. Until then it read "The state file can be backed up by copying while the
  backend runs." A plain copy of a SQLite file in WAL mode is not a backup: the newest transactions
  are in the file beside it, and the file can change while it is copied (sqlite.org, "How To
  Corrupt An SQLite Database File" and "SQLite Backup API"; search excerpts). A test shows a plain
  copy that lacks them.
- **STO-01** is closed for the file, its mode and the rule that nothing else opens a SQLite file.
  The tables it lists come with their packages.

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

State after WP-0.7 (2026-11-25):

- **SEC-06** is measured on macOS only (`docs/measurements.md`). Linux was not run.
- **API-05** is closed for `status` and `quotes`.
- **SEC-02**: the login and the files of the built frontend are public, as they must be (decided
  2026-11-27, section 3).

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
| UI-12 | A time is shown by default in the exchange time of its instrument. The user can choose local time, UTC or a time zone by its name instead, and every time says its zone. Times are compared as instants, never as written. Decided 2026-11-27 (owner, WP-0.8 review). | R0 | T |

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
| CH-06 | The time axis shows exchange time by default and can be switched to UTC, local time or a time zone by its name (UI-12, 2026-11-27). Daily charts always show the trading date. | R2 | T |
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
| PRF-09 | The backend runs without restart across every session and the weekend, without growth of memory beyond a configured bound. The bound is `[process] memory_bound_mb`, 1,024 MB, observed and not a limit. Measured at R0 over 25 hours (reworded from "a full trading day" and decided 2026-11-27, WP-0.9; section 3, "The live measurements"). | R0 | M |

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
| QA-08 | Timing checks are run on an idle machine and record the machine load with the result. They also record the CPU time beside the wall time, and the machine they ran on, so that the headroom can be judged for another machine (added 2026-11-27, owner, WP-0.9). | R0 | R |

Licence policy of QA-07, implemented in `backend/tools/licences.py`:

- The check evaluates the SPDX expression of each package. With AND, every part must be
  allowed; with OR, one part is enough. The collecting tools are not trusted with this: the
  npm checker passes `MIT AND CC-BY-3.0` because one part is allowed.
- Allowed, decided 2026-11-22: MIT, Apache-2.0, BSD-2-Clause, BSD-3-Clause, ISC, 0BSD, Zlib and
  PSF-2.0, with the metadata spellings Python packages use for them. Also decided 2026-11-22,
  found at set-up:
  - Python-2.0, the PSF licence under another identifier. Used by `argparse`, a
    development package.
  - BlueOak-1.0.0, a permissive, OSI-approved licence with a patent grant. Used by 11 npm
    development packages: glob, minimatch, tar and their relatives.
  - CC0-1.0, a public-domain dedication. It grants no patent rights. numpy, a runtime
    dependency, needs it: its expression is `BSD-3-Clause AND 0BSD AND MIT AND Zlib AND
    CC0-1.0`.
- Named exceptions, each for the licence exactly as reported, so a change of licence ends the
  exception:

| Package | Licence | Reason | Status |
|---|---|---|---|
| hypothesis | MPL-2.0 | Development only, used unmodified | Decided 2026-11-22 |
| lightningcss and its platform binaries | MPL-2.0 | A build tool of Vite, used unmodified, and not part of the built app | Decided 2026-11-22 |
| spdx-exceptions, spdx-ranges | CC-BY-3.0 | Licence-list data inside the licence checker itself; development only; not redistributed | Decided 2026-11-22 |
| certifi | MPL-2.0 (reported as `Mozilla Public License 2.0 (MPL 2.0)`) | The CA certificate bundle of httpx, data used unmodified; a runtime dependency | Decided 2026-11-22 (WP-0.3) |
| cffi | MIT-0 | The binding of argon2-cffi to its C code, used unmodified; a runtime dependency. MIT-0 is MIT without the duty to name the author | Decided 2026-11-26 (WP-0.7) |
| @axe-core/playwright, axe-core | MPL-2.0 | The accessibility checks of the browser suite (plan 3.5), as the backtester viewer has them; development only, used unmodified, not part of the built app | Decided 2026-11-27 (owner, WP-0.8 review) |
| @fontsource/jetbrains-mono | OFL-1.1 | The font of prices, as in the mock-ups. Its files are part of the built app, used unmodified. OFL-1.1 lets a font be shipped with software, and asks that every copy carries the copyright notice and the licence. Whether the metadata of the font files is enough for that was not checked: before publication (plan section 8) | Decided 2026-11-27 (owner, WP-0.8 review) |

## 5. Open points

To measure:

- ~~Whether the subscription cap counts symbols or symbol-event pairs (R0).~~ Not measured, by
  decision of 2026-11-27; the budget counts pairs (section 3, "The live measurements").
- ~~REST rate limits~~ (R0): not published and not stated in the headers; not probed, by decision
  of 2026-11-27. Stream behaviour in regular hours (R0): seen in a rehearsal on 2026-11-27, a thin
  half day (note B, addendum "WP-0.9"); the day run on 2026-11-30 measures it.
- Throughput of the stream reader together with per-flush IV inversion (R0): a replay of a
  regular-hours recording took about 49,000 events a second without a loss, against at most 590
  seen live (`docs/measurements.md`, 2026-11-27, without the DXLink decoding). The day run on
  2026-11-30 gives the live cost on an idle machine.
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
| R0 Foundation | GEN, BRK, SUB-01 to 03 and 07, MKT-01 to 03 and 07, REF-01, 04 and 05, PRC-01 to 04, STO-01, 02 and 07, API-01 to 05 and 08, SEC (except 04), UI-03, 05 and 12, PRF-09, QA (except 03 and 06) |
| R1 Trade workbench | SUB-04 and 05, MKT-04 to 06, REF-02 and 03, PRC-05 to 13, PRB-01 to 06, 12, 13 and 15, STO-05, API-06 and 07, UI, WB, PRF-01 to 03, 05 and 08, QA-03 and 06 |
| R1b Path numbers and stress | PRB-07 to 11, PRF-04 |
| R2 Chart | REF-06, CND-01 to 10, STO-06, CH (except 13), PRF-06 and 07 |
| R3 Volatility | SUB-06, CND-11, STO-03, 04 and 08, VOL |
| R4 LLM tools | SEC-04, MCP (except 06) |
| R5 Positions and risk, R5b Review | BRK-08 (account), POS, REV, MCP-06, CH-13 |
| R6 Scanners and alerts | SCN |
| R7b Probabilities, stage b | PRB-14 |
| Recorder | REC |
