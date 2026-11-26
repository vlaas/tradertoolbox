/** The rows of a `quotes` subscription, as its messages build them (API-03; channel.proto).
 *
 * A snapshot replaces everything. A change carries the rows that changed, each of them whole, and
 * leaves the others. Notes replace the notes before them; unset notes leave them, except in a
 * snapshot, which replaces everything. The order of the rows is the order of the snapshot. */
import type { Note, Quotes } from '../gen/s3t/wire/v1/channel_pb'
import { cell, rowCount } from './columns'

export interface QuoteRow {
  symbol: string
  bid: number | null
  ask: number | null
  bidSize: number | null
  askSize: number | null
  last: number | null
  lastSize: number | null
  dayVolume: number | null
  prevClose: number | null
  quoteReceivedUs: number | null
  tradeReceivedUs: number | null
}

export interface QuotesView {
  symbols: readonly string[]
  rows: ReadonlyMap<string, QuoteRow>
  notes: readonly Note[]
}

export const EMPTY: QuotesView = { symbols: [], rows: new Map(), notes: [] }

/** The view after one message. Throws when the columns of the message differ in length. */
export function apply(view: QuotesView, message: Quotes, snapshot: boolean): QuotesView {
  const base = snapshot ? EMPTY : view
  const rows = new Map(base.rows)
  const symbols = snapshot ? [] : [...base.symbols]
  const r = message.rows
  if (r !== undefined) {
    const n = rowCount(r)
    for (let i = 0; i < n; i++) {
      const symbol = r.symbol[i]
      if (!rows.has(symbol) && !symbols.includes(symbol)) symbols.push(symbol)
      rows.set(symbol, {
        symbol,
        bid: cell(r.bid, i),
        ask: cell(r.ask, i),
        bidSize: cell(r.bidSize, i),
        askSize: cell(r.askSize, i),
        last: cell(r.last, i),
        lastSize: cell(r.lastSize, i),
        dayVolume: cell(r.dayVolume, i),
        prevClose: cell(r.prevClose, i),
        quoteReceivedUs: cell(r.quoteReceivedUs, i),
        tradeReceivedUs: cell(r.tradeReceivedUs, i),
      })
    }
  }
  const notes = message.notes !== undefined ? [...message.notes.note] : base.notes
  return { symbols, rows, notes }
}

/** The symbols of a list as a person types them: split at commas and spaces, upper case, each
 * once, at most `most`. */
export function symbolsOf(text: string, most = 100): string[] {
  const seen = new Set<string>()
  for (const part of text.split(/[\s,]+/)) {
    const symbol = part.trim().toUpperCase()
    if (symbol) seen.add(symbol)
  }
  return [...seen].slice(0, most)
}
