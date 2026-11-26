/** The screen of the address (UI-01 base). An address that names no screen is replaced by the
 * default one, without a step in the history. */
import { hashOf, screenOf } from '../lib/route'

class RouteStore {
  screen = $state('')
}

export const route = new RouteStore()

export function startRoute(known: ReadonlySet<string>, fallback: string): () => void {
  const sync = () => {
    const screen = screenOf(location.hash, known)
    if (screen === null) history.replaceState(history.state, '', hashOf(fallback))
    route.screen = screen ?? fallback
  }
  sync()
  addEventListener('hashchange', sync)
  return () => removeEventListener('hashchange', sync)
}
