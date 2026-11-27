/** The texts of the status bar (BRK-06, BRK-09, SUB-07, UI-05). The order was decided 2026-11-27
 * (owner, WP-0.8 review): the browser's connection, the stream, the subscriptions, REST, the mark.
 * The bar is compact and easy to grasp: short texts, abbreviated where it can be. Each part has a
 * `title` with the whole sentence, and the information icon of the bar explains the figures.
 *
 * Each part has a level. The level picks the colour of a dot, and the text says the same in words,
 * so that no state is told by colour alone. Instants of the backend are compared with the time of
 * its data, which is the fake broker's own clock on the fake broker. Times are written in `zone`
 * (UI-12). */
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
import { count, decimal, hm, seconds, time } from './format'
import { MARKET_ZONE, zoneName } from './zones'

export type Level = 'ok' | 'warn' | 'error' | 'quiet'

export interface Part {
  /** For the bar: short. */
  text: string
  /** The whole sentence. */
  title: string
  level: Level
}

/** A gap stays in the bar this long after it ended; on the status screen it stays. */
const RECENT_GAP_US = 10 * 60 * 1_000_000

/** The browser's connection to the backend (UI-05). */
export function connectionPart(state: ConnectionState, now: number): Part {
  switch (state.phase) {
    case 'open':
      return { text: 'Backend ok', title: 'This page is connected to the backend.', level: 'ok' }
    case 'connecting':
      return state.opened === 0
        ? { text: 'Connecting', title: 'Connecting to the backend.', level: 'warn' }
        : {
            text: 'Backend lost · connecting',
            title: `Connection lost (${state.reason}); connecting again.`,
            level: 'error',
          }
    case 'waiting': {
      const wait = seconds((state.retryAt ?? now) - now)
      return {
        text: `Backend lost · retry ${wait}`,
        title: `Connection lost (${state.reason}), retrying in ${wait}.`,
        level: 'error',
      }
    }
    case 'ended':
      return { text: 'Logged out', title: `Not connected: ${state.reason}.`, level: 'error' }
    case 'idle':
      return { text: 'Not connected', title: 'Not connected.', level: 'quiet' }
  }
}

/** Whether a screen must say that the connection is lost: it is neither open nor stopped. */
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
export function streamPart(
  stream: StreamState | undefined,
  nowUs: number | null,
  zone: string = MARKET_ZONE,
): Part {
  if (stream === undefined) return { text: 'Stream ?', title: 'No status yet.', level: 'quiet' }
  const z = zoneName(zone)
  const g = stream.lastGap
  const gapTitle = g
    ? ` Last gap ${time(g.sinceUs, zone)}–${time(g.untilUs, zone)} ${z} (${g.reason}).`
    : ''
  const recent = g !== undefined && nowUs !== null && nowUs - g.untilUs < RECENT_GAP_US
  const gap = recent ? ` · gap ${seconds((g.untilUs - g.sinceUs) / 1000)}` : ''
  if (stream.stalled) {
    return {
      text: `Stream stalled${gap}`,
      title: `Stream stalled: no data since ${time(stream.lastDataAtUs, zone)} ${z}; reconnecting.${gapTitle}`,
      level: 'warn',
    }
  }
  switch (stream.phase) {
    case StreamPhase.CONNECTED: {
      const period = stream.aggregationPeriod
      const every = period === undefined ? '' : `, quotes every ${period} s`
      if (stream.resubscribing) {
        return {
          text: `Stream resubscribing${gap}`,
          title: `Stream connected${every}; sending the subscriptions again.${gapTitle}`,
          level: 'warn',
        }
      }
      return {
        text: `Stream ok${period === undefined ? '' : ` · ${period} s`}${gap}`,
        title: `Stream connected${every}.${gapTitle}`,
        level: 'ok',
      }
    }
    case StreamPhase.IDLE:
      return { text: 'Stream idle', title: 'Stream idle: nothing is subscribed.', level: 'quiet' }
    case StreamPhase.CONNECTING:
      return {
        text: `Stream connecting${gap}`,
        title: `Stream connecting.${gapTitle}`,
        level: 'warn',
      }
    case StreamPhase.DISCONNECTED:
      return {
        text: `Stream down${gap}`,
        title: `Stream disconnected${stream.reason ? `: ${stream.reason}` : ''}; reconnecting.${gapTitle}`,
        level: 'error',
      }
    default:
      return {
        text: `Stream ${phaseName(stream.phase)}`,
        title: `Stream ${phaseName(stream.phase)}.${gapTitle}`,
        level: 'error',
      }
  }
}

/** The subscription use (SUB-07). */
export function subscriptionsPart(s: Subscriptions | undefined): Part {
  if (s === undefined) return { text: 'Subs ?', title: 'No status yet.', level: 'quiet' }
  const pending = s.pending > 0 ? ` · ${count(s.pending)} pending` : ''
  const lost = s.lost > 0 ? ` · ${count(s.lost)} lost` : ''
  const level: Level = s.lost > 0 || s.rateErrors > 0 ? 'warn' : 'ok'
  return {
    text: `Subs ${count(s.granted)}/${count(s.budget)}${pending}${lost}`,
    title:
      `Subscriptions: ${count(s.granted)} of ${count(s.budget)} granted, ${count(s.pending)} ` +
      `changes pending, ${count(s.lost)} batches lost, ${count(s.rateErrors)} rate errors.`,
    level,
  }
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

const AUTH_SHORT: Record<AuthState, string> = {
  [AuthState.UNSPECIFIED]: 'token ?',
  [AuthState.NONE]: 'no token',
  [AuthState.VALID]: 'token ok',
  [AuthState.DUE]: 'token due',
  [AuthState.LAPSED]: 'token lapsed',
  [AuthState.RENEWING]: 'token renewing',
  [AuthState.REFUSED]: 'grant refused',
}

export function authName(auth: AuthState): string {
  return AUTH[auth] ?? 'token unknown'
}

/** The REST state (BRK-09): the requests of the last minute beside the limit, the token with the
 * time from which the next call renews it, a hold after a 429. */
export function restPart(
  rest: RestState | undefined,
  nowUs: number | null,
  zone: string = MARKET_ZONE,
): Part {
  if (rest === undefined) return { text: 'REST ?', title: 'No status yet.', level: 'quiet' }
  const z = zoneName(zone)
  const renews = rest.auth === AuthState.VALID && rest.renewsFromUs !== undefined
  const token = renews ? `token renews ${hm(rest.renewsFromUs, zone)}` : AUTH_SHORT[rest.auth]
  const held =
    rest.heldUntilUs !== undefined && nowUs !== null && rest.heldUntilUs > nowUs
      ? rest.heldUntilUs
      : null
  const rate = `${count(rest.requestsLastMinute)}/min ≤${decimal(rest.rate, 0)}/s`
  let title =
    `REST: ${count(rest.requestsLastMinute)} requests in the last minute; the limit is ` +
    `${decimal(rest.rate, 1)} a second, ${count(rest.burst)} at once. ${authName(rest.auth)}`
  if (renews) title += `; the next call renews it from ${time(rest.renewsFromUs, zone)} ${z}`
  if (rest.auth === AuthState.REFUSED && rest.refusal) title += `: ${rest.refusal}`
  title += '.'
  if (held !== null) title += ` Every request is held until ${time(held, zone)} ${z} after a 429.`
  const level: Level =
    rest.auth === AuthState.REFUSED
      ? 'error'
      : held !== null || rest.auth === AuthState.RENEWING
        ? 'warn'
        : 'ok'
  return {
    text: `REST ${rate} · ${token}${held === null ? '' : ` · held ${hm(held, zone)} (429)`}`,
    title,
    level,
  }
}

/** The permanent mark of GEN-11 as the backend states it ("SYNTHETIC DATA", "REPLAY 2026-11-20"),
 * or null on the live broker. */
export function sourceMark(source: Source | undefined): string | null {
  if (source === undefined || source.kind === SourceKind.LIVE) return null
  return source.mark || 'NOT LIVE'
}
