import {
  useRef, useState,
  type ComponentPropsWithoutRef, type ElementType, type ReactNode,
} from 'react'
import { Link } from 'react-router-dom'
import { mediaUrl } from '../api/client.ts'
import type { Trim, VizRecord } from '../api/types.ts'
import { money } from '../format.ts'
import { TrimmedImage } from './TrimmedImage.tsx'
import { useGridColumnCount } from './useGridColumnCount.ts'
import './DocumentCard.css'

/** Default max image height, as a multiple of the image's rendered width.
 *  2.6 = 390px on a ~150px gallery card. */
export const DEFAULT_CAP_RATIO = 2.6

/**
 * A scan that fills the width it is given, at its natural aspect ratio, capped at `capRatio` x width and
 * cropped from the bottom (top always visible). When the cap actually cuts the scan off, a short fade at
 * the bottom signals that it continues. A trimmed scan shows only its band, and the cap applies to that.
 *
 * Sizing contract: it takes the inline size its parent provides. Before the scan loads, the box reserves
 * the full cap height, so tall receipts — the common case — don't shift layout when they arrive; shorter
 * documents shrink to their own height once measured. TrimmedImage does the work.
 */
export function CappedImage({ src, alt = '', capRatio = DEFAULT_CAP_RATIO, trim = null }:
  { src: string; alt?: string; capRatio?: number | undefined; trim?: Trim | null | undefined }) {
  return <TrimmedImage className="capped-image" src={src} alt={alt} capRatio={capRatio} trim={trim} />
}

type DocumentCardOwnProps<C extends ElementType> = {
  /** Root element or component: 'div' (default), Link, 'a', 'button'…
   *  Link, 'a' and 'button' roots get the hover/focus highlight. */
  as?: C
  /** Image URL (omit for no image). */
  src?: string | undefined
  /** The band of the scan to show (null: all of it). */
  trim?: Trim | null | undefined
  /** Primary line (truncated with an ellipsis). */
  name: ReactNode
  /** Secondary line (omitted when empty). */
  caption?: ReactNode
  /** Frameless variant for tight spots (calendar cells). */
  compact?: boolean | undefined
  /** Override the image cap ratio. */
  capRatio?: number | undefined
  className?: string | undefined
  /** Extra content (e.g. actions). Don't put interactive children inside a
   *  link/button root — nested interactive elements are invalid. */
  children?: ReactNode
}

/** Own props plus whatever the chosen root accepts (to, onClick, title, aria-*). */
export type DocumentCardProps<C extends ElementType = 'div'> =
  DocumentCardOwnProps<C> & Omit<ComponentPropsWithoutRef<C>, keyof DocumentCardOwnProps<C>>

/**
 * The presentational document card: capped scan, name, caption. Knows nothing
 * about where documents live or what clicking does — pass that in.
 *
 * The card never sets its own outer margin; spacing belongs to the containing
 * layout (a grid gap, the calendar's .cal-items).
 */
export function DocumentCard<C extends ElementType = 'div'>({
  as,
  src,
  trim,
  name,
  caption,
  compact = false,
  capRatio,
  className = '',
  children,
  ...rest
}: DocumentCardProps<C>) {
  const Root: ElementType = as ?? 'div'
  const cls = ['doc-card', compact && 'doc-card--compact', className].filter(Boolean).join(' ')
  return (
    <Root className={cls} {...rest}>
      {src && <CappedImage src={src} capRatio={capRatio} trim={trim} />}
      <div className="doc-card__name">{name}</div>
      {caption ? <div className="doc-card__caption">{caption}</div> : null}
      {children}
    </Root>
  )
}

/** The fields a receipt card needs — satisfied by full records and slim gallery rows alike. */
export type CardDocument = Pick<VizRecord, 'filename' | 'path' | 'trim' | 'name' | 'date' | 'cost' | 'currency' | 'document_type'>
  & { brand_location?: string | null }

function captionFor(rec: CardDocument, showDate: boolean, showBranch: boolean): string {
  const amount = rec.document_type === 'receipt' ? money(rec.cost, rec.currency) : rec.document_type
  const caption = showDate && rec.date ? `${rec.date} · ${amount}` : amount
  return showBranch && rec.brand_location ? `${caption} · ${rec.brand_location}` : caption
}

type ReceiptCardProps = {
  rec: CardDocument
  /** Prefix the caption with the date (off where context already gives it). */
  showDate?: boolean
  /** End the caption with the branch (a brand's receipts come from several). */
  showBranch?: boolean
} & Pick<DocumentCardOwnProps<'div'>, 'compact' | 'capRatio' | 'className'>

/**
 * An archived document as a card that links to Receipt Detail.
 * The whole card is a real <a>, so middle/ctrl-click, right-click "open in new tab"
 * and keyboard (Tab + Enter) behave like any link.
 */
export function ReceiptCard({ rec, showDate = true, showBranch = false, ...cardProps }: ReceiptCardProps) {
  const name = rec.name || '(untitled)'
  return (
    <DocumentCard
      as={Link}
      to={`/receipt?file=${encodeURIComponent(rec.filename)}`}
      title={name}
      src={mediaUrl(rec.path)}
      trim={rec.trim}
      name={name}
      caption={captionFor(rec, showDate, showBranch)}
      {...cardProps}
    />
  )
}

/**
 * Responsive grid of archived-document cards (Merchant Profile, Time Capsule).
 *
 * `rowsPerPage` paginates by WHOLE ROWS: page size = rowsPerPage x the columns the
 * grid actually has, so a page never ends in a short row (a fixed item count can't:
 * the column count varies with width). Omit it to show everything.
 *
 * Pagination tracks the index of the first card shown, not a page number, so when
 * the column count changes on resize the same receipts stay on screen.
 * Remount (change `key`) to reset to the first page when the list itself changes.
 */
export function ReceiptGallery({ receipts, showDate = true, showBranch = false, rowsPerPage }:
  { receipts: readonly CardDocument[]; showDate?: boolean; showBranch?: boolean; rowsPerPage?: number }) {
  const gridRef = useRef<HTMLDivElement>(null)
  const columns = useGridColumnCount(gridRef)
  const [firstIndex, setFirstIndex] = useState(0)

  const rows = rowsPerPage !== undefined && rowsPerPage > 0 ? rowsPerPage : null
  const pageSize = rows !== null ? columns * rows : Math.max(receipts.length, 1)
  const pages = Math.max(1, Math.ceil(receipts.length / pageSize))
  const page = Math.min(Math.floor(firstIndex / pageSize), pages - 1)
  const shown = rows !== null ? receipts.slice(page * pageSize, (page + 1) * pageSize) : receipts
  const goTo = (p: number) => setFirstIndex(Math.min(Math.max(p, 0), pages - 1) * pageSize)

  return (
    <>
      <div className="receipt-gallery" ref={gridRef}>
        {shown.map((rec) => (
          <ReceiptCard key={rec.filename} rec={rec} showDate={showDate} showBranch={showBranch} />
        ))}
      </div>
      {rows !== null && pages > 1 && (
        <div className="pager">
          <button disabled={page === 0} onClick={() => goTo(page - 1)}>← Prev</button>
          <span>Page {page + 1} of {pages} — {page * pageSize + 1}–{Math.min((page + 1) * pageSize, receipts.length)} of {receipts.length}</span>
          <button disabled={page >= pages - 1} onClick={() => goTo(page + 1)}>Next →</button>
        </div>
      )}
    </>
  )
}
