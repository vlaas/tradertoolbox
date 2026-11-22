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

