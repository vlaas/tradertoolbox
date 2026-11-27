// The status bar's texts (BRK-06, BRK-09, SUB-07, GEN-11, UI-05): short in the bar, the whole
// sentence in the title, each state in words, times in the chosen zone (UI-12).
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
const MINUTE = 60 * S

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
    expect(connectionPart(state({}), 0)).toMatchObject({ text: 'Backend ok', level: 'ok' })
    const waiting = state({ phase: 'waiting', reason: 'the backend restarts', retryAt: 4000 })
    expect(connectionPart(waiting, 1000)).toEqual({
      text: 'Backend lost · retry 3 s',
      title: 'Connection lost (the backend restarts), retrying in 3 s.',
      level: 'error',
    })
    expect(connectionPart(state({ phase: 'connecting', opened: 0 }), 0).text).toBe('Connecting')
    expect(connectionPart(state({ phase: 'connecting', reason: 'x' }), 0)).toMatchObject({
      text: 'Backend lost · connecting',
      title: 'Connection lost (x); connecting again.',
    })
  })

  it('is lost for a screen in every phase but open and idle', () => {
    const lost = (['open', 'idle', 'connecting', 'waiting', 'ended'] as const).map((phase) =>
      connectionLost(state({ phase })),
    )
    expect(lost).toEqual([false, false, true, true, true])
  })
})

describe('the stream', () => {
  const gap = { sinceUs: T, untilUs: T + 2 * S, reason: 'lost' }

  it('is short in the bar and whole in its title', () => {
    const connected = create(StreamStateSchema, {
      phase: StreamPhase.CONNECTED,
      aggregationPeriod: 0.5,
    })
    expect(streamPart(connected, T)).toEqual({
      text: 'Stream ok · 0.5 s',
      title: 'Stream connected, quotes every 0.5 s.',
      level: 'ok',
    })
  })

  it('shows a gap for ten minutes after it, and keeps it in the title', () => {
    const after = create(StreamStateSchema, {
      phase: StreamPhase.CONNECTED,
      resubscribing: true,
      lastGap: gap,
    })
    expect(streamPart(after, T + 5 * MINUTE)).toEqual({
      text: 'Stream resubscribing · gap 2 s',
      title:
        'Stream connected; sending the subscriptions again. Last gap 10:00:00–10:00:02 ET (lost).',
      level: 'warn',
    })
    const later = streamPart(after, T + 11 * MINUTE)
    expect(later.text).toBe('Stream resubscribing')
    expect(later.title).toContain('Last gap 10:00:00–10:00:02 ET')
  })

  it('names a stall and a disconnection, in the chosen zone', () => {
    const stalled = create(StreamStateSchema, {
      phase: StreamPhase.CONNECTING,
      stalled: true,
      lastDataAtUs: T,
    })
    expect(streamPart(stalled, T, 'UTC')).toEqual({
      text: 'Stream stalled',
      title: 'Stream stalled: no data since 15:00:00 UTC; reconnecting.',
      level: 'warn',
    })
    const down = create(StreamStateSchema, {
      phase: StreamPhase.DISCONNECTED,
      reason: 'closed by the server',
    })
    expect(streamPart(down, T)).toMatchObject({
      text: 'Stream down',
      title: 'Stream disconnected: closed by the server; reconnecting.',
      level: 'error',
    })
    expect(streamPart(undefined, T).level).toBe('quiet')
  })
})

describe('the subscriptions', () => {
  it('are the granted of the budget, with what is pending or lost', () => {
    expect(
      subscriptionsPart(create(SubscriptionsSchema, { granted: 1046, budget: 25000 })),
    ).toEqual({
      text: 'Subs 1,046/25,000',
      title:
        'Subscriptions: 1,046 of 25,000 granted, 0 changes pending, 0 batches lost, 0 rate errors.',
      level: 'ok',
    })
    const behind = create(SubscriptionsSchema, { granted: 3, budget: 10, pending: 2, lost: 1 })
    expect(subscriptionsPart(behind)).toMatchObject({
      text: 'Subs 3/10 · 2 pending · 1 lost',
      level: 'warn',
    })
  })
})

describe('the REST state', () => {
  const rest = (patch: object) =>
    create(RestStateSchema, { rate: 2, burst: 4, requestsLastMinute: 3, ...patch })

  it('has the requests beside the limit, and the time from which the token is renewed', () => {
    const valid = restPart(rest({ auth: AuthState.VALID, renewsFromUs: T + 60 * S }), T)
    expect(valid).toEqual({
      text: 'REST 3/min ≤2/s · token renews 10:01',
      title:
        'REST: 3 requests in the last minute; the limit is 2.0 a second, 4 at once. token valid; ' +
        'the next call renews it from 10:01:00 ET.',
      level: 'ok',
    })
    expect(restPart(rest({ auth: AuthState.RENEWING }), T)).toMatchObject({
      text: 'REST 3/min ≤2/s · token renewing',
      level: 'warn',
    })
    const inUtc = restPart(rest({ auth: AuthState.VALID, renewsFromUs: T + 60 * S }), T, 'UTC')
    expect(inUtc.text).toBe('REST 3/min ≤2/s · token renews 15:01')
  })

  it('names a refused grant, and a hold after a 429 while it lasts', () => {
    const refused = restPart(rest({ auth: AuthState.REFUSED, refusal: 'invalid_grant' }), T)
    expect(refused).toMatchObject({ text: 'REST 3/min ≤2/s · grant refused', level: 'error' })
    expect(refused.title).toContain('grant refused: invalid_grant.')
    const held = rest({ auth: AuthState.VALID, heldUntilUs: T + 30 * S })
    expect(restPart(held, T)).toMatchObject({
      text: 'REST 3/min ≤2/s · token ok · held 10:00 (429)',
      level: 'warn',
    })
    expect(restPart(held, T).title).toContain('held until 10:00:30 ET after a 429')
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
