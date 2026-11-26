import { defineConfig, devices } from '@playwright/test'

// The browser suite (implementation plan 3.1 and 3.5): Chromium, Firefox and WebKit against the
// built app, served by the real backend on the fake broker (backend/tools/e2e_backend.py) on the
// loopback address. Each engine has a backend of its own, on port P + 2i with its test control on
// the next port, and runs its tests one at a time: a test that restarts its backend breaks no test
// of another engine, and the hold after wrong passwords is per engine. S3T_E2E_PORT sets P.
const first = Number(process.env.S3T_E2E_PORT ?? 4178)
const viewport = { width: 1440, height: 900 }
const engines = [
  { name: 'chromium', device: devices['Desktop Chrome'] },
  { name: 'firefox', device: devices['Desktop Firefox'] },
  { name: 'webkit', device: devices['Desktop Safari'] },
]
const port = (i: number) => first + 2 * i

export default defineConfig({
  testDir: 'e2e',
  timeout: 30_000,
  expect: { timeout: 10_000 },
  workers: engines.length,
  retries: 0,
  reporter: 'list',
  use: { trace: 'retain-on-failure', screenshot: 'only-on-failure' },
  webServer: engines.map((_, i) => ({
    command: `uv run python tools/e2e_backend.py --port ${port(i)} --control-port ${port(i) + 1} --frontend ../frontend/dist`,
    cwd: '../backend',
    url: `http://127.0.0.1:${port(i)}`,
    reuseExistingServer: false,
    gracefulShutdown: { signal: 'SIGTERM' as const, timeout: 10_000 },
    timeout: 60_000,
  })),
  projects: engines.map((engine, i) => ({
    name: engine.name,
    workers: 1,
    use: { ...engine.device, viewport, baseURL: `http://127.0.0.1:${port(i)}` },
  })),
})
