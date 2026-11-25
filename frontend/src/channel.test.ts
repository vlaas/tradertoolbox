// The messages of the connection across the two languages (API-04): channel.proto and
// status.proto. The fixtures were written by the Python code (backend/tools/generate.py); this test
// decodes them against values stated here by hand, and encodes the same bytes and JSON. The client's
// message goes the other way in use: TypeScript writes it and Python reads it.
import {
  create,
  type DescMessage,
  fromBinary,
  fromJson,
  type JsonValue,
  type MessageInitShape,
  toBinary,
  toJson,
} from '@bufbuild/protobuf'
import { describe, expect, it } from 'vitest'
import quotesFixture from '../../proto/fixtures/quotes.json'
import statusFixture from '../../proto/fixtures/status.json'
import subscribeFixture from '../../proto/fixtures/subscribe.json'
import { ClientMessageSchema, NoteKind, ServerMessageSchema } from './gen/s3t/wire/v1/channel_pb'
import { AuthState, DemandClass, SourceKind, StreamPhase } from './gen/s3t/wire/v1/status_pb'
import { cell, rowCount } from './lib/columns'

const nan = Number.NaN

const subscribe: MessageInitShape<typeof ClientMessageSchema> = {
  body: {
    case: 'subscribe',
    value: { id: 7, topic: { case: 'quotes', value: { symbol: ['SPY', 'SPX'] } } },
  },
}

const quotes: MessageInitShape<typeof ServerMessageSchema> = {
  id: 7,
  snapshot: true,
  body: {
    case: 'quotes',
    value: {
      rows: {
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
      },
      notes: { note: [{ symbol: 'NOSUCH', kind: NoteKind.UNKNOWN, text: 'not known' }] },
    },
  },
}

const status: MessageInitShape<typeof ServerMessageSchema> = {
  id: 1,
  body: {
    case: 'status',
    value: {
      source: { kind: SourceKind.FAKE, mark: 'SYNTHETIC DATA' },
      dataTimeUs: 1795186800000001,
      account: 'main',
      stream: {
        phase: StreamPhase.CONNECTED,
        sinceUs: 1795186800000000,
        reconnects: 2,
        resubscribing: true,
        lastGap: { sinceUs: 1, untilUs: 2, reason: 'lost' },
        aggregationPeriod: 0.5,
      },
      subscriptions: {
        granted: 3,
        budget: 10000,
        classes: [{ demandClass: DemandClass.VIEWS, wanted: 4, granted: 3, counted: 3 }],
      },
      rest: { auth: AuthState.VALID, rate: 2, burst: 4, heldUntilUs: 0 },
    },
  },
}

function bytes(hex: string): Uint8Array {
  return Uint8Array.from(hex.match(/../g) ?? [], (b) => Number.parseInt(b, 16))
}

function hex(data: Uint8Array): string {
  return Array.from(data, (b) => b.toString(16).padStart(2, '0')).join('')
}

interface Fixture {
  binary_hex: string
  json: unknown
}

function sameInBothLanguages<T extends DescMessage>(
  schema: T,
  fixture: Fixture,
  values: MessageInitShape<T>,
): void {
  const expected = create(schema, values)
  // vitest takes NaN as equal to NaN.
  expect(fromBinary(schema, bytes(fixture.binary_hex))).toEqual(expected)
  expect(fromJson(schema, fixture.json as JsonValue)).toEqual(expected)
  expect(hex(toBinary(schema, expected))).toBe(fixture.binary_hex)
  expect(toJson(schema, expected)).toEqual(fixture.json)
}

describe('the messages of the connection', () => {
  it('a subscription, as a page writes it', () => {
    sameInBothLanguages(ClientMessageSchema, subscribeFixture, subscribe)
  })

  it('a snapshot of quotes', () => {
    sameInBothLanguages(ServerMessageSchema, quotesFixture, quotes)
  })

  it('a status', () => {
    sameInBothLanguages(ServerMessageSchema, statusFixture, status)
  })

  it('keeps what is missing apart from what is zero', () => {
    const message = fromBinary(ServerMessageSchema, bytes(statusFixture.binary_hex))
    if (message.body.case !== 'status') throw new Error('not a status')
    expect(message.body.value.liveSinceUs).toBeUndefined()
    expect(message.body.value.rest?.heldUntilUs).toBe(0)
    expect(message.body.value.stream?.lastDataAtUs).toBeUndefined()
  })

  it('has the rows of market.proto inside', () => {
    const message = fromBinary(ServerMessageSchema, bytes(quotesFixture.binary_hex))
    if (message.body.case !== 'quotes' || !message.body.value.rows) throw new Error('no rows')
    expect(rowCount(message.body.value.rows)).toBe(2)
    expect(cell(message.body.value.rows.bid, 0)).toBe(0)
    expect(cell(message.body.value.rows.bid, 1)).toBeNull()
  })
})
