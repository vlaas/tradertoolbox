/** What is live (UI-05; decided 2026-11-27, requirements section 3, "Live or not").
 *
 * A row carries no mark. A quote or a trade is live when it was received at the `status`'s
 * `live_since_us` or later, and only while the connection is open and that `status` came on it.
 * Without a connection nothing is live, whatever the last `status` said. `prev_close` is a value
 * of the day and is not judged. */

export interface LiveBasis {
  /** The connection is open. */
  open: boolean
  /** `live_since_us` of the `status` received on the current connection; null when there is no
   * such `status` or it has none. */
  liveSinceUs: number | null
}

export const NOTHING_LIVE: LiveBasis = { open: false, liveSinceUs: null }

/** Whether a value received at `receivedUs` (null: there is none) is live. */
export function isLive(receivedUs: number | null, basis: LiveBasis): boolean {
  return (
    basis.open &&
    basis.liveSinceUs !== null &&
    receivedUs !== null &&
    receivedUs >= basis.liveSinceUs
  )
}

/** A row's state: `none` when it has no received value, `live` when every received value is
 * live, `stale` otherwise. */
export type RowState = 'none' | 'live' | 'stale'

export function rowState(received: readonly (number | null)[], basis: LiveBasis): RowState {
  const there = received.filter((us): us is number => us !== null)
  if (there.length === 0) return 'none'
  return there.every((us) => isLive(us, basis)) ? 'live' : 'stale'
}
