import { useId, type ReactNode } from 'react'
import { useShortcutKeys } from '../api/config.ts'
import type { Enhancement } from '../api/types.ts'
import { keyLabel, useHeldKey } from './useShortcuts.ts'

/**
 * Making a scan readable before OCR reads it again: which way is up, and a treatment with its sliders.
 * The server renders the result (scan_enhance.py), so the preview is exactly what OCR is given. Shared by
 * the Marked Workshop and the Experiment bench.
 */
export const TREATMENTS = [
  ['none', 'As scanned'], ['clahe', 'Local contrast'], ['contrast', 'Contrast + gamma'], ['whiten', 'Whiten paper'],
  ['mend', 'Mend white lines'],
] as const

export const ORIENTATIONS = [
  ['', '↑ upright'], ['left', '← top left'], ['right', '→ top right'], ['down', '↓ upside down'],
] as const

export const DEFAULT_ENHANCEMENT: Enhancement = {
  top_points: '', degrees: 0, treatment: 'none', clip: 3, grid: 8, contrast: 2.5, gamma: 0.5, lightness: 200, chroma: 10,
  reach: 1.5, darkness: 2,
}

/** Whether `value` changes the scan at all (a turn, a straightening or a treatment). */
export const isTreated = (value: Enhancement) =>
  value.top_points !== '' || Boolean(value.degrees) || value.treatment !== 'none'

/** How a straightening reads to a person: which way, and how far (as Fix Rotation says it). */
const describeTilt = (degrees: number) =>
  degrees === 0 ? 'no turn' : `${Math.abs(degrees).toFixed(1)}° ${degrees > 0 ? 'counter-clockwise' : 'clockwise'}`

/**
 * The turn and the treatment; with ``straighten`` (the Workshop), a straightening slider after the turn, and
 * ``suggestion``, what the rotation detectors said of the page.
 */
export function TreatmentControls({ value, onChange, straighten = false, suggestion = null }: {
  value: Enhancement
  onChange: (patch: Partial<Enhancement>) => void
  straighten?: boolean
  suggestion?: ReactNode
}) {
  const degrees = value.degrees ?? 0
  return (
    <>
      {suggestion && <p className="ingest-note">{suggestion}</p>}
      <div className="field">
        <label>The top of the page points</label>
        <div className="segmented">
          {ORIENTATIONS.map(([top, label]) => (
            <button key={top} className={value.top_points === top ? 'on' : ''}
              onClick={() => onChange({ top_points: top })}>{label}</button>
          ))}
        </div>
      </div>
      {straighten && (
        <div className="field">
          <label htmlFor="treatment-straighten">Then straighten it <strong>{describeTilt(degrees)}</strong></label>
          <input id="treatment-straighten" type="range" min={-15} max={15} step={0.1} value={-degrees}
            onChange={(e) => onChange({ degrees: Math.round(-Number(e.target.value) * 10) / 10 || 0 })} />
        </div>
      )}

      <div className="field">
        <label>Treatment</label>
        <div className="segmented">
          {TREATMENTS.map(([treatment, label]) => (
            <button key={treatment} className={value.treatment === treatment ? 'on' : ''}
              onClick={() => onChange({ treatment })}>{label}</button>
          ))}
        </div>
      </div>

      {value.treatment === 'clahe' && (
        <div className="curate-grid">
          <Slider label="Strength" value={value.clip} min={1} max={10} step={0.5} onChange={(clip) => onChange({ clip })} />
          <Slider label="Detail size" value={value.grid} min={2} max={16} step={1} onChange={(grid) => onChange({ grid })} />
        </div>
      )}
      {value.treatment === 'contrast' && (
        <div className="curate-grid">
          <Slider label="Contrast" value={value.contrast} min={0.5} max={3} step={0.1}
            onChange={(contrast) => onChange({ contrast })} />
          <Slider label="Gamma" value={value.gamma} min={0.2} max={3} step={0.1} onChange={(gamma) => onChange({ gamma })} />
        </div>
      )}
      {value.treatment === 'whiten' && (
        <div className="curate-grid">
          <Slider label="Lightness floor" value={value.lightness} min={128} max={255} step={1}
            onChange={(lightness) => onChange({ lightness })} />
          <Slider label="Colour floor" value={value.chroma} min={1} max={80} step={1}
            onChange={(chroma) => onChange({ chroma })} />
        </div>
      )}
      {value.treatment === 'mend' && (
        <>
          <p className="ingest-note">
            For white lines down a receipt, where the printer's head has dead dots: the ink is smeared sideways
            to close the breaks. More reach for wider lines, less if characters run together.
          </p>
          <div className="curate-grid">
            <Slider label="Reach" value={value.reach} min={0.5} max={3} step={0.1}
              onChange={(reach) => onChange({ reach })} />
            <Slider label="Darkness" value={value.darkness} min={1} max={3} step={0.1}
              onChange={(darkness) => onChange({ darkness })} />
          </div>
        </>
      )}
    </>
  )
}

export function Slider({ label, value, min, max, step, onChange }: {
  label: string
  value: number
  min: number
  max: number
  step: number
  onChange: (value: number) => void
}) {
  const id = useId()
  return (
    <div className="field">
      <label htmlFor={id}>{label} <strong>{value}</strong></label>
      <input id={id} type="range" min={min} max={max} step={step} value={value}
        onChange={(e) => onChange(Number(e.target.value))} />
    </div>
  )
}

/** Whether the Hold original shortcut (R unless changed in Config) is held while something is treated. */
export function useHoldOriginal(treated: boolean): boolean {
  const key = useShortcutKeys()?.hold_original
  return useHeldKey(key ?? '', treated && key !== undefined)
}

/** The keys that look past what is drawn on a treated scan: the original under it, the scan under the boxes. */
export function CompareKeys({ treated }: { treated: boolean }) {
  const keys = useShortcutKeys()
  if (!keys) return null
  return (
    <p className="config-hint workshop-compare">
      {treated ? <>Hold <kbd>{keyLabel(keys.hold_original)}</kbd> to see the original</> : 'Nothing is treated yet'}
      {' · '}hold <kbd>{keyLabel(keys.hide_boxes)}</kbd> to hide the boxes
    </p>
  )
}
