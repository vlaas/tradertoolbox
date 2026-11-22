/** Application-drawn layers as series primitives: the expected-range cone with the expiry line
 * (check 3) and the right-edge profile with strike lines (check 4). Neither implements hitTest,
 * so pointer events pass through to the chart and the drawing plugin. Each keeps the coordinates
 * of its last paint for the checks. */
import type {
  IChartApi, IPrimitivePaneRenderer, IPrimitivePaneView, ISeriesApi, ISeriesPrimitive, SeriesAttachedParameter,
  SeriesType, Time, UTCTimestamp,
} from 'lightweight-charts'
import type { FutureBar, Session } from './data'

type Target = Parameters<IPrimitivePaneRenderer['draw']>[0]

abstract class Layer implements ISeriesPrimitive<Time> {
  protected chart: IChartApi | null = null
  protected series: ISeriesApi<SeriesType> | null = null
  private update: (() => void) | null = null
  private readonly views: readonly IPrimitivePaneView[]
  visible = true

  constructor(z: 'bottom' | 'normal' | 'top' = 'top') {
    const renderer: IPrimitivePaneRenderer = {
      draw: (target: Target) => target.useMediaCoordinateSpace(({ context, mediaSize }) => {
        if (this.visible && this.chart && this.series) this.paint(context, mediaSize.width, mediaSize.height)
      }),
    }
    this.views = [{ zOrder: () => z, renderer: () => renderer }]
  }
  attached({ chart, series, requestUpdate }: SeriesAttachedParameter<Time>): void {
    this.chart = chart as IChartApi
    this.series = series as ISeriesApi<SeriesType>
    this.update = requestUpdate
  }
  detached(): void { this.chart = this.series = this.update = null }
  paneViews(): readonly IPrimitivePaneView[] { return this.views }
  redraw(): void { this.update?.() }
  setVisible(v: boolean): void { this.visible = v; this.redraw() }
  protected abstract paint(ctx: CanvasRenderingContext2D, width: number, height: number): void
  protected x(t: number): number | null { return this.chart!.timeScale().timeToCoordinate(t as UTCTimestamp) }
  protected y(p: number): number | null { return this.series!.priceToCoordinate(p) }
}

export interface ConeSpec { origin: FutureBar; spot: number; iv: number; expiry: FutureBar; path: FutureBar[]; label: string }

/** One-standard-deviation range from `origin` to `expiry`, and a vertical line at the expiry. */
export class ConeLayer extends Layer {
  spec: ConeSpec | null = null
  last: { originX: number | null; expiryX: number | null; upperY: number | null; lowerY: number | null } | null = null

  constructor() { super('bottom') }
  set(spec: ConeSpec | null): void { this.spec = spec; this.redraw() }

  protected paint(ctx: CanvasRenderingContext2D, _w: number, height: number): void {
    const s = this.spec
    if (!s) return
    const YEAR = 365 * 86400
    const up: [number, number][] = [], dn: [number, number][] = []
    for (const p of [s.origin, ...s.path, s.expiry]) {
      const x = this.x(p.time)
      if (x == null) continue
      const sd = s.iv * Math.sqrt(Math.max(0, p.utc - s.origin.utc) / YEAR)
      const yu = this.y(s.spot * Math.exp(sd)), yd = this.y(s.spot * Math.exp(-sd))
      if (yu == null || yd == null) continue
      up.push([x, yu]); dn.push([x, yd])
    }
    const ex = this.x(s.expiry.time)
    this.last = { originX: this.x(s.origin.time), expiryX: ex, upperY: up.at(-1)?.[1] ?? null, lowerY: dn.at(-1)?.[1] ?? null }
    if (up.length > 1) {
      ctx.beginPath()
      up.forEach(([x, y], i) => (i ? ctx.lineTo(x, y) : ctx.moveTo(x, y)))
      for (let i = dn.length - 1; i >= 0; i--) ctx.lineTo(dn[i][0], dn[i][1])
      ctx.closePath()
      ctx.fillStyle = 'rgba(96,165,250,0.10)'
      ctx.fill()
      ctx.strokeStyle = 'rgba(96,165,250,0.55)'
      ctx.lineWidth = 1
      for (const line of [up, dn]) {
        ctx.beginPath()
        line.forEach(([x, y], i) => (i ? ctx.lineTo(x, y) : ctx.moveTo(x, y)))
        ctx.stroke()
      }
    }
    if (ex != null) {
      ctx.beginPath()
      ctx.setLineDash([4, 4])
      ctx.strokeStyle = '#f59f00'
      ctx.moveTo(ex, 0); ctx.lineTo(ex, height)
      ctx.stroke()
      ctx.setLineDash([])
      ctx.font = '11px system-ui, sans-serif'
      ctx.fillStyle = '#f59f00'
      ctx.textAlign = 'right'
      ctx.fillText(s.label, ex - 4, 12)
      ctx.textAlign = 'left'
    }
  }
}

export interface StrikeLine { price: number; label: string; colour: string; dashed: boolean }
export interface ProfileSpec {
  strikes: StrikeLine[]
  /** P/L at expiry by underlying price, ascending in price. */
  payoff: { price: number; pl: number }[]
  /** Open interest by strike (sample numbers in the prototype). */
  oi: { strike: number; call: number; put: number }[]
}

/** Payoff curve and open-interest histogram pinned to the right edge of the pane, with labelled
 * strike lines. Everything is placed by price through the series' price scale. */
export class ProfileLayer extends Layer {
  spec: ProfileSpec | null = null
  /** Width of the band in px, measured from the right edge of the pane. */
  band = 190
  last: { width: number; strikeY: Record<string, number | null>; zeroX: number } | null = null

  constructor() { super('top') }
  set(spec: ProfileSpec | null): void { this.spec = spec; this.redraw() }

  autoscaleInfo(): { priceRange: { minValue: number; maxValue: number } } | null {
    if (!this.visible || !this.spec?.strikes.length) return null
    const p = this.spec.strikes.map((s) => s.price)
    return { priceRange: { minValue: Math.min(...p), maxValue: Math.max(...p) } }
  }

  protected paint(ctx: CanvasRenderingContext2D, width: number, _h: number): void {
    const s = this.spec
    if (!s) return
    const left = width - this.band, zeroX = width - this.band * 0.42
    const strikeY: Record<string, number | null> = {}
    ctx.font = '11px system-ui, sans-serif'
    // open interest: bars growing left from the right edge, calls above the strike row, puts below
    const maxOi = Math.max(1, ...s.oi.flatMap((o) => [o.call, o.put]))
    for (const o of s.oi) {
      const y = this.y(o.strike)
      if (y == null) continue
      const k = (this.band * 0.36) / maxOi
      ctx.fillStyle = 'rgba(77,171,247,0.45)'
      ctx.fillRect(width - o.call * k, y - 3, o.call * k, 3)
      ctx.fillStyle = 'rgba(218,119,242,0.45)'
      ctx.fillRect(width - o.put * k, y, o.put * k, 3)
    }
    // zero axis of the payoff
    ctx.strokeStyle = '#30353f'
    ctx.lineWidth = 1
    ctx.beginPath(); ctx.moveTo(zeroX, 0); ctx.lineTo(zeroX, _h); ctx.stroke()
    // payoff at expiry: x is P/L, y is price
    const maxPl = Math.max(1, ...s.payoff.map((p) => Math.abs(p.pl)))
    const kx = (this.band * 0.5) / maxPl
    for (let i = 1; i < s.payoff.length; i++) {
      const a = s.payoff[i - 1], b = s.payoff[i]
      const ya = this.y(a.price), yb = this.y(b.price)
      if (ya == null || yb == null) continue
      ctx.strokeStyle = (a.pl + b.pl) / 2 >= 0 ? '#26a69a' : '#ef5350'
      ctx.lineWidth = 2
      ctx.beginPath(); ctx.moveTo(zeroX + a.pl * kx, ya); ctx.lineTo(zeroX + b.pl * kx, yb); ctx.stroke()
    }
    // strike and breakeven lines with labels
    for (const k of s.strikes) {
      const y = this.y(k.price)
      strikeY[k.label] = y
      if (y == null) continue
      ctx.strokeStyle = k.colour
      ctx.lineWidth = 1.5
      ctx.setLineDash(k.dashed ? [6, 4] : [])
      ctx.beginPath(); ctx.moveTo(left, y); ctx.lineTo(width, y); ctx.stroke()
      ctx.setLineDash([])
      ctx.fillStyle = k.colour
      ctx.textBaseline = 'bottom'
      ctx.fillText(k.label, left + 4, y - 2)
    }
    this.last = { width, strikeY, zeroX }
  }
}

/** Background tint for the bars outside regular trading hours: early and late hours lightly, the
 * overnight session a little more. `marks` are the chart times of all bars, past and future. */
export class SessionLayer extends Layer {
  private marks: { time: number; s: Session }[] = []
  last: { runs: { s: Session; from: number; to: number }[] } | null = null

  constructor() { super('bottom') }
  set(marks: { time: number; s: Session }[]): void { this.marks = marks; this.redraw() }

  protected paint(ctx: CanvasRenderingContext2D, width: number, height: number): void {
    const m = this.marks
    if (!m.length) { this.last = { runs: [] }; return }
    const half = this.chart!.timeScale().options().barSpacing / 2
    // first mark that can be on screen
    let lo = 0, hi = m.length - 1
    while (lo < hi) {
      const mid = (lo + hi) >> 1
      const x = this.x(m[mid].time)
      if (x == null || x < -half) lo = mid + 1; else hi = mid
    }
    const runs: { s: Session; from: number; to: number }[] = []
    for (let i = lo; i < m.length; i++) {
      const x = this.x(m[i].time)
      if (x == null) continue
      if (x - half > width) break
      const r = runs[runs.length - 1]
      if (r && r.s === m[i].s && Math.abs(r.to - (x - half)) < 1.5) r.to = x + half
      else runs.push({ s: m[i].s, from: x - half, to: x + half })
    }
    for (const r of runs) {
      if (r.s === 'rth') continue
      ctx.fillStyle = r.s === 'night' ? 'rgba(139,145,156,0.13)' : 'rgba(139,145,156,0.065)'
      ctx.fillRect(r.from, 0, r.to - r.from, height)
    }
    this.last = { runs }
  }
}

