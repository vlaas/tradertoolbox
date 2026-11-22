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

No measurements yet. WP-0.1 has no performance requirement and took no timings.

WP-0.2 has no performance requirement and took no timings. The throughput of IV inversion is
measured with the stream reader at WP-0.9 (plan, section 7). The pricing agreement figures of
WP-0.2 are accuracy results, not timings. They are in the tests:
`tests/pricing/test_reference.py` and `test_greeks_mpmath.py`.

WP-0.3 has no performance requirement and took no timings. Its live check of the REST client
was a capability check, not a timing. The results are in note B, addendum "WP-0.3". The
machine was not idle at the time. The load averages (1, 5 and 15 minutes) were 76.74, 61.66
and 36.12 just before the run, and 66.88, 70.82 and 52.68 just after it. No timing would have
been valid. The fake-broker tests run on a fake clock and measure nothing.
