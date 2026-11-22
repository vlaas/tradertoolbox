import { expect, test } from '@playwright/test'
import { at, frames, log, open } from './util'

const wall = (t: number) => new Date(t * 1000).toISOString().slice(0, 16).replace('T', ' ')

test('check 6: time model: zones, daylight saving, sessions, resolutions', async ({ page }) => {
  await open(page)
  const P = (f: string) => page.evaluate(f)

  // --- 1. every resolution loads; chart times rise strictly in every zone
  const zones = ['America/New_York', 'UTC', 'Europe/Tallinn', 'Europe/London', 'Asia/Tokyo', 'Australia/Sydney', 'Africa/Cairo']
  for (const res of ['1D', '4h', '1h', '15m', '5m']) {
    const r = await page.evaluate(async ([res, zones]) => {
      const p = (window as any).__proto
      await p.setZone('America/New_York'); await p.setRes(res)
      const out: any = { bars: p.bars.length, future: p.future.length, first: new Date(p.bars[0].utc * 1000).toISOString().slice(0, 10), zones: {} }
      for (const z of zones as string[]) {
        await p.setZone(z)
        const all = [...p.bars, ...p.future]
        let ok = true
        for (let i = 1; i < all.length; i++) if (!(all[i].time > all[i - 1].time)) ok = false
        out.zones[z] = ok ? p.carried : 'NOT RISING'
      }
      await p.setZone('America/New_York')
      return out
    }, [res, zones] as const)
    log(`c6.load.${res}`, { bars: r.bars, since: r.first, futureWhitespace: r.future, barsWithCarriedOffsetByZone: r.zones })
    expect(Object.values(r.zones)).not.toContain('NOT RISING')
  }

  // --- 2. the regular open (09:30 exchange time) read in other zones, around the weeks when
  // Europe and the US are on different sides of the clock change (EU 25 Oct 2026, US 1 Nov 2026)
  const opens = await page.evaluate(async () => {
    const p = (window as any).__proto
    await p.setRes('15m')
    const out: Record<string, Record<string, string>> = {}
    for (const z of ['America/New_York', 'UTC', 'Europe/Tallinn']) {
      await p.setZone(z)
      out[z] = {}
      for (const d of ['2026-10-23', '2026-10-26', '2026-10-30', '2026-11-02']) {
        // the bar whose instant is 09:30 New York on that date
        const b = p.bars.find((b: any) => new Intl.DateTimeFormat('sv-SE', { timeZone: 'America/New_York', dateStyle: 'short', timeStyle: 'short' }).format(new Date(b.utc * 1000)) === `${d} 09:30`)
        out[z][d] = b ? new Date(b.time * 1000).toISOString().slice(11, 16) : 'no bar'
      }
    }
    await p.setZone('America/New_York')
    return out
  })
  log('c6.open_0930_NY_read_in_zone', opens)
  expect(opens['America/New_York']['2026-10-26']).toBe('09:30')
  expect(opens['Europe/Tallinn']).toEqual({ '2026-10-23': '16:30', '2026-10-26': '15:30', '2026-10-30': '15:30', '2026-11-02': '16:30' })
  expect(opens['UTC']).toEqual({ '2026-10-23': '13:30', '2026-10-26': '13:30', '2026-10-30': '13:30', '2026-11-02': '14:30' })

  // --- 3. session shading follows the exchange clock in every zone
  for (const z of ['America/New_York', 'Europe/Tallinn']) {
    const s = await page.evaluate(async (z) => {
      const p = (window as any).__proto
      await p.setZone(z); await p.setRes('15m')
      await new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(r)))
      const ts = p.chart.timeScale()
      const kind = (hhmm: string) => {
        // a visible bar at that New York time, and the run painted under it
        const f = new Intl.DateTimeFormat('sv-SE', { timeZone: 'America/New_York', timeStyle: 'short' })
        const b = [...p.bars].reverse().find((b: any) => f.format(new Date(b.utc * 1000)) === hhmm && ts.timeToCoordinate(b.time) > 0)
        const x = ts.timeToCoordinate(b.time)
        return p.sessions.last.runs.find((r: any) => r.from <= x && x <= r.to)?.s ?? 'none'
      }
      return { '03:00': kind('03:00'), '08:00': kind('08:00'), '09:30': kind('09:30'), '15:45': kind('15:45'), '16:00': kind('16:00'), '19:45': kind('19:45'), '21:00': kind('21:00'), runs: p.sessions.last.runs.length }
    }, z)
    log(`c6.sessions.${z}`, s)
    expect([s['03:00'], s['08:00'], s['09:30'], s['15:45'], s['16:00'], s['19:45'], s['21:00']]).toEqual(['night', 'pre', 'rth', 'rth', 'post', 'post', 'night'])
  }
  await page.screenshot({ path: 'test-results/c6-sessions-tallinn.png' })

  // --- 4. a drawing made on the 1-day chart lands on the opening bar of intraday charts
  await page.evaluate(async () => { const p = (window as any).__proto; p.mgr.clear(); await p.setZone('America/New_York'); await p.setRes('1D') })
  await frames(page, 3)
  await page.locator('[data-tool="trend-line"]').click()
  const dl = await page.evaluate(() => { const b = (window as any).__proto.bars; return [b.at(-12).low, b.at(-4).high] })
  for (const [i, pr] of [[-12, dl[0]], [-4, dl[1]]] as const) { const c = await at(page, i, pr); await page.mouse.click(c.x, c.y); await frames(page) }
  const utc0 = await P(`JSON.stringify(window.__proto.exportUtc().map(d => d.points.map(q => q.time)))`)
  const land: Record<string, unknown> = {}
  for (const res of ['15m', '5m', '1h', '4h']) {
    land[res] = await page.evaluate(async (res) => {
      const p = (window as any).__proto
      await p.setRes(res)
      const f = new Intl.DateTimeFormat('sv-SE', { timeZone: 'America/New_York', dateStyle: 'short', timeStyle: 'short' })
      return p.drawings()[0].points.map((q: any) => ({ ny: f.format(new Date(p.toUtc(q.time) * 1000)), onABar: p.bars.some((b: any) => b.time === q.time) }))
    }, res)
  }
  await page.evaluate(() => (window as any).__proto.setRes('1D'))
  const utc1 = await P(`JSON.stringify(window.__proto.exportUtc().map(d => d.points.map(q => q.time)))`)
  const backOnBars = await P(`window.__proto.drawings()[0].points.every(q => window.__proto.bars.some(b => b.time === q.time))`)
  log('c6.daily_anchor_on_intraday', land)
  log('c6.daily_anchor_round_trip', { instantsUnchanged: utc0 === utc1, backOnDailyBars: backOnBars })
  expect(utc0).toBe(utc1)
  expect((land['15m'] as any[]).every((a) => a.ny.endsWith('09:30') && a.onABar)).toBe(true)

  // --- 5. zone round trip keeps the drawings on the same instants and bars
  await page.evaluate(async () => { const p = (window as any).__proto; p.mgr.clear(); await p.setRes('15m') })
  await frames(page, 3)
  await page.locator('[data-tool="trend-line"]').click()
  const lo = await page.evaluate(() => { const b = (window as any).__proto.bars; return [b.at(-120).low, b.at(-30).high] })
  for (const [i, pr] of [[-120, lo[0]], [-30, lo[1]]] as const) { const c = await at(page, i, pr); await page.mouse.click(c.x, c.y); await frames(page) }
  const z0 = await page.evaluate(() => { const p = (window as any).__proto; return { utc: JSON.stringify(p.exportUtc().map((d: any) => d.points)), chart: JSON.stringify(p.drawings().map((d: any) => d.points)), idx: p.drawings()[0].points.map((q: any) => p.bars.findIndex((b: any) => b.time === q.time)) } })
  const trip: Record<string, unknown> = {}
  for (const z of ['UTC', 'Europe/Tallinn', 'Africa/Cairo', 'Asia/Tokyo', 'America/New_York']) {
    trip[z] = await page.evaluate(async ([z, utc, idx]) => {
      const p = (window as any).__proto
      await p.setZone(z)
      return { sameInstants: JSON.stringify(p.exportUtc().map((d: any) => d.points)) === utc, sameBars: JSON.stringify(p.drawings()[0].points.map((q: any) => p.bars.findIndex((b: any) => b.time === q.time))) === JSON.stringify(idx), firstAnchorReads: new Date(p.drawings()[0].points[0].time * 1000).toISOString().slice(11, 16) }
    }, [z, z0.utc, z0.idx] as const)
  }
  const z1 = await P(`JSON.stringify(window.__proto.drawings().map(d => d.points))`)
  log('c6.zone_round_trip', { ...trip, chartModelIdenticalAtEnd: z1 === z0.chart })
  expect(z1).toBe(z0.chart)

  // --- 6. reload: the stored form is UTC and comes back the same
  await open(page, false)
  const afterReload = await page.evaluate(() => JSON.stringify((window as any).__proto.exportUtc().map((d: any) => d.points)))
  log('c6.reload_from_utc_store', { same: afterReload === z0.utc })
  expect(afterReload).toBe(z0.utc)

  // --- 7. ruler across the US clock change (Fri 30 Oct 15:00 to Mon 2 Nov 10:00 New York)
  const ruler = await page.evaluate(async () => {
    const p = (window as any).__proto
    p.mgr.clear(); await p.setRes('15m')
    const f = new Intl.DateTimeFormat('sv-SE', { timeZone: 'America/New_York', dateStyle: 'short', timeStyle: 'short' })
    const bar = (s: string) => p.bars.find((b: any) => f.format(new Date(b.utc * 1000)) === s)
    const a = bar('2026-10-30 15:00'), b = bar('2026-11-02 10:00')
    p.mgr.add({ kind: 'date-and-price-range', points: [{ time: a.time, price: a.close }, { time: b.time, price: b.close }] })
    const id = p.mgr.drawings()[0].id
    const i = p.bars.indexOf(a)
    p.chart.timeScale().setVisibleLogicalRange({ from: i - 80, to: i + 260 })
    const read = async (own: boolean) => {
      p.trueElapsed = own; p.mgr.select([id]); p.mgr.redraw()
      await new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(r)))
      return p.labels[`${id}:time-span`]?.seconds
    }
    const pluginSeconds = await read(false)
    await read(true)
    return { trueSeconds: b.utc - a.utc, chartSeconds: b.time - a.time, pluginSeconds }
  })
  await page.screenshot({ path: 'test-results/c6-ruler-dst.png' })
  const h = (s: number) => `${Math.floor(s / 86400)}d ${Math.round((s % 86400) / 3600)}h`
  log('c6.ruler_across_clock_change', { trueElapsed: h(ruler.trueSeconds), pluginOnChartTimes: h(ruler.pluginSeconds), hoursOff: (ruler.trueSeconds - ruler.pluginSeconds) / 3600 })
  expect(ruler.trueSeconds - ruler.chartSeconds).toBe(3600)
})
