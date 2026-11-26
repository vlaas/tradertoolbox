// The rows of a quotes subscription: a snapshot replaces, a change merges whole rows (API-03).
import { create } from '@bufbuild/protobuf'
import { describe, expect, it } from 'vitest'
import { NoteKind, type Quotes, QuotesSchema } from '../gen/s3t/wire/v1/channel_pb'
import { apply, EMPTY, symbolsOf } from './quoterows'

const nan = Number.NaN

function message(symbols: string[], bid: number[], notes?: string[]): Quotes {
  const n = symbols.length
  const empty = () => Array<number>(n).fill(nan)
  return create(QuotesSchema, {
    rows: {
      symbol: symbols,
      bid,
      ask: empty(),
      bidSize: empty(),
      askSize: empty(),
      last: empty(),
      lastSize: empty(),
      dayVolume: empty(),
      prevClose: empty(),
      quoteReceivedUs: bid.map((b, i) => (Number.isNaN(b) ? nan : 1000 + i)),
      tradeReceivedUs: empty(),
    },
    notes:
      notes === undefined
        ? undefined
        : { note: notes.map((symbol) => ({ symbol, kind: NoteKind.UNKNOWN, text: 'not known' })) },
  })
}

describe('the quotes view', () => {
  it('takes a snapshot whole, in its order, with missing values as null', () => {
    const view = apply(EMPTY, message(['SPY', 'SPX'], [600.5, nan], ['NOSUCH']), true)
    expect(view.symbols).toEqual(['SPY', 'SPX'])
    expect(view.rows.get('SPY')?.bid).toBe(600.5)
    expect(view.rows.get('SPX')?.bid).toBeNull()
    expect(view.rows.get('SPX')?.quoteReceivedUs).toBeNull()
    expect(view.notes.map((n) => n.symbol)).toEqual(['NOSUCH'])
  })

  it('merges a change row by row, and keeps the notes when it has none', () => {
    const first = apply(EMPTY, message(['SPY', 'SPX'], [600.5, 6000], ['NOSUCH']), true)
    const next = apply(first, message(['SPX'], [6001]), false)
    expect(next.symbols).toEqual(['SPY', 'SPX'])
    expect(next.rows.get('SPY')?.bid).toBe(600.5)
    expect(next.rows.get('SPX')?.bid).toBe(6001)
    expect(next.notes.map((n) => n.symbol)).toEqual(['NOSUCH'])
    expect(first.rows.get('SPX')?.bid).toBe(6000) // the old view is not changed
  })

  it('replaces the notes when a change has them, and a snapshot replaces everything', () => {
    const first = apply(EMPTY, message(['SPY'], [600.5], ['NOSUCH']), true)
    expect(apply(first, message([], [], []), false).notes).toEqual([])
    const again = apply(first, message(['AAPL'], [230]), true)
    expect(again.symbols).toEqual(['AAPL'])
    expect(again.rows.has('SPY')).toBe(false)
    expect(again.notes).toEqual([])
  })

  it('refuses columns of different lengths', () => {
    const bad = create(QuotesSchema, { rows: { symbol: ['SPY'], bid: [] } })
    expect(() => apply(EMPTY, bad, true)).toThrow()
  })
})

describe('a typed list of symbols', () => {
  it('is split, upper-cased, each once, and cut at the most', () => {
    expect(symbolsOf(' spy, SPX  aapl,,spy ')).toEqual(['SPY', 'SPX', 'AAPL'])
    expect(symbolsOf('')).toEqual([])
    expect(symbolsOf('A B C', 2)).toEqual(['A', 'B'])
  })
})
