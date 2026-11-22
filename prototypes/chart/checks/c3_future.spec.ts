import { expect, test } from '@playwright/test'
import { frames, log, open } from './util'

test('check 3: future time', async ({ page }) => {
  await open(page)
  for (const res of ['15m', '1D']) {
    await page.evaluate(async (r) => {
      const p = (window as any).__proto
      p.mgr.clear()
      await p.setRes(r)
      // show the last bars and the whole stretch up to the expiry
      const i = p.future.findIndex((f: any) => f.time === p.cone.spec.expiry.time)
      p.chart.timeScale().setVisibleLogicalRange({ from: p.bars.length - 60, to: p.bars.length + i + 20 })
    }, res)
    await frames(page, 3)
    const state = () => page.evaluate(() => {
      const p = (window as any).__proto, ts = p.chart.timeScale(), c = p.cone
      const timeAt = (x: number) => ts.coordinateToTime(x)
      return {
        bars: p.bars.length, lastBar: p.bars.at(-1).time,
        expiryTime: c.spec.expiry.time, expiryAtX: timeAt(c.last.expiryX), originAtX: timeAt(c.last.originX), originTime: c.spec.origin.time,
        expiryX: c.last.expiryX, originX: c.last.originX, upperY: c.last.upperY,
        drawings: p.drawings().map((d: any) => ({ kind: d.kind, t: d.points.map((q: any) => q.time), x: d.points.map((q: any) => ts.timeToCoordinate(q.time)) })),
      }
    })
    const s0 = await state()
    log(`c3.${res}.cone`, { expiryOnItsTime: s0.expiryAtX === s0.expiryTime, originOnItsTime: s0.originAtX === s0.originTime, daysAhead: +((s0.expiryTime - s0.lastBar) / 86400).toFixed(1) })
    expect(s0.expiryAtX).toBe(s0.expiryTime)

    // user drawings anchored in the future: a trendline from a past bar to a future time, and a vertical line
    const pts = await page.evaluate(() => {
      const p = (window as any).__proto, b = p.bars, f = p.future, n = Math.floor(f.findIndex((x: any) => x.time === p.cone.spec.expiry.time) / 2)
      return { a: p.px(b.at(-30).time, b.at(-30).low), b: p.px(f[n].time, b.at(-1).close * 1.02), v: p.px(f[n + 3].time, b.at(-1).close), tb: f[n].time, tv: f[n + 3].time }
    })
    await page.locator('[data-tool="trend-line"]').click()
    await page.mouse.click(pts.a.x, pts.a.y); await frames(page)
    await page.mouse.click(pts.b.x, pts.b.y); await frames(page)
    await page.locator('[data-tool="vertical-line"]').click()
    await page.mouse.click(pts.v.x, pts.v.y); await frames(page)
    const s1 = await state()
    // with over 2,000 future bars on screen a bar is narrower than a pixel, so the anchor is judged in pixels
    const off = await page.evaluate(([tb, tv]) => {
      const p = (window as any).__proto, ts = p.chart.timeScale(), d = p.drawings()
      const dx = (got: number, want: number) => Math.abs(ts.timeToCoordinate(got) - ts.timeToCoordinate(want))
      return { trend: dx(d[0].points[1].time, tb), vline: dx(d[1].points[0].time, tv), barPx: ts.options().barSpacing }
    }, [pts.tb, pts.tv])
    const fut = { trendEnd: s1.drawings[0]?.t[1], wanted: pts.tb, vline: s1.drawings[1]?.t[0], vwanted: pts.tv }
    log(`c3.${res}.future_anchor`, { exact: fut.trendEnd === pts.tb && fut.vline === pts.tv, offPx: [+off.trend.toFixed(2), +off.vline.toFixed(2)], barWidthPx: +off.barPx.toFixed(2), inFuture: fut.trendEnd > s1.lastBar && fut.vline > s1.lastBar })
    expect(off.trend).toBeLessThan(1.5)
    expect(off.vline).toBeLessThan(1.5)
    expect(fut.trendEnd).toBeGreaterThan(s1.lastBar)

    // live: 12 new bars and 60 last-bar updates
    await page.evaluate(async () => {
      const p = (window as any).__proto
      for (let i = 0; i < 72; i++) { i % 6 === 5 ? p.append() : p.tick(); if (i % 6 === 0) await new Promise((r) => requestAnimationFrame(r)) }
    })
    await frames(page, 3)
    const s2 = await state()
    await page.screenshot({ path: `test-results/c3-${res}.png` })
    const same = (a: number[], b: number[]) => a.length === b.length && a.every((v, i) => Math.abs(v - b[i]) < 0.01)
    log(`c3.${res}.after_live`, {
      barsAdded: s2.bars - s1.bars,
      expiryStillOnItsTime: s2.expiryAtX === s2.expiryTime, expiryXShift: +(s2.expiryX - s1.expiryX).toFixed(2), originXShift: +(s2.originX - s1.originX).toFixed(2),
      drawingTimesSame: JSON.stringify(s2.drawings.map((d: any) => d.t)) === JSON.stringify(s1.drawings.map((d: any) => d.t)),
      drawingXSame: s2.drawings.every((d: any, i: number) => same(d.x, s1.drawings[i].x)),
    })
    expect(s2.expiryAtX).toBe(s2.expiryTime)
    expect(s2.bars - s1.bars).toBe(12)
  }
})
