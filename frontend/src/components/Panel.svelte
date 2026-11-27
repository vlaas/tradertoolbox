<script lang="ts">
  // A panel of a screen: a header with its title and, where it needs one, the information icon;
  // a body that scrolls on its own when it does not fit (UI-08 base).
  import type { Snippet } from 'svelte'
  import type { Attachment } from 'svelte/attachments'
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

  /** A body that scrolls can be reached and scrolled by the keyboard (WCAG 2.1.1): it is a stop of
   * the Tab key while its content overflows, and only then. */
  const scrollable: Attachment<HTMLElement> = (body) => {
    const look = () => {
      const over = body.scrollHeight > body.clientHeight || body.scrollWidth > body.clientWidth
      if (over) body.setAttribute('tabindex', '0')
      else body.removeAttribute('tabindex')
    }
    const sizes = new ResizeObserver(look)
    sizes.observe(body)
    const content = new MutationObserver(look)
    content.observe(body, { childList: true, subtree: true, characterData: true })
    look()
    return () => {
      sizes.disconnect()
      content.disconnect()
    }
  }
</script>

<section class="panel" aria-labelledby="panel-{id}" data-old={old || undefined}>
  <header>
    <h2 id="panel-{id}">{title}</h2>
    {#if old}<span class="chip">before the loss</span>{/if}
    <span class="spacer"></span>
    {@render tools?.()}
    {#if info}<InfoButton label="About {title}">{@render info()}</InfoButton>{/if}
  </header>
  <div class="body" {@attach scrollable}>
    {@render children()}
  </div>
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
