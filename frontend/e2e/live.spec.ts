import { expect, logIn, quoteRow, test } from './app.ts'

// The two sentences of WP-0.8's "done when", in each engine: a quote changed in the fake broker
// appears in the browser; the loss of the connection is shown and recovered from. Then the loss of
// the broker's stream (BRK-06). The control of this engine's backend changes the quote, restarts
// the backend and drops the stream (backend/tools/e2e_backend.py).

test('a quote set in the fake broker appears in the browser, and moves again when released', async ({
  page,
  control,
}) => {
  await logIn(page)
  const spy = quoteRow(page, 'SPY')
  await expect(spy).toHaveAttribute('data-state', 'live')
  await control.quote('SPY', 111.11, 111.13)
  await expect(spy.locator('[data-col=bid]')).toHaveText('111.11')
  await expect(spy.locator('[data-col=ask]')).toHaveText('111.13')
  await expect(spy).toHaveAttribute('data-state', 'live')
  await control.release('SPY')
  await expect(spy.locator('[data-col=bid]')).not.toHaveText('111.11')
})

test('the loss of the connection is shown on the screen, and the app recovers', async ({
  page,
  control,
}) => {
  test.setTimeout(60_000)
  await logIn(page)
  const spy = quoteRow(page, 'SPY')
  await expect(spy).toHaveAttribute('data-state', 'live')
  await expect(page.getByTestId('connections-opened')).toHaveText('1')

  await control.restart(3)
  // UI-05: the loss on the screen, and the values marked stale until the connection returns.
  await expect(page.getByTestId('connection-lost')).toBeVisible()
  await expect(page.getByTestId('connection-lost')).toContainText('Connection lost')
  await expect(page.getByTestId('status-connection')).toContainText('Connection lost')
  await expect(spy).toHaveAttribute('data-state', 'stale')
  await expect(spy.locator('[data-col=state]')).toHaveText('stale')

  // Back: a fresh connection with fresh snapshots, on the backend that started again.
  await expect(page.getByTestId('connection-lost')).toHaveCount(0, { timeout: 30_000 })
  await expect(page.getByTestId('status-connection')).toHaveText('Backend connected')
  await expect(page.getByTestId('connections-opened')).toHaveText('2')
  await expect(spy).toHaveAttribute('data-state', 'live')
  await control.quote('SPY', 222.22, 222.24)
  await expect(spy.locator('[data-col=bid]')).toHaveText('222.22')
})

test('a drop of the broker stream is shown, with its gap', async ({ page, control }) => {
  await logIn(page)
  await expect(page.getByTestId('stream-phase')).toHaveText('connected')
  await expect(page.getByTestId('stream-reconnects')).toHaveText('0')
  await expect(page.getByTestId('stream-gap')).toHaveText('none')
  await control.dropStream()
  await expect(page.getByTestId('stream-reconnects')).toHaveText('1')
  await expect(page.getByTestId('stream-gap')).toHaveText(/ET, /)
  await expect(page.getByTestId('stream-phase')).toHaveText('connected')
  await expect(page.getByTestId('status-stream')).toContainText('last gap')
  await expect(quoteRow(page, 'SPY')).toHaveAttribute('data-state', 'live')
})

test('the JSON form for reading along carries the same app', async ({ page, control }) => {
  // API-04: `?wire=json` in the address; the page then speaks s3t.v1.json, and every frame its
  // socket receives is text. Without the switch the frames are binary. Frames are kept per socket:
  // the old page's socket may still deliver one while the next page loads.
  const sockets: Set<string>[] = []
  page.on('websocket', (socket) => {
    const kinds = new Set<string>()
    sockets.push(kinds)
    socket.on('framereceived', (frame) => kinds.add(typeof frame.payload))
  })
  await logIn(page, '/?wire=json#/status')
  await expect(quoteRow(page, 'SPY')).toHaveAttribute('data-state', 'live')
  await control.quote('SPY', 333.33, 333.35)
  await expect(quoteRow(page, 'SPY').locator('[data-col=bid]')).toHaveText('333.33')
  await expect(page.getByTestId('status-stream')).toContainText('Stream connected')
  expect(sockets.map((kinds) => [...kinds])).toEqual([['string']])

  await page.goto('/#/status')
  await expect(quoteRow(page, 'SPY')).toHaveAttribute('data-state', 'live')
  expect(sockets).toHaveLength(2)
  expect([...sockets[1]]).toEqual(['object'])
})
