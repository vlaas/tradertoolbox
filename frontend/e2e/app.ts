import { test as base, expect, type Page } from '@playwright/test'

// What the tests of the app share: the password, a login through the screen, the errors of a
// page, and the test control of this engine's backend (backend/tools/e2e_backend.py).

export const PASSWORD = 'e2e-password-of-the-browser-suite'

/** The control of the backend of this test's engine: on the port after the backend's. */
export class Control {
  private readonly url: string

  constructor(baseURL: string) {
    const backend = new URL(baseURL)
    this.url = `http://${backend.hostname}:${Number(backend.port) + 1}`
  }

  private async send(method: string, path: string): Promise<void> {
    const answer = await fetch(`${this.url}${path}`, { method })
    if (answer.status >= 300) throw new Error(`control ${method} ${path}: ${answer.status}`)
  }

  /** The backend stops (a browser gets the close 1012), stays away, and starts again. */
  restart(downS: number): Promise<void> {
    return this.send('POST', `/restart?down=${downS}`)
  }

  /** The fake broker's next tick sends this bid and ask. */
  quote(symbol: string, bid: number, ask: number): Promise<void> {
    return this.send('POST', `/quote?symbol=${symbol}&bid=${bid}&ask=${ask}`)
  }

  release(symbol: string): Promise<void> {
    return this.send('DELETE', `/quote?symbol=${symbol}`)
  }

  /** The fake broker closes its stream sessions. */
  dropStream(): Promise<void> {
    return this.send('POST', '/drop-stream')
  }
}

export const test = base.extend<{ control: Control; errors: string[] }>({
  control: async ({ baseURL }, use) => {
    await use(new Control(baseURL ?? ''))
  },
  // The page's own errors. A browser reports an answer of 401 to a request as an error of the
  // console; the app asks for its session that way on purpose, so those are left out.
  errors: async ({ page }, use) => {
    const errors: string[] = []
    page.on('pageerror', (e) => errors.push(e.message))
    page.on('console', (m) => {
      if (m.type() === 'error' && !/401|Unauthorized/.test(m.text())) errors.push(m.text())
    })
    await use(errors)
  },
})

export { expect }

/** Logs in through the login screen, at `address`, and waits for the screen to show. */
export async function logIn(page: Page, address = '/'): Promise<void> {
  await page.goto(address)
  await page.getByLabel('Password').fill(PASSWORD)
  await page.getByRole('button', { name: 'Log in' }).click()
  await expect(page.getByRole('main', { name: 'Status' })).toBeVisible()
}

/** The row of a symbol in the status screen's quote list. */
export function quoteRow(page: Page, symbol: string) {
  return page.getByTestId(`quote-${symbol}`)
}
