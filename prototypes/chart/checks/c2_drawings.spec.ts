import { expect, test } from '@playwright/test'
import { at, frames, log, open } from './util'

const snap = (page: any) => page.evaluate(() => (window as any).__proto.drawings().map((d: any) => ({ kind: d.kind, points: d.points })))
const anchors = (page: any) => page.evaluate(() => {
  const p = (window as any).__proto
  return p.drawings().map((d: any) => d.points.map((q: any) => p.px(q.time, q.price)))
})

test('check 2: drawing round trip', async ({ page }) => {
  await open(page)
  // the 15-minute feed runs around the clock on weekdays, so the only gaps are weekends:
  // work around the last weekend gap that has bars on both sides
  const view = () => page.evaluate(async () => {
    const p = (window as any).__proto, b = p.bars, n = b.length
    let g = -1
    for (let i = 200; i < n - 80; i++) if (b[i + 1].utc - b[i].utc > 3600) g = i
    p.chart.timeScale().setVisibleLogicalRange({ from: g - 110, to: g + 80 })
    p.chart.priceScale('right').applyOptions({ autoScale: true })
    await new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(r)))
    const w = b.slice(g - 100, g + 70)
    return { n, g, gapHours: (b[g + 1].utc - b[g].utc) / 3600, lo: Math.min(...w.map((x: any) => x.low)), hi: Math.max(...w.map((x: any) => x.high)) }
  })
  const P = await view()
  log('c2.gap', { index: P.g, hours: P.gapHours })
  const mid = (P.lo + P.hi) / 2, q = (P.hi - P.lo) / 4
  const click = async (i: number, price: number) => { const c = await at(page, i, price); await page.mouse.click(c.x, c.y); await frames(page) }
  const arm = (kind: string) => page.locator(`[data-tool="${kind}"]`).click()
  const I = { t0: P.g - 90, t1: P.g - 40, v: P.g - 60, h: P.g - 70 }

  // trendline: two clicks
  await arm('trend-line'); await click(I.t0, mid - q); await click(I.t1, mid + q)
  // horizontal line and vertical line: one click each
  await arm('horizontal-line'); await click(I.h, mid + q / 2)
  await arm('vertical-line'); await click(I.v, mid)
  // ruler across the weekend gap: 6 bars before it to 6 bars after it
  const a = P.g - 5, b = P.g + 6
  await arm('date-and-price-range'); await click(a, mid - q / 2); await click(b, mid)
  await page.screenshot({ path: 'test-results/c2-placed.png' })

  const d1 = await snap(page)
  log('c2.placed', d1.map((d: any) => d.kind))
  expect(d1.map((d: any) => d.kind)).toEqual(['trend-line', 'horizontal-line', 'vertical-line', 'date-and-price-range'])

  // anchors sit on the clicked bars and prices
  const want = await page.evaluate(([a, b, i]) => {
    const p = (window as any).__proto, B = p.bars
    return { t: [B[i.t0].time, B[i.t1].time, B[i.v].time, B[a].time, B[b].time], utc: [B[a].utc, B[b].utc] }
  }, [a, b, I] as const)
  const got = { t: [d1[0].points[0].time, d1[0].points[1].time, d1[2].points[0].time, d1[3].points[0].time, d1[3].points[1].time] }
  log('c2.anchor_times', { want: want.t, got: got.t, equal: JSON.stringify(want.t) === JSON.stringify(got.t) })
  log('c2.anchor_prices', { trend: d1[0].points.map((x: any) => +x.price.toFixed(3)), wanted: [+(mid - q).toFixed(3), +(mid + q).toFixed(3)], hline: +d1[1].points[0].price.toFixed(3), hwanted: +(mid + q / 2).toFixed(3) })
  expect(got.t).toEqual(want.t)

  // ruler labels against the truth
  const lab = await page.evaluate(() => {
    const p = (window as any).__proto
    const id = p.mgr.drawings().find((d: any) => d.kind === 'date-and-price-range').id
    p.mgr.select([id]); p.mgr.redraw()
    return new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(() => r(Object.fromEntries(Object.entries(p.labels).filter(([k]) => k.startsWith(id)).map(([k, v]: any) => [k.split(':')[1], v]))))))
  }) as any
  const r = d1[3].points
  const truth = { change: r[1].price - r[0].price, percent: (r[1].price / r[0].price - 1) * 100, bars: b - a, seconds: want.utc[1] - want.utc[0], chartSeconds: r[1].time - r[0].time }
  log('c2.ruler_labels', Object.fromEntries(Object.entries(lab).map(([k, v]: any) => [k, { text: v.text, change: v.change, percent: v.percent, bars: v.bars, seconds: v.seconds }])))
  log('c2.ruler_truth', truth)

  // export, reload, import
  const json1 = await page.evaluate(() => (window as any).__proto.mgr.exportJSON())
  const px1 = await anchors(page)
  await open(page, false)
  await view()
  const d2 = await snap(page)
  log('c2.reload_equal', JSON.stringify(d1) === JSON.stringify(d2))
  expect(d2).toEqual(d1)
  const json2 = await page.evaluate(() => (window as any).__proto.mgr.exportJSON())
  log('c2.export_bytes', { bytes: json1.length, identical: json1 === json2 })

  // to 1-day and back
  await page.evaluate(async () => {
    const p = (window as any).__proto
    const t = p.drawings()[0].points[0].time
    await p.setRes('1D')
    const i = p.bars.findIndex((x: any) => x.time >= t)
    p.chart.timeScale().setVisibleLogicalRange({ from: i - 60, to: i + 40 })
  })
  await frames(page, 3)
  const dDay = await snap(page)
  const pxDay = await anchors(page)
  await page.screenshot({ path: 'test-results/c2-on-1d.png' })
  log('c2.on_1D', { modelUnchanged: JSON.stringify(dDay) === JSON.stringify(d1), anchorsWithCoordinate: pxDay.flat().filter(Boolean).length, anchorsTotal: pxDay.flat().length })
  await page.evaluate(() => (window as any).__proto.setRes('15m'))
  await view()
  const d3 = await snap(page), px3 = await anchors(page)
  await page.screenshot({ path: 'test-results/c2-back-15m.png' })
  const drift = Math.max(...px1.flat().map((p: any, i: number) => Math.hypot(p.x - px3.flat()[i].x, p.y - px3.flat()[i].y)))
  log('c2.back_to_15m', { modelEqual: JSON.stringify(d3) === JSON.stringify(d1), maxPixelDrift: +drift.toFixed(2) })
  expect(d3).toEqual(d1)

  // the other direction: a trendline placed on the 1-day chart, seen on the 15-minute chart
  await page.evaluate(async () => { const p = (window as any).__proto; p.mgr.clear(); await p.setRes('1D') })
  await frames(page, 3)
  await arm('trend-line')
  const dl = await page.evaluate(() => { const b = (window as any).__proto.bars; return [b.at(-12).low, b.at(-4).high] })
  for (const [i, pr] of [[-12, dl[0]], [-4, dl[1]]] as const) { const c = await at(page, i, pr); await page.mouse.click(c.x, c.y); await frames(page) }
  const dayLine = await snap(page)
  await page.evaluate(async () => {
    const p = (window as any).__proto
    await p.setRes('15m')
    const t = p.drawings()[0].points[0].time
    const i = p.bars.findIndex((x: any) => x.time >= t)
    p.chart.timeScale().setVisibleLogicalRange({ from: i - 40, to: p.bars.length + 20 })
  })
  await frames(page, 3)
  const onIntraday = await page.evaluate(() => {
    const p = (window as any).__proto
    return p.drawings()[0].points.map((q: any) => ({ time: new Date(q.time * 1000).toISOString().slice(0, 16), isBar: p.bars.some((b: any) => b.time === q.time), px: !!p.px(q.time, q.price) }))
  })
  await page.screenshot({ path: 'test-results/c2-dayline-on-15m.png' })
  log('c2.day_drawing_on_15m', { modelUnchanged: JSON.stringify(await snap(page)) === JSON.stringify(dayLine), anchors: onIntraday })
})
