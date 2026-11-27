<script lang="ts">
  // The status bar (product definition 6.3; BRK-06, BRK-09, SUB-07, GEN-11, UI-05). The order was
  // decided 2026-11-27 (owner): the browser's connection, the broker's stream, the subscription
  // use, the REST state, the mark; then the time of the data, "Read-only" and the information icon
  // that explains the figures. Compact: each part says its whole sentence when pointed at, and the
  // status screen has every figure.
  import { dateTime, hm } from '../lib/format'
  import {
    connectionPart,
    restPart,
    streamPart,
    subscriptionsPart,
    type Part,
  } from '../lib/statusline'
  import { zoneName } from '../lib/zones'
  import { connection } from '../stores/connection.svelte'
  import { shownZone } from '../stores/settings.svelte'
  import { status } from '../stores/status.svelte'
  import InfoButton from './InfoButton.svelte'
  import SourceMark from './SourceMark.svelte'

  let now = $state(Date.now())
  $effect(() => {
    const ticking = setInterval(() => (now = Date.now()), 1000)
    return () => clearInterval(ticking)
  })
  const current = $derived(status.current)
  const zone = $derived(shownZone())
  const nowUs = $derived(current?.dataTimeUs ?? null)
  const dataTimeUs = $derived(connection.state.dataTimeUs ?? nowUs)
  const parts: { id: string; part: Part }[] = $derived([
    { id: 'connection', part: connectionPart(connection.state, now) },
    { id: 'stream', part: streamPart(current?.stream, nowUs, zone) },
    { id: 'subscriptions', part: subscriptionsPart(current?.subscriptions) },
    { id: 'rest', part: restPart(current?.rest, nowUs, zone) },
  ])
</script>

<footer class="statusbar" data-testid="statusbar">
  {#each parts as { id, part } (id)}
    <span
      class="level {part.level}"
      class:old={id !== 'connection' && !status.fresh}
      title={id !== 'connection' && !status.fresh ? `Before the loss: ${part.title}` : part.title}
      data-testid="status-{id}">{part.text}</span
    >
  {/each}
  <SourceMark where="status" />
  <span class="spacer"></span>
  {#if dataTimeUs !== null}
    <span
      title="The time of the broker's data: {dateTime(dataTimeUs, zone)}"
      data-testid="data-time">{hm(dataTimeUs, zone)} {zoneName(zone)}</span
    >
  {/if}
  <span title="Read-only: no orders are sent, and nothing at the broker is changed">Read-only</span>
  <InfoButton label="What the status bar says">
    <p>Point at a part of the bar for its whole sentence. The status screen has every figure.</p>
    <p>
      <strong>Backend ok</strong>: this page is connected. <strong>Backend lost</strong>: the
      connection has gone; every value is marked stale until it returns, and the top bar says so.
    </p>
    <p>
      <strong>Stream ok · 0.5 s</strong>: the broker's streaming session is connected and sends
      quotes every 0.5 s. <strong>gap 2 s</strong>: data may be missing for that long between two
      sessions (shown for 10 minutes). Also: resubscribing, stalled, down.
    </p>
    <p>
      <strong>Subs 9/10,000</strong>: the symbol and event pairs held at the broker, of the budget;
      then the changes pending and the batches lost, when there are any.
    </p>
    <p>
      <strong>REST 5/min ≤2/s</strong>: the broker's requests in the last minute, and the limit a
      second. <strong>token renews 10:14</strong>: from then the next call renews the access token;
      it lapses in a quiet spell, which is no fault. <strong>held … (429)</strong>: every request
      waits after the broker answered 429.
    </p>
    <p>
      <strong>SYNTHETIC DATA</strong> or <strong>REPLAY</strong>: the backend does not run on the
      live broker. The time is that of the broker's data, in the zone chosen in the top bar.
    </p>
  </InfoButton>
</footer>

<style>
  .statusbar {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: var(--space-1) var(--space-5);
    min-height: var(--statusbar-h);
    padding: var(--space-1) var(--space-4);
    font-size: 12px;
    color: var(--muted);
    background: var(--panel);
    border-top: 1px solid var(--border);
  }
  /* Of a connection that has gone: in italics, not faded, so the contrast stays. */
  .old {
    font-style: italic;
  }
</style>
