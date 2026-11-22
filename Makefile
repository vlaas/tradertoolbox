# Self-3-Trader: development commands (implementation plan, section 1).
#
#   make setup     install the locked dependencies of backend/ and frontend/
#   make gen       regenerate Protocol Buffers code, the OpenAPI schema and types, the wire fixture
#   make check     lint, type checks, tests, the generated-code check and the licence check
#   make e2e       build the frontend and run the browser suite in Chromium, Firefox and WebKit
#   make licences  the licence check alone
#   make audit     known vulnerabilities in both lock files (needs the network)
#
# The recipes stay one line each (macOS ships GNU make 3.81); logic lives in the scripts.

BACKEND := cd backend &&
FRONTEND := cd frontend &&

# The licence policy (QA-07: allow-list and named exceptions) lives in backend/tools/licences.py.

.PHONY: setup gen check lint types test gen-check licences audit e2e

setup:
	$(BACKEND) uv sync --locked
	$(FRONTEND) npm ci --ignore-scripts --no-audit --no-fund

gen:
	$(BACKEND) uv run python tools/generate.py

check: lint types test gen-check licences

lint:
	$(BACKEND) uv run ruff check .
	$(BACKEND) uv run ruff format --check .

types:
	$(BACKEND) uv run pyright
	$(FRONTEND) npm run --silent check

test:
	$(BACKEND) uv run pytest
	$(FRONTEND) npx vitest run

gen-check:
	$(BACKEND) uv run python tools/generate.py --check

licences:
	$(BACKEND) uv run python tools/licences.py

audit:
	$(BACKEND) uv run python tools/audit.py

e2e:
	$(FRONTEND) npm run --silent build
	$(FRONTEND) npx playwright test
