/** The one connection of the app to the backend (UI-03, API-03, API-04; API-08 on this side).
 *
 * Every live value of the app comes through here. A store says which topic it wants and gets that
 * topic's messages; it never sees the WebSocket. What carries the messages is behind `Carrier`,
 * so another carrier would replace `webSocket` below and nothing else.
 *
 * The rules (backend `s3t.api.channel`; plan section 8, WP-0.7 notes; proposals of WP-0.8):
 *
 * - A subscription has a number that this side chooses. The numbers rise and are never used
 *   again. A message of a number that is not current is ignored.
 * - After a reconnect every topic is subscribed again under a new number, and its first message
 *   is a snapshot. To change a topic, subscribe under a new number first, then end the old one.
 * - The backend sends a heartbeat every 5 s. A browser is not told when its network has gone, so
 *   silence for 15 s is a loss.
 * - After a loss the next attempt waits 0.5 s, doubling to 8 s. The wait starts again at 0.5 s
 *   once a connection has brought a message.
 * - `Bye` with `SESSION_ENDED` ends the connection for good: the login screen, with the reason.
 *   A handshake that is refused asks the session: none means the login screen; the backend not
 *   answering means trying again; a session that is there twice in a row means the backend speaks
 *   another version of the messages, and the page reloads.
 * - `Refused` ends one subscription and says why (GEN-04 when the data is not there).
 */
import { create, fromBinary, fromJsonString, toBinary, toJsonString } from '@bufbuild/protobuf'
import {
  ByeKind,
  type ClientMessage,
  ClientMessageSchema,
  type Refused,
  type ServerMessage,
  ServerMessageSchema,
  type Subscribe,
} from '../gen/s3t/wire/v1/channel_pb'

export const PROTOBUF = 's3t.v1.protobuf'
export const JSON_FORM = 's3t.v1.json'
export const SILENCE_MS = 15_000
export const FIRST_WAIT_MS = 500
export const LONGEST_WAIT_MS = 8_000
/** The close code of a backend that stops or restarts. */
export const RESTART_CODE = 1012
/** The close codes this side uses: a silent connection, and a message that cannot be read. */
export const SILENT_CODE = 4000
export const UNREADABLE_CODE = 4001

/** What a subscription asks for: the `topic` of `Subscribe`. */
export type Topic = Exclude<Subscribe['topic'], { case: undefined }>

export type Wire = 'protobuf' | 'json'

/** What a carrier tells the connection. */
export interface CarrierEvents {
  open(): void
  message(data: ArrayBuffer | string): void
  close(code: number, reason: string): void
}

/** One opened carrier: it moves whole messages. */
export interface Carrier {
  send(data: Uint8Array | string): void
  close(code: number, reason: string): void
}

export type OpenCarrier = (protocol: string, events: CarrierEvents) => Carrier

/** The answer of `GET /api/session`: there is a session, there is none, or no answer. */
export type SessionProbe = () => Promise<'in' | 'out' | 'unreachable'>

export interface Listener {
  /** A message of this subscription. One with `snapshot` replaces everything said before. */
  message(message: ServerMessage): void
  /** The backend ended the subscription. It is asked again after the next reconnect. */
  refused?(refused: Refused): void
  /** The connection is gone; what this subscription said is no longer current. */
  lost?(): void
}

export interface Handle {
  /** Ask for something else: subscribe anew, then end the old subscription. */
  change(topic: Topic): void
  close(): void
}

/**
 * - `idle`: not started, or stopped.
 * - `connecting`: a carrier is opening.
 * - `open`: open; the subscriptions are sent.
 * - `waiting`: lost; the next attempt is at `retryAt`.
 * - `ended`: the login session has ended; nothing is tried until `start`.
 */
export type Phase = 'idle' | 'connecting' | 'open' | 'waiting' | 'ended'

export interface ConnectionState {
  phase: Phase
  /** Since when, in milliseconds of `Date.now`. */
  since: number
  /** Why the last connection ended, for people. Empty before the first loss. */
  reason: string
  /** When the next attempt starts, while `waiting`. */
  retryAt: number | null
  /** Connections that were open so far. */
  opened: number
  /** The time of the data, from the last heartbeat. */
  dataTimeUs: number | null
}

export interface Options {
  open: OpenCarrier
  session: SessionProbe
  wire?: Wire
  /** Every change of the state. */
  changed?: (state: ConnectionState) => void
  /** The login session has ended. */
  ended?: (reason: string) => void
  /** The backend speaks another version of the messages. */
  reload?: () => void
}

interface Entry {
  topic: Topic
  listener: Listener
  /** The number on the current connection, or null when it is not subscribed there. */
  id: number | null
}

interface Current {
  carrier: Carrier | null
  open: boolean
  heard: boolean
  bye: { kind: ByeKind; reason: string } | null
}

export class Connection {
  state: ConnectionState = {
    phase: 'idle',
    since: Date.now(),
    reason: '',
    retryAt: null,
    opened: 0,
    dataTimeUs: null,
  }

  private readonly options: Options
  private readonly wire: Wire
  private readonly entries = new Set<Entry>()
  private readonly byId = new Map<number, Entry>()
  private nextId = 1
  private wait = FIRST_WAIT_MS
  private refusedInRow = 0
  private current: Current | null = null
  private silence: ReturnType<typeof setTimeout> | undefined
  private retry: ReturnType<typeof setTimeout> | undefined

  constructor(options: Options) {
    this.options = options
    this.wire = options.wire ?? 'protobuf'
  }

  /** Connect, and keep connecting after a loss. */
  start(): void {
    if (this.state.phase !== 'idle' && this.state.phase !== 'ended') return
    this.wait = FIRST_WAIT_MS
    this.refusedInRow = 0
    this.connect()
  }

  /** Close the connection and stop. The subscriptions stay for the next `start`. */
  stop(): void {
    clearTimeout(this.retry)
    this.drop({ code: 1000, reason: 'closed by the page' })
    this.update({ phase: 'idle', retryAt: null })
  }

  subscribe(topic: Topic, listener: Listener): Handle {
    const entry: Entry = { topic, listener, id: null }
    this.entries.add(entry)
    if (this.state.phase === 'open') this.send(this.subscription(entry))
    return {
      change: (next) => this.change(entry, next),
      close: () => this.unsubscribe(entry),
    }
  }

  // ------------------------------------------------------------------ subscriptions

  private subscription(entry: Entry): ClientMessage {
    const id = this.nextId++
    entry.id = id
    this.byId.set(id, entry)
    return create(ClientMessageSchema, {
      body: { case: 'subscribe', value: { id, topic: entry.topic } },
    })
  }

  private end(entry: Entry): void {
    if (entry.id === null) return
    const id = entry.id
    this.byId.delete(id)
    entry.id = null
    this.send(create(ClientMessageSchema, { body: { case: 'unsubscribe', value: { id } } }))
  }

  private change(entry: Entry, topic: Topic): void {
    if (!this.entries.has(entry)) return
    entry.topic = topic
    if (this.state.phase !== 'open') return
    const old = entry.id
    this.send(this.subscription(entry))
    if (old !== null) {
      this.byId.delete(old)
      this.send(create(ClientMessageSchema, { body: { case: 'unsubscribe', value: { id: old } } }))
    }
  }

  private unsubscribe(entry: Entry): void {
    if (!this.entries.delete(entry)) return
    if (this.state.phase === 'open') this.end(entry)
  }

  // ------------------------------------------------------------------ the carrier

  private connect(): void {
    const current: Current = { carrier: null, open: false, heard: false, bye: null }
    this.current = current
    this.update({ phase: 'connecting', retryAt: null })
    const protocol = this.wire === 'json' ? JSON_FORM : PROTOBUF
    const mine = () => this.current === current
    current.carrier = this.options.open(protocol, {
      open: () => mine() && this.opened(current),
      message: (data) => mine() && this.received(current, data),
      close: (code, reason) => mine() && this.closed(current, code, reason),
    })
  }

  private opened(current: Current): void {
    current.open = true
    this.update({ phase: 'open', opened: this.state.opened + 1 })
    this.listen()
    for (const entry of this.entries) this.send(this.subscription(entry))
  }

  private received(current: Current, data: ArrayBuffer | string): void {
    let message: ServerMessage
    try {
      message =
        typeof data === 'string'
          ? fromJsonString(ServerMessageSchema, data, { ignoreUnknownFields: true })
          : fromBinary(ServerMessageSchema, new Uint8Array(data))
    } catch (e) {
      console.error('a message of the backend cannot be read', e)
      this.lose('a message of the backend could not be read', UNREADABLE_CODE)
      return
    }
    this.listen()
    if (!current.heard) {
      current.heard = true
      this.wait = FIRST_WAIT_MS
      this.refusedInRow = 0
    }
    const body = message.body
    if (message.id === 0) {
      if (body.case === 'heartbeat') this.update({ dataTimeUs: body.value.dataTimeUs })
      else if (body.case === 'bye')
        current.bye = { kind: body.value.kind, reason: body.value.reason }
      return
    }
    const entry = this.byId.get(message.id)
    if (entry === undefined) return
    if (body.case === 'refused') {
      this.byId.delete(message.id)
      entry.id = null
      entry.listener.refused?.(body.value)
      return
    }
    entry.listener.message(message)
  }

  private closed(current: Current, code: number, reason: string): void {
    const wasOpen = current.open
    const bye = current.bye
    this.drop()
    if (bye?.kind === ByeKind.SESSION_ENDED) {
      this.finish(bye.reason || 'the login session has ended')
    } else if (!wasOpen) {
      void this.refused()
    } else if (bye?.kind === ByeKind.PROTOCOL) {
      this.later(`the backend could not read a message: ${bye.reason}`)
    } else if (code === RESTART_CODE) {
      this.later('the backend restarts')
    } else {
      this.later(reason ? `closed: ${reason}` : `closed (${code})`)
    }
  }

  /** The handshake was refused, or the backend is not there: ask the session why. */
  private async refused(): Promise<void> {
    const answer = await this.options.session()
    if (this.state.phase === 'idle') return // stopped meanwhile
    if (answer === 'out') {
      this.finish('the backend has no session for this browser')
    } else if (answer === 'unreachable') {
      this.later('the backend does not answer')
    } else {
      this.refusedInRow += 1
      if (this.refusedInRow >= 2) this.options.reload?.()
      this.later('the backend refuses the connection')
    }
  }

  /** Give up the current carrier at once, without waiting for its close. */
  private lose(reason: string, code: number): void {
    this.drop({ code, reason })
    this.later(reason)
  }

  /** Forget the current carrier: nothing it says counts any more. `close` closes it too. */
  private drop(close?: { code: number; reason: string }): void {
    clearTimeout(this.silence)
    const current = this.current
    this.current = null
    if (close !== undefined) current?.carrier?.close(close.code, close.reason)
    this.byId.clear()
    for (const entry of this.entries) entry.id = null
    if (current?.open) for (const entry of this.entries) entry.listener.lost?.()
  }

  private later(reason: string): void {
    const wait = this.wait
    this.wait = Math.min(this.wait * 2, LONGEST_WAIT_MS)
    this.update({ phase: 'waiting', reason, retryAt: Date.now() + wait })
    clearTimeout(this.retry)
    this.retry = setTimeout(() => this.connect(), wait)
  }

  private finish(reason: string): void {
    clearTimeout(this.retry)
    this.update({ phase: 'ended', reason, retryAt: null })
    this.options.ended?.(reason)
  }

  private listen(): void {
    clearTimeout(this.silence)
    const current = this.current
    this.silence = setTimeout(() => {
      if (current !== null && this.current === current) {
        this.lose('no message for 15 s', SILENT_CODE)
      }
    }, SILENCE_MS)
  }

  private send(message: ClientMessage): void {
    const carrier = this.current?.open ? this.current.carrier : null
    if (carrier === null) return
    carrier.send(
      this.wire === 'json'
        ? toJsonString(ClientMessageSchema, message)
        : toBinary(ClientMessageSchema, message),
    )
  }

  private update(patch: Partial<ConnectionState>): void {
    const since = patch.phase !== undefined && patch.phase !== this.state.phase
    this.state = { ...this.state, ...patch, ...(since ? { since: Date.now() } : {}) }
    this.options.changed?.(this.state)
  }
}

// ------------------------------------------------------------------ the browser's parts

/** The WebSocket of this page's backend: `/api/ws` on the page's own host and port. */
export function webSocket(where: Location = location): OpenCarrier {
  const url = `${where.protocol === 'https:' ? 'wss' : 'ws'}://${where.host}/api/ws`
  return (protocol, events) => {
    const socket = new WebSocket(url, [protocol])
    socket.binaryType = 'arraybuffer'
    socket.onopen = () => events.open()
    socket.onmessage = (event: MessageEvent<ArrayBuffer | string>) => events.message(event.data)
    socket.onclose = (event) => events.close(event.code, event.reason)
    return {
      send: (data) => socket.send(data),
      close: (code, reason) => {
        socket.onopen = socket.onmessage = socket.onclose = null
        socket.close(code, reason)
      },
    }
  }
}

/** Asks the backend whether this browser has a session. */
export async function probeSession(): Promise<'in' | 'out' | 'unreachable'> {
  try {
    const answer = await fetch('/api/session', { cache: 'no-store' })
    if (answer.status === 200) return 'in'
    return answer.status === 401 ? 'out' : 'unreachable'
  } catch {
    return 'unreachable'
  }
}

/** The JSON form for reading along (API-04): `?wire=json` in the page's address. */
export function wireOf(search: string): Wire {
  return new URLSearchParams(search).get('wire') === 'json' ? 'json' : 'protobuf'
}

/** A reload at most once a minute, so that a backend that refuses for good is no reload loop. */
export function mayReload(last: number | null, now: number): boolean {
  return last === null || !(now - last < 60_000)
}
