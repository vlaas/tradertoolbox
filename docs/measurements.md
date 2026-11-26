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
- A figure that was estimated, not measured, does not belong here.

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
