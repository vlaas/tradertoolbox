<script lang="ts">
  // The status bar (product definition 6.3; BRK-06, BRK-09, SUB-07, GEN-11, UI-05). Left to right:
  // the browser's connection, the broker's stream, the subscription use, the REST state, the mark;
  // then the time of the data, the read-only note and the information icon. Terse; the status
  // screen has every figure.
  import { dateTime, time } from '../lib/format'
  import {
    connectionPart,
    restPart,
    streamPart,
    subscriptionsPart,
    type Part,
  } from '../lib/statusline'
  import { connection } from '../stores/connection.svelte'
  import { status } from '../stores/status.svelte'
  import InfoButton from './InfoButton.svelte'
  import SourceMark from './SourceMark.svelte'

  let now = $state(Date.now())
  $effect(() => {
    const ticking = setInterval(() => (now = Date.now()), 1000)
    return () => clearInterval(ticking)
  })
  const current = $derived(status.current)
  const dataTimeUs = $derived(connection.state.dataTimeUs ?? current?.dataTimeUs ?? null)
  const parts: { id: string; part: Part }[] = $derived([
    { id: 'connection', part: connectionPart(connection.state, now) },
    { id: 'stream', part: streamPart(current?.stream) },
    { id: 'subscriptions', part: subscriptionsPart(current?.subscriptions) },
    { id: 'rest', part: restPart(current?.rest, current?.dataTimeUs ?? null) },
  ])
</script>

<footer class="statusbar" data-testid="statusbar">
  {#each parts as { id, part } (id)}
    <span
      class="level {part.level}"
      class:old={id !== 'connection' && !status.fresh}
      data-testid="status-{id}">{part.text}</span
    >
  {/each}
  <SourceMark where="status" />
  <span class="spacer"></span>
  {#if dataTimeUs !== null}<span title="The time of the broker's data: {dateTime(dataTimeUs)}"
      >{time(dataTimeUs)} ET</span
    >{/if}
  <span>Read-only: no orders are sent</span>
  <InfoButton label="What the status bar says">
    <p>
      <strong>Backend</strong>: this page's connection. When it is lost, every value is marked stale
      until it returns.
    </p>
    <p>
      <strong>Stream</strong>: the broker's streaming session, and how often it sends quotes. A gap
      is a time between two sessions in which data may be missing.
    </p>
    <p>
      <strong>Subscriptions</strong>: the symbol and event pairs held at the broker, beside the
      budget. Pending: changes not sent yet.
    </p>
    <p>
      <strong>REST</strong>: the access token; the broker's requests of the last minute beside the
      configured limit (requests per second, and at once); a hold after the broker answered 429.
    </p>
    <p>
      <strong>SYNTHETIC DATA</strong> or <strong>REPLAY</strong>: the backend does not run on the
      live broker. Times are New York time (ET).
    </p>
  </InfoButton>
</footer>

<style>
  .statusbar {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: var(--space-1) var(--space-4);
    min-height: var(--statusbar-h);
    padding: var(--space-1) var(--space-4);
    font-size: 12px;
    color: var(--muted);
    background: var(--panel);
    border-top: 1px solid var(--border);
  }
  .old {
    opacity: 0.6;
  }
</style>
