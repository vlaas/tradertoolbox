<script lang="ts">
  // The status screen: every figure of the `status` topic (BRK-06, BRK-09, SUB-07, GEN-11), the
  // reference data and the data tiers of the health and features routes (REF-01, REF-04, GEN-04),
  // and a live quote list (UI-05: a row that is not live is greyed and says "stale").
  import { onDestroy, untrack } from 'svelte'
  import Panel from '../components/Panel.svelte'
  import { NoteKind, RefusalKind } from '../gen/s3t/wire/v1/channel_pb'
  import { DemandClass } from '../gen/s3t/wire/v1/status_pb'
  import { CallFailed, features, type Features, health, type Health } from '../lib/api'
  import { count, dateTime, decimal, micros, time } from '../lib/format'
  import { rowState } from '../lib/live'
  import { symbolsOf } from '../lib/quoterows'
  import { authName, connectionPart, phaseName } from '../lib/statusline'
  import { exchangeZone, MARKET_ZONE, zoneName } from '../lib/zones'
  import { connection } from '../stores/connection.svelte'
  import { QuoteList } from '../stores/quotes.svelte'
  import { shownZone } from '../stores/settings.svelte'
  import { liveBasis, status } from '../stores/status.svelte'

  const SYMBOLS_KEY = 's3t.status.symbols'
  const DEFAULT_SYMBOLS = ['SPY', 'SPX', 'AAPL']
  const REFRESH_MS = 30_000

  function saved(): string[] {
    try {
      const symbols = symbolsOf(localStorage.getItem(SYMBOLS_KEY) ?? '')
      return symbols.length ? symbols : DEFAULT_SYMBOLS
    } catch {
      return DEFAULT_SYMBOLS
    }
  }

  const quotes = new QuoteList(saved())
  onDestroy(() => quotes.close())
  let typed = $state(quotes.symbols.join(', '))

  function choose(event: SubmitEvent): void {
    event.preventDefault()
    const symbols = symbolsOf(typed)
    if (!symbols.length) return
    typed = symbols.join(', ')
    quotes.setSymbols(symbols)
    try {
      localStorage.setItem(SYMBOLS_KEY, typed)
    } catch {
      // kept for this page only
    }
  }

  // The health and features routes: at the start, after each reconnect, and every 30 s.
  let report = $state.raw<Health | null>(null)
  let tiers = $state.raw<Features | null>(null)
  let reportError = $state('')
  async function refresh(): Promise<void> {
    try {
      ;[report, tiers] = await Promise.all([health(), features()])
      reportError = ''
    } catch (e) {
      reportError = e instanceof CallFailed ? e.message : String(e)
    }
  }
  $effect(() => {
    if (connection.state.phase !== 'open') return
    untrack(() => void refresh())
    const timer = setInterval(() => void refresh(), REFRESH_MS)
    return () => clearInterval(timer)
  })

  const CLASSES: Record<DemandClass, string> = {
    [DemandClass.UNSPECIFIED]: 'unknown',
    [DemandClass.RECORDER]: 'recorder',
    [DemandClass.POSITIONS]: 'positions',
    [DemandClass.VIEWS]: 'views',
    [DemandClass.BACKGROUND]: 'background',
  }
  const NOTES: Record<NoteKind, string> = {
    [NoteKind.UNSPECIFIED]: 'no data',
    [NoteKind.UNKNOWN]: 'unknown',
    [NoteKind.CUT]: 'cut by the budget',
    [NoteKind.UNREACHABLE]: 'the broker could not be asked',
  }

  const s = $derived(status.current)
  const old = $derived(s !== null && !status.fresh)
  const basis = $derived(liveBasis())
  const yesNo = (x: boolean) => (x ? 'yes' : 'no')
  // UI-12: the times of the backend take the market's zone in exchange time; a quote its own.
  const zone = $derived(shownZone())
  const quoteZone = $derived(shownZone(MARKET_ZONE))
  const instant = (us: number | undefined | null) => dateTime(us ?? null, zone)
  const iso = (text: string | null | undefined) => dateTime(micros(text), zone)
</script>

{#snippet fact(name: string, value: string, testid?: string)}
  <dt>{name}</dt>
  <dd data-testid={testid}>{value}</dd>
{/snippet}

<div class="status">
  <Panel title="Backend">
    <dl class="facts">
      {@render fact('Connection', connectionPart(connection.state, Date.now()).text)}
      {@render fact('Connections opened', count(connection.state.opened), 'connections-opened')}
      {@render fact('Since', instant(connection.state.since * 1000))}
      {@render fact('Data time', instant(connection.state.dataTimeUs ?? s?.dataTimeUs))}
      {@render fact('Live since', instant(status.fresh ? s?.liveSinceUs : undefined))}
      {@render fact('Source', s?.source?.mark || (s ? 'the live broker' : '—'))}
      {@render fact('Account', s?.account || '—')}
      {@render fact('Version', report?.version ?? '—')}
      {@render fact('Started', iso(report?.started_at))}
    </dl>
    {#if status.refused}<p class="error">status refused: {status.refused}</p>{/if}
  </Panel>

  <Panel title="Stream" {old}>
    {#snippet info()}
      <p>
        The broker's streaming session (BRK-06). A gap is a time between two sessions in which data
        may be missing. Stalled: the last session was given up for its silence, and no data has come
        since.
      </p>
    {/snippet}
    <dl class="facts">
      {@render fact('Phase', s?.stream ? phaseName(s.stream.phase) : '—', 'stream-phase')}
      {@render fact('Since', instant(s?.stream?.sinceUs))}
      {@render fact('Connected at', instant(s?.stream?.connectedAtUs))}
      {@render fact('Last data', instant(s?.stream?.lastDataAtUs))}
      {@render fact('Reconnects', count(s?.stream?.reconnects), 'stream-reconnects')}
      {@render fact('Stalls', count(s?.stream?.stalls))}
      {@render fact('Resubscribing', s?.stream ? yesNo(s.stream.resubscribing) : '—')}
      {@render fact('Stalled', s?.stream ? yesNo(s.stream.stalled) : '—')}
      {@render fact(
        'Quotes every',
        s?.stream?.aggregationPeriod === undefined ? '—' : `${s.stream.aggregationPeriod} s`,
      )}
      {@render fact('Reason', s?.stream?.reason || '—')}
      {@render fact(
        'Last gap',
        s?.stream?.lastGap
          ? `${time(s.stream.lastGap.sinceUs, zone)}–${time(s.stream.lastGap.untilUs, zone)} ${zoneName(zone)}, ${s.stream.lastGap.reason}`
          : 'none',
        'stream-gap',
      )}
    </dl>
  </Panel>

  <Panel title="Subscriptions" {old}>
    {#snippet info()}
      <p>
        The symbol and event pairs held at the broker (SUB-07). Granted counts against the budget; a
        class is granted to itself or to a higher one, and the counted figures add up to the total.
        Pending: changes not sent yet; nothing is planned while the stream is down.
      </p>
    {/snippet}
    {@const u = s?.subscriptions}
    <dl class="facts">
      {@render fact(
        'Granted',
        u ? `${count(u.granted)} of ${count(u.budget)}` : '—',
        'subs-granted',
      )}
      {@render fact('Candles', u ? `${count(u.candles)} of ${count(u.candleBudget)}` : '—')}
      {@render fact('Sent', count(u?.sent))}
      {@render fact('Pending', count(u?.pending))}
      {@render fact(
        'Changes, last minute',
        u ? `${count(u.changesLastMinute)} of ${count(u.changeBudget)}` : '—',
      )}
      {@render fact('Rate errors', count(u?.rateErrors))}
      {@render fact('Batches lost', count(u?.lost))}
    </dl>
    {#if u?.classes.length}
      <table class="figures">
        <thead
          ><tr><th class="text">Class</th><th>Wanted</th><th>Granted</th><th>Counted</th></tr
          ></thead
        >
        <tbody>
          {#each u.classes as c (c.demandClass)}
            <tr>
              <td class="text">{CLASSES[c.demandClass] ?? 'unknown'}</td>
              <td>{count(c.wanted)}</td>
              <td>{count(c.granted)}</td>
              <td>{count(c.counted)}</td>
            </tr>
          {/each}
        </tbody>
      </table>
    {/if}
  </Panel>

  <Panel title="REST" {old}>
    {#snippet info()}
      <p>
        The broker's REST client (BRK-09). The token is renewed when a call needs it, so it lapses
        in a quiet spell; that is no fault. The limit is the configured one: requests per second on
        average, and at once. A 429 holds every request until the time the broker named.
      </p>
    {/snippet}
    {@const r = s?.rest}
    <dl class="facts">
      {@render fact('Token', r ? authName(r.auth) : '—', 'rest-auth')}
      {@render fact('Expires', instant(r?.tokenExpiresAtUs))}
      {@render fact('Renews from', instant(r?.renewsFromUs))}
      {@render fact('Last renewal', instant(r?.lastRenewalAtUs))}
      {@render fact('Renewals', count(r?.renewals))}
      {@render fact('Refusal', r?.refusal || '—')}
      {@render fact('Requests, last minute', count(r?.requestsLastMinute))}
      {@render fact('Limit', r ? `${decimal(r.rate, 1)}/s, burst ${count(r.burst)}` : '—')}
      {@render fact('Held until', instant(r?.heldUntilUs))}
      {@render fact('429 answers', count(r?.rateLimited))}
    </dl>
  </Panel>

  <Panel title="Live quotes">
    {#snippet tools()}
      <form class="symbols" onsubmit={choose}>
        <label for="quote-symbols" class="muted">Symbols</label>
        <input id="quote-symbols" bind:value={typed} spellcheck="false" autocomplete="off" />
        <button type="submit">Show</button>
      </form>
    {/snippet}
    {#snippet info()}
      <p>
        Stocks, ETFs and indexes, at most 100. A value is live when it was received since the
        stream's data counts as current; a row that is not live is dimmed and says "stale" (UI-05).
        A dash is a value that is missing, never a zero. Times are New York time.
      </p>
    {/snippet}
    {#if quotes.refused}
      <p class="error" data-testid="quotes-refused">
        {quotes.refused.kind === RefusalKind.UNAVAILABLE ? 'Unavailable' : 'Refused'}: {quotes
          .refused.reason}
      </p>
    {/if}
    <table class="figures" data-testid="quotes">
      <thead>
        <tr>
          <th class="text">Symbol</th>
          <th>Bid</th>
          <th>Ask</th>
          <th>Bid size</th>
          <th>Ask size</th>
          <th>Last</th>
          <th>Last size</th>
          <th>Volume</th>
          <th>Prev close</th>
          <th>Quote at ({zoneName(quoteZone)})</th>
          <th>Trade at ({zoneName(quoteZone)})</th>
          <th class="text">State</th>
        </tr>
      </thead>
      <tbody>
        {#each quotes.view.symbols as symbol (symbol)}
          {@const q = quotes.view.rows.get(symbol)}
          {#if q}
            <!-- Every symbol of the topic is of the market's zone (lib/zones.ts), so the columns
                 name the zone once. -->
            {@const qz = shownZone(exchangeZone(symbol))}
            {@const state = rowState([q.quoteReceivedUs, q.tradeReceivedUs], basis)}
            <tr data-testid="quote-{symbol}" data-state={state} class:stale={state === 'stale'}>
              <td class="text">{symbol}</td>
              <td class="price" data-col="bid">{decimal(q.bid)}</td>
              <td class="price" data-col="ask">{decimal(q.ask)}</td>
              <td>{count(q.bidSize)}</td>
              <td>{count(q.askSize)}</td>
              <td class="price" data-col="last">{decimal(q.last)}</td>
              <td>{count(q.lastSize)}</td>
              <td>{count(q.dayVolume)}</td>
              <td class="price">{decimal(q.prevClose)}</td>
              <td>{time(q.quoteReceivedUs, qz)}</td>
              <td>{time(q.tradeReceivedUs, qz)}</td>
              <td class="text" data-col="state">{state === 'none' ? 'no data' : state}</td>
            </tr>
          {/if}
        {/each}
      </tbody>
    </table>
    {#if quotes.view.notes.length}
      <ul class="notes" data-testid="quote-notes">
        {#each quotes.view.notes as note (note.symbol)}
          <li><strong>{note.symbol}</strong>: {NOTES[note.kind] ?? 'no data'}. {note.text}</li>
        {/each}
      </ul>
    {/if}
  </Panel>

  <Panel title="Reference data">
    {#snippet info()}
      <p>
        From the health and features routes, every 30 s. The rates are the curve in use (REF-01), or
        the flat rate and why. The calendar is the exchange calendar the sessions come from
        (REF-04). A feature is unavailable when the data tier it needs is absent (GEN-04).
      </p>
    {/snippet}
    {#if reportError}<p class="error">{reportError}</p>{/if}
    {#if report}
      <h3>Rates</h3>
      <dl class="facts">
        {@render fact(
          'In use',
          report.rates.source === 'curve'
            ? 'the curve'
            : `the flat rate, ${decimal(report.rates.flat_rate * 100, 2)}%`,
          'rates-source',
        )}
        {@render fact('Why', report.rates.reason ?? '—')}
        {@render fact('Downloads', yesNo(report.rates.downloads))}
        {@render fact('Refreshed', iso(report.rates.refreshed_at))}
        {@render fact('Next refresh', iso(report.rates.next_refresh_at))}
        {@render fact('Last error', report.rates.last_error ?? '—')}
      </dl>
      {#if report.rates.knots.length}
        <table class="figures">
          <thead
            ><tr><th class="text">Series</th><th>Days</th><th>Rate</th><th>Observed</th></tr></thead
          >
          <tbody>
            {#each report.rates.knots as k (k.series)}
              <tr>
                <td class="text">{k.series}</td>
                <td>{count(k.days)}</td>
                <td>{decimal(k.rate * 100, 3)}%</td>
                <td>{k.observed}</td>
              </tr>
            {/each}
          </tbody>
        </table>
      {/if}
      <h3>Calendar</h3>
      <dl class="facts">
        {@render fact('Source', report.calendar.source, 'calendar-source')}
        {@render fact('Covers', `${report.calendar.first} to ${report.calendar.last}`)}
        {@render fact('Closures', report.calendar.closures.join(', ') || 'none configured')}
      </dl>
      <h3>State file</h3>
      <dl class="facts">
        {@render fact('Schema version', String(report.store.version))}
        {@render fact('Size', `${count(report.store.size)} bytes`)}
        {@render fact(
          'Daily copy',
          report.copies.on ? `on, last ${iso(report.copies.last_at)}` : 'off',
        )}
      </dl>
    {/if}
    {#if tiers}
      <h3>Data tiers</h3>
      <table class="figures" data-testid="features">
        <thead
          ><tr
            ><th class="text">Feature</th><th class="text">Needs</th><th class="text">State</th></tr
          ></thead
        >
        <tbody>
          {#each tiers.features as f (f.name)}
            <tr>
              <td class="text">{f.title}</td>
              <td class="text">{f.needs.join(', ')}</td>
              <td class="text">{f.available ? 'available' : `${f.show}: ${f.reason ?? ''}`}</td>
            </tr>
          {/each}
        </tbody>
      </table>
    {/if}
  </Panel>
</div>

<style>
  .status {
    display: grid;
    grid-template-columns: repeat(4, minmax(0, 1fr));
    grid-template-rows: minmax(0, 1fr) minmax(0, 1fr);
    gap: var(--gap);
    height: 100%;
  }
  .status > :global(:nth-child(5)) {
    grid-column: span 3;
  }
  /* UI-05, decided 2026-11-27 (owner): a row that is not live is greyed as a whole and says
     "stale"; the top bar says that the connection is lost. */
  tr.stale td {
    color: var(--muted);
    background: var(--stale);
  }
  .symbols {
    display: flex;
    align-items: center;
    gap: var(--space-3);
  }
  .symbols input {
    width: 260px;
  }
  .notes {
    margin: var(--space-3) 0 0;
    padding-left: var(--space-5);
    color: var(--muted);
  }
  h3 {
    margin: var(--space-4) 0 var(--space-2);
    font-size: 12px;
    color: var(--muted);
    font-weight: 600;
  }
  h3:first-child {
    margin-top: 0;
  }
  @media (max-width: 1023px), (max-height: 599px) {
    .status {
      grid-template-columns: minmax(0, 1fr);
      grid-template-rows: none;
      height: auto;
    }
    .status > :global(:nth-child(5)) {
      grid-column: auto;
    }
  }
</style>
