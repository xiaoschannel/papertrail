/** Form controls shared by the OCR and Parse pages. */

export function ReprocessField({ value, onChange, newLabel }:
  { value: boolean; onChange: (reprocess: boolean) => void; newLabel: string }) {
  return (
    <div className="field">
      <label>Mode</label>
      <div className="segmented">
        <button className={value ? '' : 'on'} onClick={() => onChange(false)}>{newLabel}</button>
        <button className={value ? 'on' : ''} onClick={() => onChange(true)}>Redo all</button>
      </div>
    </div>
  )
}

export function LimitField({ value, onChange }: { value: number; onChange: (limit: number) => void }) {
  return (
    <div className="field">
      <label htmlFor="job-limit">At most (0 = all)</label>
      <input id="job-limit" type="number" min={0} step={1} value={value} className="limit-input"
        onChange={(e) => onChange(Math.max(0, Math.floor(Number(e.target.value) || 0)))} />
    </div>
  )
}

/** The Start button, or why a job can't start right now. */
export function StartBar({ label, disabled, blockedBy, running, pending, onStart }: {
  label: string
  disabled: boolean
  blockedBy: string | null
  running: boolean
  pending: boolean
  onStart: () => void
}) {
  return (
    <div className="start-bar">
      <button className="primary" disabled={disabled || running || blockedBy !== null || pending} onClick={onStart}>
        {pending ? 'Starting…' : label}
      </button>
      {blockedBy && <span className="ingest-note">Waiting for {blockedBy} to finish.</span>}
      {running && <span className="ingest-note">Running — progress below.</span>}
    </div>
  )
}
