import { defineConfig, devices } from '@playwright/test'

// The browser suite (implementation plan 3.1 and 3.5): Chromium, Firefox and WebKit against
// the built app, served by the real backend on the fake broker (backend/tools/e2e_backend.py) on
// the loopback address. S3T_E2E_PORT overrides the port.
const port = Number(process.env.S3T_E2E_PORT ?? 4178)
const baseURL = `http://127.0.0.1:${port}`
const viewport = { width: 1440, height: 900 }

export default defineConfig({
  testDir: 'e2e',
  timeout: 30_000,
  expect: { timeout: 10_000 },
  fullyParallel: true,
  retries: 0,
  reporter: 'list',
  use: { baseURL, trace: 'retain-on-failure', screenshot: 'only-on-failure' },
  webServer: {
    command: `uv run python tools/e2e_backend.py --port ${port} --frontend ../frontend/dist`,
    cwd: '../backend',
    url: baseURL,
    reuseExistingServer: false,
    gracefulShutdown: { signal: 'SIGTERM', timeout: 10_000 },
    timeout: 60_000,
  },
  projects: [
    { name: 'chromium', use: { ...devices['Desktop Chrome'], viewport } },
    { name: 'firefox', use: { ...devices['Desktop Firefox'], viewport } },
    { name: 'webkit', use: { ...devices['Desktop Safari'], viewport } },
  ],
})
