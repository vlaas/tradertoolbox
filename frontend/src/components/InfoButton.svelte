<script lang="ts">
  // The one information icon (UI-11 base): instructions and explanations open from it, in the
  // header of their panel or bar. The box is a native popover, so it is drawn in the top layer
  // above every scrolling area and never cut off by one (UI-09 base). It opens below its button,
  // or above it in the lower half of the window, towards the side of the window with more room,
  // and closes on Esc and on a click outside.
  import type { Snippet } from 'svelte'

  let { label, children }: { label: string; children: Snippet } = $props()
  const id = $props.id()
  let button = $state<HTMLButtonElement>()
  let box = $state<HTMLDivElement>()

  function place(event: ToggleEvent): void {
    if (event.newState !== 'open' || !button || !box) return
    const at = button.getBoundingClientRect()
    const style = box.style
    if (at.left + at.width / 2 < innerWidth / 2) {
      style.right = 'auto'
      style.left = `${Math.max(4, at.left)}px`
    } else {
      style.left = 'auto'
      style.right = `${Math.max(4, innerWidth - at.right)}px`
    }
    if (at.top > innerHeight / 2) {
      style.top = 'auto'
      style.bottom = `${innerHeight - at.top + 4}px`
    } else {
      style.bottom = 'auto'
      style.top = `${at.bottom + 4}px`
    }
  }
</script>

<button
  bind:this={button}
  type="button"
  class="info"
  popovertarget="info-{id}"
  aria-label={label}
  title={label}>i</button
>
<div bind:this={box} id="info-{id}" class="info-box" popover="auto" onbeforetoggle={place}>
  {@render children()}
</div>

<style>
  .info {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    flex: none;
    width: 20px;
    height: 20px;
    padding: 0;
    font-size: 12px;
    font-weight: 600;
    color: var(--muted);
    border-radius: 50%;
  }
  .info-box {
    position: fixed;
    inset: auto;
    margin: 0;
    width: 340px;
    max-width: calc(100vw - 8px);
    max-height: 60vh;
    overflow: auto;
    padding: var(--space-4);
    font-size: 12px;
    line-height: 1.45;
    color: var(--fg);
    background: var(--popup);
    border: 1px solid var(--accent);
    border-radius: 6px;
    box-shadow: 0 8px 20px rgb(0 0 0 / 55%);
    white-space: normal;
  }
  .info-box :global(p) {
    margin: 0 0 var(--space-3);
  }
  .info-box :global(p:last-child) {
    margin-bottom: 0;
  }
</style>
