<script lang="ts">
  // Which time the screens show (UI-12): the exchange time of each instrument (the default),
  // local time, UTC, or a zone by its name. Every time on a screen says its zone. A name of the
  // browser's list of zones applies as soon as it is typed or picked; any other name on Enter or
  // when the field is left. (WebKit gives Enter to the open list of suggestions.)
  import { isZone, localZone, MARKET_ZONE, zoneName } from '../lib/zones'
  import { setTimes, settings } from '../stores/settings.svelte'

  const FIXED = ['exchange', 'local', 'UTC']
  let mode = $state(FIXED.includes(settings.times) ? settings.times : 'zone')
  let typed = $state(FIXED.includes(settings.times) ? '' : settings.times)
  let bad = $state(false)
  const zones = Intl.supportedValuesOf('timeZone')
  const known = new Set(zones)

  function pick(value: string): void {
    mode = value
    bad = false
    if (value !== 'zone') setTimes(value)
  }

  function apply(): void {
    const name = typed.trim()
    bad = !isZone(name)
    if (!bad) setTimes(name)
  }

  /** A name of the browser's list applies at once: picking a suggestion is enough. */
  function typing(): void {
    if (known.has(typed.trim())) apply()
  }

  function enter(event: SubmitEvent): void {
    event.preventDefault()
    apply()
  }
</script>

<div class="times">
  <label>
    Times
    <select value={mode} onchange={(e) => pick(e.currentTarget.value)} data-testid="times">
      <option value="exchange">Exchange ({zoneName(MARKET_ZONE)})</option>
      <option value="local">Local ({zoneName(localZone())})</option>
      <option value="UTC">UTC</option>
      <option value="zone">A zone…</option>
    </select>
  </label>
  {#if mode === 'zone'}
    <form onsubmit={enter}>
      <input
        bind:value={typed}
        list="time-zones"
        oninput={typing}
        onchange={apply}
        placeholder="Europe/Tallinn"
        aria-label="Time zone by its name"
        aria-invalid={bad}
        spellcheck="false"
        autocomplete="off"
        data-testid="time-zone"
      />
      <datalist id="time-zones">
        {#each zones as zone (zone)}<option value={zone}></option>{/each}
      </datalist>
    </form>
  {/if}
</div>

<style>
  .times {
    display: flex;
    align-items: center;
    gap: var(--space-3);
    font-size: 12px;
    color: var(--muted);
  }
  label {
    display: flex;
    align-items: center;
    gap: var(--space-3);
  }
  select,
  input {
    height: 24px;
    font-size: 12px;
  }
  input {
    width: 150px;
  }
  input[aria-invalid='true'] {
    border-color: var(--error);
  }
</style>
