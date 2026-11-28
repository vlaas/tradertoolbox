# Measurements

Every timing or resource measurement of the project is recorded here (definition of done,
implementation plan section 1; QA-08). Results from the research notes stay in those notes.

## How to record

- One row per result: the date, the package, what was measured, the result and the method
  (script, data source, number of runs).
- Timings are taken on an idle machine and the load average (1, 5 and 15 minutes) is recorded
  with each one. The owner runs heavy data jobs on the development machine, and the chart
  prototype showed that load inflates results several times over (plan 3.7).
- Report the median, the 95th percentile and the maximum, and say whether the data was the
  synthetic market or a named local recording.
- Record the CPU time beside the wall time, and the machine (added 2026-11-27, QA-08). The product
  moves to a dedicated machine later, so the headroom is judged from the CPU time: the event loop is
  one thread, and its headroom is the share of one core it uses.
- A figure that was estimated, not measured, does not belong here. A headroom computed from a
  measured CPU time is marked "computed".

**The machine** of every row, unless the row says otherwise: the development machine, an Intel Core
i9-10850K at 3.6 GHz, 10 cores and 20 threads, 64 GB, macOS 15.7 (recorded 2026-11-27).

## Results

| Date | Package | What | Result | Load average | Method |
|---|---|---|---|---|---|
| 2026-11-23 | WP-0.4 | Live stream: recovery after a forced drop (the tool closes its own socket) | First data again 1.34 s after the close. All 8 subscriptions sent again after 1.85 s: 0.13 s to see the loss, 0.30 s reconnect wait (drawn below 1 s), 0.65 s to open and authorize, 0.77 s to send the subscriptions | 2.37, 1.75, 1.57 before; 1.51, 1.64, 1.54 after | `tools/stream_check.py`, production API, market closed (Sunday 18:55 New York). **One run**, so no median or percentile |
| 2026-11-23 | WP-0.4 | Live stream: from start to all subscriptions sent | 2.5 s: 0.8 s for the access token and the chain over REST, 1.0 s for the quote token and the handshake, 0.7 s to send 7 subscriptions | as above | Same run |
| 2026-11-23 | WP-0.4 | Live stream after the rework: opening, and recovery after a forced drop | From `connecting` to `connected`, with all six channels open: 1.15 s. After the drop: connected again, and empty, 1.64 s after the close; 8 subscriptions sent again by the tool at 1.65 s; first data 1.78 s after the close | 1.97, 11.89, 27.14 before; 2.17, 9.46, 24.68 after. The 5 and 15 minute figures show earlier load: not an idle machine | `tools/stream_check.py`, production API, Monday 16:28 New York (late session). **One run**, so no median or percentile |
| 2026-11-23 | WP-0.4 | Recording: size of the stream file, zstd against gzip. Sizes, not timings | 493 subscriptions, 10 minutes: the lines are 5,232,559 bytes. zstd with a frame every 3 s (as built, 201 frames): 760,065 bytes, 6.9 times smaller. zstd in one frame with a block flush every 3 s: 528,330, 9.9 times. zstd in one frame: 515,753, 10.1 times. gzip level 5 in one stream (part 1): 626,603, 8.4 times. **As built, the file is 1.21 times the size of gzip's** | 1.58, 2.49, 12.29 (sizes do not depend on it) | `tools/recording_size.py`: the fake broker through the feed on a virtual clock, Friday 2026-11-20 from 10:00 New York. Synthetic data; one run, and the data is the same on every run |
| 2026-11-23 | WP-0.4 | The same, smaller | 133 subscriptions, 3 minutes: lines 514,484 bytes. zstd in frames (61): 88,166, 5.8 times. Block flush: 62,771, 8.2 times. One frame: 60,119, 8.6 times. gzip level 5: 67,625, 7.6 times. As built, 1.30 times the size of gzip's | as above | `tools/recording_size.py --minutes 3 --strikes 10` |
| 2026-11-24 | WP-0.4 | Recording: the same sizes after the change to a block flush (decided 2026-11-24). Sizes, not timings | 493 subscriptions, 10 minutes, lines 5,232,559 bytes. As built now, one frame with a block flush every 3 s: 528,330 bytes, 9.9 times smaller, 0.84 of gzip's size. The other figures are those of the row of 2026-11-23: the data is the same | not taken (sizes do not depend on it) | `tools/recording_size.py`, one run. The tool computes each variant from the recorded lines, with a flush every 3 s of the data's time |
| 2026-11-27 | WP-0.8 | The built frontend (`npm run build`, Vite 8.3.6). Sizes, not timings | Script 181.74 kB (57.58 kB gzip), stylesheet 7.60 kB (2.35 kB gzip), page 0.39 kB. What the script is made of was not looked at | 24.42, 25.56, 21.20 (sizes do not depend on it) | Vite's own report of one build, 2026-11-27 01:30 local time |
| 2026-11-27 | WP-0.8 | The built frontend after the review: the font of prices and the choice of time zone. Sizes, not timings | Script 187.71 kB (59.71 kB gzip), stylesheet 8.21 kB (2.53 kB gzip), font file 21.16 kB (JetBrains Mono, Latin, regular), page 0.39 kB | 20.38, 22.80, 18.73 (sizes do not depend on it) | Vite's own report of one build, 2026-11-27 02:08 local time |
| 2026-11-27 | WP-0.9 | Live stream in regular hours, **rehearsal under load**: events and batches a second, for 2,008 pairs (SPY and SPX; Quote and Greeks of 1,000 options near the money: SPY 2026-11-27 and 2026-11-30, SPXW 2026-11-27). Counts, not timings | All events: median 471, p95 556, max 588 a second. Quote 454, 540, 576; Greeks 12, 17, 30; Trade 3.0. Batches: 7.9, 8.9, 9.3 a second. Each pair's quote comes at most every 0.5 s (the aggregation period) | 1-minute load median 14.2, p95 80.7, max 91.2; above 70 from 10:24 to 10:45 and 12:56 to 13:13 | `tools/live_day.py --until 13:30 --drops 11:00,12:30 --removal-at 11:30`, production API, the half day after Thanksgiving, 182 minutes from 10:01 to 12:59 New York; one run. A thin day: Monday's run is the measurement |
| 2026-11-27 | WP-0.9 | The same run: the reader, the event loop, IV per flush and the CPU. **Under load: not a measurement** | Wait of a batch from receipt to take: median 0.4, p95 1.9, max 16.7 ms (84,018 batches). Lag of a 100 ms timer: median 0.4, p95 2.3, max 248 ms (the maximum at 10:35, load 72). IV per flush (stand-in for MKT-04): median 214 contracts in 4.2 ms, p95 569 in 8.7 ms, max 876 contracts and 31.7 ms (20,205 flushes). CPU: median 1.7%, max 2.6% of one core. No batch lost by any reader | as above | Same run |
| 2026-11-27 | WP-0.9 | The same run: CPU per event, for the headroom. **Under load** | CPU time of the process per event, all its work included (decoding, the market state, IV per flush, the server): median 36, p95 50, max 67 µs. At the median one core takes about 28,000 events a second (computed), against at most 588 seen. The share of the event loop's thread alone was not recorded in this run | as above | Same run, `live_day.py --summary`; minutes with events (179 of 182) |
| 2026-11-27 | WP-0.9 | The same run: memory (PRF-09). Not a full day | Resident memory: median 143, p95 163, max 163 MB. Peak (the high-water mark): 157 MB at the start, 159 MB after a minute, 163 MB at the end. The bound is 1,024 MB | as above (memory does not depend on it) | `ps` and `getrusage` once a minute; 3.5 hours, not a day |
| 2026-11-27 | WP-0.9 | Live: two forced drops of the backend's session, with the manager filling it again (2,008 pairs) | 11:00: connected again 1.29 s after the close, first data 1.42 s, everything sent again 14.02 s. 12:30: 1.99 s, 2.12 s, 14.71 s. The refill is the manager's pace of 150 changes a second (2,008 / 150 = 13.4 s, computed) | 1-minute load about 20 at 11:00 and 25 at 12:30 | Same run; the tool closes the session's socket. Two runs, so no percentile |
| 2026-11-27 | WP-0.9 | Live stream in regular hours, the session alone: recovery after a forced drop | Connected again, and empty, 1.56 s after the close; 8 subscriptions sent again at 1.58 s; first data 1.70 s after the close | 17.46, 17.56, 18.52 before; 18.79, 18.06, 18.62 after | `tools/stream_check.py`, production API, 09:35 New York. One run |
| 2026-11-27 | WP-0.9 | The calendar: import and build in a fresh process, and its memory | Import of `s3t.refdata.calendar`: median 54, p95 64, max 64 ms. `load_calendar` with the import of the library: median 1,132, p95 1,225, max 1,225 ms. Peak memory 10 MB before; growth median 67, max 68 MB | 2.35, 7.40, 23.21 before; 2.99, 7.37, 23.01 after. Idle by the 1-minute figure; the 15-minute one still holds the load of the day | `tools/timings.py calendar`, 5 processes, 13:30 New York |
| 2026-11-27 | WP-0.9 | Parsing a live option chain on the event loop, against the 50 ms of PRF-05 | SPY, 12,726 contracts: median 5.3, p95 5.6, max 5.8 ms. SPX, 32,296 contracts (SPXW, SPXO, SPX): median 13.8, p95 14.3, max 15.2 ms | 2.99, 7.37, 23.01 before and after | `tools/timings.py chains`, one live read of each chain (3 requests), 20 parses each |
| 2026-11-27 | WP-0.9 | Capacity of the reader with IV per flush: a regular-hours recording replayed at multiples of its pace through the feed, the market state and the IV taker. Without the DXLink decoding | The recording: 488 pairs, 70,608 events in 603 s (117 a second). No batch lost at any multiple. 10 times: 1,135 events a second, 2% of a core; 100 times: 9,145, 6%; 300 times: 19,880, 12%; 1,000 times: 35,246, 19%; 10,000 times: 49,399, 24%. Above 300 times the replay does not keep its pace, so about 49,000 a second is the replay's limit, not the reader's. IV per flush: 160 contracts, median 2.6 ms, max 3.1 ms | 3.15, 7.33, 22.90 before; 2.65, 5.60, 20.08 after | `tools/timings.py reader rh-20261127`, a local recording of `tools/capture.py` (10 minutes from 09:40 New York, SPY and SPX, 40 strikes of one expiration). One run per multiple |
| 2026-11-27 | WP-0.9 | Recording: size of a regular-hours stream file. Sizes, not timings | 488 subscriptions, 10 minutes, 3,123 batches: 880,780 bytes in zstd; `rest.jsonl` 2.1 MB (the two chains) | 16.21, 16.83, 17.96 before (sizes do not depend on it) | `tools/capture.py rh-20261127 --symbols SPY,SPX --expirations 1 --strikes 40 --minutes 10`, production API |
| 2026-11-28 | WP-1.1 | PRF-05: a surface fit in the process pool, warm from the fit before, as the service runs it. Synthetic market, default settings, 2 workers | SPY (1,462 pairs, 28 slices, 17 with their own SVI): in the worker median 104, p95 109, max 110 ms; there and back as the event loop sees it 106, 110, 112 ms. SPX (1,946 pairs, 41 slices, 19 with SVI): 150, 223, 225 ms; there and back 152, 226, 227 ms. AAPL (590 pairs, 21 slices, 9 with SVI): 78, 80, 82 ms; there and back 79, 81, 83 ms. The worker's CPU time equals its wall time to the millisecond: a fit is one core for that long. All three at once on the 2 workers, a round: 185, 188, 191 ms. 273 tasks, none failed | 1.55, 2.71, 14.70 before; 1.79, 2.54, 13.75 after. Idle by the 1-minute figure; the 15-minute one still holds a data job that had ended. An `rclone` copy of the owner ran beside it at about 12% of a core | `tools/timings.py surface`, 04:56 New York; the fake's clock is Friday 2026-11-20 from 10:00 New York. 20 fits on demand per underlying, after the sets had filled and the synthetic market was stopped |
| 2026-11-28 | WP-1.1 | The same run: a fit per slice, and cold | Warm, the fit's time over its slices (median): 3.7 ms a slice for all three; over the slices with their own SVI 6.1 (SPY), 7.9 (SPX), 8.6 ms (AAPL). A whole surface cold, without the fit before (median of 3): SPY 178, SPX 283, AAPL 105 ms. One slice alone in a task of its own, cold, with its own SVI: SPY median 16.8, p95 19.1, max 19.1 ms (17 slices); SPX 17.0, 80.4, 80.4 ms (19); AAPL 13.1, 14.8, 14.8 ms (9). Without an SVI of its own: 2.0 ms, max 2.3 ms (45 slices) | as above | Same run. The cold fits take the inputs as the service gathers them |
| 2026-11-28 | WP-1.1 | The same run: the event loop (PRF-05: no block above 50 ms) | Steps of the service, per fit (median, p95, max). Quotes to inputs: SPY 1.6, 2.8, 3.2 ms; SPX 1.9, 2.2, 2.4; AAPL 0.9, 1.5, 1.8. The set chosen again: SPY 0.9, 1.2, 1.2 ms; SPX 1.1, 1.4, 1.5; AAPL 0.7, 0.9, 1.1. The feed's grant of the 3,998 pairs: 3.4, 5.0, 5.6 ms. The longest step since the watch began, the filling included: SPY 3.4, SPX 5.0, AAPL 2.6 ms. Lag of a 10 ms timer: with nothing running 0.24, 1.21, 2.27 ms; while the fits of one underlying ran, at most 0.25, 1.35, 2.12 ms (SPX); with all three at once 0.25, 1.42, 4.36 ms. The event loop's thread used 2.7 to 4.7% of a core while fits were asked for back to back. **The longest block seen is 5.6 ms** | as above | Same run. The steps are timed inside the service, the grant by 20 calls of `s3t.subs.budget.allocate`, the lag by a timer task. The synthetic market was stopped for all of it: its ticks are the fake's own work on the loop |
| 2026-11-28 | WP-1.1 | The same run: a worker process | What a worker of the backend imports for its first task, in a fresh process: median 455, p95 457, max 457 ms, all of it CPU; peak memory after it 61 MB (max 61). The first fit of the run with the start of a worker of this tool: 849 ms there and back (one observation). Peak memory of a worker of this tool after the fits: 90 MB; such a worker also imports the tool (requirements, section 3) | as above | Same run: 5 fresh processes for the import; `getrusage` in the workers at the end |
| 2026-11-28 | WP-1.1 | A one-off on real quotes: the 0DTE slice of SPXW fitted at every minute of two regular sessions (2026-11-17 and 2026-11-20), each fit warm from the minute before, in one process. Counts and timings; no price was kept | Per day 390 fits on every quoted strike (270 and 507 strikes): all 780 `ok` with their own SVI, the forward from parity in all; the butterfly check failed in 1; 9 quotes dropped as outliers in all. On the strikes the service would subscribe (46 and 59 at the median): 778 `ok`, and the first minute of each day `sparse`, which has no wings yet by the service's steps; the butterfly check failed in 6. **A fit took median 85 to 88, p95 104 to 110, max 186 ms, the same on both sets of strikes**: 5 times a synthetic slice. The solver stopped at its limit of 200 evaluations in 687 of 779 calls (counted on 2026-11-17) | 2.9, 3.3, 6.5 and 2.1, 2.8, 5.8 before; 2.4, 2.9, 5.9 and 2.5, 2.7, 5.1 after. Not idle: the 1-minute figure is 2 to 3 | A script outside the repository over the owner's streamer dumps, read as a stream: the latest quote of each contract at each minute from 09:31 to 16:00 New York, `fit_surface` on the one slice, the spot from the dump's minute candle, a flat rate of 4% |

WP-0.1 has no performance requirement and took no timings.

WP-0.2 has no performance requirement and took no timings. The throughput of IV inversion is
measured with the stream reader at WP-0.9 (plan, section 7). The pricing agreement figures of
WP-0.2 are accuracy results, not timings. They are in the tests:
`tests/pricing/test_reference.py` and `test_greeks_mpmath.py`.

WP-0.3 has no performance requirement and took no timings. Its live check of the REST client
was a capability check, not a timing. The results are in note B, addendum "WP-0.3". The
machine was not idle at the time. The load averages (1, 5 and 15 minutes) were 76.74, 61.66
and 36.12 just before the run, and 66.88, 70.82 and 52.68 just after it. No timing would have
been valid. The fake-broker tests run on a fake clock and measure nothing.

WP-0.5 has no performance requirement and took no timings. The reading of the chains and the
ingest of events were not timed. The counts of quotes it used are in note B, addendum
"WP-0.5" (counts, not timings). The two live checks of the instrument model were capability
checks of 13 requests each: 2026-11-23, 18:03 New York, with load averages 1.85, 1.93 and 8.45
before and 1.74, 1.90 and 8.32 after; and 2026-11-24, 05:48 New York, with 2.12, 1.62 and 1.63
before and 2.25, 1.67 and 1.64 after. The market tests run on a virtual clock and measure
nothing.

WP-0.6 has no performance requirement and took no timings. Importing `exchange-calendars` and
building the calendar took more than a second while the machine was loaded (load averages above 14).
That is not a measurement. The time and the memory are measured at WP-0.9 on an idle machine. The
live check of the rate download was a capability check of one run: six requests to FRED, all
answered, 2026-11-24, 17:40 New York, with load averages 6.40, 7.43 and 23.65 before and 5.72, 7.25
and 23.40 after. It took 10.3 s, of which 5 s are the pauses between the requests; the network and
the load decide the rest. Two requests of `curl` a few minutes later failed (note B, addendum
"WP-0.6"). The files had 35,714 to 36,756 bytes. The refresh tests run on a virtual
clock and measure nothing.

Since WP-0.6 the fake broker has holidays, and the larger of the two size runs above gives slightly
other bytes: 5,241,114 for the lines (5,232,559 before) and 529,325 as built (528,330 before). The
ratios to one decimal are the same. The smaller run gives the same bytes as before (both run
again 2026-11-25).

WP-0.7 has no performance requirement and took no timings. SEC-06 (M) is recorded here for macOS:
the backend ran as an ordinary process of an ordinary user (not root, no container) on macOS 15.7.4,
2026-11-25, 03:28 to 03:29 local time, on the fake broker. It listened on the one configured address
(`lsof`), answered the login, the health route and the page, refused a second instance, had no
connection to another machine after a minute, and ended on SIGTERM with exit status 0 and its state
file closed. The resident memory after that minute was 200 MB (`ps`): one look, not a measurement.
Load averages (1, 5 and 15 minutes): 18.13, 19.13 and 21.46 before, 16.85, 18.60 and 21.08 after, so
nothing here is a timing. **Linux was not run.** The same run is a test
(`tests/api/test_server.py`), with a WebSocket open at the end. The topic tests run on a virtual clock
and measure nothing. The browser suite (`make e2e`) passed in Chromium, Firefox and WebKit the same
night under the same load.

WP-0.8 has no performance requirement and took no timings. The size of the built frontend is the row
above. The browser suite took 37 s to 52 s for its 27 and, at the end, 30 tests, with load averages
from 11.8 to 26.4: run times of a loaded machine, not measurements. Through the suite
the app's reconnect after a restart of the backend with 3 s away took 5.6 s (Chromium) to 8.0 s
(Firefox, WebKit) of test time, under the same load; not a measurement either. The time from a
quote in the broker to the screen was not timed. On the fake broker it should be at most a tick of
1 s plus a flush of 0.5 s: computed from the two periods, not seen.

WP-0.4 has no performance requirement. The three live rows above are single observations
from the live check, not a performance run. The stream and feed tests run on a virtual clock
and measure nothing. The throughput of the reader is measured live at WP-0.9 (plan,
section 7). The two size rows are on synthetic data: a real session can compress
differently. They show that a frame every 3 s costs about a third of the compression. The
recorder was changed to a block flush on 2026-11-24 for that reason; "as built" in the rows
of 2026-11-23 means the frames of that day.

WP-0.9 is the package of the live measurements. Its rehearsal of 2026-11-27 ran on the half day after
Thanksgiving, with the machine loaded by other work (the rows above say how much). Its stream figures
are counts of a thin day, and its timings are not measurements. The day run of PRF-09 is on Monday
2026-11-30 with the machine idle (requirements, section 3, "The live measurements"). Also seen on
2026-11-27:

- On the half day the options of SPY and SPXW were quoted until 13:15 New York. From 13:16 almost no
  option quote came. Trades and the broker's Greeks went on, so no stall was declared.
- The IV of the stand-in failed for 35% of the contracts: 851,303 below the intrinsic value, 32,625
  above the cap, 359,644 not identifiable, 473,199 invalid (an input that is not finite, most likely a missing bid or
  ask; not looked at), of 4,864,627. Its
  forward is the spot without dividends, so this says nothing of R1's IV; the cost is the same for a
  failed contract.
- The day run writes only counts, times and states, into the git-ignored data directory.

QA-08 is closed by review on 2026-11-27: every timing row since WP-0.3 records the load average, and
each package that took no timing says so.

WP-1.1, part 1 (2026-11-27) took no timings. PRF-05, the surface fit in the process pool and the
longest block of the event loop, is measured in part 2 (`tools/timings.py surface`). The accuracy
results of part 1 are in the tests, not here: the fit recovers a known smile and
forward (`tests/pricing/test_surface.py`), and the density integrates to one within 1e-8 over 30
at-the-money deviations.

WP-1.1, part 2 (2026-11-28) measured PRF-05: the four rows above. What they are and are not:

- **PRF-05 is met on the synthetic market.** The fit runs in a worker, and the longest block of
  the event loop was 5.6 ms against the 50 ms allowed, with the sets of three underlyings held.
- **The 30 ms per SVI slice** of the architecture note, from `volcalc`'s documents, is now measured
  here. On the synthetic market a slice alone and cold takes 13 to 17 ms at the median, and one
  slice of 45 took 80 ms; inside a warm surface a slice with its own SVI costs 6 to 9 ms. A real
  slice took 85 ms (below).
- **Headroom on the synthetic market, computed from the measured CPU time.** One fit every 30 s
  per underlying is 0.3 to 0.5% of one core for SPY or SPX, on a worker's core and not on the
  event loop's.
- **The synthetic chains are smaller than the live ones.** SPY's set is 1,462 pairs here and about
  2,300 live (computed, not measured). The steps on the event loop grow with the pairs; at the
  measured 3.2 ms for 1,462 pairs they stay far below 50 ms (computed).
- **The synthetic smiles are clean, and a real slice costs more.** On real SPXW 0DTE quotes a fit
  of the one slice took 85 to 88 ms at the median, 5 times a synthetic one (the last row: a
  one-off, on a machine that was not idle). The solver ran to its limit of 200 evaluations in
  most calls, so the time is that of the limit and does not grow with the strikes.
- **A whole real surface was not timed.** If each slice with its own SVI took those 85 ms, a
  surface with 45 such slices would take about 4 s a fit (computed; 45 is an assumed count for
  SPX within 90 days, not counted on a live chain), in a worker. That is 13% of one worker's core
  at a fit every 30 s. PRF-05 does not depend on it: nothing of the fit runs on the event loop.
- **Not in these figures:** the work of the live stream reader, which runs on the same event loop
  (the WP-0.9 rows), and the lag while a set fills, when the fake's own ticks load the loop.
- **The filling, on the fake, not a measurement of the product:** with the three underlyings
  watched at once a first fit came 7 to 17 s after the watch and the sets had filled after 37 to
  40 s. The manager sends 150 subscriptions a second, so 3,998 pairs take 27 s (computed).
- A first run of the tool on 2026-11-27 was made under load (load average 16) to see that it works.
  Its figures are in no document.
