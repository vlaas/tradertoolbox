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
