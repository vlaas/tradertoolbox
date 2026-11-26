import { createServer } from 'node:http'
import type { AddressInfo } from 'node:net'
import { expect, type Page, test } from '@playwright/test'
import { PASSWORD } from './app.ts'

// The backend in front of the three browser engines (SEC-01, SEC-02, SEC-07, API-03, API-04). What
// a browser does by itself can only be seen here: that it keeps the cookie from scripts, sends it
// with the WebSocket, states its origin, and is allowed to connect by the content policy. Here the
// page speaks the JSON form by hand, beside the app; the app's own connection is in live.spec.ts.

const password = PASSWORD

async function login(page: Page, given: string): Promise<number> {
  return page.evaluate(async (text) => {
    const answer = await fetch('/api/login', {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ password: text }),
    })
    return answer.status
  }, given)
}

// Opens the WebSocket, subscribes, and collects messages until `count` of the subscriptions' came.
async function listen(
  page: Page,
  topics: Record<string, unknown>[],
  count: number,
): Promise<Record<string, any>[] | 'refused'> {
  return page.evaluate(
    ({ topics, count }) =>
      new Promise<Record<string, any>[] | 'refused'>((resolve, reject) => {
        const socket = new WebSocket(`ws://${location.host}/api/ws`, ['s3t.v1.json'])
        const messages: Record<string, any>[] = []
        let open = false
        socket.onopen = () => {
          open = true
          topics.forEach((topic, i) => {
            socket.send(JSON.stringify({ subscribe: { id: i + 1, ...topic } }))
          })
        }
        socket.onmessage = (event) => {
          const message = JSON.parse(event.data)
          if (message.id) messages.push(message)
          if (messages.length >= count) {
            socket.close()
            resolve(messages)
          }
        }
        socket.onclose = () => (open ? undefined : resolve('refused'))
        setTimeout(() => reject(new Error(`only ${messages.length} messages`)), 15_000)
      }),
    { topics, count },
  )
}

test('the app comes from the backend, and its data needs a login', async ({ page }) => {
  await page.goto('/')
  await expect(page.getByRole('heading', { name: 'Self-3-Trader' })).toBeVisible()
  const status = (path: string) => page.evaluate(async (p) => (await fetch(p)).status, path)
  expect(await status('/api/health')).toBe(401)
  expect(await listen(page, [{ status: {} }], 1)).toBe('refused')

  expect(await login(page, password)).toBe(200)
  expect(await status('/api/health')).toBe(200)
  // The cookie is there, and no script can read it.
  expect(await page.evaluate(() => document.cookie)).toBe('')
  const [cookie] = await page.context().cookies(`${page.url()}api/health`)
  expect(cookie).toMatchObject({ httpOnly: true, sameSite: 'Strict', path: '/api' })

  await page.reload()
  expect(await status('/api/session')).toBe(200)
  const out = await page.evaluate(async () => {
    return (await fetch('/api/logout', { method: 'POST' })).status
  })
  expect(out).toBe(204)
  expect(await status('/api/session')).toBe(401)
  expect(await listen(page, [{ status: {} }], 1)).toBe('refused')
})

test('snapshots, then changes, over the WebSocket', async ({ page }) => {
  await page.goto('/')
  expect(await login(page, password)).toBe(200)
  const messages = await listen(page, [{ status: {} }, { quotes: { symbol: ['SPY', 'SPX'] } }], 8)
  if (messages === 'refused') throw new Error('refused')

  const status = messages.filter((m) => m.id === 1)
  expect(status[0].snapshot).toBe(true)
  expect(status[0].status.source.mark).toBe('SYNTHETIC DATA')
  expect(status[0].status.account).toBe('main')

  const quotes = messages.filter((m) => m.id === 2)
  expect(quotes[0].snapshot).toBe(true)
  expect(quotes[0].quotes.rows.symbol).toEqual(['SPY', 'SPX'])
  const changes = quotes.slice(1)
  expect(changes.every((m) => !m.snapshot)).toBe(true)
  const bids = changes.flatMap((m) => {
    const at = (m.quotes.rows?.symbol ?? []).indexOf('SPY')
    return at < 0 ? [] : [m.quotes.rows.bid[at]]
  })
  expect(bids.length).toBeGreaterThan(0)
  expect(bids.every((bid) => bid > 0)).toBe(true)

  // A page that comes back gets snapshots again, now with the values in them.
  const again = await listen(page, [{ quotes: { symbol: ['SPY'] } }], 1)
  if (again === 'refused') throw new Error('refused')
  expect(again[0].snapshot).toBe(true)
})

test('a page of another origin is kept out', async ({ page, baseURL }) => {
  // Another port of the same host is the same site for the cookie, so only the origin tells.
  await page.goto('/')
  expect(await login(page, password)).toBe(200)
  const base = baseURL ?? ''
  const other = createServer((_, answer) => {
    answer.setHeader('content-type', 'text/html')
    answer.end('<!doctype html><title>another</title>')
  })
  await new Promise<void>((resolve) => other.listen(0, '127.0.0.1', resolve))
  try {
    await page.goto(`http://127.0.0.1:${(other.address() as AddressInfo).port}/`)
    const socket = await page.evaluate(
      (url) =>
        new Promise<string>((resolve) => {
          const ws = new WebSocket(url, ['s3t.v1.json'])
          ws.onopen = () => resolve('open')
          ws.onclose = () => resolve('refused')
        }),
      `${base.replace('http', 'ws')}/api/ws`,
    )
    expect(socket).toBe('refused')
    // A form of that page can send this without asking anybody, and the cookie goes with it.
    await page.evaluate(async (url) => {
      const options: RequestInit = { method: 'POST', credentials: 'include', mode: 'no-cors' }
      await fetch(url, options).catch(() => undefined)
    }, `${base}/api/logout`)
  } finally {
    other.close()
  }
  await page.goto('/')
  expect(await page.evaluate(async () => (await fetch('/api/session')).status)).toBe(200)
})
