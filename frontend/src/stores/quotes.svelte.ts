/** The store of the topic `quotes` (UI-03): a list of symbols and their latest values. Each part of
 * a screen that shows quotes has its own list; the backend keeps what all of them want. */
import { create } from '@bufbuild/protobuf'
import { QuotesTopicSchema, type Refused } from '../gen/s3t/wire/v1/channel_pb'
import type { Handle, Topic } from '../lib/connection'
import { apply, EMPTY, type QuotesView } from '../lib/quoterows'
import { subscribe } from './connection.svelte'

function topicOf(symbols: readonly string[]): Topic {
  return { case: 'quotes', value: create(QuotesTopicSchema, { symbol: [...symbols] }) }
}

export class QuoteList {
  view = $state.raw<QuotesView>(EMPTY)
  /** The backend ended the subscription: its kind and reason (GEN-04 when the data is absent). */
  refused = $state.raw<Refused | null>(null)
  symbols = $state.raw<readonly string[]>([])
  private readonly handle: Handle

  constructor(symbols: readonly string[]) {
    this.symbols = symbols
    this.handle = subscribe(topicOf(symbols), {
      message: (message) => {
        if (message.body.case !== 'quotes') return
        try {
          this.view = apply(this.view, message.body.value, message.snapshot)
          if (message.snapshot) this.refused = null
        } catch (e) {
          console.error('a quotes message cannot be read', e)
        }
      },
      refused: (refused) => (this.refused = refused),
    })
  }

  /** Ask for other symbols: subscribe anew, then end the old subscription. */
  setSymbols(symbols: readonly string[]): void {
    this.symbols = symbols
    this.refused = null
    this.handle.change(topicOf(symbols))
  }

  close(): void {
    this.handle.close()
  }
}
