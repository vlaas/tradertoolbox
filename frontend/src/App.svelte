<script lang="ts">
  // The app: the login screen until this browser has a session, then the shell.
  import { onMount } from 'svelte'
  import Shell from './components/Shell.svelte'
  import Login from './screens/Login.svelte'
  import { checkSession, session } from './stores/session.svelte'

  onMount(() => void checkSession())
</script>

{#if session.phase === 'in'}
  <Shell />
{:else if session.phase === 'out'}
  <Login />
{/if}
