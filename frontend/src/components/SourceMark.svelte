<script lang="ts">
  // The permanent mark of GEN-11: "SYNTHETIC DATA" on the fake broker, "REPLAY" with the date of
  // the recording on a replay, nothing on the live broker. It sits in the top bar, the header of
  // every screen window, and in the status bar.
  import { sourceMark } from '../lib/statusline'
  import { status } from '../stores/status.svelte'

  let { where }: { where: string } = $props()
  const mark = $derived(sourceMark(status.current?.source))
</script>

{#if mark}
  <span
    class="mark"
    data-testid="source-mark-{where}"
    title="Not the live broker: no value here is a market value">{mark}</span
  >
{/if}

<style>
  .mark {
    display: inline-flex;
    align-items: center;
    height: 22px;
    padding: 0 8px;
    font-size: 12px;
    font-weight: 600;
    letter-spacing: 0.04em;
    white-space: nowrap;
    color: var(--warn);
    border: 1px solid var(--warn);
    border-radius: 999px;
  }
</style>
