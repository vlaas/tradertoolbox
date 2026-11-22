import type { Page } from '@playwright/test'

/** Open the page with empty storage and wait for the chart handle. */
export async function open(page: Page, clear = true): Promise<void> {
  await page.goto('/')
  if (clear) {
    await page.evaluate(() => localStorage.clear())
    await page.reload()
  }
  await page.waitForFunction(() => (window as any).__proto, null, { timeout: 30_000 })
  await frames(page, 3)
}
export async function frames(page: Page, n = 2): Promise<void> {
  await page.evaluate(async (k) => { for (let i = 0; i < k; i++) await new Promise((r) => requestAnimationFrame(r)) }, n)
}
export const stat = (a: number[]) => {
  const s = [...a].sort((x, y) => x - y)
  const q = (p: number) => s[Math.min(s.length - 1, Math.floor(p * s.length))]
  return { n: s.length, median: +q(0.5).toFixed(2), p95: +q(0.95).toFixed(2), max: +s[s.length - 1].toFixed(2) }
}
/** Page coordinates of bar `i` (negative = from the end) at `price`. */
export async function at(page: Page, i: number, price: number): Promise<{ x: number; y: number }> {
  const p = await page.evaluate(([i, price]) => {
    const p = (window as any).__proto
    const b = p.bars.at(i)
    return p.px(b.time, price)
  }, [i, price])
  if (!p) throw new Error(`bar ${i} is off screen`)
  return p
}
export const log = (name: string, v: unknown) => console.log(`RESULT ${name} ${JSON.stringify(v)}`)
