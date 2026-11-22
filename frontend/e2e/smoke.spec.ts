import { expect, test } from '@playwright/test'

// The skeleton loads in each engine, and the generated Protocol Buffers code runs there
// (a binary and a JSON round trip of WireCheck, done by the page itself).
test('the app loads and the wire round trip passes', async ({ page }) => {
  const errors: string[] = []
  page.on('pageerror', (e) => errors.push(e.message))
  page.on('console', (m) => {
    if (m.type() === 'error') errors.push(m.text())
  })

  await page.goto('/')
  await expect(page).toHaveTitle('Self-3-Trader')
  await expect(page.getByRole('heading', { name: 'Self-3-Trader' })).toBeVisible()
  await expect(page.getByTestId('wire')).toHaveText('wire ok')
  expect(errors).toEqual([])
})
