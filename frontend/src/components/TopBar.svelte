<script lang="ts">
  // The top bar: the header of every screen window. Left to right: the name, the screens, the
  // loss of the connection (UI-05), the mark of GEN-11, the account's nickname (GEN-03), Log out.
  import { hashOf } from '../lib/route'
  import { SCREENS } from '../screens/registry'
  import { logOut } from '../stores/session.svelte'
  import { status } from '../stores/status.svelte'
  import ConnectionMark from './ConnectionMark.svelte'
  import SourceMark from './SourceMark.svelte'

  let { current }: { current: string } = $props()
  const account = $derived(status.current?.account ?? '')
</script>

<header class="topbar">
  <strong class="name">Self-3-Trader</strong>
  <nav aria-label="Screens">
    {#each SCREENS as screen (screen.id)}
      <a href={hashOf(screen.id)} aria-current={screen.id === current ? 'page' : undefined}
        >{screen.title}</a
      >
    {/each}
  </nav>
  <span class="spacer"></span>
  <ConnectionMark />
  <SourceMark where="top" />
  {#if account}
    <span class="chip" data-testid="account" title="The account's nickname">{account}</span>
  {/if}
  <button type="button" onclick={() => void logOut()}>Log out</button>
</header>

<style>
  .topbar {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: var(--space-4) var(--space-5);
    min-height: var(--bar-h);
    padding: 0 var(--space-4);
    background: var(--panel);
    border-bottom: 1px solid var(--border);
  }
  .name {
    font-size: 14px;
    letter-spacing: 0.02em;
  }
  nav {
    display: flex;
    gap: var(--space-2);
    align-self: stretch;
  }
  nav a {
    display: flex;
    align-items: center;
    padding: 0 12px;
    min-height: var(--bar-h);
    color: var(--muted);
    text-decoration: none;
    border-bottom: 2px solid transparent;
  }
  nav a[aria-current='page'] {
    color: var(--fg);
    font-weight: 600;
    border-bottom-color: var(--accent);
  }
</style>
