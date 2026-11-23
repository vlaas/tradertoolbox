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

WP-0.4 has no performance requirement. The three live rows above are single observations
from the live check, not a performance run. The stream and feed tests run on a virtual clock
and measure nothing. The throughput of the reader is measured live at WP-0.9 (plan,
section 7). The two size rows are on synthetic data: a real session can compress
differently. They show that a frame every 3 s costs about a third of the compression. The
recorder was changed to a block flush on 2026-11-24 for that reason; "as built" in the rows
of 2026-11-23 means the frames of that day.
