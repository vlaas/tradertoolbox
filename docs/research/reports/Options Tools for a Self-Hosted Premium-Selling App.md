# Options Tools for a Self-Hosted Premium-Selling App: Survey, Gaps and a Ranked Feature List (as of 22 Nov 2026)

The biggest gap for your plan is not a better pricing view. It is position management: grouping a trade's legs and rolls into one **campaign** with its total result, comparing **roll candidates side by side**, and checking each open position against **management rules** (for example 21 DTE, 50% of maximum profit, or a loss limit). Today traders spread these jobs across a broker platform, a journal and spreadsheets, and all of them can be built from the tier-1 Tastytrade data you already have.

## TL;DR

- **Add first:** a campaign (roll-chain) ledger, a roll/adjustment comparator, a rule engine for 21 DTE, profit target and loss limit with alerts, and earnings and ex-dividend dates shown against open positions. Each needs only tier-1 data and low-to-medium effort. Paid tools split these jobs between them: tastytrade tracks order chains, Trademetria and TradesViz merge rolls, and IBKR has a rollover tool. No single tool covers all four.
- **Downgrade or relabel:** open-interest gamma exposure with an assumed dealer side. Chilingarian (SSRN, 6 Oct 2026) concludes that the common open-interest sign convention is "a proxy whose premise open interest cannot verify". Amaya, Garcia-Ares, Pearson and Vasquez (Cboe-hosted, 25 Jan 2025) rebuilt market-maker positions from Cboe trade records; they found the largest gamma-driven rise in S&P 500 volatility was 3.3 points, which they call "not large". Also show POP as a "held to expiry" number, so it is not read as the probability for a trade you will close early.
- **Not honest with your data:** any intraday hedging-flow indicator, historical IV-rank or skew percentiles from before your recordings start (unless you buy tier 3), and live Greeks on every SPX strike at once. Use the tastytrade backtester API as a stopgap for historical checks.

## How to read this report

- **Sourced:** stated by a named vendor page, review site, paper or repository. Prices give the check date reported by that source.
- **[VM]** marks claims whose only source is the vendor's own marketing or a competitor's comparison page.
- **Judgement:** my own assessment, labelled as such.
- **Thin evidence for forum demand.** My search tool almost never returned Reddit threads, so I have very few verified forum quotes (see Unmet needs). Treat the demand evidence as weak and partly inferred from what vendors chose to build.

## 1. Tool inventory (prices dated)

| Tool | Price and tiers (date checked, by whom) | Main features | What users use it for | Data source (where documented) |
|---|---|---|---|---|
| OptionStrat | Free (15-min delayed); Live Tools $39.99/mo; Live Flow $99.99/mo; annual saves 12% (membership page snapshot 13 Oct 2026, findtradingtools; also checked 5 Aug 2026 by realtimeoption)\[1\] | Builder, optimizer, P/L chart and table, flow; earnings and ex-dividend dates in the builder; saved-strategy tracking with history (findmymoat)\[2\] | Building trades and payoff views; reviewers say Live Flow only pays off for daily flow readers (agenttrading.ai)\[3\] | Real-time OPRA on paid plans (aistockpickerapp); Bjerksund-Stensland American model (agenttrading.ai)\[3\]\[4\] |
| OptionNet Explorer (ONE) | 10-day trial £10; 3 months £165; 12 months £535, excluding VAT, "Non-commercial use only" (vendor payment page, current)\[5\] | Desktop design, manual historical backtesting, live monitoring in one environment; adjustments and commissions tracked into cumulative P&L (warriortrading)\[5\]\[6\]\[7\] | Manual backtesting of adjusted multi-leg trades; reviewers call it the best tool for that job (optionstradingiq)\[8\]\[9\] | 5-minute historical data (financialtechwiz); broker connections; Windows only (optionstradingiq)\[8\]\[10\] |
| tastytrade platform | Free with account; options $1 to open, $0 to close, $10 cap per leg (stockbrokers.com, quantvps checked 10 Oct 2026)\[11\]\[12\] | Curve analysis, beta-weighted delta, order chains, IV rank, liquidity rating, backtesting tool (quantvps, stockbrokers.com)\[11\]\[12\] | Premium selling with portfolio beta-weighting; reviewers note no economic calendar in the app (stockbrokers.com)\[11\] | Own feed; open API and an official MCP server (developer.tastytrade.com)\[13\] |
| thinkorswim (Schwab) | Free with account | Analyze tab: Add Simulated Trades, Risk Profile (with portfolio beta weighting), Probability Analysis, thinkBack, Earnings; OnDemand replays past sessions (Schwab thinkManual and FAQ)\[14\]\[15\] | Risk profiles, price slices, historical option prices via thinkBack (optionspilot)\[16\] | Schwab; thinkBack "nearly a decade" of options data (fsonews, Oct 2026)\[17\] |
| Interactive Brokers TWS | Free with account | Risk Navigator: what-if portfolio, custom price/date/volatility scenarios, beta analysis, risk by underlying and industry. Probability Lab, Volatility Lab (nine tools incl. multi-expiry skew over time, OI across expirations, IV vs HV against peers), Rollover Options, ORATS Backtester (IBKR pages)\[18\]\[19\]\[20\] | Portfolio risk drill-down and scenarios\[19\] | IBKR |
| TradeStation OptionStation Pro | Not verified in this pass | Not verified in this pass | — | — |
| Option Alpha | Pro $149/mo or $99/mo billed annually; free with qualifying TradeStation ($10k), Tradier or tastytrade ($5k) accounts; 50 bots, $100k per bot (alltradingtools, checked 25 Sep 2026)\[21\] | No-code bots, backtester, SmartPricing | Automating rule-based premium selling | Connected broker |
| Option Omega | Lite $29.99, Essential $99.99, Plus $129.99, Premium $299.99 per month; annual $24.99/$49.99/$74.99/$249.99 (ORATS comparison page, read 29 Sep 2026)\[22\] | 1-minute backtests on 12 tickers; SPX PM only; payoff modeling; automation on Premium; no paper trading (ORATS page citing OO docs)\[22\] | Systematic 0DTE and short-DTE backtests | 1-minute data back to 2013 (financialtechwiz); fills are mid plus a flat amount regardless of order size (backtest.ai test)\[23\]\[24\] |
| ORATS | Trading Tools $99/mo individual, $199/user pro, $49 student;\[22\] Data API $199 (delayed) / $299 (live) / $599 (live intraday) / $899 (all-in) (orats.com, Sep 2026)\[22\]\[25\] | Backtester on any US optionable name on daily data since 2007, about 140 names on 1-minute data since Oct 2020; option scanner ranked by theoretical edge; paper trading;\[22\] broker orders via IBKR, TradeStation, Tradier [VM for comparisons]\[22\]\[25\]\[26\] | Backtesting and scanning | Smoothed-surface ("SMV") Greeks and IV (orats.com)\[27\] |
| Cboe LiveVol | Not verified in this pass | — | — | Cboe |
| Market Chameleon\[28\] | Total Access / Premium $99/mo,\[29\] 7-day trial; Options Trader SKU marked sold out (daystoexpiry, 13 Aug 2026)\[30\] | Earnings implied vs actual moves, IV crush, earnings backtests, screeners, CSV downloads (optionsmatrixpro, daystoexpiry) [competitor pages]\[28\]\[30\] | Earnings research and screening | Delayed 15 min on public pages (daystoexpiry)\[30\] |
| Barchart | Not verified (search budget exhausted) | — | — | — |
| OptionCharts, Predicting Alpha | Not verified in this pass | — | — | — |
| SpotGamma | Essential $99/mo, Alpha $299/mo; annual $891 / $2,691 (SpotGamma support page); 75% off first month until 30 Nov (homepage, current)\[31\]\[32\] | Equity Hub (3,500+ stocks), key levels, HIRO, TRACE, Founder's Notes. Conflict: the homepage puts HIRO/TRACE in Alpha; tradeecho (13 Oct 2026) says both are now on Essential\[31\]\[33\] | Daily levels and commentary | Own model; no public API (flashalpha)\[34\] |
| MenthorQ | Free tier; Premium $129/mo; Pro $349/mo; first month $39 / $174.50; yearly $1,164 / $3,108 (findtradingtools, 8–9 Sep 2026; MenthorQ guides)\[35\]\[36\] | Gamma levels incl. futures options, quant models, QUIN AI, 10 chart integrations\[37\] | Futures-options gamma levels on charts [VM] | Own models |
| Unusual Whales | Free; Retail Basic $50, Pro $75, Max $120 per month (stated on a MenthorQ comparison page, competitor source)\[36\] | Flow, GEX heatmap, SPX market-maker exposure\[36\] | Flow (out of your scope) | — |
| TradingView options | Not verified in this pass | — | — | — |
| TradesViz | Basic free (stocks only); Pro $19.99/mo or $14.99/mo yearly; Platinum $29.99/mo or $22.49/mo yearly (optionclaws, Nov 2026). Conflict: another review gives $13.99 for Pro yearly\[38\]\[39\] | Options Greeks vs performance, spread detection, options backtester and flow on Platinum; API and MCP on Pro and Platinum (financialtechwiz)\[40\]\[41\] | Deep journal analytics | Broker imports (250+)\[39\] |
| Tradervue / TraderSync | Both from $29.95/mo (tradespad); TraderSync auto-recognises spreads on every plan; Tradervue's free plan does not cover options (financialtechwiz)\[40\]\[42\] | Journals | Journaling | Imports |
| Trademetria (added) | From $19.95/mo; free plan 30 orders/month (financialtechwiz)\[40\] | Manual merge of spreads and rolls into one trade with total P&L; auto expire and assign (trademetria.com)\[43\] | Frequent rollers\[40\] | Imports incl. tastytrade\[43\] |
| Days to Expiry (added) | Pro $50/mo (vendor, verified 23 Sep 2026) [VM]\[44\] | Roll-adjusted cost basis, per-leg P&L, weekly execution plan [VM]\[44\] | Wheel tracking\[44\] | IBKR Flex, SnapTrade\[44\] |
| Optioneer (added) | Not verified | Wheel Tracker, Roll Assistant, scenario views [VM]\[6\] | Position management\[6\] | — |

Closed or renamed: none of the starting list was confirmed closed in this pass. tastyworks is now tastytrade.\[45\]

## 2. Feature inventory

Tier column: 1 = live broker data, 2 = your own recordings, 3 = local minute history; "none" = cannot be supplied. Effort is my judgement for one developer on your stack.

| Group | Feature | Tools that have it | Data needed | Lowest tier | Effort |
|---|---|---|---|---|---|
| Construction | Multi-leg builder from chain | OptionStrat, ToS, tastytrade, ONE | Chain, quotes | 1 | planned |
| Construction | Optimizer (best structure for a price target) | OptionStrat, IBKR Strategy Builder/Probability Lab | Chain, quotes, model | 1 | medium |
| Construction | Events (earnings, ex-div) in the builder\[2\] | OptionStrat | Event dates | 1 | low |
| Payoff/what-if | Price, date and vol curves | All builders | Model | 1 | planned |
| Payoff/what-if | Portfolio-level what-if with real positions | IBKR Risk Navigator, ToS beta-weighted Risk Profile\[14\]\[19\] | Positions, Greeks, beta | 1 | medium |
| Probability/risk | POP at expiry, P50, CVaR | tastytrade, ToS, OptionStrat | Model | 1 | planned |
| Probability/risk | Market-implied distribution vs own view | IBKR Probability Lab\[18\] | Full chain | 1 | medium |
| Volatility | Skew, term structure, IVR | IBKR Vol Lab, tastytrade, ORATS | Chain, metrics | 1 | planned |
| Volatility | Skew over time, IV vs HV against peers | IBKR Volatility Lab\[20\] | IV series, peers | 2 | medium |
| Volatility | Earnings implied vs past actual moves | Market Chameleon | Event dates, candles, past implied moves | 1 (actual), 2 (past implied) | low–medium |
| Positioning | OI by strike, GEX estimate | SpotGamma, MenthorQ, Unusual Whales | Daily OI, Greeks | 1 | planned |
| Positioning | Intraday hedging-flow indicator (HIRO type) | SpotGamma | Signed trade tape | none | — |
| Scanning | Metric pre-filter, strategy screens | Market Chameleon, ORATS, ToS | Metrics, chains | 1 | planned |
| Scanning | Ranking by theoretical edge\[22\] | ORATS | Smoothed surface plus history | 2–3 | high |
| Charting | Ticket on chart | (planned; rare elsewhere) | Candles, ticket | 1 | planned |
| Management | Order chains / roll history\[45\] | tastytrade Order Chains\[12\] | Transactions | 1 | low |
| Management | Roll candidate tool | IBKR Rollover Options, Optioneer Roll Assistant [VM], Robinhood analyzer "Roll"\[18\]\[46\] | Chain, position | 1 | medium |
| Management | Beta-weighted delta, BP use | tastytrade, ToS, IBKR | Positions, beta, balances | 1 | low |
| Journaling | Merge rolls into one campaign\[47\] | Trademetria (manual), Days to Expiry [VM], TradesViz\[40\] | Transactions | 1 | low–medium |
| Journaling | Performance by strategy, tag, Greek at entry\[40\] | TradesViz | Transactions, entry snapshot | 1–2 | medium |
| Journaling | Fill vs mid ("execution quality")\[48\] | Premium Tracker Sheets add-on\[49\] | Fills, quote at fill | 2 | low |
| Alerts | Rule exits (profit target, stop, DTE) | Option Alpha bots, Option Omega exits | Positions, quotes | 1 | low |
| Backtesting | Minute-level structured backtests | Option Omega, ORATS | Minute history | 3 | high |
| Backtesting | Manual "replay a day" backtest | ONE, ToS OnDemand/thinkBack\[8\]\[17\] | Historical chains | 3 | high |
| Backtesting | Broker-hosted backtest API | tastytrade Backtester (via hplin/tastytrade-research-mcp)\[50\] | Broker API | 1 | low–medium |

## 3. Unmet needs (what the sources show)

**Verified forum evidence (thin):**
- Elite Trader, Options forum, "I gave up on my options tracking spreadsheet…": "you end up constantly fixing formulas and barely having time left to think about trades". Date not shown on the page; probably 2025–2026. A wheel seller says spreadsheets break once rolls, assignments and cost-basis adjustments come in.\[51\] Caveat: the poster was promoting a tracker of their own.\[51\]
- Option Omega feature board (optionomega.canny.io, viewed 22 Nov 2026; titles and votes only, no dates): "Show P/L Per Strategy In Portfolio Test Results" (58 votes), "IVR filter to add" (63), "Automation — Avoid Wide BID-ASK spreads on exits" (42), "Live trades - show account" (23), "Cumulative Model for all live trades" (4). Most are marked complete,\[52\] so the demand was real but has been met inside that product.

**What vendor behaviour suggests (inference):** several products now sell the same missing piece. Days to Expiry markets "roll-adjusted cost basis" and says r/thetagang tracking is a manual weekly spreadsheet ritual [VM].\[44\] Trademetria's main options pitch is merging rolls into one trade.\[43\] Optioneer sells a "Roll Assistant" [VM].\[6\] tastytrade has offered Order Chains since a Business Wire announcement on 21 Dec 2020 ("tastyworks Announces Breakthrough in Brokerage Technology"); the feature tracks a trade "from its open, to any rolls or adjustments, to its close" and promised traders could "kiss their spreadsheet goodbye".

**Judgement:** the gaps that most often force a second or third subscription are these:
1. Campaign P&L that survives rolls, assignment and multiple brokers.
2. Roll comparison with buying-power effect.
3. Performance by strategy and management rule.
4. Earnings implied-vs-actual history, which needs Market Chameleon at $99.\[29\]
5. Backtesting, which needs Option Omega or ORATS at $99–$100\[22\] on top of a builder at $40.

Building the same workflow from Market Chameleon ($99) and OptionStrat Live Tools ($39.99) costs $138.99/month, by one competitor's arithmetic [VM].\[28\]

## 4. Workflows across tools

| Step that forces a tool switch | Who links it well, and how |
|---|---|
| Chart to payoff | ToS: linked panels carry symbol and strikes from watchlist to chain to Analyze (contentwave review).\[53\] Rarely does any tool draw the ticket on the underlying chart; your plan already covers this. |
| Scan result to ticket | ORATS: scanner, backtester and broker order in one dashboard.\[26\] IBKR Probability Lab: strategy scanner from a custom distribution.\[54\] |
| Open position to roll comparison | tastytrade: curve analysis on the existing position.\[55\] IBKR: Rollover Options.\[18\] Robinhood: analyzer with Open/Close/Roll (not for multi-leg).\[46\] None shows several roll candidates side by side with BP and probability deltas (judgement). |
| Live trade to backtest of same structure | ONE: backtest and live monitoring in a single environment [VM].\[5\]\[6\] Option Omega: Model plus Backtest tiers, and live-trade comparison requests on its board. ORATS: paper trading and backtester share data.\[22\] |
| Position to journal | TradesViz and Trademetria import from tastytrade, but campaigns must be merged by hand (Trademetria).\[43\] |

**Judgement:** your shared trade ticket is the right core. Extend it with a **campaign ID** so a roll creates a new ticket version inside the same campaign. Then the chart, what-if, journal and backtester all inherit the history.

## 5. Managing and reviewing positions: best-in-class displays

- **Roll/adjustment candidates:** IBKR Rollover Options and tastytrade curve analysis show the effect of one roll on the risk profile (purepowerpicks review).\[55\] No source showed a ranked multi-candidate table. Build one with columns for net credit, new breakevens, days added, delta change, BP change, POP/P50 change and credit per day added (judgement).
- **Rules (21 DTE, profit target):** only the automation tools (Option Alpha bots, Option Omega exits) encode rules. Analysis platforms leave rules to the trader.
- **Portfolio view:** tastytrade shows beta-weighted delta, with positions grouped by underlying and net delta/theta per group (insigtrade).\[56\] IBKR Risk Navigator adds risk by underlying and industry, plus what-if portfolios.\[19\] ToS adds a beta-weighted Risk Profile.\[14\] Earnings and dividend dates against open positions appear in OptionStrat's builder,\[2\] but in no portfolio view found.
- **Campaigns:** tastytrade Order Chains; Trademetria manual merge; Days to Expiry [VM].\[12\]\[43\]\[44\]
- **Tagging/performance by strategy:** TradesViz (Greeks vs performance, spread detection).\[40\] Premium Tracker scores trades and reports win rate by delta bracket and execution quality.\[48\]\[49\]

## 6. Volatility and positioning displays: useful vs decoration

- **Used for decisions (sourced plus judgement):** IV rank/percentile and term structure (in every premium-selling platform); IV vs historical volatility (IBKR Vol Lab compares 30-day IV and HV against price);\[57\] earnings implied vs past actual moves (Market Chameleon's core earnings product).\[28\]
- **Mostly decoration (judgement):** 3D surfaces, and single-day skew without history.
- **GEX from open interest, the evidence:**
  - Amaya, Garcia-Ares, Pearson and Vasquez, "0DTE Index Options and Market Volatility: How Large is Their Impact?" (Cboe-hosted, 25 Jan 2025), used Cboe trade records tagged by participant (July 2020–June 2023). Customer-vs-market-maker trades were only 68.7% of SPX volume, and aggregate market-maker gamma was usually *positive*. The paper reports "the median impact being to reduce daily volatility by 0.08 percentage points"; the largest effect was +3.3 points, against a 4.5-point standard deviation of daily changes in realised volatility.
  - Adams, Dim, Eraker, Fontaine, Ornthanalai and Vilkov, "Do S&P 500 Options Increase Market Volatility? Evidence from 0DTEs" (15 Jul 2026), found that on 0DTE expiration days 10-minute S&P 500 realised volatility was "lower by 61 annualized basis points", about 7%. A secondary summary also reports market-maker gamma positive in about 88% of 10-minute intervals; I could not confirm that figure in the paper.
  - Chilingarian, "The Sign of Dealer Gamma…" (SSRN, 6 Oct 2026), found that the per-strike "gamma flip" shown on dashboards is "one of a median seven candidates near spot". It differs from the repriced zero-gamma root by a median 17 index points (563 S&P 500 sessions, 2023–2025).
  - The SEC Staff Report on Equity and Options Market Structure Conditions in Early 2021 (18 Oct 2021) states: "Staff did not find evidence of a gamma squeeze in GME during January 2021."
  - Official OI is prior-night data and stays static during the session (SpotGamma citing the Cboe FAQ).\[58\]
- **Conclusion:** OI by strike is fact; signed GEX is a convention.\[59\]\[60\] For a 30–45 DTE seller its value is marginal.

## 7. Scanners and alerts

**Criteria used by premium sellers:** IV rank, liquidity, earnings exclusion, credit at a target delta, credit/width, bid-ask width. Sourced: the IVR filter is the most-voted Option Omega request (63); tastytrade shows a liquidity rating and IV rank;\[11\] the bid-ask-on-exit request had 42 votes.\[52\] The rest is judgement.

**What works within your subscription limits (judgement):**
- Stage 1, pre-filter on per-symbol metrics: IVR, IV−HV, liquidity rating, days to earnings, beta. This costs no stream subscriptions.
- Stage 2, sweep only the 2–3 expirations in your DTE windows and only strikes within about ±2 expected moves. One symbol at roughly 40 strikes × 2 sides × 3 expirations is about 240 option subscriptions, so 50–100 symbols per rotation fit well under 25,000 alongside your positions.
- Alerts that work: price crossing a short strike or breakeven, % of max profit reached, DTE threshold, IVR crossing, earnings entering the expiry window, ex-dividend before expiry on short ITM calls.
- Alerts that do not work: whole-market flow, intraday OI changes.

## 8. LLM use

- **Broker MCP servers:**
  - tastytrade's official MCP server (TypeScript, 30 stars, updated 12 Oct 2026). `TASTYTRADE_READ_ONLY=1` "withholds and refuses all 14 write and destructive tools". It exposes Resources ("computed account views") and Prompts ("pre-composed tool-call plans").\[61\]\[62\] Order flow is gated by a dry run first.\[13\] Its README says read-only mode is its only lever and there is no tool-filtering variable.\[62\]
  - Community servers: hplin/tastytrade-mcp (Python, live trading off by default behind `ENABLE_LIVE_TRADING`, keyring credentials);\[63\] ferdousbhai/tasty-agent (`get_greeks` capped at 100 contracts and a 30 s timeout);\[64\] mgcrea/mcp-tastytrade (REST fallback when DXLink is off; `get_chain_with_greeks`, earnings and dividend history);\[65\] hplin/tastytrade-research-mcp (wraps the tastytrade Backtester API: available-dates, create_backtest, simulate_trade).\[50\]
- **Vendor assistants:** ORATS "Otto",\[66\] MenthorQ QUIN, TradesViz API/MCP, FlashAlpha hosted MCP [VM].
- **What is reported to work or fail:** I found no independent evaluations. One arXiv paper (2512.17923) tests whether LLMs can infer gamma patterns from anonymised data.\[67\] It is a research claim, not tool evidence.
- **Design lessons (from the READMEs plus judgement):**
  1. Use coarse, task-level tools ("position_review", "roll_candidates") over raw-quote tools. tasty-agent's 100-contract cap shows that raw chain dumps do not scale.
  2. Return numbers with units, as-of timestamps and data tier, and have every computed figure come from a tool.
  3. Make read-only structural: do not register write tools at all, rather than relying on a flag.
  4. Expose daily digests as MCP Resources, as tastytrade does with computed account views.

## 9. Open source

| Project | Licence / language / activity | Reuse or learn |
|---|---|---|
| tastytrade/tastytrade-mcp | TypeScript; official; updated Oct 2026\[61\] | Read-only gating, resource/prompt pattern, dry-run flow |
| tastytrade/tastytrade-api-js | TypeScript; 107 stars\[61\] | API and DXLink reference |
| tastytrade/tastytrade-sdk-python | Python; **archived**\[61\] | Do not build on it |
| hplin/tastytrade-research-mcp | Backtester API wrapper\[50\] | Learn the endpoint shapes for a tier-1 "quick backtest" |
| mgcrea/mcp-tastytrade | TypeScript; Docker image\[65\] | REST fallback when the stream is saturated |
| TradeTally | Free, open-source, self-hosted journal (alternativeto)\[68\] | Journal schema ideas; licence not verified |

Not verified in this pass (my own knowledge; check licence and activity before use): QuantLib (BSD-style), py_vollib, OpenBB (AGPL), optopsy (options backtester).

## 10. Free complementary data (not verified in this pass; check terms)

From my own knowledge, not sourced here:
- Rates: FRED and the US Treasury par yield curve.
- Volatility indices: Cboe's historical VIX-family downloads.
- Futures positioning: CFTC Commitments of Traders (public).
- Volume and OI statistics: OCC and Cboe daily market statistics.
- Earnings timing: SEC EDGAR 8-K filings.

Commercial earnings and economic calendars (Nasdaq and similar) usually prohibit scraping. tastytrade already supplies earnings and dividend dates, so prefer those.

## Ranked shortlist: 15 features not in your plan

| # | Feature | Tier | Effort | Best existing tool | Why it earns a place (judgement) |
|---|---|---|---|---|---|
| 1 | Campaign ledger: legs, rolls and assignments grouped, with net credit, total P&L and days in trade | 1 | low–med | tastytrade Order Chains; Trademetria | Your main management actions (close, roll) are invisible without it, and the journal depends on it. |
| 2 | Roll/adjustment comparator: ranked candidates with credit, breakevens, days added, Δdelta, ΔBP, ΔPOP/P50 | 1 | medium | IBKR Rollover Options | Turns your most frequent decision into one screen. No tool found ranks several candidates. |
| 3 | Management-rule engine (21 DTE, x% max profit, loss multiple) with per-position status and alerts | 1 | low | Option Alpha bots | Encodes your own rules without order automation. |
| 4 | Event overlay: earnings and ex-dividend dates against every open expiry; flag for early-assignment dividend risk | 1 | low | OptionStrat (builder only) | Data is already in tastytrade metrics; no portfolio view found shows it. |
| 5 | Concentration and BP dashboard: by underlying, expiry week and sector; BP utilisation; theta/BP | 1 | low–med | IBKR Risk Navigator | Premium sellers usually fail through clustered risk, not single trades. |
| 6 | BP impact preview for a ticket or roll via order dry run | 1 | low | tastytrade MCP dry run | Gives real broker margin, not an estimate. Needs a trade-scope token, so keep it isolated. |
| 7 | Performance analytics by strategy, tag, DTE, delta and IVR at entry, and by exit reason | 1–2 | medium | TradesViz | Shows whether your management rules actually pay. |
| 8 | Entry-context snapshot plus decision log on every ticket version | 1–2 | low | none found | Makes post-mortems and rule review honest. |
| 9 | Earnings implied move vs historical realised earnings moves | 1 (realised), 2 (past implied) | low–med | Market Chameleon | Replaces a $99/month subscription for event decisions. |
| 10 | Expected-move scorecard per symbol (how often the 1-SD move held) | 2 | low | none found | Calibrates strike selection on your own names. |
| 11 | Vol cone / IV vs subsequent realised volatility (premium-richness history) | 1 (cones), 2 (IV) | medium | IBKR Volatility Lab | Tests directly whether selling is paid. |
| 12 | Fill-quality tracker: fill vs mid at fill time, per symbol | 2 | low | Premium Tracker | Slippage is a large hidden cost on wide chains. |
| 13 | Beta-plus-IV-shock scenario grid for the whole portfolio | 1 | medium | IBKR Risk Navigator custom scenarios | Short-vega books lose on joint price and vol moves. |
| 14 | Bridge to the tastytrade Backtester API before tier 3 exists | 1 | low–med | hplin/tastytrade-research-mcp | Gives historical evidence from day one. |
| 15 | Daily management digest as an MCP Resource (rules hit, events, alerts, concentration) | 1 | low | tastytrade MCP computed views | The safest and most useful LLM entry point. |

## Planned features of low value or commonly misleading

- **Signed GEX by strike and the "gamma flip":** the dealer side is assumed, the flip is ambiguous (median seven candidates, 17 points apart), and the measured volatility effect is small (Amaya et al., Chilingarian).\[69\]\[70\] Keep OI by strike; label GEX as a convention.
- **POP at expiry as a headline number:** your trades are closed early, so POP at expiry is the wrong event. Lead with P50 or "probability of hitting the target before the DTE limit" (judgement).
- **Replicas of tastytrade POP, P50 and CVaR:** useful for consistency, but these are vendor numbers you cannot audit. Show your own model next to them, never alone (judgement).
- **Automatic intraday range marking:** low value at 30–45 DTE; keep it for 0DTE only (judgement).
- **Open interest in intraday views:** OI is static prior-night data (Cboe via SpotGamma) and misleads if shown as live.\[58\]

## Attractive but not honestly doable with your data

- **Intraday hedging-flow or "HIRO-style" indicators:** these need a signed, market-wide trade tape, which you do not have.
- **Dealer-positioning sign:** cannot be recovered from OI (Chilingarian; Amaya et al.).\[69\]\[70\]
- **IV-rank, skew or term-structure percentiles from before your install date:** no historical option prices without tier 3. tastytrade's IVR is a single current value, not a series.
- **Backtests of earnings IV crush or expected-move accuracy before your recordings start:** same reason.
- **Live Greeks on every SPX strike and expiry at once:** one chain exceeds 25,000 subscriptions, so the view must be windowed.
- **Market-wide real-time scanners on live Greeks:** only feasible as a rotating sweep, so results are stale by up to one rotation.
- **Theoretical-edge ranking like ORATS:** needs a smoothed surface fitted on long history. It becomes possible only after months of tier-2 data or with tier 3.

## Caveats

- Several prices come from third-party reviews that report their own check dates; promotions (SpotGamma, MenthorQ) change monthly.
- TradeStation OptionStation Pro, Cboe LiveVol, Barchart, OptionCharts, Predicting Alpha and TradingView's options features were not verified in this pass.
- Forum demand evidence is thin: one verified post with no confirmed date, plus one vendor board.
- The rankings and effort estimates are my judgement for your stack.

## Sources

1. [OptionStrat Review (2026): Free plan, Live Tools \$39.99 a month](https://findtradingtools.com/tools/optionstrat/)
2. [OptionStrat Review, Pricing, and Features (2026)](https://www.findmymoat.com/tools/optionstrat)
3. [OptionStrat Review and Whether Live Flow Is Worth It](https://agenttrading.ai/blog/optionstrat-review)
4. [OptionStrat Review 2026: What the Free Tier Won't Show You](https://aistockpickerapp.com/reviews/optionstrat/deep-dive)
5. [ONE 10 Day Trial Subscription](https://www.optionnetexplorer.com/PaymentONEDISC_Trial_vks92oe92lj93msjwoqw9291k2l1m2k0.aspx)
6. [Optioneer vs OptionNet Explorer: Portfolio Workflow or Backtesting?](https://optioneer.io/compare/optionnet-explorer)
7. [OptionNET Explorer Review](https://www.warriortrading.com/optionnet-explorer-review-2017/)
8. [What are the alternatives to OptionVue?](https://optionstradingiq.com/optionvue-alternatives/)
9. [Best Options Backtesting Software - TechBullion](https://techbullion.com/best-options-backtesting-software/)
10. [Best Options Backtesting Software](https://www.financialtechwiz.com/post/best-options-backtesting-software/)
11. [tastytrade Review 2026: Pros & Cons - StockBrokers.com](https://www.stockbrokers.com/review/tastytrade)
12. [Best Options Brokers (2026): 6 Compared on Fees and Tools](https://quantvps.com/blog/top-options-trading-platforms-compared-and-ranked)
13. [MCP Server · tastytrade Developer Docs](https://developer.tastytrade.com/docs/sdks-and-tools/mcp-server/)
14. [FAQ - Analyze](https://toslc.thinkorswim.com/center/faq/Analyze)
15. [Analyze](https://toslc.thinkorswim.com/center/howToTos/thinkManual/Analyze)
16. [Schwab thinkorswim Options Tools Guide: Everything You Should Use](https://optionspilot.app/blog/schwab-thinkorswim-options-tools-guide)
17. [FSO NEWS: thinkorswim Backtesting: How to Test Options Strategies Step by Step](https://www.fsonews.com/2026/10/blog-post_232.html)
18. [Options Trading Tools Built for Your Strategy](https://www.interactivebrokers.com/en/trading/options-trading-tools.php)
19. [Risk Navigator](https://www.interactivebrokers.com/en/trading/risk-navigator.php)
20. [Volatility Lab](https://www.interactivebrokers.co.uk/en/software/pdfhighlights/PDF-Vol_Lab.php)
21. [Option Alpha: Features, Pricing and Alternatives (2026)](https://alltradingtools.com/tools/option-alpha/)
22. [ORATS vs Option Omega: 0DTE Backtesting Compared](https://orats.com/compare/orats-vs-option-omega)
23. [Best Options Backtesting Software (2026)](https://backtest.ai/compare/best-options-backtesting-software)
24. [Option Omega Review 2026: Backtest, Automate, Pricing](https://www.financialtechwiz.com/post/option-omega-review/)
25. [ORATS vs ThetaData: Options Data API Comparison](https://orats.com/compare/orats-vs-thetadata)
26. [Trading Tools - An all-in-one package for options research, backtesting, trading, and risk management.](https://orats.com/trading-tools)
27. [ORATS Pricing & Cost (2026) — Subscription Plans & Review](https://newsletters.markets/cost/orats)
28. [Options Matrix Pro vs Market Chameleon: Features & Pricing](https://www.optionsmatrixpro.com/compare/market-chameleon)
29. [Is Market Chameleon Worth It And What Does It Bring To The Table?](https://www.modestmoney.com/is-market-chameleon-worth-it/)
30. [Market Chameleon Alternative for Options Income Traders](https://www.daystoexpiry.com/blog/market-chameleon-alternative)
31. [SpotGamma](https://spotgamma.com/)
32. [What is the cost of a SpotGamma Subscription?](https://support.spotgamma.com/hc/en-us/articles/1500002666102-What-is-the-cost-of-a-SpotGamma-Subscription)
33. [Best GEX Tools for Options Traders (2026), Compared](https://tradeecho.com/best-gex-tools)
34. [SpotGamma Review 2026: Pricing, Features, and How It Compares](https://flashalpha.com/articles/spotgamma-review-2026-pricing-features-alternatives)
35. [MenthorQ Review and Pricing — Find Trading Tools](https://findtradingtools.com/tools/menthorq/)
36. [MenthorQ vs Unusual Whales: Options Flow or Positioning Data? Guide - MenthorQ](https://menthorq.com/guide/menthorq-vs-unusual-whales-options-flow-or-positioning-data/)
37. [MenthorQ Review 2026: Gamma Levels, QUIN AI and Pricing](https://seventrading.net/brokers/menthorq-review-2026/)
38. [TradesViz Review 2026: Pros & Cons](https://daytradingz.com/tradesviz-review/)
39. [Best Options Trading Journal 2026, by Use Case · OptionClaws](https://optionclaws.com/best-options-trading-journals)
40. [Best Trading Journal for Options 2026: 7 Compared](https://www.financialtechwiz.com/post/best-trading-journal-for-options/)
41. [TradesViz Review 2026: Is It Worth It? Plans, AI and MCP](https://www.financialtechwiz.com/post/tradesviz-review/)
42. [TradesViz Review (2026): Features, Pricing, Pros & Cons and More](https://tradespad.com/blog/tradesviz-review)
43. [Options trading journal](https://trademetria.com/options-trading-journal)
44. [r/thetagang Tracker Alternative 2026: Ditch the Weekly Spreadsheet](https://www.daystoexpiry.com/thetagang-tracker-alternative)
45. [www.businesswire.com](https://www.businesswire.com/news/home/20201221005210/en)
46. [Options analyzer | Robinhood](https://robinhood.com/us/en/support/articles/options-analyzer)
47. [Complete guide to import settings in TradesViz - TradesViz Blog](https://www.tradesviz.com/blog/import-settings-guide/)
48. [Options trader journal with analytics](https://workspace.google.com/marketplace/app/premium_tracker/87170287711?hl=fil)
49. [Options trader journal with analytics](https://workspace.google.com/marketplace/app/premium_tracker/87170287711?hl=it)
50. [GitHub - hplin/tastytrade-research-mcp: Research-focused MCP server for tastytrade historical market data and backtesting, including Backtester API integration and historical candles for XSP/SPX and SPY options regression testing. · GitHub](https://github.com/hplin/tastytrade-research-mcp)
51. [I gave up on my options tracking spreadsheet, how do you track P&L and assignments?](https://www.elitetrader.com/et/threads/i-gave-up-on-my-options-tracking-spreadsheet-how-do-you-track-p-l-and-assignments.388041/)
52. [Option Omega Feedback](https://optionomega.canny.io/)
53. [Thinkorswim Options Tools Review — 2026 Desktop Update](https://contentwave.net/article/thinkorswim-review-2026-deep-options-tools-for-active-traders)
54. [Probability Lab](https://cwt1.interactivebrokers.com/probabilitylab/)
55. [Tastytrade Review (2026): Is It Worth It for Options Traders?](https://purepowerpicks.com/tastytrade-review/)
56. [Tastytrade Review 2026: Fees, Platform, and Verdict](https://insigtrade.com/blog/tastytrade-review-2026-fees-platform-and-is-it-worth-it)
57. [Navigating Volatility Lab](https://www.interactivebrokers.com/en/trading/fif-volatility-lab.php)
58. [Gamma Exposure (GEX): How to Read a GEX Chart](https://spotgamma.com/gamma-exposure-gex/)
59. [Gamma Exposure - GEX](https://www.luxalgo.com/library/concept/gamma-exposure/)
60. [Dealer Gamma Exposure (GEX) Explained](https://www.getswoopr.com/options-trading/options-signals/gamma-dealer-positioning/)
61. [tastytrade · GitHub](https://github.com/tastytrade/)
62. [GitHub - tastytrade/tastytrade-mcp: MCP server for the tastytrade open API: quotes, positions, balances, and dry-run-gated order flow for LLM agents. · GitHub](https://github.com/tastytrade/tastytrade-mcp)
63. [GitHub - hplin/tastytrade-mcp: MCP server connecting autonomous AI agents to Tastytrade (OAuth2, keyring-stored credentials, gated live trading with risk controls). · GitHub](https://github.com/hplin/tastytrade-mcp)
64. [GitHub - ferdousbhai/tasty-agent: Let Claude manage your tastytrade portfolio. · GitHub](https://github.com/ferdousbhai/tasty-agent)
65. [GitHub - mgcrea/mcp-tastytrade: Model Context Protocol server for the TastyTrade brokerage API · GitHub](https://github.com/mgcrea/mcp-tastytrade)
66. [Options Backtester - Test your strategies](https://orats.com/backtester)
67. [Inferring Latent Market Forces: Evaluating LLM Detection of Gamma Exposure Patterns via Obfuscation Testing](https://arxiv.org/pdf/2512.17923)
68. [OptionsVault AlternativesStock Trading Apps like OptionsVault](https://alternativeto.net/software/optionsvault)
69. [The Sign of Dealer Gamma: An Inventory Convention, a Scale, and Two Definitions of the Gamma Flip in S&P 500 Gamma Exposure by Aren Chilingarian :: SSRN](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=7131778)
70. [Dealer Gamma Exposure and Its Measured Effect on Realized Volatility](https://quantropy.ai/blog/dealer-gamma-exposure-and-realized-volatility)
