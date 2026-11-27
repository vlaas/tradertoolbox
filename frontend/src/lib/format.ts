/** How numbers and instants are written (UI-07 base). Tabular figures come from the stylesheet.
 * A missing value is a dash, never zero. An instant is written in a zone and says which
 * (UI-12): the zone comes from the person's choice (`lib/zones.ts`); New York when none is given. */
import { MARKET_ZONE, zoneName } from './zones'

export const MISSING = '—'

const fixed = new Map<number, Intl.NumberFormat>()

/** A number with a fixed count of decimals (UI-07), grouped. */
export function decimal(x: number | null | undefined, decimals = 2): string {
  if (x === null || x === undefined || Number.isNaN(x)) return MISSING
  let format = fixed.get(decimals)
  if (format === undefined) {
    format = new Intl.NumberFormat('en-US', {
      minimumFractionDigits: decimals,
      maximumFractionDigits: decimals,
    })
    fixed.set(decimals, format)
  }
  return format.format(x)
}

/** A count, grouped, without decimals. */
export function count(x: number | null | undefined): string {
  return decimal(x, 0)
}

const formats = new Map<string, Intl.DateTimeFormat>()

function formatIn(zone: string, kind: 'seconds' | 'minutes' | 'day'): Intl.DateTimeFormat {
  const key = `${kind} ${zone}`
  let format = formats.get(key)
  if (format === undefined) {
    format =
      kind === 'day'
        ? new Intl.DateTimeFormat('en-CA', {
            timeZone: zone,
            year: 'numeric',
            month: '2-digit',
            day: '2-digit',
          })
        : new Intl.DateTimeFormat('en-US', {
            timeZone: zone,
            hourCycle: 'h23',
            hour: '2-digit',
            minute: '2-digit',
            ...(kind === 'seconds' ? { second: '2-digit' } : {}),
          })
    formats.set(key, format)
  }
  return format
}

function date(us: number | null | undefined): Date | null {
  return us === null || us === undefined || Number.isNaN(us) ? null : new Date(us / 1000)
}

/** The time of an instant in microseconds, in a zone: 14:05:09. Without the zone's name, for
 * columns and lines that name it once. */
export function time(us: number | null | undefined, zone: string = MARKET_ZONE): string {
  const d = date(us)
  return d === null ? MISSING : formatIn(zone, 'seconds').format(d)
}

/** Hours and minutes only, for the status bar: 14:05. */
export function hm(us: number | null | undefined, zone: string = MARKET_ZONE): string {
  const d = date(us)
  return d === null ? MISSING : formatIn(zone, 'minutes').format(d)
}

/** The date and time of an instant in microseconds, with the zone's name:
 * 2026-11-20 10:00:00 ET. */
export function dateTime(us: number | null | undefined, zone: string = MARKET_ZONE): string {
  const d = date(us)
  if (d === null) return MISSING
  return `${formatIn(zone, 'day').format(d)} ${formatIn(zone, 'seconds').format(d)} ${zoneName(zone, d)}`
}

/** An ISO instant of the REST answers, in microseconds; null for none. */
export function micros(iso: string | null | undefined): number | null {
  if (iso === null || iso === undefined) return null
  const ms = Date.parse(iso)
  return Number.isNaN(ms) ? null : ms * 1000
}

/** Whole seconds, at least 1, for a wait: "3 s". */
export function seconds(ms: number): string {
  return `${Math.max(1, Math.ceil(ms / 1000))} s`
}
