import { expect, test } from '@playwright/test'
import { frames, log, open } from './util'

test('check 5: panes, and touch by emulation', async ({ page }) => {
  await open(page)
  const cdp = await page.context().newCDPSession(page)
  type P = { x: number; y: number }
  const send = (type: string, pts: P[]) => cdp.send('Input.dispatchTouchEvent', { type: type as any, touchPoints: pts.map((p, id) => ({ x: Math.round(p.x), y: Math.round(p.y), id, radiusX: 8, radiusY: 8, force: 1 })) })
  const lerp = (a: P, b: P, t: number) => ({ x: a.x + (b.x - a.x) * t, y: a.y + (b.y - a.y) * t })
  /** One or two fingers from `from` to `to`, in steps, 16 ms apart. */
  const swipe = async (from: P[], to: P[], steps = 12, hold = 0) => {
    await send('touchStart', from)
    if (hold) await page.waitForTimeout(hold)
    for (let i = 1; i <= steps; i++) { await send('touchMove', from.map((f, k) => lerp(f, to[k], i / steps))); await page.waitForTimeout(16) }
    await page.waitForTimeout(120) // rest before lifting, so the chart adds no kinetic scroll
    await send('touchEnd', [])
    await frames(page, 4)
  }
  const tap = async (p: P) => { await send('touchStart', [p]); await page.waitForTimeout(40); await send('touchEnd', []); await frames(page, 3) }
  const range = () => page.evaluate(() => { const r = (window as any).__proto.chart.timeScale().getVisibleLogicalRange(); return { from: r.from, to: r.to, width: r.to - r.from } })
  const info = await page.evaluate(() => {
    const p = (window as any).__proto
    return { panes: p.chart.panes().length, paneHeights: p.chart.panes().map((x: any) => x.getHeight()), touch: navigator.maxTouchPoints, barSpacing: p.chart.timeScale().options().barSpacing }
  })
  log('c5.panes', info)
  expect(info.panes).toBe(3)

  const box = (await page.getByTestId('chart').boundingBox())!
  const c = { x: box.x + box.width * 0.4, y: box.y + 200 }

  // pan with one finger
  const r0 = await range()
  await swipe([c], [{ x: c.x + 300, y: c.y }])
  const r1 = await range()
  log('c5.pan', { shiftedBars: +(r0.from - r1.from).toFixed(1), widthKept: Math.abs(r1.width - r0.width) < 0.5 })
  expect(r0.from - r1.from).toBeGreaterThan(5)

  // pinch out with two fingers
  await swipe([{ x: c.x - 60, y: c.y }, { x: c.x + 60, y: c.y }], [{ x: c.x - 240, y: c.y }, { x: c.x + 240, y: c.y }])
  const r2 = await range()
  log('c5.pinch', { barsBefore: +r1.width.toFixed(0), barsAfter: +r2.width.toFixed(0) })
  expect(r2.width).toBeLessThan(r1.width * 0.8)

  // place a horizontal line with a tap, and a trendline with two taps
  await page.locator('[data-tool="horizontal-line"]').tap()
  const hy = c.y + 60
  await tap({ x: c.x, y: hy })
  await page.locator('[data-tool="trend-line"]').tap()
  await tap({ x: c.x - 200, y: c.y + 150 }); await tap({ x: c.x + 150, y: c.y + 100 })
  const placed = await page.evaluate(() => (window as any).__proto.drawings().map((d: any) => ({ kind: d.kind, n: d.points.length, price: d.points[0].price })))
  const wantPrice = await page.evaluate((y) => (window as any).__proto.candle.coordinateToPrice(y), hy - box.y)
  log('c5.touch_place', { placed: placed.map((d: any) => d.kind), hlinePriceError: +(placed[0]?.price - wantPrice).toFixed(3) })
  expect(placed.map((d: any) => d.kind)).toEqual(['horizontal-line', 'trend-line'])

  // a swipe that starts on an unselected drawing pans the chart and leaves the drawing alone
  await tap({ x: c.x + 380, y: c.y - 120 }) // tap empty space: clear the selection
  const sel0 = await page.evaluate(() => (window as any).__proto.mgr.selection().length)
  const r3 = await range()
  await swipe([{ x: c.x, y: hy }], [{ x: c.x + 200, y: hy }])
  const r4 = await range()
  const p4 = await page.evaluate(() => (window as any).__proto.drawings()[0].points[0].price)
  log('c5.swipe_over_unselected_drawing', { selectionBefore: sel0, pannedBars: +(r3.from - r4.from).toFixed(1), drawingMoved: Math.abs(p4 - placed[0].price) > 1e-9 })

  // tap selects it; then a drag on it moves the drawing and holds the chart
  await tap({ x: c.x, y: hy })
  const sel1 = await page.evaluate(() => (window as any).__proto.mgr.selection().length)
  const r5 = await range()
  await swipe([{ x: c.x, y: hy }], [{ x: c.x + 40, y: hy - 80 }])
  const r6 = await range()
  const p6 = await page.evaluate(() => (window as any).__proto.drawings()[0].points[0].price)
  await page.screenshot({ path: 'test-results/c5-touch.png' })
  log('c5.touch_drag_selected_drawing', { selectedByTap: sel1, priceChange: +(p6 - p4).toFixed(2), chartHeld: Math.abs(r6.from - r5.from) < 0.01 })
  expect(sel1).toBe(1)
  expect(p6).toBeGreaterThan(p4)
})
