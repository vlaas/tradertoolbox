/** Reading the columns of a wire message by the rules of market.proto: a missing number is NaN
 * on the wire and null in the app, zero is a value, and an instant is microseconds since 1970 in
 * UTC. Every view reads a column through `cell`, so no view can show a missing bid as zero. */

/** The number at row `i`, or null when it is missing. A row that is not there is missing. */
export function cell(column: readonly number[], i: number): number | null {
  const value = column[i]
  return value === undefined || Number.isNaN(value) ? null : value
}

/** The instant at row `i`, or null when it is missing. A Date holds milliseconds: use `cell`
 * where the microseconds matter. */
export function instant(column: readonly number[], i: number): Date | null {
  const micros = cell(column, i)
  return micros === null ? null : new Date(micros / 1000)
}

/** The number of rows of a message: the length that all its columns share. Throws when they
 * differ, because then no row can be trusted. */
export function rowCount(message: object): number {
  let count: number | null = null
  for (const value of Object.values(message)) {
    if (!Array.isArray(value)) continue
    if (count !== null && value.length !== count) throw new Error('columns of different lengths')
    count = value.length
  }
  return count ?? 0
}
