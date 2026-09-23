import { useState, type ReactNode } from 'react'
import { createPortal } from 'react-dom'
import { inputUrl } from '../api/client.ts'
import { useShortcutKeys } from '../api/config.ts'
import type { Batch, TopPoints } from '../api/types.ts'
import { keyLabel, useDialogKeys } from './useShortcuts.ts'
import './scans.css'

/**
 * A batch's section on the ingest pages (Fix Rotation, Slice, Group), which show every unarchived batch at
 * once, one after another: scanning several batches before any of them is worked on is the usual case.
 */
export function batchTitle(batch: Batch): string {
  return `Batch ${batch.batch_id}`
}

export function batchHint(batch: Batch): string {
  return `${batch.file_count} file${batch.file_count === 1 ? '' : 's'} · ${batch.start_datetime} to ${batch.end_datetime}`
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

/** How tall a scan may be in a viewer whose controls are docked under it: the window, less their room. */
const dockedScanHeight = () => {
  const rem = parseFloat(getComputedStyle(document.documentElement).fontSize) || 16
  return Math.max(12 * rem, window.innerHeight * 0.96 - 19 * rem)
}

/** The size to show a scan at when it is turned ``turn`` degrees (a multiple of 90), to fit the viewer
 *  (``across`` side by side, at most ``maxHeight`` tall): its box and the scan inside it, which the turn
 *  then lays across the box. */
function turnedFit(natural: [number, number], turn: number, across = 1, maxHeight?: number) {
  const [w, h] = natural
  const sideways = Math.abs(turn) % 180 === 90
  const [boxW, boxH] = sideways ? [h, w] : [w, h]
  const rem = parseFloat(getComputedStyle(document.documentElement).fontSize) || 16
  const wide = (Math.min(69 * rem, window.innerWidth * 0.92) - (across - 1) * 12) / across
  const scale = Math.min(1, wide / boxW, (maxHeight ?? window.innerHeight * 0.72) / boxH)
  return { box: { width: boxW * scale, height: boxH * scale }, scan: { width: w * scale, height: h * scale } }
}

/** How far to shrink a scan tilted ``tilt`` degrees so all of it fits where the level one did, corners
 *  included (as straightening saves it, on a canvas grown to hold them). */
function tiltFit(natural: [number, number] | null, tilt: number): number {
  if (natural === null || !tilt) return 1
  const [w, h] = natural
  const cos = Math.abs(Math.cos((tilt * Math.PI) / 180))
  const sin = Math.abs(Math.sin((tilt * Math.PI) / 180))
  return Math.min(w / (w * cos + h * sin), h / (w * sin + h * cos))
}

type Size = [number, number]
/** What straightening leaves around the ink at least, as a share of the scan's shorter side (deskew). */
const INK_MARGIN = 0.01

/** The canvas a scan is turned onto to keep every corner (deskew.grown_size, Pillow's to the pixel). */
function grownSize([w, h]: Size, tilt: number): Size {
  const theta = (-tilt * Math.PI) / 180
  const cos = Math.cos(theta)
  const sin = Math.sin(theta)
  const corners: Size[] = [[0, 0], [w, 0], [w, h], [0, h]]
  const xs = corners.map(([x, y]) => cos * (x - w / 2) + sin * (y - h / 2) + w / 2)
  const ys = corners.map(([x, y]) => -sin * (x - w / 2) + cos * (y - h / 2) + h / 2)
  return [Math.ceil(Math.max(...xs)) - Math.floor(Math.min(...xs)), Math.ceil(Math.max(...ys)) - Math.floor(Math.min(...ys))]
}

/** The page a scan holds once turned ``tilt`` degrees level, taken to be the crooked page's bounding box
 *  (deskew.levelled_size). Null when no page fits, or for no turn. */
function levelledSize([w, h]: Size, tilt: number): Size | null {
  if (!tilt) return null
  const cos = Math.abs(Math.cos((tilt * Math.PI) / 180))
  const sin = Math.abs(Math.sin((tilt * Math.PI) / 180))
  const det = cos * cos - sin * sin
  const a = (w * cos - h * sin) / det
  const b = (h * cos - w * sin) / det
  return a < 1 || b < 1 ? null : [Math.round(a), Math.round(b)]
}

/** What straightening keeps of the grown canvas (deskew.straightened_crop): the levelled page, but never
 *  less than the ink (``outline``, fractions of the scan) and a margin round it. Null: nothing is cropped. */
function straightenedCrop(natural: Size, tilt: number, outline: number[][]):
  { grown: Size; box: [number, number, number, number] } | null {
  const page = levelledSize(natural, tilt)
  if (page === null) return null
  const [w, h] = natural
  const grown = grownSize(natural, tilt)
  let left = (grown[0] - page[0]) / 2
  let top = (grown[1] - page[1]) / 2
  let right = left + page[0]
  let bottom = top + page[1]
  const cos = Math.cos((tilt * Math.PI) / 180)
  const sin = Math.sin((tilt * Math.PI) / 180)
  const margin = INK_MARGIN * Math.min(w, h)
  for (const [fx = 0, fy = 0] of outline) {
    const dx = fx * w - w / 2
    const dy = fy * h - h / 2
    const x = grown[0] / 2 + dx * cos + dy * sin
    const y = grown[1] / 2 - dx * sin + dy * cos
    left = Math.min(left, x - margin)
    top = Math.min(top, y - margin)
    right = Math.max(right, x + margin)
    bottom = Math.max(bottom, y + margin)
  }
  return {
    grown,
    box: [Math.max(0, Math.floor(left)), Math.max(0, Math.floor(top)),
      Math.min(grown[0], Math.ceil(right)), Math.min(grown[1], Math.ceil(bottom))],
  }
}

/** A scan as straightening ``tilt`` degrees (counter-clockwise) would save it: turned and cropped to the
 *  levelled page, keeping all its ink (``outline``, from the server), sized to fit the viewer ``across``
 *  side by side. Until the outline arrives, and for a scan no page fits in, it is shown whole, shrunk to
 *  keep its corners in view. */
export function StraightenedScan({ src, alt, tilt, outline, across = 1, maxHeight }: {
  src: string
  alt: string
  tilt: number
  outline: number[][] | undefined
  across?: number
  maxHeight?: number | undefined
}) {
  const [natural, setNatural] = useState<Size | null>(null)
  const img = (style: object) => (
    <img src={src} alt={alt} style={style}
      onLoad={(e) => setNatural([e.currentTarget.naturalWidth, e.currentTarget.naturalHeight])} />
  )
  const crop = natural && outline && straightenedCrop(natural, tilt, outline)
  if (natural === null || !crop) {
    // CSS turns clockwise for positive angles; a tilt is counter-clockwise
    return <div className="scan-viewer__frame">{img({ transform: `rotate(${-tilt}deg) scale(${tiltFit(natural, tilt)})` })}</div>
  }
  const [left, top, right, bottom] = crop.box
  const rem = parseFloat(getComputedStyle(document.documentElement).fontSize) || 16
  const wide = (Math.min(69 * rem, window.innerWidth * 0.92) - (across - 1) * 12) / across
  const scale = Math.min(1, wide / (right - left), (maxHeight ?? window.innerHeight * 0.78) / (bottom - top))
  return (
    <div className="scan-viewer__frame scan-viewer__straightened"
      style={{ width: (right - left) * scale, height: (bottom - top) * scale }}>
      {img({
        width: natural[0] * scale, height: natural[1] * scale,
        // the scan turns about its middle, which is the grown canvas's middle
        left: (crop.grown[0] / 2 - left) * scale, top: (crop.grown[1] / 2 - top) * scale,
        transform: `translate(-50%, -50%) rotate(${-tilt}deg)`,
      })}
    </div>
  )
}

/** A scan at full size, for deciding whether a page continues the previous document, is upright, or
 *  where to cut a sheet, or just to read it. The dialogs' Cancel shortcut closes it. The scan is the
 *  input-folder file ``filename`` at ``version``, or ``src`` when given (an archived document's). ``turn``
 *  (degrees clockwise, a multiple of 90) and ``tilt`` (degrees counter-clockwise, a few) preview a fix
 *  without changing anything: the scan as it is, beside it turned and straightened (cropped as
 *  straightening would, keeping the ink in ``outline``); ``guides`` draws level and plumb lines over them
 *  to judge a tilt by; ``footer`` goes under the caption. ``scan`` shows something else in the scan's place
 *  (the trim rulers, which draw the scan themselves) unless a fix is being previewed: trims are measured
 *  on the scan as it is stored, so a page is turned first. ``children`` go in the caption. */
export function ScanViewer({ label, filename, version = 0, src, onClose, children, turn = 0, tilt = 0, outline,
  guides = false, footer, scan }: {
  label: ReactNode
  filename?: string | undefined
  /** The file's mtime, so a rotated scan isn't shown from the browser's cache. */
  version?: number
  /** The scan's URL, when it isn't an input-folder file. */
  src?: string | undefined
  onClose: () => void
  scan?: ReactNode
  children?: ReactNode
  turn?: number
  tilt?: number
  outline?: number[][] | undefined
  guides?: boolean
  footer?: ReactNode
}) {
  const [natural, setNatural] = useState<[number, number] | null>(null)
  const close = useShortcutKeys()?.cancel
  useDialogKeys(close ? { [close]: onClose } : {})
  // With controls under it (Fix Rotation's), the viewer takes the window's height and keeps them at the
  // bottom, whatever the scan's size, so a key or the mouse finds them in one place scan after scan.
  const docked = footer !== undefined
  const maxHeight = docked ? dockedScanHeight() : undefined

  const url = src ?? inputUrl(filename ?? '', version)
  const alt = typeof label === 'string' ? `Scan ${label}` : 'Scan'
  /** The scan in its frame, turned ``turnBy`` and straightened ``by``. */
  const frame = (turnBy: number, by: number) => {
    if (by && !turnBy) {
      return <StraightenedScan src={url} alt={`${alt}, straightened`} tilt={by} outline={outline} across={2}
        maxHeight={maxHeight} />
    }
    // CSS turns clockwise for positive angles; a tilt is counter-clockwise
    const tilted = by ? ` rotate(${-by}deg) scale(${tiltFit(natural, by)})` : ''
    const img = (style?: object) => (
      <img src={url} alt={alt} style={style}
        onLoad={(e) => setNatural([e.currentTarget.naturalWidth, e.currentTarget.naturalHeight])} />
    )
    if (!turnBy || natural === null) {
      return <div className="scan-viewer__frame">{img(turnBy ? { visibility: 'hidden' } : undefined)}</div>
    }
    const fit = turnedFit(natural, turnBy, 2, maxHeight)
    return (
      <div className="scan-viewer__frame">
        <div className="scan-viewer__turned" style={fit.box}>
          {img({ ...fit.scan, maxWidth: 'none', maxHeight: 'none', transform: `translate(-50%, -50%) rotate(${turnBy}deg)${tilted}` })}
        </div>
      </div>
    )
  }

  // Drawn at the top of the page: a card opens it from inside containers (a calendar day) that would
  // otherwise clip it, or become what its fixed backdrop is placed against.
  return createPortal(
    <div className="modal-backdrop scan-viewer" data-modal-open onClick={onClose} role="dialog" aria-modal="true">
      <figure className={[guides && 'scan-viewer--guides', docked && 'scan-viewer--docked'].filter(Boolean).join(' ') || undefined}
        onClick={(e) => e.stopPropagation()}>
        {turn || tilt ? (
          <div className="scan-viewer__compare">
            <div><span>As scanned</span>{frame(0, 0)}</div>
            <div><span>{turn && tilt ? 'Turned and straightened' : turn ? 'Turned upright' : 'Straightened'}</span>
              {frame(turn, tilt)}</div>
          </div>
        ) : scan ?? frame(0, 0)}
        <div className="scan-viewer__controls">
          <figcaption>
            <strong>{label}</strong> {filename}
            {children}
            <button onClick={onClose}>Close{close && <> <kbd>{keyLabel(close)}</kbd></>}</button>
          </figcaption>
          {footer}
        </div>
      </figure>
    </div>,
    document.body,
  )
}

/** "1:9–11" for consecutive keys of one batch, "1:9" for one. */
export function keyRange(keys: string[]): string {
  const first = keys[0]
  const last = keys[keys.length - 1]
  if (first === undefined || last === undefined) return ''
  return first === last ? first : `${first}–${last.split(':')[1] ?? last}`
}
