import { useId } from 'react'
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
] as const

export const ORIENTATIONS = [
  ['', '↑ upright'], ['left', '← top left'], ['right', '→ top right'], ['down', '↓ upside down'],
] as const

export const DEFAULT_ENHANCEMENT: Enhancement = {
  top_points: '', treatment: 'none', clip: 3, grid: 8, contrast: 2.5, gamma: 0.5, lightness: 200, chroma: 10,
}

/** Whether `value` changes the scan at all (a turn or a treatment). */
export const isTreated = (value: Enhancement) => value.top_points !== '' || value.treatment !== 'none'

export function TreatmentControls({ value, onChange }: {
  value: Enhancement
  onChange: (patch: Partial<Enhancement>) => void
}) {
  return (
    <>
      <div className="field">
        <label>The top of the page points</label>
        <div className="segmented">
          {ORIENTATIONS.map(([top, label]) => (
            <button key={top} className={value.top_points === top ? 'on' : ''}
              onClick={() => onChange({ top_points: top })}>{label}</button>
          ))}
        </div>
      </div>

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
