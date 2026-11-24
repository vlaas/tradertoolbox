// The column rules of market.proto across the two languages (MKT-07, GEN-10). The fixture was
// written by the Python code (backend/tools/generate.py); this test decodes it against values
// stated here by hand.
import { create, fromBinary, fromJson, type JsonValue, toBinary, toJson } from '@bufbuild/protobuf'
import { describe, expect, it } from 'vitest'
import fixture from '../../proto/fixtures/quoterows.json'
import { type QuoteRows, QuoteRowsSchema } from './gen/s3t/wire/v1/market_pb'
import { cell, instant, rowCount } from './lib/columns'

const nan = Number.NaN
const expected = create(QuoteRowsSchema, {
  symbol: ['SPY', 'SPX'],
  bid: [0, nan],
  ask: [0.05, nan],
  bidSize: [12, nan],
  askSize: [0, nan],
  last: [nan, 6000.25],
  lastSize: [nan, nan],
  dayVolume: [0, nan],
  prevClose: [599.5, 5990],
  quoteReceivedUs: [1795186800000001, nan],
  tradeReceivedUs: [nan, 1795186800500000],
})

function bytes(hex: string): Uint8Array {
  return Uint8Array.from(hex.match(/../g) ?? [], (b) => Number.parseInt(b, 16))
}

const decoded: [string, QuoteRows][] = [
  ['binary', fromBinary(QuoteRowsSchema, bytes(fixture.binary_hex))],
  ['JSON', fromJson(QuoteRowsSchema, fixture.json as JsonValue)],
]

describe.each(decoded)('QuoteRows written by Python, from %s', (_, rows) => {
  it('holds the stated values', () => {
    expect(rows).toEqual(expected) // vitest takes NaN as equal to NaN and 0 as not missing
    expect(rowCount(rows)).toBe(2)
  })

  it('keeps zero and missing apart', () => {
    expect(cell(rows.bid, 0)).toBe(0)
    expect(cell(rows.bid, 1)).toBeNull()
    expect(cell(rows.askSize, 0)).toBe(0)
    expect(cell(rows.last, 0)).toBeNull()
    expect(cell(rows.dayVolume, 0)).toBe(0)
  })

  it('keeps an instant to the microsecond, as a plain number', () => {
    expect(cell(rows.quoteReceivedUs, 0)).toBe(1795186800000001)
    expect(instant(rows.tradeReceivedUs, 1)?.toISOString()).toBe('2026-11-20T15:00:00.500Z')
    expect(instant(rows.quoteReceivedUs, 1)).toBeNull()
  })
})

describe('the column rules', () => {
  it('encode to the same bytes and JSON as Python', () => {
    const hex = Array.from(toBinary(QuoteRowsSchema, expected), (b) =>
      b.toString(16).padStart(2, '0'),
    ).join('')
    expect(hex).toBe(fixture.binary_hex)
    expect(toJson(QuoteRowsSchema, expected)).toEqual(fixture.json)
  })

  it('refuse columns of different lengths', () => {
    expect(() => rowCount(create(QuoteRowsSchema, { symbol: ['SPY'], bid: [] }))).toThrow()
    expect(rowCount(create(QuoteRowsSchema))).toBe(0)
  })

  it('take a row that is not there as missing', () => {
    expect(cell([], 0)).toBeNull()
    expect(cell([Number.POSITIVE_INFINITY], 0)).toBe(Number.POSITIVE_INFINITY)
  })
})
