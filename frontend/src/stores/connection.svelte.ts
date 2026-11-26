/** The app's one connection (UI-03): the module `lib/connection.ts` on this page's WebSocket, and
 * its state for the screens. A store of a topic subscribes here; nothing else opens a WebSocket. */
import {
  Connection,
  type ConnectionState,
  type Handle,
  type Listener,
  mayReload,
  probeSession,
  type Topic,
  webSocket,
  wireOf,
} from '../lib/connection'

const RELOADED_AT = 's3t.reloaded-at'

/** The backend speaks another version of the messages: load the page it serves now, but at most
 * once a minute. */
function reload(): void {
  let last: number | null = null
  try {
    const saved = sessionStorage.getItem(RELOADED_AT)
    last = saved === null ? null : Number(saved)
  } catch {
    // no storage: reload anyway
  }
  if (!mayReload(last, Date.now())) return
  try {
    sessionStorage.setItem(RELOADED_AT, String(Date.now()))
  } catch {
    // as above
  }
  location.reload()
}

const endings: ((reason: string) => void)[] = []

class ConnectionStore {
  state = $state.raw<ConnectionState>({
    phase: 'idle',
    since: Date.now(),
    reason: '',
    retryAt: null,
    opened: 0,
    dataTimeUs: null,
  })
}

export const connection = new ConnectionStore()

const core = new Connection({
  open: webSocket(),
  session: probeSession,
  wire: wireOf(location.search),
  changed: (state) => (connection.state = state),
  ended: (reason) => endings.forEach((end) => end(reason)),
  reload,
})

/** Calls `end` when the login session has ended. */
export function whenEnded(end: (reason: string) => void): void {
  endings.push(end)
}

export function startConnection(): void {
  core.start()
}

export function stopConnection(): void {
  core.stop()
}

/** For the stores of the topics only. */
export function subscribe(topic: Topic, listener: Listener): Handle {
  return core.subscribe(topic, listener)
}
