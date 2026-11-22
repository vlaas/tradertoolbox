# Research C: Charting library for the underlying chart

Desk research on 2026-11-20 from repositories, licence files, documentation and the npm
registry. Nothing was run: no library installed, no chart rendered. Marks used per claim:

- **[V]** read today in the cited source (section 9).
- **[M]** from memory, not checked today. Treat as a hypothesis.
- **[N]** not measured by anyone here. Performance and touch claims are all [N].

Not tested: rendering speed with 8,000 candles, touch behaviour, the quality of any drawing
tool, save and restore round trips, tree-shaken bundle sizes.

## 1. Answer

Keep Lightweight Charts (move from 5.2.1 to 5.3.0). Take the user drawing tools from the
community plugin `lightweight-charts-drawing` behind an own thin interface, and keep building
the application overlays (strikes, ranges, cone, payoff, OI/GEX) as own primitives, as the
backtester viewer already does. If the plugin fails the prototype, build the four required
tools (trendline, horizontal line, vertical line, ruler) by hand on the same primitive
interface. KLineChart is the only credible switch and is the fallback if both routes fail.

## 2. Candidates ruled out on licence or scope

| Candidate | Finding |
|---|---|
| TradingView Advanced Charts | **Not available.** TradingView: "we don't provide the Advanced Charts and the Trading Platform libraries for personal use, hobbies, studies, or testing. These licenses are only available to companies for use in public web projects and/or applications" [V]. Distributed through a private GitHub repository on request, not npm [V], so it could not sit in an open repository either |
| Highcharts Stock 13.1.1 (2026-09-20) | Proprietary. The EULA grants a free Personal Use licence to a natural person for "entirely non-commercial, non-professional" use, and forbids to "distribute, sublicense, or otherwise make available to any third party the original or any modified version of the source code or compiled software" [V]. Every user of an open-source release would need an own licence, and trading for income is hard to call non-professional. Technically the most complete: Stock Tools has segment, ray, horizontal and vertical line, `measureX`/`measureY`/`measureXY` and a save-to-localStorage binding; indicators include SMA, RSI, ATR [V] |
| amCharts 5 Stock 5.23.0 (2026-11-19) | Linkware, not an OSI licence: may be bundled with open-source projects, but the built-in logo may not be removed [V]. Has Trend Line, Horizontal Line, Vertical Line and Measure tools and `serializeDrawings()` / `unserializeDrawings()` [V]. 124 kB gzip for the main entry of the core package [V] |
| DXcharts Lite 2.7.39 (2026-10-08) | MPL-2.0 [V], 105 stars [V]. **No drawings and no studies in the open package**: they live in `dxchart5-modules`, which requires DXcharts Core (`@dx-private/dxchart5-core`, private registry or ZIP, part of the DXcharts Library; a paid product [M]), and "starting from 5.20.4, they don't work with DXcharts Lite" [V]. Has a `timezone` option, `updateLastCandle` / `addLastCandle`, panes and custom drawers [V]. 85 kB gzip, depends on rxjs [V] |
| ECharts 6.1.0, uPlot, night-vision | ECharts (Apache-2.0, already in the viewer) and uPlot have no drawing tools [M]. night-vision: last release 2023-10-23 [V] |

The backtester viewer's licence check allows only MIT, Apache-2.0, BSD, ISC and 0BSD [V,
`viewer/package.json`]. MPL-2.0 and the two proprietary licences fail it.

## 3. The two real options

| | Lightweight Charts 5.3.0 | KLineChart 10.1.1 |
|---|---|---|
| Licence | Apache-2.0 [V]. Requires the NOTICE text and a link to tradingview.com on a page the user sees; the `attributionLogo` option (default on) satisfies the link [V] | Apache-2.0 [V]. No requirement beyond the licence known [M] |
| Release, activity | 5.3.0 on 2026-11-10; four releases in 12 months; repository pushed 2026-11-18; 17.9k stars; 1.79 M npm downloads a week [V] | 10.1.1 on 2026-11-16; six releases since 2026-07-31; 4.3k stars; 124k downloads a week [V]. Docs are written in Chinese first with an English translation [V] |
| Drawing tools in the library | None. The docs: "Let a user draw on the chart: Not available ... write a plugin" [V] | Built in: segment, ray, straight line, horizontal and vertical line/ray/segment, price line, channel, parallel line, Fibonacci, brush, annotation, tag [V] |
| Ruler | Not in the library | Not built in. `measure` exists in `@klinecharts/extension` 0.1.0 (2026-07-24, 6 stars) [V] |
| Save and restore | "The library does not serialize its state" [V] | No export call. `getOverlays()` returns the overlays; `createOverlay({name, points:[{timestamp, value}]})` recreates one without interaction [V]. Round trip written by hand, not tested [N] |
| Indicators | None built in. Repository examples cover moving average and ten others, not RSI or ATR [V]. Compute and plot as series | 27 built in: MA, SMA, EMA, RSI, VOL, BOLL, MACD and others. **No ATR** [V]; custom indicators through `registerIndicator` [V] |
| Panes | Yes since v5: `addPane`, resize, hide [V] | Yes, an indicator gets a pane by `paneId` [V] |
| Application overlays | Series and pane primitives draw on a canvas context with `priceToCoordinate` and `timeToCoordinate`; primitives can add axis labels and take part in autoscale [V, the viewer's `overlay.ts`, `bands.ts`]. A volume-profile example anchors a histogram to prices [V] | Custom overlays return figures (line, polygon, rect, text, path, arc) from `createPointFigures` with access to both axes and the pane bounds; custom figures get the canvas context [V] |
| Live data | `series.update()` for the last bar and for appends [V] | Only through `setDataLoader` with `getBars` / `subscribeBar` callbacks; the v9 direct update calls are gone [V] |
| 8,000 candles | [N]. Opt-in data conflation since 5.1 for "tens of thousands of data points or more" [V] | [N]. Release notes show recent repaint optimisation work [V] |
| Timezone | **None.** All times are UTC; the docs tell the caller to shift timestamps [V]. The viewer's ET-as-UTC encoding is the documented approach | `setTimezone('America/New_York')` [V] |
| Session gaps | The time scale is an index of the bars present, so nights and weekends take no space [V]. A time without a bar has no coordinate [M]; the viewer's primitives snap to the nearest bar for that reason [V] | Index-based as well [M] |
| Touch | Pinch, kinetic scroll and a tracking mode are options [V, typings]; behaviour [N] | "Supports mobile" [V]; behaviour [N] |
| Size, whole package | 195 kB min, 62 kB gzip, one dependency [V, Bundlephobia]. Tree-shakeable, so less in practice [M] | 229 kB min, 59 kB gzip, no dependencies [V, Bundlephobia] |
| Svelte | Plain DOM container; in use in the viewer [V] | Plain DOM container (`init('id')`) [V] |

## 4. Drawing tools for Lightweight Charts

| Source | State |
|---|---|
| Official packages (TradingView, Apache-2.0) | Published 2026-09-16 and 2026-11-13: `@tradingview/lwc-plugin-vertical-line` 1.0.1 (programmatic line with a time-axis label), `-user-price-lines` 1.0.0 (the user adds price lines with a button at the right edge), `-session-highlighting` 1.0.0, and `@tradingview/lwc-toolkit` 1.1.0 with base classes and hit-test helpers [V]. No trendline, no ruler, no persistence |
| Official examples | `trend-line` and `rectangle-drawing-tool` are unpublished "proof-of-concept" code [V]. The trend line is static: its source has no pointer handling. The rectangle tool places by two clicks and its source has no hit test or drag [V] |
| `lightweight-charts-drawing` 0.11.1 (MIT) | Covers every tool in section 6.1: `trend-line`, `horizontal-line`, `vertical-line`, and the measurers `price-range`, `date-range`, `date-and-price-range`; 86 tools in total. `exportJSON()` / `importJSON()` with migration of saved drawings; programmatic `add()`; magnet; a view-only mode; touch hit tolerance options. Peer dependency `lightweight-charts ^5.0.0`, no other dependency; 69 kB gzip whole. It leaves to the host: toolbar, storage, undo stack, dialogs, context menu [V, README] |
| The same plugin, the caution | Repository created 2025-12-18; 157 stars; two contributors (84 and 9 commits); 7,095 downloads a week; version 0.2 broke the 0.1 API; eighteen releases between 2026-10-01 and 2026-11-19 [V]. All of the feature claims above are the README's. None was run [N] |
| `lightweight-charts-line-tools-core` | MPL-2.0, last push 2026-07-16, 107 stars [V]. Not examined further |

## 5. Recommendation

Keep Lightweight Charts. Reasons, in order of weight:

1. The larger and riskier half of R2 and R3 is application drawing, not user drawing: strike
   segments, range templates, the expected-range cone, the payoff profile, OI and GEX by
   strike. No library has these. The viewer already has working primitives for strike
   segments with labels, horizontal and vertical annotation lines, shaded time bands and
   autoscale participation, on this exact interface.
2. Every library with drawing tools and a complete indicator set is closed to this project by
   licence (section 2). The choice is between two Apache-2.0 libraries only.
3. KLineChart's built-in lead is smaller than it looks for this requirement list: it has the
   lines but not the ruler (a 0.1.0 extension), not ATR, and no save format. Against that it
   costs a second chart API next to the viewer's, a rewrite of the existing primitives, and a
   pull-style data loader in front of a push WebSocket.
4. The MIT plugin, if it holds up, removes most of the hand work and has the save format.
   Lightweight Charts itself has about fourteen times KLineChart's npm usage.

**Main risk:** the drawing layer depends on a 0.x plugin that is eleven months old and
effectively has one maintainer. Mitigation: pin the exact version, call it only through an
own `Drawings` interface, and store drawings in an own versioned record so the plugin can be
replaced by hand-built tools without losing saved drawings.

**Second risk:** the time model. There is no timezone and no coordinate for a time without a
bar. The cone, expiry lines and any drawing right of the last bar need whitespace bars for
future sessions, and saved drawing times must survive the ET-as-UTC encoding and a change of
resolution. KLineChart has the timezone call; the future-bar question is the same there [M].

## 6. What has to be built by hand

Estimates are rough working days for one developer, not measured.

| Item | Basis | Days |
|---|---|---|
| Chart component: history load, live `update`, resolution switch, ET encoding, future whitespace bars, legend and crosshair readout | Viewer `TradeChart.svelte` as a start | 3 |
| SMA 15/50/100/200, RSI, ATR as computed series; volume pane; pane toggles | New, small; values can come from the backend | 2 |
| Drawing toolbar, selection, delete, keyboard, undo | New; the plugin supplies the tools only | 2 to 3 |
| Persistence per symbol: backend table and endpoints, versioned record | New | 1 to 2 |
| Range templates: opening range and other ranges as lines, critical moments as vertical lines | Extends `overlay.ts` (`hlines` with `from`/`to`, `vlines`) | 1 to 2 |
| Ticket on chart: strikes, breakevens | `overlay.ts` segments and lines, reused | 1 |
| Expected-range cone | New primitive | 1 to 2 |
| Payoff profile and OI/GEX histogram pinned to the right edge | New primitives; the volume-profile example as pattern | 3 to 4 |
| **Total with the plugin** | | **14 to 19** |
| Extra if the plugin is rejected: trendline, horizontal line, vertical line and ruler with placement, hit test, drag, touch and serialisation | Official examples and `lwc-toolkit` as a base | 8 to 12 more |

Under KLineChart the first, fifth, sixth, seventh and eighth rows are written again from
nothing, the ruler and ATR are still added, and persistence is still hand-built.

## 7. Prototype proposal

One Svelte page on Lightweight Charts 5.3.0 with the plugin pinned at 0.11.1, fed by recorded
SPY data. It should prove five things:

1. **Load and live update.** 8,001 daily and 6,025 15-minute candles; replace the last bar
   twice a second and append bars. Record `setData` and frame times. Pass: no visible stall.
2. **Drawing round trip.** Place a trendline, a horizontal line, a vertical line and a
   date-and-price range on the 15-minute chart with ET-encoded times. Export, reload the
   page, import, switch to 1-day and back. Pass: every anchor returns to the same price and
   bar, and the ruler shows price, percent and time correctly across an overnight gap.
3. **Future time.** Draw a cone and a vertical line to an expiry 30 days ahead on whitespace
   bars. Pass: both hold their place while live bars are appended, and a user drawing can be
   anchored in the future area.
4. **Right-edge profile.** A payoff curve and an OI/GEX histogram pinned to the right of the
   pane, with labelled strike segments from `overlay.ts`. Pass: they follow the price scale
   through zoom, scroll and autoscale, and do not block the plugin's pointer handling.
5. **Panes and touch.** Volume, RSI and ATR panes with four SMA lines; on a tablet: pan,
   pinch, place and drag a drawing. A touch fail is recorded, not blocking (phone is "Later").

If check 2 or 4 fails because of the plugin, repeat check 2 with one hand-built tool
(horizontal line) to size the fallback before deciding on KLineChart.

## 8. Open points

- Saved drawings: store true UTC instants and convert at the chart boundary (safer), or the
  ET-encoded chart time.
- The attribution: keep `attributionLogo` on, or put the NOTICE text and link in an About page.
- ATR and RSI in the frontend or in the backend pricing core (task E).

## 9. Sources (all fetched 2026-11-20)

- npm registry for versions, dates, licences: `registry.npmjs.org/<package>`; weekly downloads: `api.npmjs.org/downloads/point/last-week/<package>`; sizes: `bundlephobia.com/api/size?package=<package>@<version>`
- Lightweight Charts: https://github.com/tradingview/lightweight-charts (README, NOTICE, releases v5.1.0 to v5.3.0, `plugins-2026-09-16`, `plugins-2026-11-13`, `packages/`, `plugin-examples/`, `indicator-examples/`); docs sources `website/docs/time-zones.md`, `panes.md`, `time-scale.md`, `product-comparison.md`; typings of the installed 5.2.1 in `backtester/viewer/node_modules`
- https://github.com/deepentropy/lightweight-charts-drawing (README, `docs/TOOLS.md`, CHANGELOG, GitHub API metadata); https://github.com/difurious/lightweight-charts-line-tools-core (metadata only)
- KLineChart: https://github.com/klinecharts/KLineChart (releases, source tree `src/extension/`, `docs/guide/overlay.md`, `data-integration.md`, `docs/en-US/guide/indicator.md`, `figure.md`, `faq.md`, `v9-to-v10.md`, API references `setTimezone`, `getOverlays`, `createOverlay`); https://github.com/klinecharts/extension
- TradingView Advanced Charts eligibility: https://www.tradingview.com/free-charting-libraries/ (FAQ "Can I get a library for personal use?")
- DXcharts: https://github.com/devexperts/dxcharts-lite (README, `docs/generated/FullChartConfig.md`, `ChartComponent.md`); https://devexperts.com/dxcharts/documentation-for-developers/dxcharts-lite/quick-start-core
- Highcharts: https://shop.highcharts.com/license-eula (sections 1.2, 1.5, 3); https://www.highcharts.com/docs/stock/technical-indicator-series; `ts/Stock/StockTools/StockToolsBindings.ts` and `docs/stock/stock-tools.md` in https://github.com/highcharts/highcharts
- amCharts: https://unpkg.com/@amcharts/amcharts5@5.23.0/LICENSE; https://www.amcharts.com/docs/v5/charts/stock/toolbar/drawing-control/; https://www.amcharts.com/docs/v5/charts/stock/serializing-indicators-annotations/

Not usable: `shop.highcharts.com/non-commercial` returned a JavaScript shell; the EULA page
was used. The Advanced Charts licence agreement was not read, only TradingView's public FAQ.

## 10. Prototype results (2026-11-22)

Code: `prototypes/chart/`. Lightweight Charts 5.3.0 with `lightweight-charts-drawing` 0.11.1,
real SPY candles from the broker (7,999 daily, 6,122 15-minute), driven by Playwright in
headless browsers. **All five checks, and a sixth on the time model (section 11), pass in
Chromium, Firefox and WebKit. The recommendation of section 5 stands: Lightweight Charts with
the plugin.**

| Check | Result | Measured |
|---|---|---|
| 1 Load and live update | Pass (Chromium, Firefox, WebKit) | On an idle machine: loading all bars with four SMA lines and two panes takes 50 to 80 ms in Chromium, 60 to 110 ms in WebKit and about 100 to 145 ms in Firefox. At two updates a second no frame was late in any of the three. One update costs 1 to 5 ms |
| 2 Drawing round trip | Pass (Chromium, Firefox, WebKit) | Trendline, horizontal line, vertical line and ruler placed by clicks; every anchor on the clicked bar; saved, reloaded and re-imported identical; to 1-day and back with zero pixel drift. The ruler across a weekend gap read 2.58 (0.34%), 11 bars, 2 d 2 h 45 m, all correct |
| 3 Future time | Pass (Chromium, Firefox, WebKit) | Cone and expiry line 30 days ahead stay on their times while 12 bars are appended; a trendline end and a vertical line can be placed in the future area and do not move. With 2,400 future bars on screen a bar is 0.7 px wide; there Firefox and WebKit put the anchor one bar beside the click, Chromium on it |
| 4 Right-edge profile | Pass (Chromium, Firefox, WebKit) | Strike lines, payoff curve and open-interest bars follow the price scale through wheel zoom, scroll, price-scale drag, autoscale and window resize (error 0 px), stay pinned to the right edge, and do not block placing, selecting or dragging a drawing under them |
| 5 Panes and touch | Pass, touch by emulation only | Three panes, volume inside the price pane. With emulated touch events: one-finger pan, two-finger pinch, placing by tap, selecting by tap, dragging a selected drawing while the chart holds still, and a swipe over an unselected drawing pans. No real tablet was used |

**Limits of these results**

- The first runs were made while other jobs loaded the machine (load average 48 to 86 on 20
  cores); loading then took up to 0.5 s in Chromium and Firefox and WebKit dropped frames. The
  figures in the table are from a rerun on the idle machine. Headless Firefox and WebKit paint
  at 30 frames a second, so their frame figures show steadiness, not speed.
- Touch was emulated in headless Chromium. A real tablet or phone is still untested.
- Not exercised: undo, multi-select, magnet, text tools, many drawings at once, the plugin's
  other 82 tools.

**What the prototype taught**

1. **Future whitespace belongs in a series of its own.** A whitespace-only line series extends
   the time scale, and the candle series keeps accepting plain `update()` calls for the last
   bar and for new bars. Putting whitespace into the candle series would make every live bar a
   historical update, which the library documents as slower (not tried).
2. **The plugin snaps anchors to bar times and keeps them when the resolution changes.** A
   drawing made on the 15-minute chart is shown on the 1-day chart by interpolation inside the
   day's bar, and the saved model is untouched. The ruler's bar count is computed in the
   current resolution, so it reads "0 bars" there.
3. **A drawing made on the 1-day chart lands at 00:00 on the intraday chart**, the start of
   the calendar day, not the session open. Whether a daily anchor should map to the open is a
   design choice for the requirements.
4. **The broker's 15-minute feed runs around the clock on weekdays.** There is no overnight
   gap, only the weekend. Regular hours only is a different candle symbol (`tho=true`, 1,664
   bars for the same 90 days). The chart needs a session switch.
5. **The feed contains bad prints.** One 15-minute bar opened at 692.15 between neighbours near
   713. The chart needs a filter or a marked-as-suspect state.
6. **Strike lines in the autoscale squash the candles** once the view scrolls into history
   where the price was far from the strikes. They should take part in the autoscale only while
   the latest bar is on screen, or as a switch.
7. **Loading is dominated by the indicator series**, not the candles: of about 250 ms, the four
   SMA series took about 190 ms. Loading a window of bars first and the rest on scroll would
   remove most of it.
8. **The saved drawings hold chart times**, which are ET-encoded for intraday bars. For storage
   they should be converted to true UTC instants at the boundary (the first open point of
   section 8). A ruler across a daylight-saving change was not tested; in ET-encoded time it
   would be one hour off the true elapsed time.
9. **Playwright can run all of this headless**, including pointer and touch input and reading
   coordinates back from the chart. The same approach serves the testing plan.

Bundle of the prototype page (Svelte, the chart library, the plugin): 439 kB, 142 kB gzip.

## 11. Time model, sessions and resolutions (2026-11-22)

Decisions of 2026-11-22: intraday charts show the 24-hour feed, with the hours outside the
regular session tinted; the time axis shows exchange time by default and can be switched to
UTC or local time; a drawing made on the 1-day chart is anchored at the market open; weekly and
monthly charts come later. The prototype was extended to try this, as check 6.

**The model**

- Every bar and every drawing anchor is a true UTC instant. Drawings are stored in that form.
- At the chart boundary an instant is shifted by the zone's offset at that instant, so that the
  chart's UTC rendering reads as wall-clock time in the chosen zone. The offset comes from the
  browser's time-zone database, so each region's own daylight-saving dates apply.
- A daily bar's instant is the open of its session (09:30 exchange time). That alone puts a
  daily anchor on the opening bar of an intraday chart.
- Daily charts always show the trading date, whatever zone is selected.
- On a change of resolution or zone the drawings leave the plugin as instants and are put back
  under the new mapping.
- Sessions are classified by the exchange clock, never by the display zone.

**Check 6 results (pass in Chromium, Firefox and WebKit)**

| Point | Result |
|---|---|
| Resolutions 1D, 4h, 1h, 15m and 5m load, and chart times rise strictly in seven zones | Yes. Only a zone that changes its clocks on a weekday needs help (see below) |
| The 09:30 New York open read in other zones around the weeks when Europe and the US differ | Tallinn: 16:30 on 23 Oct, 15:30 on 26 and 30 Oct, 16:30 on 2 Nov. UTC: 13:30, 13:30, 13:30, 14:30. All correct |
| Session tint (overnight, early, regular, late) | Same bars tinted in New York and Tallinn display |
| Daily drawing on intraday charts | Both anchors on the 09:30 bar of the 15-minute and 5-minute charts. On the 1-hour and 4-hour charts 09:30 lies inside a bar, and the plugin places the anchor part-way through it |
| Zone round trip (New York, UTC, Tallinn, Cairo, Tokyo, New York) | Same instants, same bars, identical model at the end |
| Reload from the UTC store | Identical |
| Ruler from Fri 30 Oct 15:00 to Mon 2 Nov 10:00 New York, across the US clock change | True elapsed time 2 d 20 h; the plugin, working on chart times, says 2 d 19 h. The label hook replaces it with the true value |

**Findings**

1. **A clock set back while bars keep coming would repeat an hour**, and chart times must rise.
   With a 24-hour feed this happens only in zones that change on a weekday; of those tried,
   Cairo (Thursday night). The prototype carries the earlier offset until the data has a gap,
   so labels in that zone read one hour ahead for the rest of that week (108 of 6,122
   15-minute bars). Exchange time, UTC and European, Japanese and Australian zones need
   nothing, because their changes fall in the weekend gap.
2. **The ruler's elapsed time has to come from the instants**, not from chart times. The
   plugin's label hook is enough for that.
3. **Hourly and 4-hour bars do not start at the open.** The broker's 1-hour bars start on the
   hour, and its 4-hour bars are aligned to UTC, so in New York time they start at 00:00, 04:00,
   08:00… in summer and 03:00, 07:00, 11:00… in winter. The open and the close fall inside a
   bar. With `a=s` the broker aligns bars to the session, but then delivers the regular session
   only. Decided 2026-11-22: the backend builds such bars from finer ones (30-minute bars
   today), as a general rule for any resolution whose broker bars do not start at the open.

**What the broker gives per resolution** (SPY, 24-hour feed)

| Resolution | Candles | Since | Limit |
|---|---|---|---|
| 1 minute | 8,000 | 2026-11-12 | 8,000 candles: about 6 trading days |
| 5 minutes | 8,000 | 2026-10-13 | 8,000 candles: about 28 trading days |
| 15 minutes | 6,122 | 2026-08-24 | about 90 days |
| 30 minutes | 3,071 | 2026-08-24 | about 90 days |
| 1 hour | 4,748 | 2026-02-10 | about 9 months |
| 4 hours | 1,195 | 2026-02-10 | about 9 months |
| 1 day | 7,999 | 1995-02-07 | 8,000 candles |
| 1 week | 1,765 | 1993-01-25 | none met |
| 1 month | 407 | 1993-01-01 | none met |

This corrects the earlier note, which gave about six weeks for 1-minute candles: that came from
a stray first event. Longer intraday history has to come from the local underlying store.

