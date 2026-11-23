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

WP-0.4, part 1, has no performance requirement. The two rows above are single observations
from the live check, not a performance run. The stream tests run on a virtual clock and
measure nothing. The throughput of the reader is measured live at WP-0.9 (plan, section 7).
