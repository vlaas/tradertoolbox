// The connection module against a fake carrier on fake timers (UI-03, UI-05, API-03): every rule of
// its header, one test or more each.
import {
  create,
  fromBinary,
  fromJsonString,
  type MessageInitShape,
  toBinary,
  toJsonString,
} from '@bufbuild/protobuf'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import {
  ByeKind,
  type ClientMessage,
  ClientMessageSchema,
  RefusalKind,
  type ServerMessage,
  ServerMessageSchema,
} from '../gen/s3t/wire/v1/channel_pb'
import { QuotesTopicSchema, StatusTopicSchema } from '../gen/s3t/wire/v1/channel_pb'
import {
  type Carrier,
  type CarrierEvents,
  Connection,
  FIRST_WAIT_MS,
  JSON_FORM,
  LONGEST_WAIT_MS,
  type Listener,
  mayReload,
  PROTOBUF,
  RESTART_CODE,
  SILENCE_MS,
  SILENT_CODE,
  type Topic,
  UNREADABLE_CODE,
  wireOf,
} from './connection'

type Init = MessageInitShape<typeof ServerMessageSchema>

class FakeCarrier implements Carrier {
  sent: ClientMessage[] = []
  closedWith: [number, string] | null = null
  readonly protocol: string
  private readonly events: CarrierEvents

  constructor(protocol: string, events: CarrierEvents) {
    this.protocol = protocol
    this.events = events
  }

  send(data: Uint8Array | string): void {
    this.sent.push(
      typeof data === 'string'
        ? fromJsonString(ClientMessageSchema, data)
        : fromBinary(ClientMessageSchema, data),
    )
  }

  close(code: number, reason: string): void {
    this.closedWith = [code, reason]
  }

  // The backend's side.
  accept(): void {
    this.events.open()
  }

  push(init: Init): void {
    const message = create(ServerMessageSchema, init)
    if (this.protocol === JSON_FORM) this.events.message(toJsonString(ServerMessageSchema, message))
    else this.events.message(toBinary(ServerMessageSchema, message).slice().buffer)
  }

  raw(data: ArrayBuffer | string): void {
    this.events.message(data)
  }

  end(code = 1006, reason = ''): void {
    this.events.close(code, reason)
  }

  /** The numbers subscribed and ended, in order: [+1, +2, -1] */
  get moves(): number[] {
    return this.sent.map((m) =>
      m.body.case === 'subscribe' ? m.body.value.id : -(m.body.value?.id ?? 0),
    )
  }
}

class Rig {
  carriers: FakeCarrier[] = []
  session: 'in' | 'out' | 'unreachable' = 'in'
  ended: string[] = []
  reloads = 0
  connection: Connection

  constructor(wire: 'protobuf' | 'json' = 'protobuf') {
    this.connection = new Connection({
      open: (protocol, events) => {
        const carrier = new FakeCarrier(protocol, events)
        this.carriers.push(carrier)
        return carrier
      },
      session: async () => this.session,
      wire,
      ended: (reason) => this.ended.push(reason),
      reload: () => (this.reloads += 1),
    })
  }

  get last(): FakeCarrier {
    const carrier = this.carriers.at(-1)
    if (!carrier) throw new Error('no carrier')
    return carrier
  }

  get phase(): string {
    return this.connection.state.phase
  }

  /** Started and open. */
  open(): FakeCarrier {
    if (this.phase === 'idle') this.connection.start()
    this.last.accept()
    return this.last
  }
}

class Recorder implements Listener {
  got: ServerMessage[] = []
  refusals: string[] = []
  losses = 0
  message(message: ServerMessage): void {
    this.got.push(message)
  }
  refused(refused: { reason: string }): void {
    this.refusals.push(refused.reason)
  }
  lost(): void {
    this.losses += 1
  }
}

const status: Topic = { case: 'status', value: create(StatusTopicSchema) }
const quotes = (...symbol: string[]): Topic => ({
  case: 'quotes',
  value: create(QuotesTopicSchema, { symbol }),
})
const heartbeat = (dataTimeUs: number): Init => ({
  id: 0,
  body: { case: 'heartbeat', value: { dataTimeUs } },
})
const statusMessage = (id: number, snapshot = false): Init => ({
  id,
  snapshot,
  body: { case: 'status', value: { account: 'main' } },
})

beforeEach(() => {
  vi.useFakeTimers()
  vi.setSystemTime(new Date('2026-11-27T12:00:00Z'))
})

afterEach(() => {
  vi.useRealTimers()
})

describe('subscriptions', () => {
  it('are sent when the connection opens, under rising numbers, in the protobuf form', () => {
    const rig = new Rig()
    const a = new Recorder()
    rig.connection.subscribe(status, a)
    rig.connection.start()
    expect(rig.phase).toBe('connecting')
    expect(rig.last.protocol).toBe(PROTOBUF)
    expect(rig.last.sent).toEqual([]) // nothing before it is open
    rig.last.accept()
    expect(rig.phase).toBe('open')
    rig.connection.subscribe(quotes('SPY'), new Recorder())
    expect(rig.last.moves).toEqual([1, 2])
    const second = rig.last.sent[1]
    expect(second.body.case === 'subscribe' && second.body.value.topic.case).toBe('quotes')
  })

  it('get the messages of their number, and those of an unknown number are ignored', () => {
    const rig = new Rig()
    const a = new Recorder()
    const b = new Recorder()
    rig.connection.subscribe(status, a)
    rig.connection.subscribe(quotes('SPY'), b)
    const carrier = rig.open()
    carrier.push(statusMessage(1, true))
    carrier.push(statusMessage(9))
    carrier.push({ id: 2, snapshot: true, body: { case: 'quotes', value: {} } })
    expect(a.got.map((m) => m.snapshot)).toEqual([true])
    expect(b.got.map((m) => m.body.case)).toEqual(['quotes'])
  })

  it('change by subscribing anew and then ending the old one; the old number is ignored', () => {
    const rig = new Rig()
    const a = new Recorder()
    const handle = rig.connection.subscribe(quotes('SPY'), a)
    const carrier = rig.open()
    handle.change(quotes('SPY', 'SPX'))
    expect(carrier.moves).toEqual([1, 2, -1])
    const again = carrier.sent[1]
    expect(again.body.case === 'subscribe' && again.body.value.topic.value).toMatchObject({
      symbol: ['SPY', 'SPX'],
    })
    carrier.push({ id: 1, body: { case: 'quotes', value: {} } })
    carrier.push({ id: 2, snapshot: true, body: { case: 'quotes', value: {} } })
    expect(a.got.map((m) => m.id)).toEqual([2])
  })

  it('a change while not connected is what the next connection asks for', () => {
    const rig = new Rig()
    const handle = rig.connection.subscribe(quotes('SPY'), new Recorder())
    rig.connection.start()
    handle.change(quotes('AAPL'))
    rig.last.accept()
    const first = rig.last.sent[0]
    expect(rig.last.moves).toEqual([1])
    expect(first.body.case === 'subscribe' && first.body.value.topic.value).toMatchObject({
      symbol: ['AAPL'],
    })
  })

  it('close with an unsubscription, and are not asked for again', () => {
    const rig = new Rig()
    const handle = rig.connection.subscribe(status, new Recorder())
    const carrier = rig.open()
    handle.close()
    expect(carrier.moves).toEqual([1, -1])
    carrier.end()
    vi.advanceTimersByTime(FIRST_WAIT_MS)
    rig.last.accept()
    expect(rig.last.moves).toEqual([])
  })

  it('a refusal ends the subscription with its reason; it is asked again after a reconnect', () => {
    const rig = new Rig()
    const a = new Recorder()
    rig.connection.subscribe(quotes('SPY'), a)
    const carrier = rig.open()
    const reason = 'quotes: the broker refused the grant'
    carrier.push({
      id: 1,
      body: { case: 'refused', value: { kind: RefusalKind.UNAVAILABLE, reason } },
    })
    carrier.push({ id: 1, body: { case: 'quotes', value: {} } })
    expect(a.refusals).toEqual([reason])
    expect(a.got).toEqual([])
    carrier.end()
    vi.advanceTimersByTime(FIRST_WAIT_MS)
    expect(rig.open().moves).toEqual([2])
  })
})

describe('a loss', () => {
  it('is followed by fresh subscriptions under new numbers, and the listeners hear of it', () => {
    const rig = new Rig()
    const a = new Recorder()
    rig.connection.subscribe(status, a)
    rig.connection.subscribe(quotes('SPY'), new Recorder())
    const first = rig.open()
    first.end(1006)
    expect(rig.phase).toBe('waiting')
    expect(a.losses).toBe(1)
    vi.advanceTimersByTime(FIRST_WAIT_MS - 1)
    expect(rig.carriers).toHaveLength(1)
    vi.advanceTimersByTime(1)
    const second = rig.open()
    expect(second.moves).toEqual([3, 4])
    first.push(statusMessage(1)) // the old carrier says nothing any more
    second.push(statusMessage(3, true))
    expect(a.got.map((m) => m.id)).toEqual([3])
    expect(rig.connection.state.opened).toBe(2)
  })

  it('waits 0.5 s, doubling to 8 s, and from 0.5 s again once a connection brought a message', () => {
    const rig = new Rig()
    rig.connection.start()
    const waits: number[] = []
    for (let i = 0; i < 6; i++) {
      rig.last.accept()
      rig.last.end()
      const state = rig.connection.state
      waits.push((state.retryAt ?? 0) - Date.now())
      vi.advanceTimersByTime(waits.at(-1) ?? 0)
    }
    expect(waits).toEqual([500, 1000, 2000, 4000, LONGEST_WAIT_MS, LONGEST_WAIT_MS])
    rig.last.accept()
    rig.last.push(heartbeat(1))
    rig.last.end()
    expect((rig.connection.state.retryAt ?? 0) - Date.now()).toBe(FIRST_WAIT_MS)
  })

  it('is silence for 15 s: the carrier is given up at once', () => {
    const rig = new Rig()
    const carrier = rig.open()
    vi.advanceTimersByTime(SILENCE_MS - 1000)
    carrier.push(heartbeat(1_795_186_800_000_000))
    expect(rig.connection.state.dataTimeUs).toBe(1_795_186_800_000_000)
    vi.advanceTimersByTime(SILENCE_MS - 1)
    expect(rig.phase).toBe('open')
    vi.advanceTimersByTime(1)
    expect(carrier.closedWith?.[0]).toBe(SILENT_CODE)
    expect(rig.phase).toBe('waiting')
    expect(rig.connection.state.reason).toBe('no message for 15 s')
    carrier.end(1006) // its close comes later, if at all, and counts for nothing
    expect(rig.carriers).toHaveLength(1)
    vi.advanceTimersByTime(FIRST_WAIT_MS)
    expect(rig.carriers).toHaveLength(2)
  })

  it('by a restart of the backend says so', () => {
    const rig = new Rig()
    rig.open().end(RESTART_CODE)
    expect(rig.connection.state.reason).toBe('the backend restarts')
  })

  it('by a message that cannot be read closes the carrier and connects again', () => {
    const rig = new Rig()
    const carrier = rig.open()
    vi.spyOn(console, 'error').mockImplementation(() => {})
    carrier.raw(new Uint8Array([0xff, 0xff, 0xff]).buffer)
    expect(carrier.closedWith?.[0]).toBe(UNREADABLE_CODE)
    expect(rig.phase).toBe('waiting')
  })
})

describe('the end of the login session', () => {
  it('comes with Bye: the reason goes to the login screen, and nothing is tried', () => {
    const rig = new Rig()
    const carrier = rig.open()
    const reason = 'the login session has ended'
    carrier.push({ id: 0, body: { case: 'bye', value: { kind: ByeKind.SESSION_ENDED, reason } } })
    carrier.end(1000)
    expect(rig.phase).toBe('ended')
    expect(rig.ended).toEqual([reason])
    vi.advanceTimersByTime(60_000)
    expect(rig.carriers).toHaveLength(1)
    rig.connection.start() // after a new login
    expect(rig.carriers).toHaveLength(2)
  })

  it('is asked when a handshake is refused: no session means the login screen', async () => {
    const rig = new Rig()
    rig.session = 'out'
    rig.connection.start()
    rig.last.end(1006)
    await vi.advanceTimersByTimeAsync(0)
    expect(rig.phase).toBe('ended')
    expect(rig.ended).toEqual(['the backend has no session for this browser'])
  })

  it('no answer means trying again', async () => {
    const rig = new Rig()
    rig.session = 'unreachable'
    rig.connection.start()
    rig.last.end(1006)
    await vi.advanceTimersByTimeAsync(0)
    expect(rig.phase).toBe('waiting')
    expect(rig.connection.state.reason).toBe('the backend does not answer')
    await vi.advanceTimersByTimeAsync(FIRST_WAIT_MS)
    expect(rig.carriers).toHaveLength(2)
    expect(rig.reloads).toBe(0)
  })

  it('a session that is there twice while the handshake is refused reloads the page', async () => {
    const rig = new Rig()
    rig.connection.start()
    rig.last.end(1006)
    await vi.advanceTimersByTimeAsync(FIRST_WAIT_MS)
    expect(rig.reloads).toBe(0)
    rig.last.end(1006)
    await vi.advanceTimersByTimeAsync(0)
    expect(rig.reloads).toBe(1)
  })
})

describe('the JSON form', () => {
  it('is asked for by its subprotocol, and both directions are JSON text', () => {
    const rig = new Rig('json')
    const a = new Recorder()
    rig.connection.subscribe(status, a)
    const carrier = rig.open()
    expect(carrier.protocol).toBe(JSON_FORM)
    expect(carrier.moves).toEqual([1])
    carrier.push(statusMessage(1, true))
    carrier.raw('{"id": 1, "status": {"account": "x"}, "aFieldOfLater": 3}')
    expect(a.got.map((m) => m.body.case === 'status' && m.body.value.account)).toEqual([
      'main',
      'x',
    ])
  })
})

describe('stop', () => {
  it('closes the carrier and connects no more', () => {
    const rig = new Rig()
    const carrier = rig.open()
    rig.connection.stop()
    expect(carrier.closedWith?.[0]).toBe(1000)
    expect(rig.phase).toBe('idle')
    vi.advanceTimersByTime(60_000)
    expect(rig.carriers).toHaveLength(1)
  })

  it('while the session is asked keeps it stopped', async () => {
    const rig = new Rig()
    rig.connection.start()
    rig.last.end(1006)
    rig.connection.stop()
    await vi.advanceTimersByTimeAsync(10_000)
    expect(rig.phase).toBe('idle')
    expect(rig.carriers).toHaveLength(1)
  })
})

describe('the page', () => {
  it('reads the JSON switch from its address', () => {
    expect(wireOf('?wire=json')).toBe('json')
    expect(wireOf('')).toBe('protobuf')
    expect(wireOf('?wire=xml')).toBe('protobuf')
  })

  it('reloads at most once a minute', () => {
    expect(mayReload(null, 0)).toBe(true)
    expect(mayReload(1000, 60_999)).toBe(false)
    expect(mayReload(1000, 61_000)).toBe(true)
  })
})
