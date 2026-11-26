<script lang="ts">
  // The shell: top bar, the screen of the address, status bar. At laptop size the window does not
  // scroll and each area that can overflow scrolls on its own; below 1024 by 600 the areas stack
  // and the page scrolls as a whole (UI-08 base; proposal of WP-0.8).
  import { onMount } from 'svelte'
  import { DEFAULT_SCREEN, SCREENS } from '../screens/registry'
  import { route, startRoute } from '../stores/route.svelte'
  import StatusBar from './StatusBar.svelte'
  import TopBar from './TopBar.svelte'

  onMount(() => startRoute(new Set(SCREENS.map((s) => s.id)), DEFAULT_SCREEN))
  const screen = $derived(SCREENS.find((s) => s.id === route.screen) ?? SCREENS[0])
  $effect(() => {
    document.title = `${screen.title} · Self-3-Trader`
  })
</script>

<div class="shell">
  <TopBar current={screen.id} />
  <main class="screen" aria-label={screen.title}>
    <screen.component />
  </main>
  <StatusBar />
</div>

<style>
  .shell {
    display: grid;
    grid-template-rows: auto 1fr auto;
    min-height: 100vh;
  }
  .screen {
    min-width: 0;
    padding: var(--gap);
  }
  @media (min-width: 1024px) and (min-height: 600px) {
    .shell {
      height: 100vh;
      overflow: hidden;
    }
    .screen {
      min-height: 0;
      overflow: hidden;
    }
  }
</style>
