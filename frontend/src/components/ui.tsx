import type { ReactNode } from 'react'
import { truncate } from '../format.ts'

export function Card({ title, hint, children, className = '' }:
  { title?: ReactNode; hint?: ReactNode; children?: ReactNode; className?: string }) {
  return (
    <section className={`card ${className}`}>
      {(title || hint) && (
        <div className="card-head">
          {title && <h2>{title}</h2>}
          {hint && <span className="hint">{hint}</span>}
        </div>
      )}
      {children}
    </section>
  )
}

/** Charts always render into a fixed-height box (layout rule 1). */
export function ChartCard({ title, hint, children }: { title?: ReactNode; hint?: ReactNode; children?: ReactNode }) {
  return (
    <Card title={title} hint={hint}>
      <div className="chart-box">{children}</div>
    </Card>
  )
}

export function Tile({ label, value }: { label: ReactNode; value: ReactNode }) {
  return (
    <div className="tile">
      <div className="label">{label}</div>
      <div className="value">{value ?? '—'}</div>
    </div>
  )
}

export function Loading({ what = 'data' }: { what?: string }) {
  return <div className="state">Loading {what}…</div>
}

export function ErrorState({ error }: { error: unknown }) {
  const message = error instanceof Error ? error.message : String(error)
  return <div className="state error">Failed to load: {message}</div>
}

export function Empty({ children = 'Nothing to show.' }: { children?: ReactNode }) {
  return <div className="state">{children}</div>
}

/** Tooltip styling that follows the light/dark CSS variables. */
export const tooltipStyle = {
  contentStyle: {
    background: 'var(--panel)',
    border: '1px solid var(--border)',
    borderRadius: 8,
    fontSize: 12,
    color: 'var(--text)',
  },
  labelStyle: { color: 'var(--muted)', fontSize: 11 },
  itemStyle: { color: 'var(--text)' },
}

const niceStep = (raw: number): number => {
  if (!(raw > 0)) return 1
  const mag = 10 ** Math.floor(Math.log10(raw))
  const n = raw / mag
  return (n <= 1 ? 1 : n <= 2 ? 2 : n <= 5 ? 5 : 10) * mag
}

export type NiceAxis = { domain: [number, number | 'auto']; ticks?: number[] }

/** Axis domain + explicit round ticks for a value column.
 *
 *  Why not let Recharts do it: its automatic domain nice-rounds the tick
 *  interval, so ONE slightly-negative month (refunds outweighing purchases)
 *  can drag the floor down a whole tick step and donate a quarter of the plot
 *  to blank space. Passing a literal domain fixes the space but yields
 *  arbitrary edge labels (e.g. 387,512).
 *
 *  So: tight domain (floor only deep enough for the actual negatives) plus
 *  hand-built round ticks from 0 up. Round labels, ~2% negative band, and no
 *  data is ever clipped out of view.
 */
export function niceAxis<T>(rows: readonly T[] | undefined, key: keyof T, tickCount = 5): NiceAxis {
  const values = (rows ?? [])
    .map((r) => r[key])
    .filter((v): v is T[keyof T] & number => typeof v === 'number' && !Number.isNaN(v))
  if (!values.length) return { domain: [0, 'auto'] }

  const dataMin = Math.min(0, ...values)
  const dataMax = Math.max(0, ...values)
  if (dataMax === 0) return { domain: [dataMin, 1], ticks: [0] }

  const step = niceStep(dataMax / tickCount)
  const max = Math.ceil(dataMax / step) * step
  const floorUnit = step / 10
  const min = dataMin < 0 ? -Math.ceil(-dataMin / floorUnit) * floorUnit : 0

  const ticks: number[] = []
  for (let t = 0; t <= max + step / 1000; t += step) ticks.push(Math.round(t))
  return { domain: [min, max], ticks }
}

export const axisProps = {
  stroke: 'var(--muted)',
  tick: { fill: 'var(--muted)', fontSize: 11 },
  tickLine: false,
}

/** Y-axis label for horizontal bars — truncated so long JP names can't
 *  push the plot area into nothing (layout rule 1 corollary). */
export const truncTick = (n: number) => (v: unknown): string => truncate(v == null ? '' : String(v), n)
