/** The texts of the status bar (BRK-06, BRK-09, SUB-07, UI-05; layout proposed at WP-0.8).
 *
 * Each part has a text and a level. The level picks a colour, and the text says the same in words,
 * so that no state is told by colour alone. Instants of the backend are compared with the time of
 * its data, which is the fake broker's own clock on the fake broker. */
import {
  AuthState,
  type RestState,
  SourceKind,
  type Source,
  type StreamState,
  StreamPhase,
  type Subscriptions,
} from '../gen/s3t/wire/v1/status_pb'
import type { ConnectionState } from './connection'
import { count, decimal, seconds, time } from './format'

export type Level = 'ok' | 'warn' | 'error' | 'quiet'

export interface Part {
  text: string
  level: Level
}

/** The browser's connection to the backend (UI-05). */
export function connectionPart(state: ConnectionState, now: number): Part {
  switch (state.phase) {
    case 'open':
      return { text: 'Backend connected', level: 'ok' }
    case 'connecting':
      return state.opened === 0
        ? { text: 'Connecting to the backend', level: 'warn' }
        : { text: `Connection lost (${state.reason}), connecting`, level: 'error' }
    case 'waiting': {
      const wait = seconds((state.retryAt ?? now) - now)
      return { text: `Connection lost (${state.reason}), retrying in ${wait}`, level: 'error' }
    }
    case 'ended':
      return { text: `Not connected: ${state.reason}`, level: 'error' }
    case 'idle':
      return { text: 'Not connected', level: 'quiet' }
  }
}

/** Whether a screen must say that the connection is lost: it was open once and is not now, or
 * it never opened. */
export function connectionLost(state: ConnectionState): boolean {
  return state.phase !== 'open' && state.phase !== 'idle'
}

const PHASES: Record<StreamPhase, string> = {
  [StreamPhase.UNSPECIFIED]: 'unknown',
  [StreamPhase.IDLE]: 'idle',
  [StreamPhase.CONNECTING]: 'connecting',
  [StreamPhase.CONNECTED]: 'connected',
  [StreamPhase.STALLED]: 'stalled',
  [StreamPhase.DISCONNECTED]: 'disconnected',
  [StreamPhase.CLOSED]: 'closed',
}

export function phaseName(phase: StreamPhase): string {
  return PHASES[phase] ?? 'unknown'
}

/** The broker's stream (BRK-06): connected, resubscribing, stalled, a gap. */
export function streamPart(stream: StreamState | undefined): Part {
  if (stream === undefined) return { text: 'Stream: no status yet', level: 'quiet' }
  const gap = stream.lastGap
    ? `, last gap ${time(stream.lastGap.sinceUs)}–${time(stream.lastGap.untilUs)} ET`
    : ''
  if (stream.stalled) {
    return {
      text: `Stream stalled: no data since ${time(stream.lastDataAtUs)} ET${gap}`,
      level: 'warn',
    }
  }
  switch (stream.phase) {
    case StreamPhase.CONNECTED: {
      if (stream.resubscribing)
        return { text: `Stream connected, resubscribing${gap}`, level: 'warn' }
      const every =
        stream.aggregationPeriod === undefined ? '' : `, quotes every ${stream.aggregationPeriod} s`
      return { text: `Stream connected${every}${gap}`, level: 'ok' }
    }
    case StreamPhase.IDLE:
      return { text: 'Stream idle: nothing subscribed', level: 'quiet' }
    case StreamPhase.CONNECTING:
      return { text: `Stream connecting${gap}`, level: 'warn' }
    case StreamPhase.DISCONNECTED: {
      const why = stream.reason ? `: ${stream.reason}` : ''
      return { text: `Stream disconnected${why}, reconnecting${gap}`, level: 'error' }
    }
    default:
      return { text: `Stream ${phaseName(stream.phase)}${gap}`, level: 'error' }
  }
}

/** The subscription use (SUB-07). */
export function subscriptionsPart(s: Subscriptions | undefined): Part {
  if (s === undefined) return { text: 'Subscriptions: no status yet', level: 'quiet' }
  const pending = s.pending > 0 ? `, ${count(s.pending)} pending` : ''
  const lost = s.lost > 0 ? `, ${count(s.lost)} batches lost` : ''
  const level: Level = s.lost > 0 || s.rateErrors > 0 ? 'warn' : 'ok'
  return { text: `Subscriptions ${count(s.granted)} of ${count(s.budget)}${pending}${lost}`, level }
}

const AUTH: Record<AuthState, string> = {
  [AuthState.UNSPECIFIED]: 'token unknown',
  [AuthState.NONE]: 'no token yet',
  [AuthState.VALID]: 'token valid',
  [AuthState.DUE]: 'token due: the next call renews it',
  [AuthState.LAPSED]: 'token lapsed: the next call renews it',
  [AuthState.RENEWING]: 'renewing the token',
  [AuthState.REFUSED]: 'grant refused',
}

export function authName(auth: AuthState): string {
  return AUTH[auth] ?? 'token unknown'
}

/** The REST state (BRK-09): the token, the request rate beside its limit, a hold after a 429. */
export function restPart(rest: RestState | undefined, nowUs: number | null): Part {
  if (rest === undefined) return { text: 'REST: no status yet', level: 'quiet' }
  let token = authName(rest.auth)
  if (rest.auth === AuthState.VALID && rest.renewsFromUs !== undefined) {
    token += `, renews from ${time(rest.renewsFromUs)} ET`
  }
  if (rest.auth === AuthState.REFUSED && rest.refusal) token += `: ${rest.refusal}`
  const rate = `${count(rest.requestsLastMinute)} req/min, limit ${decimal(rest.rate, 0)}/s burst ${count(rest.burst)}`
  const held =
    rest.heldUntilUs !== undefined && nowUs !== null && rest.heldUntilUs > nowUs
      ? `, held until ${time(rest.heldUntilUs)} ET after a 429`
      : ''
  const level: Level =
    rest.auth === AuthState.REFUSED
      ? 'error'
      : held || rest.auth === AuthState.RENEWING
        ? 'warn'
        : 'ok'
  return { text: `REST: ${token} · ${rate}${held}`, level }
}

/** The permanent mark of GEN-11 as the backend states it ("SYNTHETIC DATA", "REPLAY 2026-11-20"),
 * or null on the live broker. */
export function sourceMark(source: Source | undefined): string | null {
  if (source === undefined || source.kind === SourceKind.LIVE) return null
  return source.mark || 'NOT LIVE'
}
