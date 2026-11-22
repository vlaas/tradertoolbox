# Screen mock-ups

Source of the two mock-up screens: the trade workbench (`project/Main.dc.html`) and the chart
window (`project/Chart.dc.html`), with the canvas index `project/canvas.json`. The decisions
taken from them are in `docs/product_definition.md`, section 6.3.

They are published as a Claude Design artifact:
https://claude.ai/artifact/4WMzVB4aDHEP94qirwLdMg (private to its owner). This folder holds the
state of the fifth draft, 2026-11-22.

- All figures are sample data from an invented volatility smile. Nothing here is market data.
- Each file is one self-contained screen: markup inside `<x-dc>`, logic in the script block
  below it. The format rules are in the artifact's own reference files.
- To publish a change, send only the changed files to the artifact, with this folder as the
  root so that they are served as `project/...`.
- `node check_logic.js project/Main.dc.html` runs a screen's logic outside the browser and
  reports template values that are missing. It cannot see layout.
- The two screens do not share state on the canvas, so the chart always shows the starting
  iron condor.
