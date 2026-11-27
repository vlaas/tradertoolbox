import { AxeBuilder } from '@axe-core/playwright'
import type { Page } from '@playwright/test'
import { expect, logIn, quoteRow, test } from './app.ts'

// The accessibility checks of plan 3.5, as the backtester viewer has them: WCAG 2 A and AA, with a
// contrast of 4.5:1 for text, in both themes, on every screen and state of the shell (decided
// 2026-11-27, owner, WP-0.8 review).

async function violations(page: Page): Promise<string[]> {
  const { violations } = await new AxeBuilder({ page }).withTags(['wcag2a', 'wcag2aa']).analyze()
  return violations.map(
    (v) =>
      `${v.id} (${v.impact}): ${v.nodes
        .slice(0, 3)
        .map((n) => n.target.join(' '))
        .join(' | ')}`,
  )
}

for (const colorScheme of ['dark', 'light'] as const) {
  test.describe(`the ${colorScheme} theme`, () => {
    test.use({ colorScheme })

    test('the login screen, with an error', async ({ page }) => {
      await page.goto('/')
      await expect(page.getByLabel('Password')).toBeVisible()
      await page.getByLabel('Password').fill('not the password')
      await page.getByRole('button', { name: 'Log in' }).click()
      await expect(page.getByTestId('login-error')).toBeVisible()
      expect(await violations(page)).toEqual([])
    })

    test('the status screen, and the explanation of the status bar', async ({ page }) => {
      await logIn(page)
      await expect(quoteRow(page, 'SPY')).toHaveAttribute('data-state', 'live')
      await expect(page.getByTestId('rates-source')).toBeVisible()
      expect(await violations(page)).toEqual([])
      await page.getByRole('button', { name: 'What the status bar says' }).click()
      await expect(page.getByText('Point at a part of the bar')).toBeVisible()
      expect(await violations(page)).toEqual([])
    })

    test('the status screen while the connection is lost', async ({ page, control }) => {
      test.setTimeout(60_000)
      await logIn(page)
      await expect(quoteRow(page, 'SPY')).toHaveAttribute('data-state', 'live')
      await control.restart(4)
      await expect(quoteRow(page, 'SPY')).toHaveClass(/stale/)
      await expect(page.getByTestId('connection-lost')).toBeVisible()
      // Soft: the test waits for the backend to come back either way, for the next test.
      expect.soft(await violations(page)).toEqual([])
      await expect(page.getByTestId('connection-lost')).toHaveCount(0, { timeout: 30_000 })
    })
  })
}
