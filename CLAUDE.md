# Self-3-Trader (repo `tradertoolbox`)

A self-hosted, single-user options-analysis application: a Python backend that holds the
broker connection and all numerics, and a Svelte web application. The repository skeleton
(`backend/`, `frontend/`, `proto/`, `config/`) is in place since WP-0.1; product code starts
with WP-0.2. Besides it there are a throw-away chart prototype and the screen mock-ups.

## Documents

Read only what the task needs. When two documents disagree, the one higher in this list
holds.

| Document | Holds |
|---|---|
| `docs/implementation_plan.md` | Work packages, their order, the testing plan, the status table. Section 9 says how to start a package |
| `docs/technical_requirements.md` | Technology selection and the numbered requirements (`PRC-08`, `WB-12`, ...) |
| `docs/product_definition.md` | Purpose, principles, data tiers, releases, screen layout (6.3), probability stages (6.4), decisions with dates |
| `docs/research/E_architecture.md` | The architecture decisions and their reasons |
| `docs/research/B_broker_api_capability.md` | Measured facts about the Tastytrade API |
| `docs/research/C_charting.md` | Charting choice; prototype results (10) and the time model (11) |
| `docs/research/E_code_audit.md` | What can be reused from the owner's other repositories |
| `docs/research/reports/` | Two outside research reports (feature landscape, probability methods). Their claims are not all verified |
| `docs/mockups/` | Source of the screen mock-ups, with its own readme |

## Rules that always hold

- **Read-only towards the broker.** No call that places, changes or cancels an order, and no
  other call that changes account state. The margin dry-run is not to be tried yet.
- **Live broker calls** are allowed for stated checks only: read-only, rate-limited, a few
  subscriptions at a time. Prefer the fake broker and synthetic data for everything else.
- **Secrets.** Credentials are in a git-ignored `.env` (variable names are listed in
  `prototypes/chart/README.md`). Never print, log, store or quote their values, and do not
  read the file to look at it.
- **The account number never appears** in output, logs, files or replies. Mask it.
- **No market data in the repository.** Downloads go to git-ignored directories.
- **No machine-specific values in documents or code:** addresses, host names and local paths
  are configuration.
- **The owner's other repositories** (`volcalc`, `tt`, `backtester`, `bt_dataset`,
  `streamer`, beside this one) are sources to read and port from. Never change them.

## How to work here

- Work by package from the implementation plan, to its definition of done.
- A decision is written into the product definition or the requirements with its date. What
  the owner has not confirmed is written as a proposal and listed in the reply.
- State what was verified and what was not. A result that was only computed, not seen, is
  reported as such.
- Timing measurements need an idle machine: the owner runs heavy data jobs on it. Record the
  load average with every timing.
- Offer a commit when a package or a document is complete; commit when the owner says so.
- Documents are written plainly: short sentences, requirement identifiers where they exist,
  no figure without saying whether it was measured or estimated.

## Commands

```
make setup      # uv sync --locked in backend/, npm ci --ignore-scripts in frontend/
make gen        # regenerate Protocol Buffers code, OpenAPI schema and types, the wire fixture
make check      # ruff, pyright, pytest, svelte-check, tsc, vitest, stale check, licence check
make e2e        # build the frontend; Playwright against the backend on the fake broker, 3 browsers
make licences   # the licence check alone (policy in backend/tools/licences.py)
make audit      # known vulnerabilities in both lock files; needs the network

# configuration: copy config/s3t.example.toml to config/s3t.toml (git-ignored)

# set the password of the app (asks for it), then run the backend; it serves frontend/dist.
# [broker] source = "fake" in the configuration runs it without the broker
cd backend && uv run python tools/set_password.py --config ../config/s3t.toml
cd backend && uv run python -m s3t --config ../config/s3t.toml

# live read-only check of the broker client: a stated check, 13 requests at 1 per second
cd backend && uv run python tools/broker_check.py --config ../config/s3t.toml

# live read-only check of the stream: a stated check, one session, 9 subscriptions, 90 seconds
cd backend && uv run python tools/stream_check.py --config ../config/s3t.toml

# record a session into <data_dir>/recordings/NAME (git-ignored); --fake needs no broker
cd backend && uv run python tools/capture.py NAME --fake --minutes 1 --config ../config/s3t.toml

# size of a recording's stream file, zstd against gzip, on the fake broker (keeps nothing)
cd backend && uv run python tools/recording_size.py

# copy the state file, also while the backend runs (writes to paths.backup_dir). The running
# backend makes a copy a day by itself
cd backend && uv run python tools/state_backup.py --config ../config/s3t.toml

# live check of the rate download from FRED: a stated check, 6 keyless requests, no broker
cd backend && uv run python tools/rates_check.py --config ../config/s3t.toml

# chart prototype (throw-away): see prototypes/chart/README.md
cd prototypes/chart && npm install --ignore-scripts && npx playwright test

# screen mock-ups: run a screen's logic outside the browser
cd docs/mockups && node check_logic.js project/Main.dc.html
```

Generated code is committed and owned by `backend/tools/generate.py`. The list of generated
trees is the root `.gitattributes`: `backend/src/s3t/wire/`, `frontend/src/gen/`,
`proto/fixtures/`. Never edit them by hand; change the `.proto` file or the API and run
`make gen`.

## Mock-ups

The mock-ups are published as a Claude Design artifact (link in `docs/mockups/README.md`). To
change them: edit the files in `docs/mockups/project/`, run the logic check, and publish only
the changed files in one call with `docs/mockups` as the root. Do not render or open the page
to verify unless the owner asks; say that the layout was not seen.
