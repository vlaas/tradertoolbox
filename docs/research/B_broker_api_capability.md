# Research B: Tastytrade API capability

Live read-only probe on 2026-11-19, 19:17 ET (market closed), production API, one call per
second, with the personal OAuth grant used by `streamer`. One run, one evening. Everything
here is measured unless marked otherwise.

Not tested: regular-hours behaviour, REST rate limits, futures streaming symbols, candles of
expired contracts, origins other than `http://localhost:5173`.

## 1. Authentication

- `POST /oauth/token` with the refresh token and client secret returns an access token valid
  for 900 s and an `id_token`.
- The access token is a JWT. Its `scope` claim is `read openid accounts:<acct>:owner`.
  **The existing grant is read-only**: there is no `trade` scope, so the application cannot
  send an order with these credentials.
- The streaming token from `GET /api-quote-tokens` is valid for 24 h, level `api`.

## 2. Browser access

| Request | Result |
|---|---|
| `GET /market-metrics` with a valid token, no `Origin` header | 200 |
| The same request with `Origin: http://localhost:5173` | **403** |
| `OPTIONS /market-metrics` preflight from that origin | 403 |
| `OPTIONS /oauth/token` preflight from that origin | 204 |
| DXLink websocket with that `Origin` | accepted |

The REST API refused a request that carried a browser origin, although the response headers
advertise `access-control-allow-origin: *`. The streaming websocket did not object.

**Consequence:** REST calls must go through the backend. A browser-only application is not
possible with this origin; other origins were not tried.

## 3. REST content

| Endpoint | What it gives |
|---|---|
| `/market-metrics?symbols=` | IV index, IV rank (three variants), IV percentile, 5-day IV change, IV per expiration (24 for the first symbol), HV 30/60/90, IV minus HV, liquidity rating/rank/value, beta, 3-month correlation to SPY, dividend dates and amounts, earnings date with time of day and estimate flag, market cap, sector, industry, borrow rate |
| `/market-data/by-type` | Snapshot quote per symbol: bid, ask, mid, mark, last, open, day high/low, previous close, year high/low, halt state |
| `/option-chains/{u}/nested` | Roots, expirations (type, date, DTE, settlement), strikes with OCC and streamer symbols, tick sizes, deliverables |
| `/instruments/equity-options/{occ}` | Exercise style, `expires-at`, `stops-trading-at`, settlement type |
| `/futures-option-chains/ES/nested` | 22 futures; 54 option expirations with their own underlying future, exercise style, `cash-settled`, `notional-value`, `display-factor`, `strike-factor` |
| `/accounts/{a}/positions` | 23 fields: symbol, quantity and direction, average open price, close price, multiplier, realized day gain. **No mark and no Greeks**; those come from the stream |
| `/accounts/{a}/transactions` | Fills with fees, commission, order id, execution time: enough for a trade journal |
| `/watchlists`, `/public-watchlists` | Own lists and 65 public lists |
| `/market-time/equities/sessions/current` | Session open, close, extended close, next and previous session, state |

`/market-metrics` is a ready pre-filter for scanners.

## 4. Chain sizes

| Underlying | Contracts | Detail |
|---|---|---|
| SPY | 12,326 | 32 expirations, up to 792 DTE |
| SPX | 31,158 | SPXW 20,408 (39 expirations); SPX 10,058 (20 expirations, AM-settled, up to 1,855 DTE); SPXO 692 |
| ES options | 20,282 | 54 expirations |

A full SPX chain exceeds the 25,000-subscription session cap with a single event type.
Chain views must subscribe by expiration and strike window.

## 5. Streaming events (default field lists)

| Event | Fields of use |
|---|---|
| Quote | bid/ask price, size, exchange code |
| Trade | price, size, change, day volume, day turnover, extended-hours flag |
| Summary | day open/high/low/close, previous close, previous day volume, **openInterest** |
| Greeks | price, volatility, delta, gamma, theta, rho, vega |
| TheoPrice | price, underlyingPrice, delta, gamma, dividend, interest (both 0.0 on the sample) |
| Profile | description, trading status, halt times, 52-week high/low, beta, ex-dividend amount and day, shares |
| TimeAndSale | price, size, bid and ask at the trade, sale conditions, **aggressorSide**, spreadLeg |
| Candle | open, high, low, close, volume, vwap, bidVolume, askVolume, **impVolatility**, openInterest |

- The server granted the requested aggregation period (0.5 s).
- No `Underlying`, `Series` or `TradeETH` event arrived in 12 s for SPY, SPX or the option.
- SPX delivers Quote and Trade without sizes; its Quote was 7113.84 / 7226.78 after hours, so
  an index quote is not a usable price. Use Trade.
- The stream Greeks carry no vanna, charm or volga.

## 6. Candle history depth

All subscribed from time 0.

| Symbol and period | Candles | First candle | Limit |
|---|---|---|---|
| `SPY{=d}` | 8,001 | 1995-02-06 | 8,000 candles |
| `SPX{=d}` | 8,001 | 1995-02-07 | 8,000 candles |
| `SPY{=h}` | 4,748 | 2026-02-08 | about 9 months |
| `SPY{=15m}` | 6,025 | 2026-08-22 | about 90 days |
| `SPY{=15m,tho=true}` | 1,639 | 2026-08-22 | about 90 days |
| `SPX{=15m}` | 1,829 | 2026-08-22 | about 90 days |
| `SPY{=m}` | 8,001 | 2026-10-08 | 8,000 candles. **Corrected 2026-11-22:** that is about 6 trading days on the 24-hour feed; the first date here came from a stray event. See the addendum |

Option contracts (SPY puts at the money, 29, 35 and 393 DTE):

| Period | Result |
|---|---|
| `{=d}` | 0 or 1 candle: **no daily history for a contract** |
| `{=15m}` | 72 to 437 candles since 2026-08-22: only bars with trades, so sparse |
| `{=m}` | 73 to 757 candles since 2026-10-08, sparse |
| `{=15m,price=mark}` | about 350 candles since 2026-11-06: continuous, but **two weeks only** |
| `{=d,price=mark}` | nothing |

**Consequences:**

- The 1-day chart is fully served by the broker (31 years).
- The 15-minute chart gets about 90 days from the broker. Anything longer comes from the
  local underlying store or from what the backend records.
- Option price charts from the broker are either sparse (trades) or two weeks deep (mark).
  A useful option or position chart needs the local store or own recording.

## 7. Published limits (from the documentation, not measured)

5 concurrent streaming sessions; 25,000 subscriptions per session; 10,000 subscription
changes per minute; 100 candle subscriptions.

## Addendum 2026-11-22: implied volatility on candles, backtester API, margin dry-run

One more read-only run, on a Sunday.

**`impVolatility` on underlying candles.** Daily candles subscribed from time 0:

| Symbol | Daily candles | With IV | First IV | Holes after the first IV |
|---|---|---|---|---|
| SPX | 8,000 | 2,519 | 2016-11-14 | none |
| SPY | 8,000 | 2,362 | 2016-11-14 | 2022-12-30 to 2023-08-11; 2025-10-20 to 22 |
| AAPL | 8,000 | 2,362 | 2016-11-14 | 157 days (not located; same count as SPY) |
| IWM | 6,658 | 2,315 | 2016-11-14 | 2022-12-30 to 2023-10-20 with two days present; 2025-10-20 to 22 |
| GDX | 5,156 | 2,362 | 2016-11-14 | 2022-12-30 to 2023-08-11; 2025-10-20 to 22 |
| SLV | 5,173 | 2,503 | 2016-11-14 | several short ones in 2023; 2025-10-20 to 22 |
| SOFI | 1,376 | 1,321 | 2021-06-02 | 2023-08-14 to 2023-10-20 and shorter ones |

- Values are plausible: SPY 0.81 on 2020-03-16, 0.39 on 2018-02-05.
- Hourly SPY candles carry the value too (4,723 of 4,748 since 2026-02-10).
- The daily value of 2026-11-20 (0.354) equals the after-hours hourly values; the last
  regular-hours hour showed 0.390.
- Against the `volcalc` lake's 30-day at-the-money IV the broker value is 2 to 5 vol points
  higher. The table is in the product definition, section 6.5.
- The definition of the value is not documented by Tastytrade; the docs defer to dxFeed.

**Backtester API** (from the developer docs, not called): host
`backtester.vast.tastyworks.com`; `POST /backtests`, `GET /backtests/{id}`, `/logs`,
`/cancel`, `GET /available-dates`, `POST /simulate-trade`. Responses hold trials and snapshots
with P/L and the underlying price; a simulated trade returns price and delta of the given
legs. No IV, IV rank or per-contract series. The required scope, the symbol list, the
resolution and the limits are not stated.

**Margin** (from the docs, not called): `GET /margin/accounts/{account}/requirements` for the
current state, `POST /margin/accounts/{account}/dry-run` for a hypothetical order without
placing it. The scope needed for the dry-run is not stated. Not tried (decision of
2026-11-22).

**Official MCP server** (from the docs): `github.com/tastytrade/tastytrade-mcp`, self-hosted,
84 tools, resources and prompts; `TASTYTRADE_READ_ONLY=1` withholds and refuses the 14 write
tools.

**Candle depth and alignment per resolution** (SPY, measured 2026-11-22):

- 1 minute and 5 minutes are limited to 8,000 candles: about 6 and 28 trading days on the
  24-hour feed. 15 and 30 minutes reach back about 90 days, 1 hour and 4 hours about 9 months,
  weekly and monthly to 1993.
- The intraday feed runs around the clock on weekdays (96 15-minute bars a day).
- 1-hour bars start on the hour. 4-hour bars are aligned to UTC. Daily, weekly and monthly
  bars are stamped at midnight UTC of the day, the Monday and the first of the month.
- `a=s` aligns bars to the session open (09:30, 10:30…) and delivers the regular session only.
- The table and the consequences are in `C_charting.md`, section 11.


## Addendum 2026-11-22 (WP-0.3): the REST client with the read-only grant

One run of `backend/tools/broker_check.py` on Sunday 2026-11-22, evening, market closed.
Production API, one request per second, 10 requests: one token renewal and nine reads. All
measured.

- Every read on the client's allow-list answered 200 with the existing grant. Its scopes
  were `read openid accounts:<acct>:owner`, as in section 1. No 429 at one request per
  second (BRK-07).
- Paths that were accepted:
  - futures symbols without the leading slash (`/instruments/futures/ESM7`,
    `/futures-option-chains/ES/nested`);
  - a future-option symbol percent-encoded as one segment (`./ESH7 E4BZ6 261222C6550`);
  - an OCC symbol with its spaces encoded.
- Quote token: level `api`. The address host is `tasty-openapi-dxlink-md-ws.dxfeed.com`. The
  keys of the response were not compared, so whether it states an expiry was not seen.
- Keys compared with the fake broker:
  - The SPY chain and the equity option are equal key for key.
  - The live documents carry more keys than the fake. Equities: aggregated trading sessions,
    cusip, instrument sub-type. Futures: exchange data, roll rules, cut-off times. Metrics:
    a 15-day IV index, earnings per share, P/E, liquidity running state.
  - The fake had three keys that the live documents lack. They were removed: tick sizes on
    SPX, `roll-target-symbol` on a future, and `dividend-updated-at` in the metrics.
  - Two keys of the futures chain were missing from the fake and were added:
    `futures[].streamer-symbol` and `expirations[].cash-settled`.
- Instants are written as `2026-12-24T18:15:00.000+00:00`.
- The SPY weekly of Christmas week falls on Thursday 24 December, with `expires-at` at
  18:15 UTC (13:15 ET). That is the early close plus 15 minutes, as 16:15 is on a normal day.
- ES options have weeklies on other weekdays than Friday too, for example root `E4B` on a
  Tuesday.
- The list of futures in the futures chain is not in date order. The first entry was `/ESM7`.
- Units of the SPY metrics:
  - Fractions: the IV index (0.395), its 5-day change (0.209) and the percentile.
  - Percent: the 30-day IV (33.53), HV 30 (24.07) and IV minus HV (9.46).
  - **The IV index rank was above 1**: 1.443 in the main and the `tos` field, 1.0 in the
    `tw` field. A typed model must not assume that a rank lies between 0 and 1.
  - The first entry of the IV list per expiration had no `implied-volatility` value. It was
    dated 2026-11-20, two days earlier.
  - SPY's `dividend-rate-per-share` and `dividend-yield` were 0.0.


## Addendum 2026-11-23 (WP-0.4): the streaming session

One run of `backend/tools/stream_check.py` on Sunday 2026-11-22, 18:55 New York (2026-11-23
in UTC), market closed. Production API, one session, 9 subscriptions, 88 seconds. All
measured, on one run.

- **The quote-token response states its expiry.** Its keys are `dxlink-url`, `expires-at`,
  `issued-at`, `level` and `token`. This closes the open point of the WP-0.3 addendum.
- The session opened with the sequence of the client: SETUP, AUTH after the first
  `UNAUTHORIZED`, one channel per event type, the compact format.
- The server granted the aggregation period asked, 0.5 s, on every channel.
- The server accepted the field lists as asked for all six event types, Candle included, and
  kept their order.
- Every subscription delivered its current value at once on a closed market: Quote, Trade,
  Summary and Profile of SPY, Quote and Trade of SPX, Quote and Greeks of one SPY option.
- **A candle snapshot** (`SPY{=d}` from ten days back) came in one message, newest first.
  The first event carried the flag 4 (snapshot begin). The last event carried 10: snapshot
  end and remove. So the last event of a snapshot is a marker, not a bar. Its other values
  were not looked at.
- The SPX quote sizes arrive as text (`NaN`), as section 5 says.
- **Removal:** after `remove` of the SPY quote no SPY quote came in 13 s. On a closed market
  none would have come anyway, so this shows only that the server accepts the message. After
  the reconnect the removed pair was not subscribed again and stayed silent.
- **Forced drop:** the tool closed its own socket. The server answered with close code 1000.
  The session was authorized again 1.08 s after the close, and the first data came 1.34 s
  after it (`docs/measurements.md`). The gap item named the last batch before the drop.
- No rate error, no parse error and no error message from the server.

Not tested: regular hours; removal on a ticking symbol; the subscription cap; the change
limit; a stall; a token that expires during a session. All are for WP-0.9.

## Addendum 2026-11-23 (WP-0.4, after the rework): the session without a subscription set

One run of `backend/tools/stream_check.py` on Monday 2026-11-23, 16:28 New York, in the late
session. Production API, one session, 9 subscriptions, 87 seconds. All measured, on one run.

- **All six channels opened at once.** The session asks for the six channels right after
  the authorization and sets each one up. It was `connected` 1.15 s after `connecting`.
- The server granted 0.5 s and accepted the six field lists as asked, in their order.
  Every one of the six channels had a subscription in this run. Whether the server states a
  field list before the first subscription of a channel was not looked at.
- **Removal on a ticking symbol.** SPY sent 37 quotes in the 32 s before the removal and
  none in the 13 s after it. In regular hours this is still to be seen (WP-0.9).
- **The marker that ends a candle snapshot** (flags 10) has a time and no prices. The
  snapshot of `SPY{=d}` from ten days back had 7 events: 6 bars and the marker, newest
  first.
- **After a forced drop the new session is empty.** The tool closed its own socket (close
  code 1000). The session was connected again 1.64 s later and held nothing. The tool sent
  its 8 subscriptions again itself, and the first data came 1.78 s after the close.
- The SPX quote sizes, text on the wire, arrive in the application as NaN.
- No rate error, no parse error and no error message from the server.

Not tested: the subscription manager against the live server (the tool talks to the session
alone); regular hours; the subscription cap; the change limit; a stall. All are for WP-0.9.

## Addendum 2026-11-24: what the owner's streamer dumps show about the events

Read on 2026-11-24: the dumps the owner's `streamer` wrote on six trading days, 2026-11-16
to 2026-11-23. They hold SPXW options with 0 DTE (Quote, Greeks, Summary) and the candles
`SPX{=s}` and `SPX{=m}`. They are not DXLink messages: each event went through the
streamer and is written with its field names. Today's DXLink sends the compact format only.
Only counts and value types were taken, no prices. All measured.

**Candles** (every candle event of the six days, 423,183 in all):

- **Every event has a time**, the markers included. None was 0 or missing.
- The flags seen: 0 (plain), 4 (snapshot begin), 1 (transaction pending) and 18 (snapshot
  snip with remove). The flags 8 and 10 (snapshot end) did not occur.
- **A history asked from the start is cut at 8,000 bars** and ends differently from the one
  of the WP-0.4 live check. The first event has the flag 4. 8,000 bars follow, newest first.
  The last event has the flags 18, no prices, a count of 0, and a time at a midnight before
  the oldest bar: about 3 days before it for `SPX{=s}`, about 16 days for `SPX{=m}`
  (2026-11-23). The rule behind that midnight is not known.
- So a snapshot ends in one of two ways: "snapshot end" with remove (10) when the history
  is complete from the start asked for (seen live 2026-11-23), "snapshot snip" with remove
  (18) when it was cut.
- **The snip marker comes again during the day.** An update of the bar in progress carries
  the flag 1, and the next event of the symbol is the marker with the flags 18 and the
  same time as before. Per day between 1 and 7,907 such pairs.
- On these index candles volume, VWAP, bid and ask volume and open interest are always
  NaN, written as text. The implied volatility is present on about half of the events.

**Quotes, Greeks and Summary** (the first three million lines of 2026-11-23):

- Quote of an SPXW option: the bid time, the ask time, the event time and the sequence
  are always 0. A size is a number or the text `NaN`: 5.0% of the bid sizes and 1.0% of the
  ask sizes were `NaN`. The ask's exchange code was empty on exactly those events; the
  bid's was empty on 0.6% of the events.
- Greeks: the time is always present; the flags are 0.
- Summary: the day's open, high and low are `NaN` on 15% of the events; the day's close is
  always `NaN` during the day; the previous close is `NaN` on 6.5%. The open interest is a
  whole number.
- Trade and Profile do not occur: the streamer does not subscribe them.

**Consequences taken on 2026-11-24:** a candle's time is always present in the application's
record, and an event without one is ignored and counted. The end of a snapshot is read from
either flag. The fake sends the snip marker when it cuts a history at 8,000 bars.

Not looked at: the order of the fields on the wire (the dumps are not wire data); whether
the time of the "snapshot end" marker is the start asked for; other symbols than SPX and
SPXW.

## Addendum 2026-11-24 (WP-0.5): what the owner's captures and dumps show about instruments and quotes

Read on 2026-11-24, counts and value types only. Two sources, both the owner's:
`tt` captures of 2026-11-11 to 2026-11-19 (133 of them hold the documents; stocks, ETFs, SPX, XSP)
and the streamer dump of 2026-11-23 (the first three million lines, 2,895,906 quotes of SPXW
options).

**Quotes.**

- **A missing bid arrives as price 0 with size `NaN`:** 133,773 times. A bid of 0 never came with
  a numeric size. No price of `NaN` came, and no infinity. No ask came as 0. (MKT-07)
- A price with a size of `NaN` occurs: 10,371 bids and 30,269 asks. What it means is not known.
- **Consecutive quotes of a symbol are never identical** (0.00% of 2,893,802). In 29.7% the bid and
  the ask are the same and only sizes moved.

**Instruments and chains.**

- **`expires-at` is not the settlement instant of a stock or ETF option.** For AAPL, AMD, GLD,
  NVDA, QQQ, SPY and TSLA it is 16:15 New York and equals `stops-trading-at` (13:15 on an early
  close: GLD and SPY). For SPXW and XSP both are 16:00. The streamer dump shows 13:00 for SPXW on
  2026-11-27. `tt` measured that the platform counts to 16:00 for SPY (its ticket notes of
  2026-11-16).
- The nested chain states no exercise style and no instant of expiry. A contract read alone does:
  `exercise-style` American for stocks and ETFs, European for SPXW and XSP.
- `active` is false for SPX and XSP.
- The deliverable of an index option is of the type `Cash`, amount `0.0`, without symbol and
  without instrument type. `market-time-instrument-collection` is "Cash Settled Equity Option"
  for stock options too.
- `option-tick-sizes` of AAPL, AMD, GLD, NVDA and TSLA: 0.01 below 3 and 0.05 above. SPY and QQQ:
  0.01. `tick-sizes` is absent for the indexes.
- SPX lists three roots in the chain: SPXW, SPXO, SPX, in this order. `expiration-type` seen in the
  streamer's instrument lines: Weekly, End-Of-Month, and Regular and Quarterly in the captures.

Not looked at: whether a price without a size is usable; AM-settled contracts (seen in the run
below); prices of any kind.

## Addendum 2026-11-24 (WP-0.5, live): the documents against the instrument model

One run of `backend/tools/broker_check.py` on Monday 2026-11-23, 18:03 New York (23:03 UTC), after the
close. Production API, one request per second, 13 requests, one token renewal. All measured, on
one run. Load average 1.85, 1.93, 8.45 before and 1.74, 1.90, 8.32 after. No 429.

- **SPY, SPX and /ES parsed into the model:** 31 expirations of SPY (12,478 contracts); SPXW 39
  (23,104), SPXO 2 (862) and SPX 19 (9,392); all 54 of the /ES options, each with its own future.
- **SPXO is AM-settled and weekly,** European, cash settled, multiplier 100. Two expirations.
  **SPX is AM-settled and monthly** (19 expirations, all `Regular`).
- **An AM-settled option:** `expires-at` is 09:30 New York on the expiration date, and
  `stops-trading-at` is the afternoon before: 16:15 for SPX, 16:00 for SPXO.
- SPXW has no `Regular` expirations: 31 Weekly, 4 End-Of-Month, 4 Quarterly. SPY has Regular,
  Weekly and Quarterly.
- **Dollars per point of /ES: 50,** from the future and from its options
  (notional-value / display-factor). `strike-factor` is 1.0 for all 54 expirations.
- The /ES chain has one option-chains item, with the style American for all 54. Weekly E-mini
  options are European as far as is known outside the API; no weekly contract was read alone,
  so the chain's style is not confirmed for them. The quarterly is `Regular` and
  AM-settled, the weeklies `Weekly`, EW `End-Of-Month`.
- Ticks of /ES options have four steps: 0.05 below 5, 0.1 below 20, 0.25 below 100, 0.5 above.
- **One document was refused by the parser:** a future option read alone has a `settlement-type`
  that is neither AM nor PM. The value was not looked at. The document has
  `future-option-product.is-am-settled`. The parser reads that key since then; this was not seen
  live.
- `active` is false for SPX and true for SPY, as in the captures.
- Keys only in the live documents, beyond the list of the WP-0.3 addendum: `roll-target-symbol`
  on /ESZ6. The WP-0.3 check had read /ESM7, which lacks it, so the key is optional. Nothing
  reads it.

Not tested: the fix above; other products than ES; a contract whose exercise style differs inside a
root; the SPX chain timed.
