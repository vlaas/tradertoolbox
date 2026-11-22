/** The prototype chart: candles with volume in the price pane, four SMA lines, RSI and ATR
 * panes, future whitespace, the drawing plugin, the cone and the right-edge profile, and a
 * simulated live feed. `build` returns the handle the page and the checks use. */
import {
  CandlestickSeries, CrosshairMode, HistogramSeries, LineSeries, createChart,
  type IChartApi, type ISeriesApi, type UTCTimestamp,
} from 'lightweight-charts'
import { DrawingManager, type Drawing, type DrawingKind } from 'lightweight-charts-drawing'
import { BAR_SEC, EXCHANGE, decode, encode, encodeBars, futureInstants, intraday, load, session, type Bar, type FutureBar, type Raw, type Res } from './data'
import { atr, rsi, sma } from './indicators'
import { ConeLayer, ProfileLayer, SessionLayer, type ProfileSpec } from './layers'

const C = { bg: '#0f1115', fg: '#d6d9df', muted: '#8b919c', grid: '#1b1e26', border: '#30353f', up: '#26a69a', down: '#ef5350' }
const SMAS: [number, string][] = [[15, '#f59f00'], [50, '#60a5fa'], [100, '#da77f2'], [200, '#d6d9df']]
const STORE = 's3t-proto:drawings-utc:SPY'
const INTERVAL: Record<Res, string> = { '1D': '1D', '4h': '240', '1h': '60', '15m': '15', '5m': '5' }
const span = (sec: number) => { const s = Math.round(Math.abs(sec) / 60), d = Math.floor(s / 1440), h = Math.floor((s % 1440) / 60), m = s % 60; return [d && `${d}d`, h && `${h}h`, (m || (!d && !h)) && `${m}m`].filter(Boolean).join(' ') }
export const TOOLS: { kind: DrawingKind; label: string }[] = [
  { kind: 'trend-line', label: 'Trendline' },
  { kind: 'horizontal-line', label: 'H line' },
  { kind: 'vertical-line', label: 'V line' },
  { kind: 'date-and-price-range', label: 'Ruler' },
]

function rng(seed: number): () => number {
  return () => {
    seed = (seed + 0x6d2b79f5) | 0
    let t = Math.imul(seed ^ (seed >>> 15), 1 | seed)
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296
  }
}
const frame = () => new Promise<number>((r) => requestAnimationFrame(r))

export async function build(el: HTMLElement) {
  const raw = {} as Record<Res, Raw[]>
  const rawOf = async (r: Res) => (raw[r] ??= await load(r))
  await rawOf('1D'); await rawOf('15m')
  const chart: IChartApi = createChart(el, {
    autoSize: true,
    layout: { background: { color: C.bg }, textColor: C.muted, fontSize: 11, panes: { separatorColor: C.border, enableResize: true } },
    grid: { vertLines: { color: C.grid }, horzLines: { color: C.grid } },
    rightPriceScale: { borderColor: C.border },
    timeScale: { borderColor: C.border, timeVisible: true, secondsVisible: false, rightOffset: 2 },
    crosshair: { mode: CrosshairMode.Normal },
  })
  const candle = chart.addSeries(CandlestickSeries, {
    upColor: C.up, downColor: C.down, wickUpColor: C.up, wickDownColor: C.down, borderVisible: false,
  })
  // volume inside the price pane, on its own overlay scale at the bottom (product definition 6.3)
  const volume = chart.addSeries(HistogramSeries, { priceScaleId: 'vol', priceFormat: { type: 'volume' }, lastValueVisible: false, priceLineVisible: false })
  chart.priceScale('vol').applyOptions({ scaleMargins: { top: 0.84, bottom: 0 } })
  const smas = SMAS.map(([, color]) => chart.addSeries(LineSeries, { color, lineWidth: 1, lastValueVisible: false, priceLineVisible: false, crosshairMarkerVisible: false }))
  // whitespace only: extends the time scale into the future without touching the candle series
  const future = chart.addSeries(LineSeries, { lastValueVisible: false, priceLineVisible: false, crosshairMarkerVisible: false })
  const rsiS = chart.addSeries(LineSeries, { color: '#60a5fa', lineWidth: 1, priceLineVisible: false }, 1)
  const atrS = chart.addSeries(LineSeries, { color: '#f59f00', lineWidth: 1, priceLineVisible: false }, 2)
  const panes = chart.panes()
  panes[0].setStretchFactor(5)
  panes[1]?.setStretchFactor(1)
  panes[2]?.setStretchFactor(1)

  let res: Res = '15m'
  /** The zone of the time axis on intraday charts. Daily charts always show the trading date. */
  let zone = EXCHANGE
  let bars: Bar[] = []
  let fut: FutureBar[] = []
  let futNext = 0
  let carried = 0
  /** Replace the ruler's elapsed time by the true one, taken from the instants. */
  let trueElapsed = true
  let importing = false
  const zoneFor = (r: Res) => (intraday(r) ? zone : EXCHANGE)
  // exact maps for bar times (they may carry an offset, see encodeBars); the formula for anything else
  let byTime = new Map<number, number>(), byUtc = new Map<number, number>()
  const toUtc = (t: number) => byTime.get(t) ?? decode(t, zoneFor(res))
  const toChart = (u: number) => byUtc.get(u) ?? encode(u, zoneFor(res))
  const timings = { parts: {} as Record<string, Record<string, number>>, setData: {} as Record<string, number>, firstFrame: {} as Record<string, number>, updates: [] as number[], frames: [] as number[] }
  const labels: Record<string, unknown> = {}

  const mgr: DrawingManager = new DrawingManager(chart, candle, {
    magnet: 'off',
    timeInfo: () => ({ timeZone: 'UTC', intraday: intraday(res) }),
    touchHitTolerance: 22,
    formatLabel: (l): string | undefined => {
      labels[`${l.drawingId}:${l.id}`] = l
      if (!trueElapsed) return undefined
      const own = (): string | null => { const q = mgr.get(l.drawingId)?.points as { time: number }[] | undefined; return q && q.length > 1 ? span(toUtc(q[1].time) - toUtc(q[0].time)) : null }
      if (l.id === 'time-span') return own() ?? undefined
      if (l.id === 'stats-row') { const o = own(); return o ? l.parts.map((p) => (p.id === 'time-span' ? o : p.text)).join(', ') : undefined }
      return undefined
    },
  })
  /** The drawings with every anchor as a UTC instant: the form that is stored. */
  function exportUtc(): any[] {
    const list = JSON.parse(mgr.exportJSON()) as any[]
    for (const d of list) for (const q of d.points ?? []) q.time = toUtc(q.time)
    return list
  }
  function importUtc(list: any[]): void {
    importing = true
    mgr.importJSON(list.map((d) => ({ ...d, points: (d.points ?? []).map((q: any) => ({ ...q, time: toChart(q.time) })) })))
    importing = false
  }
  mgr.on('change', () => { if (!importing) localStorage.setItem(STORE, JSON.stringify(exportUtc())) })

  const cone = new ConeLayer()
  const profile = new ProfileLayer()
  const sessions = new SessionLayer()
  candle.attachPrimitive(sessions)
  candle.attachPrimitive(cone)
  candle.attachPrimitive(profile)

  function ticket(spot: number): ProfileSpec {
    const r5 = (x: number) => Math.round(x / 5) * 5
    const sp = r5(spot * 0.93), lp = sp - 10, sc = r5(spot * 1.05), lc = sc + 10, credit = 3.07
    const pl = (s: number) => 100 * (credit - Math.max(sp - s, 0) + Math.max(lp - s, 0) - Math.max(s - sc, 0) + Math.max(s - lc, 0))
    const payoff = []
    for (let s = lp - 25; s <= lc + 25; s += 1) payoff.push({ price: s, pl: pl(s) })
    const oi = []
    for (let k = r5(spot * 0.88); k <= r5(spot * 1.1); k += 5) {
      const d = Math.abs(k - spot) / spot
      oi.push({ strike: k, call: Math.round(9000 * Math.exp(-((d - 0.03) ** 2) / 0.002) * (k % 10 ? 0.5 : 1)), put: Math.round(12000 * Math.exp(-((d - 0.05) ** 2) / 0.003) * (k % 10 ? 0.4 : 1)) })
    }
    return {
      strikes: [
        { price: lp, label: `+1 P ${lp}`, colour: C.up, dashed: true }, { price: sp, label: `−1 P ${sp}`, colour: C.down, dashed: false },
        { price: sc, label: `−1 C ${sc}`, colour: C.down, dashed: false }, { price: lc, label: `+1 C ${lc}`, colour: C.up, dashed: true },
        { price: sp - credit, label: `BE ${(sp - credit).toFixed(2)}`, colour: C.muted, dashed: true }, { price: sc + credit, label: `BE ${(sc + credit).toFixed(2)}`, colour: C.muted, dashed: true },
      ],
      payoff, oi,
    }
  }

  let first = true
  async function apply(r: Res, z: string): Promise<void> {
    // drawings leave in UTC under the old mapping and come back under the new one
    const keep = first ? JSON.parse(localStorage.getItem(STORE) ?? '[]') : exportUtc()
    first = false
    const src = await rawOf(r)
    res = r; zone = z
    const enc = encodeBars(src.map((b) => ({ ...b })), zoneFor(r))
    bars = enc.bars; carried = enc.carried
    fut = futureInstants(src, r, r === '1D' ? 60 : 35).map((u) => ({ utc: u, time: encode(u, zoneFor(r)) as UTCTimestamp }))
    futNext = 0
    byTime = new Map(); byUtc = new Map()
    for (const b of [...bars, ...fut]) { byTime.set(b.time, b.utc); byUtc.set(b.utc, b.time) }
    const t0 = performance.now()
    const parts: Record<string, number> = {}
    const timed = (name: string, f: () => void) => { const a = performance.now(); f(); parts[name] = +(performance.now() - a).toFixed(1) }
    chart.applyOptions({ timeScale: { timeVisible: intraday(r) } })
    timed('candles', () => candle.setData(bars))
    timed('volume', () => volume.setData(bars.map((b) => ({ time: b.time, value: b.volume, color: b.close >= b.open ? 'rgba(38,166,154,0.30)' : 'rgba(239,83,80,0.30)' }))))
    timed('sma4', () => SMAS.forEach(([n], i) => smas[i].setData(sma(bars, n))))
    timed('rsi_atr', () => { rsiS.setData(rsi(bars)); atrS.setData(atr(bars)) })
    timed('whitespace', () => future.setData(fut.map((f) => ({ time: f.time }))))
    timings.parts[r] = parts
    timings.setData[r] = performance.now() - t0
    mgr.setInterval(INTERVAL[r])
    importUtc(keep)
    sessions.set(intraday(r) ? [...bars, ...fut].map((b) => ({ time: b.time, s: session(b.utc, BAR_SEC[r]) })) : [])
    const n = bars.length
    chart.timeScale().setVisibleLogicalRange({ from: n - 170, to: n + (r === '1D' ? 30 : 60) })
    chart.priceScale('right').applyOptions({ autoScale: true })
    await frame(); await frame()
    timings.firstFrame[r] = performance.now() - t0
    const last = bars[n - 1]
    const origin = { time: last.time, utc: last.utc }
    const expiry = fut.find((f) => f.utc >= last.utc + 30 * 86400) ?? fut[fut.length - 1]
    const path = fut.filter((f, i) => f.time < expiry.time && (r === '1D' || i % Math.max(1, Math.round(7200 / BAR_SEC[r])) === 0))
    cone.set({ origin, spot: last.close, iv: 0.19, expiry, path, label: 'Expiry, 30 d' })
    profile.set(ticket(raw['1D'][raw['1D'].length - 1].close))
  }
  const setRes = (r: Res) => apply(r, zone)
  const setZone = (z: string) => apply(res, z)

  // ---- simulated live feed
  const rand = rng(7)
  function refreshTail(): void {
    const b = bars[bars.length - 1]
    candle.update(b)
    volume.update({ time: b.time, value: b.volume, color: b.close >= b.open ? 'rgba(38,166,154,0.30)' : 'rgba(239,83,80,0.30)' })
    SMAS.forEach(([n], i) => {
      if (bars.length < n) return
      let s = 0
      for (let k = bars.length - n; k < bars.length; k++) s += bars[k].close
      smas[i].update({ time: b.time, value: s / n })
    })
    const tail = bars.slice(-300)
    const r = rsi(tail).at(-1), a = atr(tail).at(-1)
    if (r) rsiS.update(r)
    if (a) atrS.update(a)
  }
  /** Change the last bar, as a quote update does. */
  function tick(): void {
    const t0 = performance.now()
    const b = bars[bars.length - 1]
    b.close = Math.max(1, b.close + (rand() - 0.5) * b.close * 0.0008)
    b.high = Math.max(b.high, b.close); b.low = Math.min(b.low, b.close); b.volume += Math.round(rand() * 5000)
    refreshTail()
    timings.updates.push(performance.now() - t0)
  }
  /** Start the next bar on the next future time. */
  function append(): boolean {
    const f = fut[futNext]
    if (!f) return false
    futNext++
    const t0 = performance.now()
    const c = bars[bars.length - 1].close
    bars.push({ time: f.time, utc: f.utc, open: c, high: c, low: c, close: c, volume: 0 })
    refreshTail()
    timings.updates.push(performance.now() - t0)
    return true
  }
  let timer = 0, raf = 0, n = 0
  function startLive(ms = 500, appendEvery = 6): void {
    stopLive()
    timings.updates = []; timings.frames = []
    let prev = performance.now()
    const loop = (t: number) => { timings.frames.push(t - prev); prev = t; raf = requestAnimationFrame(loop) }
    raf = requestAnimationFrame(loop)
    timer = window.setInterval(() => (++n % appendEvery === 0 ? append() : tick()), ms)
  }
  function stopLive(): void { clearInterval(timer); cancelAnimationFrame(raf); timer = raf = 0 }

  /** Page coordinates of a chart time and price in the price pane. */
  function px(time: number, price: number): { x: number; y: number } | null {
    const x = chart.timeScale().timeToCoordinate(time as UTCTimestamp), y = candle.priceToCoordinate(price)
    const r = el.getBoundingClientRect()
    return x == null || y == null ? null : { x: r.left + x, y: r.top + y }
  }

  await apply('15m', EXCHANGE)
  return {
    chart, candle, mgr, cone, profile, sessions, timings, labels, toUtc, toChart, exportUtc,
    get zone() { return zone }, get carried() { return carried }, setZone,
    set trueElapsed(v: boolean) { trueElapsed = v; mgr.redraw() },
    get res() { return res }, get bars() { return bars }, get future() { return fut }, get futureNext() { return futNext },
    get live() { return timer !== 0 },
    setRes, tick, append, startLive, stopLive, px,
    drawings: (): Drawing[] => mgr.drawings().map((d) => JSON.parse(JSON.stringify(d))),
    storeKey: STORE,
  }
}
export type Proto = Awaited<ReturnType<typeof build>>
