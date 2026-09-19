import type { BoxRect, FieldBox, ReviewPage, TopPoints } from '../../api/types.ts'
import { Empty } from '../ui.tsx'
import './ScanOverlay.css'

/** Colors per extracted field, matching box_drawing.FIELD_COLORS in the Streamlit app. */
export const FIELD_COLORS: Record<string, string> = {
  name: '#4285f4',
  title: '#4285f4',
  date: '#34a853',
  time: '#00bcd4',
  cost: '#ea4335',
}
const DEFAULT_FIELD_COLOR = '#787878'

export const fieldColor = (field: string | undefined) => (field && FIELD_COLORS[field]) || DEFAULT_FIELD_COLOR

/** A box no field cites (every box OCR found, before Parse): its own colour, as box_drawing.draw_all_boxes. */
const BOX_COLORS = ['#4285f4', '#ea4335', '#34a853', '#fbbc04', '#00bcd4', '#9c27b0', '#ff7043', '#3f51b5']
const boxColor = (box: FieldBox) =>
  box.fields.length ? fieldColor(box.fields[0]) : BOX_COLORS[box.index % BOX_COLORS.length] ?? DEFAULT_FIELD_COLOR
/** A cited box is labelled with its fields; an uncited one with its index, as the prompt tags it (P1-BOX-3). */
const boxLabel = (box: FieldBox) => (box.fields.length ? box.fields.join(', ') : String(box.index))

const pct = (v: number) => `${v / 10}%`

/** Where the page's top points in the file, as File Index names it ('' = already upright). */
export type Turn = TopPoints | ''

const SCALE = 1000
/** A box measured on the file, placed on the file turned upright from `turn` (see document_grouping.ROTATIONS). */
function turned(r: BoxRect, turn: Turn): BoxRect {
  switch (turn) {
    case 'left': return { x1: SCALE - r.y2, y1: r.x1, x2: SCALE - r.y1, y2: r.x2 }    // 90° clockwise
    case 'right': return { x1: r.y1, y1: SCALE - r.x2, x2: r.y2, y2: SCALE - r.x1 }   // 90° counter-clockwise
    case 'down': return { x1: SCALE - r.x2, y1: SCALE - r.y2, x2: SCALE - r.x1, y2: SCALE - r.y1 }
    default: return r
  }
}

/**
 * The document's page scans with the OCR boxes its extracted fields cite drawn on top.
 *
 * Box coordinates are on the OCR's 0-1000 scale relative to the image, so they are placed with
 * percentages and never need the image's pixel size. Hovering a box reports its first field;
 * boxes citing `activeFields` are emphasized (the form highlights the matching input).
 *
 * The Workshop shows a treated copy: `turn` says how that copy is turned from the file the boxes were
 * measured on, and `originalUrl` + `showOriginal` swap in the untreated file (both images stay loaded,
 * so the swap is instant).
 */
export function ScanOverlay({
  pages, imageUrl, activeFields, onHoverField, turn = '', originalUrl, showOriginal = false,
  missing = 'is not in the input folder',
}: {
  pages: ReviewPage[]
  imageUrl: (filename: string) => string
  activeFields: readonly string[]
  onHoverField: (field: string | null) => void
  turn?: Turn
  originalUrl?: ((filename: string) => string) | undefined
  showOriginal?: boolean
  /** What to say about a page whose scan isn't on disk. */
  missing?: string
}) {
  const isActive = (box: FieldBox) => box.fields.some((f) => activeFields.includes(f))
  const original = showOriginal && originalUrl !== undefined
  const boxTurn: Turn = original ? '' : turn
  return (
    <div className="scan-pages">
      {pages.map((page, i) => (
        <figure key={page.file_key} className="scan-page">
          {page.image_available && page.filename ? (
            <div className="scan-frame">
              <img src={imageUrl(page.filename)} alt={`Page ${i + 1}`} hidden={original} />
              {originalUrl && <img src={originalUrl(page.filename)} alt={`Page ${i + 1}, as scanned`} hidden={!original} />}
              {page.boxes.map((box) => box.rects.map((rect, j) => {
                const r = turned(rect, boxTurn)
                return (
                  <div
                    key={`${box.index}-${j}`}
                    className={`field-box${isActive(box) ? ' active' : ''}${r.y1 < 40 ? ' label-below' : ''}`}
                    style={{
                      left: pct(r.x1), top: pct(r.y1), width: pct(r.x2 - r.x1), height: pct(r.y2 - r.y1),
                      ['--box-color' as string]: boxColor(box),
                    }}
                    title={`${boxLabel(box)}${box.text ? ` — ${box.text}` : ''}`}
                    onMouseEnter={() => onHoverField(box.fields[0] ?? null)}
                    onMouseLeave={() => onHoverField(null)}
                  >
                    {j === 0 && <span className="field-box__label">{boxLabel(box)}</span>}
                  </div>
                )
              }))}
            </div>
          ) : (
            <Empty>{page.filename ? `${page.filename} ${missing}.` : 'No image for this page.'}</Empty>
          )}
          <figcaption>Page {i + 1}{page.filename ? ` · ${page.filename}` : ''}</figcaption>
        </figure>
      ))}
    </div>
  )
}
