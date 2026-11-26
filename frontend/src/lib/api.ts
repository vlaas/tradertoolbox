/** The REST calls of the app, typed by the schema generated from the backend (API-02). No type
 * here is written by hand. The text of an error is the backend's `ApiError`. */
import type { components, paths } from '../gen/api'

export type Health = components['schemas']['Health']
export type Features = components['schemas']['FeaturesReport']
export type SessionReport = components['schemas']['SessionReport']
export type ApiError = components['schemas']['ApiError']

type LoginBody = paths['/api/login']['post']['requestBody']['content']['application/json']

/** The answer of a call that did not succeed: the status (0 when the backend did not answer),
 * the backend's error, and the wait it asked for. */
export class CallFailed extends Error {
  readonly status: number
  readonly code: string
  readonly retryAfterS: number | null

  constructor(status: number, code: string, message: string, retryAfterS: number | null = null) {
    super(message)
    this.status = status
    this.code = code
    this.retryAfterS = retryAfterS
  }
}

async function call<T>(path: string, init?: RequestInit): Promise<T> {
  let answer: Response
  try {
    answer = await fetch(path, { cache: 'no-store', ...init })
  } catch {
    throw new CallFailed(0, 'unreachable', 'the backend does not answer')
  }
  if (answer.ok) return (answer.status === 204 ? undefined : await answer.json()) as T
  let error: ApiError = { code: 'http', message: `HTTP ${answer.status}` }
  try {
    error = (await answer.json()) as ApiError
  } catch {
    // not the backend's error: keep the status
  }
  const retry = Number(answer.headers.get('retry-after'))
  throw new CallFailed(answer.status, error.code, error.message, retry > 0 ? retry : null)
}

export function login(password: string): Promise<SessionReport> {
  const body: LoginBody = { password }
  return call('/api/login', {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify(body),
  })
}

export function logout(): Promise<void> {
  return call('/api/logout', { method: 'POST' })
}

export function health(): Promise<Health> {
  return call('/api/health')
}

export function features(): Promise<Features> {
  return call('/api/features')
}
