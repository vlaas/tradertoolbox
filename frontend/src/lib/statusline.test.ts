// The status bar's texts (BRK-06, BRK-09, SUB-07, GEN-11, UI-05): each state in words.
import { create } from '@bufbuild/protobuf'
import { describe, expect, it } from 'vitest'
import {
  AuthState,
  RestStateSchema,
  SourceKind,
  SourceSchema,
  StreamPhase,
  StreamStateSchema,
  SubscriptionsSchema,
} from '../gen/s3t/wire/v1/status_pb'
import type { ConnectionState } from './connection'
import {
  connectionLost,
  connectionPart,
  restPart,
  sourceMark,
  streamPart,
  subscriptionsPart,
} from './statusline'

// 2026-11-20 15:00:00 UTC, 10:00:00 New York.
const T = Date.UTC(2026, 10, 20, 15, 0, 0) * 1000
const S = 1_000_000

const state = (patch: Partial<ConnectionState>): ConnectionState => ({
  phase: 'open',
  since: 0,
  reason: '',
  retryAt: null,
  opened: 1,
  dataTimeUs: null,
  ...patch,
})

describe('the connection to the backend', () => {
  it('says when it is lost, why, and when it is tried again', () => {
    expect(connectionPart(state({}), 0)).toEqual({ text: 'Backend connected', level: 'ok' })
    const waiting = state({ phase: 'waiting', reason: 'the backend restarts', retryAt: 4000 })
    expect(connectionPart(waiting, 1000)).toEqual({
      text: 'Connection lost (the backend restarts), retrying in 3 s',
      level: 'error',
    })
    expect(connectionPart(state({ phase: 'connecting', opened: 0 }), 0).text).toBe(
      'Connecting to the backend',
    )
    expect(connectionPart(state({ phase: 'connecting', reason: 'x' }), 0).text).toBe(
      'Connection lost (x), connecting',
    )
  })

  it('is lost for a screen in every phase but open and idle', () => {
    const lost = (['open', 'idle', 'connecting', 'waiting', 'ended'] as const).map((phase) =>
      connectionLost(state({ phase })),
    )
    expect(lost).toEqual([false, false, true, true, true])
  })
})

describe('the stream', () => {
  it('states its phase, the aggregation period, resubscribing, a stall and the last gap', () => {
    const connected = create(StreamStateSchema, {
      phase: StreamPhase.CONNECTED,
      aggregationPeriod: 0.5,
    })
    expect(streamPart(connected)).toEqual({
      text: 'Stream connected, quotes every 0.5 s',
      level: 'ok',
    })
    const after = create(StreamStateSchema, {
      phase: StreamPhase.CONNECTED,
      resubscribing: true,
      lastGap: { sinceUs: T, untilUs: T + 2 * S, reason: 'lost' },
    })
    expect(streamPart(after)).toEqual({
      text: 'Stream connected, resubscribing, last gap 10:00:00–10:00:02 ET',
      level: 'warn',
    })
    const stalled = create(StreamStateSchema, {
      phase: StreamPhase.CONNECTING,
      stalled: true,
      lastDataAtUs: T,
    })
    expect(streamPart(stalled)).toEqual({
      text: 'Stream stalled: no data since 10:00:00 ET',
      level: 'warn',
    })
    const down = create(StreamStateSchema, {
      phase: StreamPhase.DISCONNECTED,
      reason: 'closed by the server',
    })
    expect(streamPart(down)).toEqual({
      text: 'Stream disconnected: closed by the server, reconnecting',
      level: 'error',
    })
    expect(streamPart(undefined).level).toBe('quiet')
  })
})

describe('the subscriptions', () => {
  it('are the granted beside the budget, with what is pending or lost', () => {
    expect(
      subscriptionsPart(create(SubscriptionsSchema, { granted: 1046, budget: 25000 })),
    ).toEqual({ text: 'Subscriptions 1,046 of 25,000', level: 'ok' })
    const behind = create(SubscriptionsSchema, { granted: 3, budget: 10, pending: 2, lost: 1 })
    expect(subscriptionsPart(behind)).toEqual({
      text: 'Subscriptions 3 of 10, 2 pending, 1 batches lost',
      level: 'warn',
    })
  })
})

describe('the REST state', () => {
  const rest = (patch: object) =>
    create(RestStateSchema, { rate: 2, burst: 4, requestsLastMinute: 3, ...patch })

  it('has the token with its renewal, and the rate beside the limit', () => {
    expect(restPart(rest({ auth: AuthState.VALID, renewsFromUs: T + 60 * S }), T)).toEqual({
      text: 'REST: token valid, renews from 10:01:00 ET · 3 req/min, limit 2/s burst 4',
      level: 'ok',
    })
    expect(restPart(rest({ auth: AuthState.RENEWING }), T).level).toBe('warn')
  })

  it('names a refused grant, and a hold after a 429 while it lasts', () => {
    const refused = restPart(rest({ auth: AuthState.REFUSED, refusal: 'invalid_grant' }), T)
    expect(refused).toEqual({
      text: 'REST: grant refused: invalid_grant · 3 req/min, limit 2/s burst 4',
      level: 'error',
    })
    const held = rest({ auth: AuthState.VALID, heldUntilUs: T + 30 * S })
    expect(restPart(held, T).text).toContain('held until 10:00:30 ET after a 429')
    expect(restPart(held, T).level).toBe('warn')
    expect(restPart(held, T + 31 * S).text).not.toContain('held')
  })
})

describe('the mark of GEN-11', () => {
  it('is the backend’s text off the live broker, and none on it', () => {
    const mark = (kind: SourceKind, text: string) =>
      sourceMark(create(SourceSchema, { kind, mark: text }))
    expect(mark(SourceKind.FAKE, 'SYNTHETIC DATA')).toBe('SYNTHETIC DATA')
    expect(mark(SourceKind.REPLAY, 'REPLAY 2026-11-20')).toBe('REPLAY 2026-11-20')
    expect(mark(SourceKind.LIVE, '')).toBeNull()
    expect(mark(SourceKind.FAKE, '')).toBe('NOT LIVE') // never no mark off the live broker
    expect(sourceMark(undefined)).toBeNull()
  })
})
