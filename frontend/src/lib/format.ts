/** How numbers and instants are written (UI-07 base). Tabular figures come from the stylesheet.
 * A missing value is a dash, never zero. Instants are New York time, the exchange time (CH-06's
 * default), and say so with "ET". */

export const MISSING = '—'
const ZONE = 'America/New_York'

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

const clock = new Intl.DateTimeFormat('en-US', {
  timeZone: ZONE,
  hourCycle: 'h23',
  hour: '2-digit',
  minute: '2-digit',
  second: '2-digit',
})
const day = new Intl.DateTimeFormat('en-CA', {
  timeZone: ZONE,
  year: 'numeric',
  month: '2-digit',
  day: '2-digit',
})

function date(us: number | null | undefined): Date | null {
  return us === null || us === undefined || Number.isNaN(us) ? null : new Date(us / 1000)
}

/** The New York time of an instant in microseconds: 14:05:09. */
export function time(us: number | null | undefined): string {
  const d = date(us)
  return d === null ? MISSING : clock.format(d)
}

/** The New York date and time of an instant in microseconds: 2026-11-20 10:00:00 ET. */
export function dateTime(us: number | null | undefined): string {
  const d = date(us)
  return d === null ? MISSING : `${day.format(d)} ${clock.format(d)} ET`
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
