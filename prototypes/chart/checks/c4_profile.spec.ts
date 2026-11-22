import { expect, test } from '@playwright/test'
import { frames, log, open } from './util'

test('check 4: right-edge profile', async ({ page }) => {
  await open(page)
  await page.evaluate(() => (window as any).__proto.setRes('1D'))
  await frames(page, 3)
  // what the layer painted against what the price scale says now
  const compare = () => page.evaluate(() => {
    const p = (window as any).__proto, L = p.profile.last, pane = p.chart.paneSize(0)
    let worst = 0
    const ys: Record<string, number> = {}
    for (const k of p.profile.spec.strikes) {
      const y = p.candle.priceToCoordinate(k.price)
      ys[k.label] = y
      worst = Math.max(worst, Math.abs((L.strikeY[k.label] ?? NaN) - y))
    }
    const r = p.chart.timeScale().getVisibleLogicalRange()
    return { worst, pinned: L.width === pane.width, paneWidth: pane.width, y: ys, from: r.from, to: r.to, auto: p.chart.priceScale('right').options().autoScale }
  })
  const box = (await page.getByTestId('chart').boundingBox())!
  const cx = box.x + box.width * 0.4, cy = box.y + 250
  const steps: [string, () => Promise<void>][] = [
    ['initial', async () => {}],
    ['wheel zoom in', async () => { await page.mouse.move(cx, cy); for (let i = 0; i < 6; i++) await page.mouse.wheel(0, -240) }],
    ['wheel zoom out', async () => { await page.mouse.move(cx, cy); for (let i = 0; i < 12; i++) await page.mouse.wheel(0, 240) }],
    ['drag scroll into the past', async () => { await page.mouse.move(cx, cy); await page.mouse.down(); await page.mouse.move(cx + 420, cy + 10, { steps: 12 }); await page.mouse.up() }],
    ['drag the price scale', async () => { const x = box.x + box.width - 25; await page.mouse.move(x, cy); await page.mouse.down(); await page.mouse.move(x, cy + 140, { steps: 10 }); await page.mouse.up() }],
    ['drag the pane vertically', async () => { await page.mouse.move(cx, cy); await page.mouse.down(); await page.mouse.move(cx, cy + 90, { steps: 8 }); await page.mouse.up() }],
    ['autoscale again', async () => { await page.evaluate(() => (window as any).__proto.chart.priceScale('right').applyOptions({ autoScale: true })) }],
    ['resize the window', async () => { await page.setViewportSize({ width: 1100, height: 760 }) }],
  ]
  let prev: any = null
  for (const [name, act] of steps) {
    await act()
    await frames(page, 4)
    const c = await compare()
    const moved = prev ? +Math.abs(c.y['−1 P 640'] - prev.y['−1 P 640']).toFixed(1) : 0
    log(`c4.${name}`, { maxErrorPx: +c.worst.toFixed(3), pinnedToRightEdge: c.pinned, strikeMovedPx: moved, autoScale: c.auto, bars: +(c.to - c.from).toFixed(0) })
    expect(c.worst).toBeLessThan(0.01)
    expect(c.pinned).toBe(true)
    prev = c
  }
  await page.setViewportSize({ width: 1440, height: 900 })
  await page.evaluate(async () => { const p = (window as any).__proto; await p.setRes('1D') })
  await frames(page, 4)

  // pointer handling under the band: place, select and drag a drawing there
  const band = await page.evaluate(() => {
    const p = (window as any).__proto, w = p.chart.paneSize(0).width, r = document.querySelector('[data-testid=chart]')!.getBoundingClientRect()
    const price = p.bars.at(-1).close * 1.04
    return { x: r.left + w - 90, y: r.top + p.candle.priceToCoordinate(price), price, inBand: 90 < p.profile.band }
  })
  await page.locator('[data-tool="horizontal-line"]').click()
  await page.mouse.click(band.x, band.y); await frames(page)
  const placed = await page.evaluate(() => (window as any).__proto.drawings().map((d: any) => ({ kind: d.kind, price: d.points[0].price })))
  await page.mouse.click(band.x - 300, band.y + 120); await frames(page) // click away: deselect
  await page.mouse.click(band.x, band.y); await frames(page) // select it under the band
  const selected = await page.evaluate(() => (window as any).__proto.mgr.selection().length)
  await page.mouse.move(band.x, band.y); await page.mouse.down(); await page.mouse.move(band.x, band.y - 60, { steps: 8 }); await page.mouse.up(); await frames(page)
  const after = await page.evaluate(() => (window as any).__proto.drawings()[0]?.points[0].price)
  await page.screenshot({ path: 'test-results/c4-band.png' })
  log('c4.pointer_under_band', { clickInsideBand: band.inBand, placed: placed.length, placedPriceError: +(placed[0]?.price - band.price).toFixed(3), selectedByClick: selected, draggedBy: +(after - placed[0]?.price).toFixed(2) })
  expect(placed.length).toBe(1)
  expect(selected).toBe(1)
  expect(after).toBeGreaterThan(placed[0].price)
})
