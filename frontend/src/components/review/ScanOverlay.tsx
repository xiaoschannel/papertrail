import type { FieldBox, ReviewPage } from '../../api/types.ts'
import { Empty } from '../ui.tsx'

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

const pct = (v: number) => `${v / 10}%`

/**
 * The document's page scans with the OCR boxes its extracted fields cite drawn on top.
 *
 * Box coordinates are on the OCR's 0-1000 scale relative to the image, so they are placed with
 * percentages and never need the image's pixel size. Hovering a box reports its first field;
 * boxes citing `activeFields` are emphasized (the form highlights the matching input).
 */
export function ScanOverlay({ pages, imageUrl, activeFields, onHoverField }: {
  pages: ReviewPage[]
  imageUrl: (filename: string) => string
  activeFields: readonly string[]
  onHoverField: (field: string | null) => void
}) {
  const isActive = (box: FieldBox) => box.fields.some((f) => activeFields.includes(f))
  return (
    <div className="scan-pages">
      {pages.map((page, i) => (
        <figure key={page.file_key} className="scan-page">
          {page.image_available && page.filename ? (
            <div className="scan-frame">
              <img src={imageUrl(page.filename)} alt={`Page ${i + 1}`} />
              {page.boxes.map((box) => box.rects.map((r, j) => (
                <div
                  key={`${box.index}-${j}`}
                  className={`field-box${isActive(box) ? ' active' : ''}${r.y1 < 40 ? ' label-below' : ''}`}
                  style={{
                    left: pct(r.x1), top: pct(r.y1), width: pct(r.x2 - r.x1), height: pct(r.y2 - r.y1),
                    ['--box-color' as string]: fieldColor(box.fields[0]),
                  }}
                  title={`${box.fields.join(', ')}${box.text ? ` — ${box.text}` : ''}`}
                  onMouseEnter={() => onHoverField(box.fields[0] ?? null)}
                  onMouseLeave={() => onHoverField(null)}
                >
                  {j === 0 && <span className="field-box__label">{box.fields.join(', ')}</span>}
                </div>
              )))}
            </div>
          ) : (
            <Empty>{page.filename ? `${page.filename} is not in the input folder.` : 'No image for this page.'}</Empty>
          )}
          <figcaption>Page {i + 1}{page.filename ? ` · ${page.filename}` : ''}</figcaption>
        </figure>
      ))}
    </div>
  )
}
