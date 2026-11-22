# Chart prototype

Throw-away code for the five checks of `docs/research/C_charting.md`, section 7, and a sixth on
the time model. Results are in sections 10 and 11 of that note. Nothing here is meant to be kept as product code.

- Lightweight Charts 5.3.0 and `lightweight-charts-drawing` 0.11.1, both pinned, in one Svelte 5
  page.
- `src/lib/chart.ts`: candles with volume in the price pane, four SMA lines, RSI and ATR panes,
  future whitespace in a series of its own, the drawing plugin, a simulated live feed.
- `src/lib/layers.ts`: the expected-range cone with the expiry line, and the right-edge profile
  (payoff curve, open-interest bars, strike lines). The open interest is invented.
- `src/lib/data.ts`: the time model: bars and anchors as UTC instants, encoded for a zone at the
  chart boundary; sessions by the exchange clock.
- `checks/`: the checks as Playwright tests. They print `RESULT` lines.

## Run

```
npm install --ignore-scripts
python tools/fetch_candles.py <env_file> public/data    # needs httpx, websockets, python-dotenv
npm run dev            # http://localhost:5177
npx playwright test          # Chromium, emulated touch, Firefox and WebKit
```

The env file is a plain dotenv file with the OAuth values of your Tastytrade API application:

```
TASTY_REFRESH_TOKEN=...
TASTY_CLIENT_SECRET=...
TASTY_IS_TEST=false        # optional; true uses the sandbox host
```

`fetch_candles.py` downloads SPY candles with your own Tastytrade credentials (read-only).
`public/data/` is git-ignored: market data is never committed.

Check 1 measures frame times, so it only means something on an idle machine. The default fetch
downloads every resolution the checks use.
