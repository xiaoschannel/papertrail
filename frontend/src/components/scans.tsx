import { useEffect, useState, type ReactNode } from 'react'
import { inputUrl } from '../api/client.ts'
import { useShortcutKeys } from '../api/config.ts'
import type { Batch, TopPoints } from '../api/types.ts'
import { keyLabel, keyOf } from './useShortcuts.ts'

/** The batch picker the File Index pages (Slice, Group) share. */
export function BatchSelect({ id, batches, value, onChange }: {
  id: string
  batches: Batch[]
  value: number
  onChange: (batchId: number) => void
}) {
  return (
    <div className="field">
      <label htmlFor={id}>Batch</label>
      <select id={id} value={value} onChange={(e) => onChange(Number(e.target.value))}>
        {batches.map((b) => (
          <option key={b.batch_id} value={b.batch_id}>
            Batch {b.batch_id} — {b.file_count} files — {b.start_datetime} to {b.end_datetime}
          </option>
        ))}
      </select>
    </div>
  )
}

/** Prev / page N of M / Next, with a box to jump to a page. Renders nothing for a single page. */
export function Pager({ page, pageCount, onPage }: { page: number; pageCount: number; onPage: (page: number) => void }) {
  if (pageCount <= 1) return null
  return (
    <div className="pager">
      <button disabled={page === 0} onClick={() => onPage(page - 1)}>← Prev</button>
      <span>
        Page
        <input className="page-jump" type="number" min={1} max={pageCount} value={page + 1}
          aria-label="Skip to page"
          onChange={(e) => onPage(Math.min(pageCount, Math.max(1, Number(e.target.value) || 1)) - 1)} />
        of {pageCount}
      </span>
      <button disabled={page >= pageCount - 1} onClick={() => onPage(page + 1)}>Next →</button>
    </div>
  )
}

/** The rotate arrows, named for where the top of the page points now. */
export const ARROWS: { top: TopPoints; label: string; title: string }[] = [
  { top: 'left', label: '←', title: 'Top points left' },
  { top: 'right', label: '→', title: 'Top points right' },
  { top: 'down', label: '↓', title: 'Upside down' },
]

/** How a scan whose top points this way looks, in words. */
export const FACING: Record<TopPoints, string> = {
  left: 'on its side, top to the left',
  right: 'on its side, top to the right',
  down: 'upside down',
}

/** The three arrows that turn a scan upright, named for where its top points now. */
export function RotateButtons({ disabled, title, suggested, onRotate }: {
  disabled: boolean
  /** Why they're disabled, when they are for a reason worth saying. */
  title?: string
  /** The arrow for where the scan's top seems to point, when it looks turned: shown lit. */
  suggested?: TopPoints | undefined
  onRotate: (top: TopPoints) => void
}) {
  return (
    <span className="page-tile__rotate" title={title ?? 'Which way the top of the page points now'}>
      {ARROWS.map((a) => (
        <button key={a.top} disabled={disabled} className={a.top === suggested ? 'page-tile__suggested' : undefined}
          title={title ?? (a.top === suggested ? `Looks ${FACING[a.top]} — press to turn it upright` : a.title)}
          onClick={() => onRotate(a.top)}>{a.label}</button>
      ))}
    </span>
  )
}

/** The size to show a scan at when it is turned ``turn`` degrees (a multiple of 90), to fit the viewer:
 *  its box and the scan inside it, which the turn then lays across the box. */
function turnedFit(natural: [number, number], turn: number) {
  const [w, h] = natural
  const sideways = Math.abs(turn) % 180 === 90
  const [boxW, boxH] = sideways ? [h, w] : [w, h]
  const rem = parseFloat(getComputedStyle(document.documentElement).fontSize) || 16
  const scale = Math.min(1, Math.min(69 * rem, window.innerWidth * 0.92) / boxW, (window.innerHeight * 0.72) / boxH)
  return { box: { width: boxW * scale, height: boxH * scale }, scan: { width: w * scale, height: h * scale } }
}

/** A scan at full size, for deciding whether a page continues the previous document, is upright, or
 *  where to cut a sheet. The dialogs' Cancel shortcut closes it. ``turn`` previews it turned (degrees
 *  clockwise, a multiple of 90) without changing it; ``footer`` goes under the caption. */
export function ScanViewer({ label, filename, version, onClose, children, turn = 0, footer }: {
  label: string
  filename: string
  /** The file's mtime, so a rotated scan isn't shown from the browser's cache. */
  version: number
  onClose: () => void
  children?: ReactNode
  turn?: number
  footer?: ReactNode
}) {
  const [natural, setNatural] = useState<[number, number] | null>(null)
  const close = useShortcutKeys()?.cancel
  useEffect(() => {
    if (!close) return undefined
    const onKey = (event: KeyboardEvent) => {
      if (keyOf(event) === close && !event.ctrlKey && !event.metaKey && !event.altKey) onClose()
    }
    document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
  }, [close, onClose])

  return (
    <div className="modal-backdrop scan-viewer" data-modal-open onClick={onClose} role="dialog" aria-modal="true">
      <figure onClick={(e) => e.stopPropagation()}>
        {(() => {
          const img = (style?: object) => (
            <img src={inputUrl(filename, version)} alt={`Scan ${label}`} style={style}
              onLoad={(e) => setNatural([e.currentTarget.naturalWidth, e.currentTarget.naturalHeight])} />
          )
          if (!turn || natural === null) return img(turn ? { visibility: 'hidden' } : undefined)
          const fit = turnedFit(natural, turn)
          return (
            <div className="scan-viewer__turned" style={fit.box}>
              {img({ ...fit.scan, maxWidth: 'none', maxHeight: 'none', transform: `translate(-50%, -50%) rotate(${turn}deg)` })}
            </div>
          )
        })()}
        <figcaption>
          <strong>{label}</strong> {filename}
          {children}
          <button onClick={onClose}>Close{close && <> <kbd>{keyLabel(close)}</kbd></>}</button>
        </figcaption>
        {footer}
      </figure>
    </div>
  )
}

/** "1:9–11" for consecutive keys of one batch, "1:9" for one. */
export function keyRange(keys: string[]): string {
  const first = keys[0]
  const last = keys[keys.length - 1]
  if (first === undefined || last === undefined) return ''
  return first === last ? first : `${first}–${last.split(':')[1] ?? last}`
}
