import { expect, logIn, PASSWORD, test } from './app.ts'

// The shell in each engine (UI-01, UI-05, UI-08 base, GEN-03, GEN-11, SEC-01): the login screen,
// the screen of the address, the marks, the status bar, the logout. Against this engine's backend.

test('the login screen, then the status screen with its marks, and no page scroll', async ({
  page,
  errors,
}) => {
  await page.goto('/#/status')
  await expect(page.getByRole('heading', { name: 'Self-3-Trader' })).toBeVisible()
  // One wrong password: the hold after five is for the backend of this engine.
  await page.getByLabel('Password').fill('not the password')
  await page.getByRole('button', { name: 'Log in' }).click()
  await expect(page.getByTestId('login-error')).toHaveText('The password is wrong.')
  await page.getByLabel('Password').fill(PASSWORD)
  await page.getByRole('button', { name: 'Log in' }).click()

  await expect(page.getByRole('main', { name: 'Status' })).toBeVisible()
  await expect(page).toHaveURL(/#\/status$/)
  await expect(page).toHaveTitle('Status · Self-3-Trader')
  await expect(page.getByTestId('source-mark-top')).toHaveText('SYNTHETIC DATA')
  await expect(page.getByTestId('source-mark-status')).toHaveText('SYNTHETIC DATA')
  await expect(page.getByTestId('account')).toHaveText('main')
  await expect(page.getByTestId('status-connection')).toHaveText('Backend connected')
  await expect(page.getByTestId('status-stream')).toContainText('Stream connected')
  await expect(page.getByTestId('status-subscriptions')).toHaveText(/^Subscriptions \d+ of [\d,]+/)
  await expect(page.getByTestId('status-rest')).toContainText('REST: token valid')
  await expect(page.getByTestId('status-rest')).toContainText('limit 2/s burst 4')
  await expect(page.getByTestId('connection-lost')).toHaveCount(0)
  await expect(page.getByTestId('rates-source')).toHaveText('the flat rate, 4.00%')
  await expect(page.getByTestId('calendar-source')).toContainText('exchange-calendars')

  // UI-08 base: at 1440 by 900 the window does not scroll.
  const scrolls = await page.evaluate(() => {
    const root = document.documentElement
    return [root.scrollHeight > innerHeight, root.scrollWidth > innerWidth]
  })
  expect(scrolls).toEqual([false, false])

  // UI-09 base: an explanation opens above everything and inside the window.
  await page.getByRole('button', { name: 'What the status bar says' }).click()
  const box = page.getByText("this page's connection").locator('xpath=ancestor::*[@popover]')
  await expect(box).toBeVisible()
  const at = await box.boundingBox()
  expect(at).not.toBeNull()
  if (at) {
    expect(at.x).toBeGreaterThanOrEqual(0)
    expect(at.y).toBeGreaterThanOrEqual(0)
    expect(at.x + at.width).toBeLessThanOrEqual(1440)
    expect(at.y + at.height).toBeLessThanOrEqual(900)
  }
  await page.keyboard.press('Escape')
  await expect(box).toBeHidden()
  expect(errors).toEqual([])
})

test('an address of no screen is replaced, and a reload keeps the session and the screen', async ({
  page,
}) => {
  await logIn(page, '/#/nosuch')
  await expect(page).toHaveURL(/#\/status$/)
  await page.reload()
  await expect(page.getByRole('main', { name: 'Status' })).toBeVisible()
  await expect(page.getByLabel('Password')).toHaveCount(0)
})

test('a logout ends the session here and in the other page of the browser', async ({ page }) => {
  await logIn(page)
  const other = await page.context().newPage()
  await other.goto('/')
  await expect(other.getByRole('main', { name: 'Status' })).toBeVisible() // the same session
  await expect(other.getByTestId('status-connection')).toHaveText('Backend connected')

  await other.getByRole('button', { name: 'Log out' }).click()
  await expect(other.getByTestId('login-reason')).toHaveText('You have logged out.')
  // The first page is told by its connection, and says why.
  await expect(page.getByTestId('login-reason')).toHaveText(
    'Logged out: the login session has ended.',
  )
  await page.reload()
  await expect(page.getByLabel('Password')).toBeVisible()
})
