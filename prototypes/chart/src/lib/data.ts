/** Candle loading and the time model of the prototype.
 *
 * Every bar and every drawing anchor is a true UTC instant. Lightweight Charts has no time-zone
 * support, so at the chart boundary an instant is "encoded": shifted by the zone's offset at that
 * instant, so that its UTC rendering reads as wall-clock time in the chosen zone. The offset comes
 * from the browser's time-zone database, per instant, so each region's own daylight-saving dates
 * apply. A daily bar's instant is the session open of its day, which makes a daily anchor land on
 * the opening bar of an intraday chart. */
import type { UTCTimestamp } from 'lightweight-charts'

export type Row = [number, number, number, number, number, number]
export type Res = '1D' | '4h' | '1h' | '15m' | '5m'
export const RES: Res[] = ['1D', '4h', '1h', '15m', '5m']
export const FILES: Record<Res, string> = { '1D': 'spy_d', '4h': 'spy_4h', '1h': 'spy_1h', '15m': 'spy_15m', '5m': 'spy_5m' }
export const BAR_SEC: Record<Res, number> = { '1D': 86400, '4h': 14400, '1h': 3600, '15m': 900, '5m': 300 }
export const EXCHANGE = 'America/New_York'
export const intraday = (r: Res) => r !== '1D'
const OPEN = 9.5 * 3600, CLOSE = 16 * 3600, DAY = 86400

export interface Raw { utc: number; open: number; high: number; low: number; close: number; volume: number }
export interface Bar extends Raw { time: UTCTimestamp }
export interface FutureBar { time: UTCTimestamp; utc: number }
export type Session = 'rth' | 'pre' | 'post' | 'night'

const fmts = new Map<string, Intl.DateTimeFormat>()
const caches = new Map<string, Map<number, number>>()

/** Seconds to add to a UTC instant so that it reads as wall-clock time in `zone`. */
export function offset(zone: string, utcSec: number): number {
  if (zone === 'UTC') return 0
  let cache = caches.get(zone)
  if (!cache) caches.set(zone, (cache = new Map()))
  const key = Math.floor(utcSec / 900)
  let o = cache.get(key)
  if (o === undefined) {
    let f = fmts.get(zone)
    if (!f) fmts.set(zone, (f = new Intl.DateTimeFormat('en-US', { timeZone: zone, hourCycle: 'h23', year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', second: '2-digit' })))
    const p: Record<string, number> = {}
    for (const part of f.formatToParts(new Date(key * 900 * 1000))) if (part.type !== 'literal') p[part.type] = Number(part.value)
    o = Date.UTC(p.year, p.month - 1, p.day, p.hour, p.minute, p.second) / 1000 - key * 900
    cache.set(key, o)
  }
  return o
}
export const encode = (utc: number, zone: string) => utc + offset(zone, utc)
/** The instant whose wall-clock reading in `zone` is `wall`. In a repeated hour it picks one of the two. */
export function decode(wall: number, zone: string): number {
  let u = wall - offset(zone, wall)
  u = wall - offset(zone, u)
  return u
}
/** Exchange calendar day (days since the epoch), seconds into it and weekday of an instant. */
export function exchangeClock(utc: number): { day: number; tod: number; dow: number } {
  const e = encode(utc, EXCHANGE), day = Math.floor(e / DAY)
  return { day, tod: e - day * DAY, dow: new Date(day * DAY * 1000).getUTCDay() }
}
export const openInstant = (day: number) => decode(day * DAY + OPEN, EXCHANGE)

/** The session a bar belongs to, by the middle of the bar in exchange time. */
export function session(utc: number, barSec: number): Session {
  const { tod } = exchangeClock(utc + barSec / 2)
  return tod >= OPEN && tod < CLOSE ? 'rth' : tod >= 4 * 3600 && tod < OPEN ? 'pre' : tod >= CLOSE && tod < 20 * 3600 ? 'post' : 'night'
}

export async function load(res: Res): Promise<Raw[]> {
  const rows: Row[] = await (await fetch(`/data/${FILES[res]}.json`)).json()
  return rows.map(([ms, open, high, low, close, volume]) => ({
    // the broker stamps a daily bar with midnight UTC of its day: move it to that day's open
    utc: res === '1D' ? openInstant(Math.round(ms / 1000 / DAY)) : ms / 1000, open, high, low, close, volume,
  }))
}

/** Chart times for a zone. They must rise strictly. Where a zone sets its clocks back while bars
 * keep coming, the true reading would repeat an hour, so the earlier offset is carried until the
 * data has a gap long enough to absorb the hour. `carried` counts the bars affected. */
export function encodeBars(raw: Raw[], zone: string): { bars: Bar[]; carried: number } {
  const bars: Bar[] = []
  let prevT = -Infinity, prevUtc = 0, carried = 0
  for (const r of raw) {
    let t = encode(r.utc, zone)
    if (t <= prevT) { t = r.utc + (prevT - prevUtc); carried++ }
    bars.push({ ...r, time: t as UTCTimestamp })
    prevT = t; prevUtc = r.utc
  }
  return { bars, carried }
}

/** Instants of the bars to come, for whitespace on the time scale. Daily: the open of the next
 * weekdays. Intraday: the exchange-time pattern of each weekday in the last week of data, repeated.
 * Holidays and early closes are ignored in the prototype. */
export function futureInstants(raw: Raw[], res: Res, calendarDays: number): number[] {
  const last = raw[raw.length - 1], c0 = exchangeClock(last.utc), out: number[] = []
  if (res === '1D') {
    for (let d = c0.day + 1; d <= c0.day + calendarDays; d++) {
      const dow = new Date(d * DAY * 1000).getUTCDay()
      if (dow !== 0 && dow !== 6) out.push(openInstant(d))
    }
    return out
  }
  const pattern = new Map<number, Set<number>>()
  for (let i = raw.length - 1; i >= 0 && raw[i].utc > last.utc - 7 * DAY; i--) {
    const c = exchangeClock(raw[i].utc)
    if (!pattern.has(c.dow)) pattern.set(c.dow, new Set())
    pattern.get(c.dow)!.add(Math.round(c.tod))
  }
  for (let d = c0.day; d <= c0.day + calendarDays; d++) {
    const tods = pattern.get(new Date(d * DAY * 1000).getUTCDay())
    if (!tods) continue
    for (const tod of [...tods].sort((a, b) => a - b)) {
      const u = decode(d * DAY + tod, EXCHANGE)
      if (u > last.utc) out.push(u)
    }
  }
  return out
}
