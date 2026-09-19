import { Link } from 'react-router-dom'
import type { ReactNode } from 'react'

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
  // "Set the … path in Config first." is a setup step, not a failure: send the user there.
  if (message.includes('in Config first')) {
    return <div className="state">{message} <Link className="rowlink" to="/config">Open Config →</Link></div>
  }
  return <div className="state error">Failed to load: {message}</div>
}

export function Empty({ children = 'Nothing to show.' }: { children?: ReactNode }) {
  return <div className="state">{children}</div>
}
