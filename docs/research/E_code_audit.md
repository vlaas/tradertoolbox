# Research E, part 1: audit of the existing projects

Done on 2026-11-20 by reading code in `volcalc`, `bt_dataset`, `backtester`, `tt` and
`streamer`. No tests or builds were run. Throughput figures are from those projects' docs.

## 1. Pricing and analytics

### What exists

| Capability | Best base | Notes |
|---|---|---|
| European price, IV, Greeks | `volcalc/src/vrp/iv/bsm.py`, `black76.py` | numpy and scipy only; tested against a frozen `bt_dataset` reference; fail codes; lifts as a pure library |
| Forward from put-call parity | `volcalc` `iv/forward.py` `fit_forward`, fed quote mids as `bt_dataset/pipeline/carry.py` does | Live data is quotes, not prints. Imports `vrp.config`, so it needs decoupling |
| Rates and dividends | `volcalc` `refdata/rates.py`, `iv/forward.py` | Continuous rates, discrete dividends |
| Time to expiry | `volcalc` `iv/clocks.py` | Settlement instant, early closes, business clock |
| SVI and SSVI, density, ITM probability on the smile | `volcalc` `surface/` | `q_itm` and `density` give skew-aware probabilities |
| Model-free IV, constant maturity | `volcalc` `surface/mfiv.py` | |
| Expected move | `backtester/lib/expected_move.py` | Pure, chain-level; 17 tests |
| Payoff at expiry, breakevens, max profit and loss | `tt/tt_capture.py` (`payoff_pnl`, `breakevens`, `payoff_extremes`) | Scalar, single expiry; rewrite on numpy |
| Margin (Reg-T) | `backtester/broker/margin_model.py` | Has a tastytrade preset; 53 tests |
| Early assignment rule | `backtester/broker/settlement.py`, `bt_dataset/pipeline/flags.py` | Bound to the position book; port the rule |
| American price | `volcalc` `iv/crr.py` | Scalar Python loop, a diagnostic only |

### What does not exist

- **POP, P50 and CVaR code.** `tt/FINDINGS.md` holds the rules in prose; no file implements
  them. There is no path simulator in any repo.
- Portfolio CVaR.
- American IV and Greeks, and a vectorised American pricer.
- Vanna, charm, volga in the array code (a scalar version is in
  `market_insight/compute/bsm.py`).
- A profit-and-loss surface over a price by date grid.
- Multi-expiry structures (calendars, diagonals).

European Greeks are pure functions of (S, K, T, r, q, sigma), so what-if on price, date and
volatility works with the existing code.

### Conventions that differ between the repos

Merging the code changes numbers unless one convention is chosen for each row.

| Topic | Variants found |
|---|---|
| Year length | 525,960 minutes (`bt_dataset` Greeks); 525,600 (`volcalc`); 365 days (`backtester`, `tt`) |
| Rates | Quoted CMT yields, flat below 30 days (`bt_dataset`); continuous with a SOFR knot (`volcalc`); flat 2.93% (the platform's POP) |
| Dividends | Continuous yield (`bt_dataset`); escrowed cash dividends (`volcalc`); none (`backtester` delta, `tt`) |
| Greek units | Vega per 1% and theta per calendar day, spot delta (`bsm_greeks`); vega per unit, forward delta (`black_greeks`) |
| Expiry instant | 16:15 for SPXW (`backtester` `em_iv`); 16:00 ET (the platform's POP) |
| Max loss | USD per contract (`backtester`); per share (`volcalc`); including the credit (`tt`) |
| SSVI | Fixed gamma, fitted in total variance (`bt_dataset`); free gamma, fitted in IV (`volcalc`) |

### Dependencies

- `volcalc` needs Python 3.13 and pins exact versions (duckdb 1.4.0). `backtester` requires
  duckdb 1.5.6 or newer. The two cannot be installed together as they are.
- No repo uses numba.
- `bt_dataset` modules import a flat `config`, so its functions must be ported, not imported.

**Consequence:** extract the pure modules into a new package inside this repo rather than
adding the projects as submodules.

### Speed (from docs)

IV inversion 0.35 million per second on one thread; SVI about 30 ms per slice.

## 2. Broker client

| Code | Verdict |
|---|---|
| `tt/tt_capture.py` `TTClient` (OAuth, REST) and `DXLinkFeed` (asyncio; waits for authorisation; explicit field lists; server field order respected; rate pacing and backoff) | **Base for the backend's broker module.** Lacks reconnect, unsubscribe and candles |
| `tt/tests/fake_tt.py` (fake REST and DXLink server) | Reuse as the test double; extend with candles, removal, default field lists, disconnects |
| `streamer/download_bars.py` `finalize_day` and writers | Reuse: pure and tested |
| `streamer/tasty_feed.py`, `dx.py`, `streamer.py` loop | Rewrite. No reconnect; the reader thread dies silently; errors and oversized packets are ignored |

### What replacing the streamer means

The streamer records SPX only: SPXW PM contracts with DTE up to 3 (Quote, Greeks, Summary;
about 7,500 subscriptions), `SPX{=s}` and `SPX{=m}` candles, and a daily open-interest sweep
of all active SPXW contracts (about 17,000) in batches of 300. It runs on another host.

Its files feed the 0DTE pipeline of `bt_dataset` through `decode_tasty_dump` (Rust). A
replacement must keep:

1. The same file names and the three JSONL line shapes, with values as received.
2. `eventType` and the consumed fields in each entry.
3. Receipt timestamps with the same conflation, or a recalibration of the 819 ms shift in
   `bt_dataset/config_0dte.py`.
4. The same subscription set, to the close.
5. The daily full-chain definitions and first Summary per contract.
6. Per-minute NTP lines.
7. Outputs of `download_bars.py` byte-compatible, run at least every three weeks.

It should also add what the streamer lacks: reconnect and resubscribe within seconds, with
stall detection. Recorded history shows gaps of 60 s or more on 38% of captured days.

The recorder uses about 7,800 of the 25,000 subscriptions, leaving about 17,000 for
interactive use.

**Recommendation:** build the recorder as a module of the backend, run it beside the old
streamer (two of five sessions) until its output is shown equal, then retire the streamer.
It should not gate the first release.

## 3. Web viewer (`backtester/viewer`, `backtester/view`)

The viewer is a request-response reader of static run directories. It has no live data,
no indicators, no user drawing tools and no login.

| Part | Verdict |
|---|---|
| Tooling: Vite, Playwright, accessibility and offline checks, OpenAPI-to-TypeScript types, licence allow-list | Lift as is |
| Design tokens (`app.css`: dark and light, colour-blind palette, compact density), settings and toast stores, modal, keyboard map, PNG and TSV export, ECharts wrapper | Lift as is |
| Lightweight Charts primitives: vertical bands, horizontal and vertical lines with labels and strike segments, bid/ask band | Lift as is: these are the basis for strikes and markers on the chart |
| Service patterns: Arrow transport, cache, error envelope, pure ASGI middleware, query cancellation | Lift as patterns |
| Chart components (`TimeSeriesChart`, `TradeChart`) | Lift with changes: they redraw whole arrays; live charts need incremental `update()`, volume and indicators |
| `DataTable` (virtual rows) | Close to a rewrite for an option chain: no keyed rows, no per-cell updates, no calls/strike/puts layout |
| Shell, routing by URL hash, API client | Lift with changes: remove run concepts |
| Views, domain components, service routes | Not reusable |

### What must be built new for the frontend

- A live transport from backend to browser (WebSocket or server-sent events) and stores that
  apply incremental updates.
- An option-chain grid with streaming cells.
- Indicators (SMA, RSI, ATR) and the volume pane.
- Drawing tools: trendline, ruler, user-placed horizontal and vertical lines, with storage.
- Backend login.

### Recorded lessons worth keeping

- Lightweight Charts has no timezone support; the viewer encodes ET wall clock as UTC.
- A custom series must paint only the visible range.
- In Svelte 5, never read reactive state on a request path (it caused hundreds of aborted
  requests); use `$state.raw` for large data.
- Starlette's `BaseHTTPMiddleware` hides client disconnects.

## 4. Decisions this audit asks for

1. One set of pricing conventions (table in section 1). **Decided 2026-11-20:** `volcalc`'s
   (525,600-minute year, continuous rates, discrete dividends, settlement-instant clock), with
   a separate, labelled "platform" mode for the POP, P50 and CVaR replicas (flat rate, calendar
   days, 16:00 ET). The full list is in `docs/product_definition.md` section 11.1.
2. Python 3.13 and one dependency set for the new repo.
3. Extraction, not submodules, for the pricing code.
4. Recorder take-over as its own step, validated side by side.
