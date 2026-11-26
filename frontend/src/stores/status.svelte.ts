/** The store of the topic `status` (UI-03): the whole state of the backend, subscribed while the
 * app is connected, because the live rule needs it beside every other topic (UI-05). */
import { create } from '@bufbuild/protobuf'
import { StatusTopicSchema } from '../gen/s3t/wire/v1/channel_pb'
import type { Status } from '../gen/s3t/wire/v1/status_pb'
import type { LiveBasis } from '../lib/live'
import { connection, subscribe } from './connection.svelte'

class StatusStore {
  /** The last status; it may be of a connection that has gone. */
  current = $state.raw<Status | null>(null)
  /** The last status came on the current connection. */
  fresh = $state(false)
  /** Why the backend refused the topic, if it did. */
  refused = $state('')
}

export const status = new StatusStore()

subscribe(
  { case: 'status', value: create(StatusTopicSchema) },
  {
    message(message) {
      if (message.body.case !== 'status') return
      status.current = message.body.value
      status.fresh = true
      status.refused = ''
    },
    refused(refused) {
      status.refused = refused.reason
    },
    lost() {
      status.fresh = false
    },
  },
)

/** What the live rule needs now (UI-05). */
export function liveBasis(): LiveBasis {
  const open = connection.state.phase === 'open' && status.fresh
  return { open, liveSinceUs: open ? (status.current?.liveSinceUs ?? null) : null }
}
