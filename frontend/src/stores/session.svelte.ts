/** The login session of this browser (SEC-01). The login screen stands in for every screen while
 * there is none. A session that ends while a page is open takes it to the login screen, with the
 * reason (plan section 8, WP-0.7: 48 hours without use). */
import { CallFailed, logout } from '../lib/api'
import { probeSession } from '../lib/connection'
import { startConnection, stopConnection, whenEnded } from './connection.svelte'

const RETRY_MS = 3000

class SessionStore {
  phase = $state<'unknown' | 'out' | 'in'>('unknown')
  /** Why the login screen is shown, for people; empty when there is nothing to say. */
  reason = $state('')
}

export const session = new SessionStore()

whenEnded((reason) => {
  session.phase = 'out'
  session.reason = `Logged out: ${reason}.`
})

/** Asks the backend at the start; asks again while it does not answer. */
export async function checkSession(): Promise<void> {
  const answer = await probeSession()
  if (answer === 'in') {
    loggedIn()
  } else if (answer === 'out') {
    session.phase = 'out'
    session.reason = ''
  } else {
    session.phase = 'out'
    session.reason = 'The backend does not answer. Trying again.'
    setTimeout(() => void (session.phase === 'out' && checkSession()), RETRY_MS)
  }
}

export function loggedIn(): void {
  session.phase = 'in'
  session.reason = ''
  startConnection()
}

export async function logOut(): Promise<void> {
  stopConnection()
  try {
    await logout()
  } catch (e) {
    if (!(e instanceof CallFailed && e.status === 401)) console.error('logout', e)
  }
  session.phase = 'out'
  session.reason = 'You have logged out.'
}
