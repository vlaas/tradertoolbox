import { expect, test } from '@playwright/test'
import { frames, log, open, stat } from './util'

test('check 1: load and live update', async ({ page }) => {
  await open(page)
  for (const res of ['1D', '15m']) {
    const set: number[] = [], first: number[] = []
    for (let k = 0; k < 7; k++) {
      const t = await page.evaluate(async (r) => { const p = (window as any).__proto; await p.setRes(r); return [p.timings.setData[r], p.timings.firstFrame[r], p.bars.length, p.future.length, p.timings.parts[r]] }, res)
      set.push(t[0]); first.push(t[1])
      if (k === 0) log(`c1.${res}.bars`, { bars: t[2], futureWhitespace: t[3] })
      if (k === 3) log(`c1.${res}.setData_parts_ms`, t[4])
    }
    log(`c1.${res}.setData_ms`, stat(set))
    log(`c1.${res}.firstFrame_ms`, stat(first))
    expect(stat(set).median).toBeLessThan(1000) // a resolution switch; the live limit is below

    // live: last bar replaced twice a second, a new bar every 6th update, for 8 seconds
    await page.evaluate(() => (window as any).__proto.startLive(500, 6))
    await page.waitForTimeout(8000)
    const live = await page.evaluate(() => { const p = (window as any).__proto; p.stopLive(); return { updates: p.timings.updates, frames: p.timings.frames.slice(2), bars: p.bars.length } })
    log(`c1.${res}.live_update_ms`, stat(live.updates))
    log(`c1.${res}.live_frame_ms`, { ...stat(live.frames), over50: live.frames.filter((f: number) => f > 50).length })
    expect(stat(live.frames).max).toBeLessThan(100)

    // burst: 500 last-bar updates and 100 appended bars, one frame awaited after each
    const burst = await page.evaluate(async () => {
      const p = (window as any).__proto
      const fr: number[] = []
      p.timings.updates = []
      let prev = performance.now()
      for (let i = 0; i < 600; i++) {
        i % 6 === 5 ? p.append() : p.tick()
        const t: number = await new Promise((r) => requestAnimationFrame(r))
        fr.push(t - prev); prev = t
      }
      return { updates: p.timings.updates, frames: fr.slice(1) }
    })
    log(`c1.${res}.burst_update_ms`, stat(burst.updates))
    log(`c1.${res}.burst_frame_ms`, { ...stat(burst.frames), over50: burst.frames.filter((f: number) => f > 50).length })
  }
  await frames(page)
})
