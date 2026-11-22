<script lang="ts">
  import { onMount } from 'svelte'
  import type { DrawingKind } from 'lightweight-charts-drawing'
  import { TOOLS, build, type Proto } from './lib/chart'
  import { EXCHANGE, RES, type Res } from './lib/data'

  let host: HTMLDivElement
  let proto = $state<Proto | null>(null)
  let res = $state<Res>('15m')
  let tool = $state<DrawingKind | null>(null)
  let count = $state(0)
  let live = $state(false)
  let showCone = $state(true)
  let showProfile = $state(true)
  let info = $state('loading')
  let zone = $state(EXCHANGE)
  const local = Intl.DateTimeFormat().resolvedOptions().timeZone
  const ZONES = [[EXCHANGE, 'Exchange'], ['UTC', 'UTC'], [local, `Local (${local})`]]

  onMount(() => {
    build(host).then((p) => {
      proto = p
      ;(window as unknown as { __proto: Proto }).__proto = p
      count = p.mgr.drawings().length
      p.mgr.on('tool', (k) => (tool = k))
      p.mgr.on('change', () => (count = p.mgr.drawings().length))
      describe()
    }).catch((e) => (info = `failed: ${e}`))
  })

  function describe() {
    const p = proto
    if (!p) return
    info = `${p.bars.length} bars, setData ${p.timings.setData[p.res]?.toFixed(0)} ms, first frame ${p.timings.firstFrame[p.res]?.toFixed(0)} ms`
  }
  async function pick(r: Res) { res = r; await proto?.setRes(r); describe() }
  async function pickZone(z: string) { zone = z; await proto?.setZone(z); describe() }
  function arm(k: DrawingKind | null) { proto?.mgr.setTool(k) }
  function del() { const p = proto; if (p) for (const id of p.mgr.selection()) p.mgr.remove(id) }
  function toggleLive() { const p = proto; if (!p) return; live ? p.stopLive() : p.startLive(); live = !live }
</script>

<div class="page">
  <div class="bar">
    <b>SPY</b>
    <span class="seg">
      {#each RES as r}
        <button class:on={res === r} onclick={() => pick(r)}>{r}</button>
      {/each}
    </span>
    <span class="seg">
      <button class:on={tool === null} onclick={() => arm(null)}>Cursor</button>
      {#each TOOLS as t}
        <button class:on={tool === t.kind} data-tool={t.kind} onclick={() => arm(t.kind)}>{t.label}</button>
      {/each}
    </span>
    <button onclick={del}>Delete</button>
    <button onclick={() => proto?.mgr.clear()}>Clear</button>
    <span class="seg">
      <button class:on={showCone} onclick={() => { showCone = !showCone; proto?.cone.setVisible(showCone) }}>Cone</button>
      <button class:on={showProfile} onclick={() => { showProfile = !showProfile; proto?.profile.setVisible(showProfile) }}>Profile</button>
    </span>
    <select value={zone} onchange={(e) => pickZone(e.currentTarget.value)}>
      {#each ZONES as [z, name]}<option value={z}>{name}</option>{/each}
    </select>
    <button class:on={live} onclick={toggleLive}>{live ? 'Live on' : 'Live off'}</button>
    <span class="muted">{count} drawings · {info}</span>
  </div>
  <div class="chart" bind:this={host} data-testid="chart"></div>
</div>

<style>
  .page { display: flex; flex-direction: column; height: 100%; }
  .bar { display: flex; gap: 10px; align-items: center; padding: 6px 10px; border-bottom: 1px solid #30353f; background: #161920; flex-wrap: wrap; }
  .seg { display: inline-flex; }
  .seg button { border-radius: 0; margin-left: -1px; }
  button { background: #0f1115; color: #d6d9df; border: 1px solid #30353f; padding: 4px 9px; font: inherit; cursor: pointer; border-radius: 3px; }
  select { background: #0f1115; color: #d6d9df; border: 1px solid #30353f; padding: 4px 6px; font: inherit; border-radius: 3px; }
  button.on { border-color: #60a5fa; color: #60a5fa; position: relative; z-index: 1; }
  .muted { color: #8b919c; margin-left: auto; }
  .chart { flex: 1; min-height: 0; touch-action: none; }
</style>
