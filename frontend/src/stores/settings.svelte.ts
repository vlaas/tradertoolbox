/** The person's choices for the screens, kept in this browser (local storage; every window of
 * the browser shares them). Now one: which time the screens show (UI-12). */
import { choiceOf, MARKET_ZONE, zoneOf } from '../lib/zones'

const TIMES_KEY = 's3t.times'

function saved(): string {
  try {
    return choiceOf(localStorage.getItem(TIMES_KEY))
  } catch {
    return choiceOf(null)
  }
}

class Settings {
  /** `exchange`, `local`, or the name of a zone. */
  times = $state(saved())
}

export const settings = new Settings()

export function setTimes(choice: string): void {
  settings.times = choiceOf(choice)
  try {
    localStorage.setItem(TIMES_KEY, settings.times)
  } catch {
    // kept for this page only
  }
}

/** The zone of a time of an instrument whose exchange zone is `exchange`; the times of the
 * backend that belong to no instrument take the market's zone. */
export function shownZone(exchange: string = MARKET_ZONE): string {
  return zoneOf(settings.times, exchange)
}
