import { useEffect, useRef, useState, type KeyboardEvent, type PointerEvent, type ReactNode } from 'react'
import type { Trim } from '../api/types.ts'
import { WHOLE } from './TrimmedImage.tsx'
import './TrimEditor.css'

/** The thinnest band a trim may keep, as a fraction of the page (models.MIN_TRIM_BAND). */
export const MIN_BAND = 0.02
const STEP = 0.005
const BIG_STEP = 0.05

type Edge = 'top' | 'bottom'

const clamp = (value: number, low: number, high: number) => Math.min(high, Math.max(low, value))
/** Four decimals, as the server keeps them, so a trim read back compares equal to the one saved. */
const rounded = (value: number) => Math.round(value * 10000) / 10000
const percent = (value: number) => `${Math.round(value * 1000) / 10}%`

/**
 * Two rulers across the whole scan, one for the top cut and one for the bottom: what lies outside them (a
 * coupon, a survey, a header) is never read by OCR and never shown. The scan itself is never changed, so
 * a cut can always be moved back out.
 *
 * Drag a ruler, or press anywhere on the scan to bring the nearer ruler there. Each ruler is also a
 * slider: focus it and use the arrow keys (Shift for bigger steps), Home and End. Nothing is kept until
 * Save; `value` is the trim as saved (null: the whole page).
 */
export function TrimEditor({ src, alt, value, onSave, saving = false, blockedBy = null, error = null, note }: {
  /** The whole scan, as the file is stored: a trim is measured on it. */
  src: string
  alt: string
  value: Trim | null
  onSave: (trim: Trim | null) => void
  saving?: boolean
  /** Why saving has to wait (a job is reading this page), or null. */
  blockedBy?: string | null
  error?: string | null
  note?: ReactNode
}) {
  const saved = value ?? WHOLE
  const [draft, setDraft] = useState<Trim>(saved)
  // A save (or another tab's) changes what is stored: start again from it.
  useEffect(() => setDraft({ top: saved.top, bottom: saved.bottom }), [saved.top, saved.bottom])
  const stage = useRef<HTMLDivElement>(null)
  const drag = useRef<{ edge: Edge; offset: number } | null>(null)
  const [dragging, setDragging] = useState<Edge | null>(null)

  const place = (edge: Edge, at: number) => setDraft((current) => edge === 'top'
    ? { ...current, top: clamp(at, 0, current.bottom - MIN_BAND) }
    : { ...current, bottom: clamp(at, current.top + MIN_BAND, 1) })
  const fractionAt = (clientY: number) => {
    const box = stage.current?.getBoundingClientRect()
    return box && box.height > 0 ? (clientY - box.top) / box.height : 0
  }

  const onPointerDown = (event: PointerEvent<HTMLDivElement>) => {
    if (event.button !== 0) return
    const at = fractionAt(event.clientY)
    const handle = (event.target as HTMLElement).closest<HTMLElement>('[data-edge]')?.dataset.edge as Edge | undefined
    // A ruler grabbed by its handle keeps where it was grabbed; a press on the scan brings the nearer ruler.
    const edge = handle ?? (Math.abs(at - draft.top) <= Math.abs(at - draft.bottom) ? 'top' : 'bottom')
    drag.current = { edge, offset: handle ? at - draft[edge] : 0 }
    if (!handle) place(edge, at)
    setDragging(edge)
    event.currentTarget.setPointerCapture(event.pointerId)
    // No text selection or image drag, which also stops the press focusing anything: focus the ruler
    // that moved, so the arrow keys fine-tune it straight after.
    event.preventDefault()
    stage.current?.querySelector<HTMLElement>(`[data-edge="${edge}"]`)?.focus()
  }
  const onPointerMove = (event: PointerEvent<HTMLDivElement>) => {
    if (drag.current) place(drag.current.edge, fractionAt(event.clientY) - drag.current.offset)
  }
  const endDrag = () => {
    drag.current = null
    setDragging(null)
  }
  const onKey = (edge: Edge) => (event: KeyboardEvent<HTMLDivElement>) => {
    const step = event.shiftKey ? BIG_STEP : STEP
    const moves: Record<string, number> = {
      ArrowUp: draft[edge] - step, ArrowDown: draft[edge] + step, PageUp: draft[edge] - BIG_STEP,
      PageDown: draft[edge] + BIG_STEP, Home: 0, End: 1,
    }
    const to = moves[event.key]
    if (to === undefined) return
    event.preventDefault()
    place(edge, to)
  }

  const kept = { top: rounded(draft.top), bottom: rounded(draft.bottom) }
  const whole = kept.top <= 0 && kept.bottom >= 1
  const changed = kept.top !== rounded(saved.top) || kept.bottom !== rounded(saved.bottom)

  const ruler = (edge: Edge, label: string) => (
    <div className={`trim-editor__ruler trim-editor__ruler--${edge}${dragging === edge ? ' is-dragging' : ''}`}
      style={{ top: percent(draft[edge]) }}>
      <div className="trim-editor__handle" data-edge={edge} role="slider" tabIndex={0} aria-label={label}
        aria-orientation="vertical" aria-valuemin={0} aria-valuemax={100}
        aria-valuenow={Math.round(draft[edge] * 1000) / 10} aria-valuetext={`${percent(draft[edge])} down the page`}
        onKeyDown={onKey(edge)}>
        ✂ {edge === 'top' ? 'Top' : 'Bottom'}
      </div>
    </div>
  )

  return (
    <div className="trim-editor">
      <div className={`trim-editor__stage${dragging ? ' is-dragging' : ''}`} ref={stage}
        onPointerDown={onPointerDown} onPointerMove={onPointerMove} onPointerUp={endDrag} onPointerCancel={endDrag}>
        <img src={src} alt={alt} draggable={false} />
        <div className="trim-editor__off" style={{ top: 0, height: percent(draft.top) }} />
        <div className="trim-editor__off" style={{ top: percent(draft.bottom), bottom: 0 }} />
        {ruler('top', 'Top cut')}
        {ruler('bottom', 'Bottom cut')}
      </div>

      <div className="trim-editor__bar">
        <span className="trim-editor__kept">
          {whole ? 'Nothing removed' : `${percent(1 - (kept.bottom - kept.top))} removed`}
          {changed && <em> · not saved</em>}
        </span>
        <button disabled={whole || saving} onClick={() => setDraft(WHOLE)} title="Move both rulers back to the edges">
          Whole page
        </button>
        <button disabled={!changed || saving} onClick={() => setDraft(saved)}>Undo changes</button>
        <button className="primary" disabled={!changed || saving || blockedBy !== null}
          onClick={() => onSave(whole ? null : kept)}>
          {saving ? 'Saving…' : 'Save trim'}
        </button>
      </div>
      {blockedBy && <p className="ingest-note">Waiting for {blockedBy} to finish before a trim can be saved.</p>}
      {error && <div className="error-banner" role="alert">{error}</div>}
      {note && <p className="ingest-note trim-editor__note">{note}</p>}
    </div>
  )
}
