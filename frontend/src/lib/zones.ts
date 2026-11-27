/** Which time the screens show (UI-12; decided 2026-11-27, owner, WP-0.8 review).
 *
 * By default a time is the exchange time of its instrument. The person can choose local time,
 * UTC or any time zone by its name instead. Every time on a screen says its zone. Instants are
 * compared as instants (microseconds since 1970 in UTC), never as written times, so the choice
 * changes what is shown and nothing else.
 *
 * A choice is one string: `exchange`, `local`, or the name of a zone (`UTC`, `Europe/Tallinn`). */

/** The zone of the calendar of stocks, ETFs and indexes (XNYS; requirements, section 3, the
 * calendar). Times of the backend that belong to no instrument are in this zone too. */
export const MARKET_ZONE = 'America/New_York'

export const DEFAULT_CHOICE = 'exchange'

/** The exchange zone of a symbol of the `quotes` topic. That topic has stocks, ETFs and indexes
 * only (decided 2026-11-26), all on the New York calendar. Futures are Chicago's (CMES); the zone
 * of an instrument comes from the backend when they come (plan section 8, WP-0.8). */
export function exchangeZone(_symbol: string): string {
  return MARKET_ZONE
}

/** Whether the browser knows a zone of this name. */
export function isZone(name: string): boolean {
  if (!name) return false
  try {
    new Intl.DateTimeFormat('en-US', { timeZone: name })
    return true
  } catch {
    return false
  }
}

export function localZone(): string {
  return Intl.DateTimeFormat().resolvedOptions().timeZone
}

/** A saved choice, or the default when it is none or no longer a zone. */
export function choiceOf(saved: string | null | undefined): string {
  if (saved === 'exchange' || saved === 'local') return saved
  return saved && isZone(saved) ? saved : DEFAULT_CHOICE
}

/** The zone in which a time is shown, for an instrument of the exchange zone `exchange`. */
export function zoneOf(choice: string, exchange: string = MARKET_ZONE): string {
  if (choice === 'exchange') return exchange
  if (choice === 'local') return localZone()
  return isZone(choice) ? choice : exchange
}

const SHORT: Record<string, string> = {
  'America/New_York': 'ET',
  'America/Chicago': 'CT',
  UTC: 'UTC',
  'Etc/UTC': 'UTC',
}

/** The short name a time is labelled with: ET and CT for the two exchange zones, else the
 * browser's short name at that instant (EET, GMT+2). */
export function zoneName(zone: string, at: Date = new Date()): string {
  const known = SHORT[zone]
  if (known) return known
  const parts = new Intl.DateTimeFormat('en-US', { timeZone: zone, timeZoneName: 'short' })
  return parts.formatToParts(at).find((p) => p.type === 'timeZoneName')?.value ?? zone
}
