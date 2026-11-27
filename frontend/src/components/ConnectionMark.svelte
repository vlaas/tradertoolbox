<script lang="ts">
  // The loss of the connection, on every screen (UI-05): in the top bar while the app is not
  // connected, with the reason and the wait until the next attempt.
  import { connectionLost, connectionPart } from '../lib/statusline'
  import { connection } from '../stores/connection.svelte'

  let now = $state(Date.now())
  const lost = $derived(connectionLost(connection.state))
  $effect(() => {
    if (!lost) return
    now = Date.now()
    const ticking = setInterval(() => (now = Date.now()), 500)
    return () => clearInterval(ticking)
  })
  const part = $derived(connectionPart(connection.state, now))
</script>

{#if lost}
  <span class="lost level {part.level}" data-testid="connection-lost">{part.title}</span>
{/if}

<style>
  .lost {
    height: 22px;
    padding: 0 8px;
    font-size: 12px;
    font-weight: 600;
    color: var(--error);
    border: 1px solid var(--error);
    border-radius: 999px;
  }
  .lost.warn {
    color: var(--warn);
    border-color: var(--warn);
  }
</style>
