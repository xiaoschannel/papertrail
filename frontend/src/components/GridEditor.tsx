import { useEffect, useRef, useState, type CSSProperties, type KeyboardEvent, type PointerEvent } from 'react'
import { inputUrl } from '../api/client.ts'
import { useShortcutKeys } from '../api/config.ts'
import type { SheetGrid } from '../api/types.ts'
import { keyLabel, keyOf } from './useShortcuts.ts'
import './GridEditor.css'

/** Grid coordinates are on a 0-1000 scale of the sheet, like OCR boxes (slicing.GRID_SCALE). */
const SCALE = 1000
/** As in slicing.MAX_GRID. */
export const MAX_GRID = 12
/** The narrowest a row or column may get, so a line can't be dragged onto its neighbour. */
const MIN_GAP = 10
/** How far an arrow key moves a focused line (Shift: ten times as far). */
const STEP = 2
/** The box a crop's preview is fitted into, in px. */
const PREVIEW = { w: 160, h: 128 }

type Frame = SheetGrid['frame']
type Cell = [number, number]
type Handle = { kind: 'row' | 'col'; index: number } | { kind: 'edge'; side: 'x1' | 'y1' | 'x2' | 'y2' }

const cellId = ([r, c]: Cell) => `${r},${c}`
/** A handle's identity across renders (each render makes new handle objects). */
const handleId = (h: Handle) => (h.kind === 'edge' ? h.side : `${h.kind}${h.index}`)

/** n - 1 lines spacing ``n`` bands evenly between ``lo`` and ``hi``. */
function even(lo: number, hi: number, n: number): number[] {
  return Array.from({ length: n - 1 }, (_, i) => Math.round(lo + ((hi - lo) * (i + 1)) / n))
}

/** Lines between ``lo`` and ``hi`` moved to keep their places between ``lo2`` and ``hi2``. */
function rescale(lines: number[], lo: number, hi: number, lo2: number, hi2: number): number[] {
  return lines.map((v) => Math.round(lo2 + ((v - lo) * (hi2 - lo2)) / (hi - lo)))
}

const allCells = (rows: number, cols: number): Cell[] =>
  Array.from({ length: rows * cols }, (_, i) => [Math.floor(i / cols) + 1, (i % cols) + 1] as Cell)

/** Why ``grid`` can't be saved yet, or null (mirrors slicing.validate_grid for what the editor allows). */
export function gridProblem(grid: SheetGrid): string | null {
  const f = grid.frame
  if (grid.cells.length === 0) return 'Mark at least one cell as holding a receipt, or unslice the sheet.'
  for (const [lo, lines, hi] of [[f.y1, grid.row_lines, f.y2], [f.x1, grid.col_lines, f.x2]] as const) {
    const edges = [lo, ...lines, hi]
    if (edges.some((v, i) => i > 0 && v <= (edges[i - 1] ?? v))) {
      return 'Two lines ran into each other: pull them apart, or use fewer rows or columns.'
    }
  }
  if (grid.row_lines.length === 0 && grid.col_lines.length === 0
    && f.x1 === 0 && f.y1 === 0 && f.x2 === SCALE && f.y2 === SCALE) {
    return 'A single cell covering the whole sheet cuts nothing off: add rows or columns, or trim the frame.'
  }
  return null
}

/**
 * Cut a sheet of small receipts taped together: an m x n grid over a frame on the scan. Drag the frame's
 * edges and the lines between rows and columns onto the gaps between receipts (or focus one and use the
 * arrow keys), and click a cell to mark it empty when the grid ran out of receipts.
 */
export function GridEditor({ sheetKey, filename, version, initial, busy, canUnslice, error, paused = false, onSave, onUnslice, onCancel }: {
  sheetKey: string
  filename: string
  /** The sheet file's mtime: the grid has to be drawn on the sheet as it is now, not a cached copy. */
  version: number
  initial: SheetGrid | null
  busy: boolean
  canUnslice: boolean
  error: string | null
  /** A dialog is open over the editor, and the Cancel shortcut is its to answer. */
  paused?: boolean
  onSave: (grid: SheetGrid) => void
  onUnslice: () => void
  onCancel: () => void
}) {
  const [frame, setFrame] = useState<Frame>(initial?.frame ?? { x1: 0, y1: 0, x2: SCALE, y2: SCALE })
  const [rowLines, setRowLines] = useState<number[]>(initial?.row_lines ?? even(0, SCALE, 2))
  const [colLines, setColLines] = useState<number[]>(initial?.col_lines ?? even(0, SCALE, 2))
  const rows = rowLines.length + 1
  const cols = colLines.length + 1
  const [filled, setFilled] = useState<Set<string>>(
    () => new Set((initial?.cells ?? allCells(rows, cols)).map((c) => cellId(c as Cell))))
  const [natural, setNatural] = useState<{ w: number; h: number } | null>(null)
  const surface = useRef<HTMLDivElement>(null)
  const drag = useRef<string | null>(null)

  // The dialogs' Cancel shortcut (Config's Shortcuts) closes the editor, unless it is typed into a field.
  const close = useShortcutKeys()?.cancel
  useEffect(() => {
    if (paused || !close) return undefined
    const onKey = (event: globalThis.KeyboardEvent) => {
      const typing = event.target instanceof HTMLElement && event.target.closest('input, textarea, select')
      if (keyOf(event) === close && !typing && !event.ctrlKey && !event.metaKey && !event.altKey) onCancel()
    }
    document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
  }, [close, onCancel, paused])

  const cells = allCells(rows, cols).filter((c) => filled.has(cellId(c)))    // reading order
  const grid: SheetGrid = { frame, row_lines: rowLines, col_lines: colLines, cells }
  const problem = gridProblem(grid)
  const ys = [frame.y1, ...rowLines, frame.y2]
  const xs = [frame.x1, ...colLines, frame.x2]

  const resize = (axis: 'rows' | 'cols', n: number) => {
    const count = Math.min(MAX_GRID, Math.max(1, Math.round(n) || 1))
    const [oldRows, oldCols] = [rows, cols]
    const [newRows, newCols] = axis === 'rows' ? [count, cols] : [rows, count]
    if (axis === 'rows') setRowLines(even(frame.y1, frame.y2, count))
    else setColLines(even(frame.x1, frame.x2, count))
    // cells still in the grid keep their state; new ones start out holding a receipt
    setFilled(new Set(allCells(newRows, newCols)
      .filter(([r, c]) => r > oldRows || c > oldCols || filled.has(cellId([r, c])))
      .map(cellId)))
  }

  /** Move a handle to ``value`` (0-1000), kept inside its neighbours. */
  const place = (handle: Handle, value: number) => {
    if (handle.kind !== 'edge') {
      const lines = handle.kind === 'row' ? rowLines : colLines
      const edges = handle.kind === 'row' ? [frame.y1, ...rowLines, frame.y2] : [frame.x1, ...colLines, frame.x2]
      const lo = (edges[handle.index] ?? 0) + MIN_GAP
      const hi = (edges[handle.index + 2] ?? SCALE) - MIN_GAP
      const next = lines.map((v, i) => (i === handle.index ? Math.min(hi, Math.max(lo, Math.round(value))) : v))
      if (handle.kind === 'row') setRowLines(next)
      else setColLines(next)
      return
    }
    const { side } = handle
    const vertical = side === 'y1' || side === 'y2'
    const bands = vertical ? rows : cols
    const [lo, hi] = vertical ? [frame.y1, frame.y2] : [frame.x1, frame.x2]
    const room = bands * MIN_GAP
    const moved = side === 'x1' || side === 'y1'
      ? Math.min(hi - room, Math.max(0, Math.round(value)))
      : Math.max(lo + room, Math.min(SCALE, Math.round(value)))
    const [lo2, hi2] = side === 'x1' || side === 'y1' ? [moved, hi] : [lo, moved]
    if (vertical) setRowLines(rescale(rowLines, lo, hi, lo2, hi2))
    else setColLines(rescale(colLines, lo, hi, lo2, hi2))
    setFrame({ ...frame, [side]: moved })
  }

  const valueAt = (event: PointerEvent, handle: Handle): number | null => {
    const rect = surface.current?.getBoundingClientRect()
    if (!rect || rect.width === 0 || rect.height === 0) return null
    const vertical = handle.kind === 'row' || (handle.kind === 'edge' && (handle.side === 'y1' || handle.side === 'y2'))
    return vertical ? ((event.clientY - rect.top) / rect.height) * SCALE : ((event.clientX - rect.left) / rect.width) * SCALE
  }
  const current = (handle: Handle): number =>
    handle.kind === 'edge' ? frame[handle.side] : (handle.kind === 'row' ? rowLines : colLines)[handle.index] ?? 0

  const handleProps = (handle: Handle, label: string) => ({
    role: 'slider' as const,
    tabIndex: 0,
    'aria-label': label,
    'aria-valuemin': 0,
    'aria-valuemax': SCALE,
    'aria-valuenow': current(handle),
    onPointerDown: (event: PointerEvent) => {
      event.preventDefault()
      event.currentTarget.setPointerCapture(event.pointerId)
      drag.current = handleId(handle)
    },
    onPointerMove: (event: PointerEvent) => {
      if (drag.current !== handleId(handle)) return
      const value = valueAt(event, handle)
      if (value !== null) place(handle, value)
    },
    onPointerUp: () => { drag.current = null },
    onPointerCancel: () => { drag.current = null },
    onKeyDown: (event: KeyboardEvent) => {
      const vertical = handle.kind === 'row' || (handle.kind === 'edge' && (handle.side === 'y1' || handle.side === 'y2'))
      const keys = vertical ? { ArrowUp: -1, ArrowDown: 1 } : { ArrowLeft: -1, ArrowRight: 1 }
      const direction = keys[event.key as keyof typeof keys]
      if (direction === undefined) return
      event.preventDefault()
      place(handle, current(handle) + direction * STEP * (event.shiftKey ? 10 : 1))
    },
  })

  const pct = (v: number) => `${v / 10}%`
  const toggle = (cell: Cell) => {
    const next = new Set(filled)
    if (next.has(cellId(cell))) next.delete(cellId(cell))
    else next.add(cellId(cell))
    setFilled(next)
  }

  /** A filled cell's crop, drawn from the scan with CSS (no image is cut until Save). */
  const preview = ([r, c]: Cell): CSSProperties => {
    const [x1, x2, y1, y2] = [xs[c - 1] ?? 0, xs[c] ?? SCALE, ys[r - 1] ?? 0, ys[r] ?? SCALE]
    const [w, h] = [x2 - x1, y2 - y1]
    const aspect = natural ? (w * natural.w) / (h * natural.h) : w / h
    const wide = aspect > PREVIEW.w / PREVIEW.h
    return {
      backgroundImage: `url("${inputUrl(filename, version)}")`,
      backgroundSize: `${(SCALE / w) * 100}% ${(SCALE / h) * 100}%`,
      backgroundPosition: `${w < SCALE ? (x1 / (SCALE - w)) * 100 : 0}% ${h < SCALE ? (y1 / (SCALE - h)) * 100 : 0}%`,
      // a width and a ratio rather than a height, so a narrower column scales it down without stretching
      width: wide ? PREVIEW.w : Math.round(PREVIEW.h * aspect),
      aspectRatio: String(aspect),
    }
  }

  return (
    <div className="modal-backdrop grid-editor" data-modal-open role="dialog" aria-modal="true"
      aria-labelledby="grid-editor-title">
      <div className="grid-editor__panel">
        <header className="grid-editor__head">
          <h2 id="grid-editor-title">Slice {sheetKey}</h2>
          <div className="grid-editor__size">
            <label>Rows
              <input type="number" min={1} max={MAX_GRID} value={rows} onChange={(e) => { if (e.target.value !== '') resize('rows', Number(e.target.value)) }} />
            </label>
            <label>Columns
              <input type="number" min={1} max={MAX_GRID} value={cols} onChange={(e) => { if (e.target.value !== '') resize('cols', Number(e.target.value)) }} />
            </label>
          </div>
          <p className="ingest-note">
            Drag the frame and the lines onto the gaps between receipts. Click a cell to mark it empty.
            Fix the sheet's rotation before slicing: the crops are cut the way it faces.
          </p>
        </header>

        <div className="grid-editor__body">
          <div className="grid-editor__sheet">
            <div className="grid-editor__surface" ref={surface}>
              <img src={inputUrl(filename, version)} alt={`Scan ${sheetKey}`} draggable={false}
                onLoad={(e) => setNatural({ w: e.currentTarget.naturalWidth, h: e.currentTarget.naturalHeight })} />
              <div className="grid-editor__shade" style={{
                clipPath: `polygon(evenodd, 0 0, 100% 0, 100% 100%, 0 100%, 0 0, ${pct(frame.x1)} ${pct(frame.y1)}, ${pct(frame.x1)} ${pct(frame.y2)}, ${pct(frame.x2)} ${pct(frame.y2)}, ${pct(frame.x2)} ${pct(frame.y1)}, ${pct(frame.x1)} ${pct(frame.y1)})`,
              }} />
              {allCells(rows, cols).map((cell) => {
                const [r, c] = cell
                const on = filled.has(cellId(cell))
                const number = cells.findIndex(([fr, fc]) => fr === r && fc === c) + 1
                return (
                  <button key={cellId(cell)} className={`grid-editor__cell${on ? ' on' : ''}`}
                    style={{ left: pct(xs[c - 1] ?? 0), top: pct(ys[r - 1] ?? 0),
                      width: pct((xs[c] ?? SCALE) - (xs[c - 1] ?? 0)), height: pct((ys[r] ?? SCALE) - (ys[r - 1] ?? 0)) }}
                    aria-pressed={on} title={on ? 'Holds a receipt: click to mark it empty' : 'Empty: click if it holds a receipt'}
                    onClick={() => toggle(cell)}>
                    {on ? <span>{number}</span> : <span className="grid-editor__empty">empty</span>}
                  </button>
                )
              })}
              {rowLines.map((y, i) => (
                <div key={`r${i}`} className="grid-editor__line grid-editor__line--row"
                  style={{ top: pct(y), left: pct(frame.x1), width: pct(frame.x2 - frame.x1) }}
                  {...handleProps({ kind: 'row', index: i }, `Line below row ${i + 1}`)} />
              ))}
              {colLines.map((x, i) => (
                <div key={`c${i}`} className="grid-editor__line grid-editor__line--col"
                  style={{ left: pct(x), top: pct(frame.y1), height: pct(frame.y2 - frame.y1) }}
                  {...handleProps({ kind: 'col', index: i }, `Line right of column ${i + 1}`)} />
              ))}
              {(['y1', 'y2'] as const).map((side) => (
                <div key={side} className="grid-editor__line grid-editor__line--row grid-editor__edge"
                  style={{ top: pct(frame[side]), left: pct(frame.x1), width: pct(frame.x2 - frame.x1) }}
                  {...handleProps({ kind: 'edge', side }, side === 'y1' ? 'Top of the frame' : 'Bottom of the frame')} />
              ))}
              {(['x1', 'x2'] as const).map((side) => (
                <div key={side} className="grid-editor__line grid-editor__line--col grid-editor__edge"
                  style={{ left: pct(frame[side]), top: pct(frame.y1), height: pct(frame.y2 - frame.y1) }}
                  {...handleProps({ kind: 'edge', side }, side === 'x1' ? 'Left of the frame' : 'Right of the frame')} />
              ))}
            </div>
          </div>

          <aside className="grid-editor__crops" aria-label="The crops, in the order they become pages">
            <h3>{cells.length} crop{cells.length === 1 ? '' : 's'}</h3>
            <ol>
              {cells.map((cell) => (
                <li key={cellId(cell)}>
                  <div className="grid-editor__crop" style={preview(cell)} role="img"
                    aria-label={`Row ${cell[0]}, column ${cell[1]}`} />
                  <span>{cells.indexOf(cell) + 1} · r{cell[0]}c{cell[1]}</span>
                </li>
              ))}
            </ol>
          </aside>
        </div>

        <footer className="modal__actions grid-editor__actions">
          {(error ?? problem) && <span className={error ? 'error-banner' : 'ingest-note'} role={error ? 'alert' : undefined}>
            {error ?? problem}
          </span>}
          {canUnslice && <button className="danger-outline" disabled={busy} onClick={onUnslice}>Unslice</button>}
          <button onClick={onCancel}>Cancel{close && <> <kbd>{keyLabel(close)}</kbd></>}</button>
          <button className="primary" disabled={busy || problem !== null} onClick={() => onSave(grid)}>Save</button>
        </footer>
      </div>
    </div>
  )
}
