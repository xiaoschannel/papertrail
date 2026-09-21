import { useEffect, type ReactNode } from 'react'
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

/** The three arrows that turn a scan upright, named for where its top points now. */
export function RotateButtons({ disabled, title, onRotate }: {
  disabled: boolean
  /** Why they're disabled, when they are for a reason worth saying. */
  title?: string
  onRotate: (top: TopPoints) => void
}) {
  return (
    <span className="page-tile__rotate" title={title ?? 'Which way the top of the page points now'}>
      <button disabled={disabled} title={title ?? 'Top points left'} onClick={() => onRotate('left')}>←</button>
      <button disabled={disabled} title={title ?? 'Top points right'} onClick={() => onRotate('right')}>→</button>
      <button disabled={disabled} title={title ?? 'Upside down'} onClick={() => onRotate('down')}>↓</button>
    </span>
  )
}

/** A scan at full size, for deciding whether a page continues the previous document, is upright, or
 *  where to cut a sheet. The dialogs' Cancel shortcut closes it. */
export function ScanViewer({ label, filename, version, onClose, children }: {
  label: string
  filename: string
  /** The file's mtime, so a rotated scan isn't shown from the browser's cache. */
  version: number
  onClose: () => void
  children?: ReactNode
}) {
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
        <img src={inputUrl(filename, version)} alt={`Scan ${label}`} />
        <figcaption>
          <strong>{label}</strong> {filename}
          {children}
          <button onClick={onClose}>Close{close && <> <kbd>{keyLabel(close)}</kbd></>}</button>
        </figcaption>
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
