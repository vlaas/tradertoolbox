# Research A: prompt for Claude research mode

Paste everything below the line into a research-mode conversation. The prompt is
self-contained; it needs no access to the local repos.

---

I am building a self-hosted, single-user options-analysis application for my own trading. I
need a survey of what existing tools offer and what traders miss, ending in a ranked list of
features to add to my plan. Please research vendor documentation, pricing pages, trader
forums and open-source repositories, and link a source for every claim about a tool.

## Context

- **Trader:** experienced, self-directed, sells premium. Main horizon 30-45 DTE, also
  7-14 DTE and up to 90 DTE; 0DTE is secondary. Typical structures: short puts and calls,
  strangles, vertical spreads, iron condors, later calendars and diagonals. Positions are
  usually managed before expiry: closed at a share of maximum profit, rolled, or closed at a
  loss limit. US equity, ETF and index options first, futures options later.
- **Application:** a backend on a home server (Python) and a browser frontend. One user, no
  hosted service, no commercial intent, likely open source. It first replaces OptionStrat
  (builder and payoff views) and the part of TradingView I use for underlying charts.
- **Central idea:** the tools share one trade ticket. A ticket built in the option chain can
  be opened on the chart, in the what-if tool, from a scanner result and later in a
  backtester, without retyping anything.
- **Data, in three tiers:**
  1. *Live broker data* (Tastytrade API with DXLink streaming): option chains, quotes,
     trades, Greeks, open interest (updated daily), per-symbol metrics (IV rank and
     percentile, IV per expiration, historical volatility, liquidity rating, beta,
     correlation to SPY, earnings and dividend dates), my positions and transactions, and
     underlying candles (daily for 30 years, intraday for weeks to months). Limits: 25,000
     streaming subscriptions per session (one full SPX chain is larger than that) and 100
     candle subscriptions. No historical option prices. No market-wide trade tape in
     practice.
  2. *Own recordings* from the day of installation: daily chain and volatility-surface
     snapshots, open interest, IV series.
  3. *Optional local history:* 1-minute option prices for a set of liquid underlyings over
     several years, and a local backtester.
- **LLM:** the backend's functions are exposed as read-only tools over MCP, first to Claude.
  The model calls the tools and never computes prices or probabilities itself.

## Already planned

Do not spend effort rediscovering these. Look for what is missing from the list, and for
tools that do one of these things clearly better than the usual way.

- Option chain with expected move, ticket builder, payoff graph and table, what-if on
  price, date and volatility, saved tickets, watchlists.
- POP, P50 (probability of reaching 50% of maximum profit) and CVaR, both as replicas of the
  Tastytrade numbers and from my own models.
- Underlying chart with moving averages, RSI, ATR, volume, trendlines, horizontal and
  vertical lines, a ruler, automatic marking of intraday ranges from a template, and the
  ticket drawn on the chart (strikes, breakevens, expected range, payoff profile).
- Volatility: skew and term structure, IV rank and percentile, expected range and its change
  over time, bid/ask width analysis, open interest and estimated gamma exposure by strike.
- Positions (read-only): delta and gamma risk over time, CVaR per position and portfolio,
  assignment risk, available management moves such as rolling for a credit.
- Scanners with a pre-filter on per-symbol metrics, alerts and notifications, custom markers
  on watchlists.
- With local history: correlation, simulation of positions and management tactics, hand-off
  of a ticket to the backtester.

## Out of scope

- Order entry, a hosted or multi-user service, teaching content.
- Market-wide options flow and unusual-activity feeds (not reachable within the streaming
  limits).
- A scripting language or community indicators for charts.
- The mathematics of probability and risk models. A separate research task covers that;
  here I only need to know which numbers tools show and whether traders use them.

## Questions

1. **Tool inventory.** For each tool: current price and tiers, main features, what users say
   they actually use it for, and its data source where documented. Starting list, which may
   be incomplete or out of date, so add what I missed and drop what has closed: OptionStrat,
   OptionNet Explorer, the tastytrade platform, thinkorswim (Analyze tab, thinkBack,
   OnDemand), Interactive Brokers (Risk Navigator, Probability Lab, Volatility Lab),
   TradeStation OptionStation Pro, Option Alpha, Option Omega, ORATS, Cboe LiveVol, Market
   Chameleon, Barchart, OptionCharts, Predicting Alpha, SpotGamma, MenthorQ, Unusual Whales,
   TradingView's options features, and trade journals such as TradesViz, Tradervue and
   TraderSync.

2. **Feature inventory.** A deduplicated feature list grouped by: trade construction, payoff
   and what-if, probability and risk numbers, volatility analysis, positioning (open interest
   and gamma exposure), scanning, charting, position and portfolio management, journaling and
   performance review, alerts, backtesting. For each feature: which tools have it, the data
   it needs, the lowest of my three tiers that can supply it (or "none"), and a rough effort
   to build (low, medium, high).

3. **Unmet needs.** What do premium sellers ask for that no tool does well, or that needs
   several subscriptions to get? Look at r/thetagang, r/options, r/tastytrade, Elite Trader
   and vendor feedback boards. Quote the requests with links and dates, and prefer the last
   three years.

4. **Workflows across tools.** Which steps force traders to move between tools today, for
   example from a chart to a payoff view, from a scan result to a ticket, from an open
   position to a roll comparison, or from a live trade to a backtest of the same structure?
   Which tools link such steps well, and how?

5. **Managing and reviewing positions.** What do the best tools show a premium seller for:
   comparing roll and adjustment candidates; rules such as managing at 21 DTE or at a profit
   target; the portfolio view (beta-weighted delta, buying-power use, theta, concentration by
   underlying and expiry, earnings and dividend dates against open positions); grouping the
   legs and rolls of one trade into a campaign with its total result; tagging and performance
   by strategy?

6. **Volatility and positioning displays.** Which displays do traders report using for
   decisions, and which are mostly decoration: skew, term structure, surface, IV against
   realised volatility, volatility cones, expected move against realised moves, earnings
   implied move against past earnings moves, open-interest and gamma-exposure profiles? Is
   there evidence for or against the usefulness of gamma exposure estimated from open
   interest with an assumed dealer side?

7. **Scanners and alerts.** Which scan criteria and alert conditions do premium sellers
   actually use? Which of them work when the stream is limited by subscriptions and the
   scanner has to pre-filter on per-symbol metrics and then sweep chains in rotation?

8. **LLM use.** Existing examples of options or brokerage tools that expose functions to a
   language model: MCP servers for brokers and market data, vendor assistants. Which tasks
   are reported to work, which fail, and what is said about tool design (how fine-grained
   the tools are, how results are shaped, how the model is kept from inventing numbers)?

9. **Open source.** Projects that implement any of the above: licence, language, activity
   and quality. Say what could be reused or learned from each, not only that it exists.

10. **Free complementary data.** Sources that are legal for personal use and would fill gaps
    in the broker data: interest rates, volatility indices, earnings and economic calendars,
    dividends, exchange volume and open-interest statistics, futures positioning reports.
    Note the terms of use for each.

## Output wanted

- A tool table (question 1) with prices dated.
- A feature table (question 2).
- A ranked shortlist of 15 features that are **not** in my planned list, by value against
  effort for this trader. For each: data tier, effort, which tool does it best, and one
  sentence on why it earns a place.
- A second list: features in my planned list that the evidence says are of low value or
  commonly misleading, with the reason.
- A third list: features that look attractive but cannot be done honestly with the data
  described above, with the reason.
- Clear separation between what the sources establish and what is your own judgement. Mark
  every claim whose only source is vendor marketing.
