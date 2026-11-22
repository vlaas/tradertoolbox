<script lang="ts">
  // Placeholder shell (WP-0.1); WP-0.8 replaces it. The round trip shows that the generated
  // Protocol Buffers code runs in each browser engine of the e2e suite.
  import { roundTrip } from './lib/wirecheck'

  const wire = roundTrip()
</script>

<main>
  <h1>Self-3-Trader</h1>
  <p data-testid="wire">{wire}</p>
</main>
