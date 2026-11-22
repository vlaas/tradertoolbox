import type { UTCTimestamp } from 'lightweight-charts'
import type { Bar } from './data'

export interface Pt { time: UTCTimestamp; value: number }

export function sma(bars: Bar[], n: number): Pt[] {
  const out: Pt[] = []
  let sum = 0
  for (let i = 0; i < bars.length; i++) {
    sum += bars[i].close
    if (i >= n) sum -= bars[i - n].close
    if (i >= n - 1) out.push({ time: bars[i].time, value: sum / n })
  }
  return out
}

/** Wilder RSI. */
export function rsi(bars: Bar[], n = 14): Pt[] {
  const out: Pt[] = []
  let up = 0, dn = 0
  for (let i = 1; i < bars.length; i++) {
    const ch = bars[i].close - bars[i - 1].close
    const u = Math.max(ch, 0), d = Math.max(-ch, 0)
    if (i <= n) { up += u / n; dn += d / n } else { up = (up * (n - 1) + u) / n; dn = (dn * (n - 1) + d) / n }
    if (i >= n) out.push({ time: bars[i].time, value: dn === 0 ? 100 : 100 - 100 / (1 + up / dn) })
  }
  return out
}

/** Wilder ATR. */
export function atr(bars: Bar[], n = 14): Pt[] {
  const out: Pt[] = []
  let a = 0
  for (let i = 1; i < bars.length; i++) {
    const b = bars[i], pc = bars[i - 1].close
    const tr = Math.max(b.high - b.low, Math.abs(b.high - pc), Math.abs(b.low - pc))
    a = i <= n ? a + tr / n : (a * (n - 1) + tr) / n
    if (i >= n) out.push({ time: b.time, value: a })
  }
  return out
}
