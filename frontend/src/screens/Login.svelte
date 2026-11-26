<script lang="ts">
  // The login screen (SEC-01). It stands in for every screen while this browser has no session,
  // and the address stays, so that the screen it names shows after the login. It says why it is
  // shown when a session ended, and what the backend answered.
  import { onMount } from 'svelte'
  import { CallFailed, login } from '../lib/api'
  import { seconds } from '../lib/format'
  import { loggedIn, session } from '../stores/session.svelte'

  let password = $state('')
  let busy = $state(false)
  let error = $state('')
  let heldUntil = $state<number | null>(null)
  let now = $state(Date.now())
  let field = $state<HTMLInputElement>()

  onMount(() => field?.focus())

  $effect(() => {
    if (heldUntil === null) return
    const ticking = setInterval(() => {
      now = Date.now()
      if (heldUntil !== null && now >= heldUntil) heldUntil = null
    }, 250)
    return () => clearInterval(ticking)
  })

  function answer(e: unknown): string {
    if (!(e instanceof CallFailed)) return String(e)
    switch (e.status) {
      case 0:
        return 'The backend does not answer.'
      case 401:
        return 'The password is wrong.'
      case 409:
        return 'No password is set on the backend. Set one with tools/set_password.py.'
      case 429:
        now = Date.now()
        heldUntil = now + (e.retryAfterS ?? 1) * 1000
        return 'Too many wrong passwords: the login is held for a while.'
      default:
        return e.message
    }
  }

  async function submit(event: SubmitEvent): Promise<void> {
    event.preventDefault()
    if (busy || heldUntil !== null || !password) return
    busy = true
    error = ''
    try {
      await login(password)
      password = ''
      loggedIn()
    } catch (e) {
      error = answer(e)
      password = ''
    } finally {
      busy = false
      field?.focus()
    }
  }
</script>

<main class="login">
  <form onsubmit={submit} aria-labelledby="login-title">
    <h1 id="login-title">Self-3-Trader</h1>
    {#if session.reason}<p class="reason" data-testid="login-reason">{session.reason}</p>{/if}
    <label for="password">Password</label>
    <input
      bind:this={field}
      bind:value={password}
      id="password"
      type="password"
      autocomplete="current-password"
      required
      disabled={busy}
    />
    <button type="submit" disabled={busy || heldUntil !== null}>
      {heldUntil === null ? 'Log in' : `Wait ${seconds(heldUntil - now)}`}
    </button>
    {#if error}<p class="error" role="alert" data-testid="login-error">{error}</p>{/if}
  </form>
</main>

<style>
  .login {
    display: grid;
    place-items: center;
    min-height: 100vh;
    padding: var(--space-5);
  }
  form {
    display: flex;
    flex-direction: column;
    gap: var(--space-3);
    width: min(320px, 100%);
    padding: var(--space-5);
    background: var(--panel);
    border: 1px solid var(--border);
    border-radius: 6px;
  }
  h1 {
    margin-bottom: var(--space-3);
    font-size: 18px;
  }
  label {
    color: var(--muted);
  }
  button {
    margin-top: var(--space-2);
  }
  .reason {
    margin: 0;
    color: var(--warn);
  }
  .error {
    margin: 0;
  }
</style>
