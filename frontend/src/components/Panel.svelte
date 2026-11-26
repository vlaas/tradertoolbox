<script lang="ts">
  // A panel of a screen: a header with its title and, where it needs one, the information icon;
  // a body that scrolls on its own when it does not fit (UI-08 base).
  import type { Snippet } from 'svelte'
  import InfoButton from './InfoButton.svelte'

  let {
    title,
    children,
    info,
    tools,
    old = false,
  }: {
    title: string
    children: Snippet
    info?: Snippet
    tools?: Snippet
    /** The figures are of a connection that has gone. */
    old?: boolean
  } = $props()
  const id = $props.id()
</script>

<section class="panel" aria-labelledby="panel-{id}" data-old={old || undefined}>
  <header>
    <h2 id="panel-{id}">{title}</h2>
    {#if old}<span class="chip">before the loss</span>{/if}
    <span class="spacer"></span>
    {@render tools?.()}
    {#if info}<InfoButton label="About {title}">{@render info()}</InfoButton>{/if}
  </header>
  <div class="body">{@render children()}</div>
</section>

<style>
  .panel {
    display: flex;
    flex-direction: column;
    min-width: 0;
    min-height: 0;
    background: var(--panel);
    border: 1px solid var(--border);
    border-radius: 4px;
  }
  header {
    display: flex;
    align-items: center;
    gap: var(--space-4);
    padding: var(--space-2) var(--space-4);
    border-bottom: 1px solid var(--border);
  }
  h2 {
    font-size: 14px;
    font-weight: 600;
  }
  .body {
    flex: 1;
    min-height: 0;
    overflow: auto;
    padding: var(--space-3) var(--space-4);
  }
  .panel[data-old] .body {
    color: var(--muted);
  }
</style>
