import { defineConfig, devices } from '@playwright/test'

// The browser suite (implementation plan 3.1 and 3.5): Chromium, Firefox and WebKit against
// the built app. Until the backend serves the app (WP-0.7) it is served by `vite preview` on
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
    command: `npx vite preview --host 127.0.0.1 --port ${port} --strictPort`,
    url: baseURL,
    reuseExistingServer: false,
    timeout: 60_000,
  },
  projects: [
    { name: 'chromium', use: { ...devices['Desktop Chrome'], viewport } },
    { name: 'firefox', use: { ...devices['Desktop Firefox'], viewport } },
    { name: 'webkit', use: { ...devices['Desktop Safari'], viewport } },
  ],
})
