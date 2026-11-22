import { defineConfig } from '@playwright/test'

export default defineConfig({
  testDir: 'checks',
  timeout: 120_000,
  workers: 1,
  reporter: 'list',
  outputDir: 'test-results',
  use: { baseURL: 'http://localhost:5177', browserName: 'chromium' },
  webServer: { command: 'npm run dev', url: 'http://localhost:5177', reuseExistingServer: true, timeout: 60_000 },
  projects: [
    { name: 'desktop', testIgnore: /touch/, use: { viewport: { width: 1440, height: 900 } } },
    { name: 'tablet', testMatch: /touch/, use: { viewport: { width: 1180, height: 820 }, hasTouch: true } },
    { name: 'firefox', testIgnore: /touch/, use: { browserName: 'firefox', viewport: { width: 1440, height: 900 } } },
    { name: 'webkit', testIgnore: /touch/, use: { browserName: 'webkit', viewport: { width: 1440, height: 900 } } },
  ],
})
