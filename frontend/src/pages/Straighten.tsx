import { useMemo, useRef, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Link } from 'react-router-dom'
import { api, inputThumbUrl } from '../api/client.ts'
import type { Grouping, GroupingPage, TopPoints } from '../api/types.ts'
import { CappedImage } from '../components/DocumentCard.tsx'
import { useBatchHolder } from '../components/jobs.tsx'
import { BatchSelect, Pager, RotateButtons } from '../components/scans.tsx'
import { TiltBadge, TiltNotice, useTiltedPages } from '../components/tilts.tsx'
import { PageViewer, TurnNotice, useTurnedPages, type Viewing } from '../components/turns.tsx'
import { Card, Empty, ErrorState, Loading } from '../components/ui.tsx'
import { useGridColumnCount } from '../components/useGridColumnCount.ts'
import './ingest.css'

/** The grid's rows per page, as on Group. */
const ROWS_PER_PAGE = 6

/**
 * Straighten: the one place a scan is turned. Before a sheet is cut or pages are grouped, each scan fed in
 * sideways or upside down is turned upright, and each fed in crooked is levelled; the page points out
 * the ones that look so, and any scan can be turned from its full-size view.
 */
export default function Straighten() {
  const [batchId, setBatchId] = useState<number | undefined>(undefined)
  // The batch's pages, as Group lists them: crops are left out here, since they are cut the way their sheet is.
  const grouping = useQuery({
    queryKey: ['ingest', 'grouping', batchId],
    queryFn: () => api.ingest.grouping(batchId),
    placeholderData: (previous) => previous,
  })
  return (
    <div className="ingest-page ingest-page--wide">
      <h1>Straighten</h1>
      <p className="page-sub">
        Turn scans that were fed in sideways or upside down upright, and level the ones fed in slightly crooked,
        before a sheet is <Link to="/slice">sliced</Link> or the pages are <Link to="/group">grouped</Link>: what is
        cut or read from a scan is taken the way it faces.
      </p>
      {grouping.isPending ? <Loading what="scans" />
        : grouping.error ? <ErrorState error={grouping.error} />
          : grouping.data.blocker || grouping.data.batch_id === null
            ? <Card title="Scans"><Empty>{grouping.data.blocker ?? 'No batches.'}</Empty></Card>
            : <Scans key={grouping.data.batch_id} data={grouping.data} batchId={grouping.data.batch_id} onBatch={setBatchId} />}
    </div>
  )
}

/** Why a scan can't be turned (rotated or straightened): a sliced sheet stays as it was cut. */
const turnRefusal = (page: GroupingPage): string | null =>
  page.sliced ? 'Unslice the sheet on Slice to turn it' : null

function Scans({ data, batchId, onBatch }: { data: Grouping; batchId: number; onBatch: (id: number) => void }) {
  const queryClient = useQueryClient()
  const holder = useBatchHolder(batchId)
  const [viewing, setViewing] = useState<Viewing | null>(null)
  const [page, setPage] = useState(0)
  const grid = useRef<HTMLDivElement>(null)
  const columns = useGridColumnCount(grid, 6)

  const scans = useMemo(() => data.pages.filter((p) => !p.crop_of), [data])
  const scansByKey = useMemo(() => new Map(scans.map((p) => [p.key, p])), [scans])
  const turned = useTurnedPages(batchId, scansByKey)
  const tilted = useTiltedPages(batchId, scansByKey)

  const rotate = useMutation({
    mutationFn: ({ key, top }: { key: string; top: TopPoints }) => api.ingest.rotate(key, top),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ['ingest'] })
      for (const root of ['review-queue', 'review-doc']) void queryClient.invalidateQueries({ queryKey: [root] })
    },
  })

  const perPage = ROWS_PER_PAGE * Math.max(1, columns)
  const pageCount = Math.max(1, Math.ceil(scans.length / perPage))
  const shownPage = Math.min(page, pageCount - 1)
  const start = shownPage * perPage
  const pager = <Pager page={shownPage} pageCount={pageCount} onPage={setPage} />
  const locked = holder === null ? null : `Waiting for ${holder.title}: it is using batch ${batchId}.`
  const flagged = (flags: Map<string, unknown>) => scans.map((s) => s.key).filter((k) => flags.has(k))

  return (
    <Card title="Scans" className="card--full"
      hint="The arrows say where a scan's top points now; open a scan to preview a turn before it is saved.">
      <div className="controls">
        <BatchSelect id="straighten-batch" batches={data.batches} value={batchId} onChange={onBatch} />
      </div>
      {rotate.error && <div className="error-banner" role="alert">{rotate.error.message}</div>}
      <TurnNotice query={turned.query} turns={turned.turns}
        onReview={() => setViewing({ keys: flagged(turned.turns), at: 0, review: 'turned' })} />
      <TiltNotice query={tilted.query} tilts={tilted.tilts}
        onReview={() => setViewing({ keys: flagged(tilted.tilts), at: 0, review: 'tilted' })} />
      {pager}
      <div className="page-grid" ref={grid}>
        {scans.slice(start, start + perPage).map((scan) => {
          const refusal = turnRefusal(scan)
          const open = () => setViewing({ keys: [scan.key], at: 0, review: false })
          return (
            <div className="page-cell" key={scan.key}>
              <figure className={`page-tile${scan.tossed ? ' tossed' : ''}`}>
                <div className="page-tile__tools">
                  <RotateButtons disabled={rotate.isPending || holder !== null || !scan.image_available || refusal !== null}
                    {...(refusal ? { title: refusal } : {})}
                    suggested={turned.turns.get(scan.key)}
                    onRotate={(top) => rotate.mutate({ key: scan.key, top })} />
                  {tilted.tilts.has(scan.key) && <TiltBadge degrees={tilted.tilts.get(scan.key) ?? 0} onOpen={open} />}
                </div>
                <div className="page-tile__scan">
                  {scan.image_available
                    ? (
                      <button className="page-tile__zoom" onClick={open}
                        title={refusal ?? 'Show this scan full size, and straighten it'}>
                        <CappedImage src={inputThumbUrl(scan.filename, scan.image_version)} alt={`Scan ${scan.key}`} trim={scan.trim} />
                      </button>
                    )
                    : <span className="ingest-note">Scan not in the input folder</span>}
                </div>
                <figcaption>
                  <strong>{scan.key}</strong>{' '}
                  {scan.sliced && <span className="page-badge">sliced</span>}
                  {scan.tossed && !scan.sliced && <span className="page-badge">tossed</span>}{' '}
                  {scan.filename}
                </figcaption>
              </figure>
            </div>
          )
        })}
      </div>
      {pager}
      {viewing && <PageViewer viewing={viewing} pages={scansByKey} turns={turned.turns} tilts={tilted.tilts}
        cannotTurn={(key) => { const p = scansByKey.get(key); return p ? turnRefusal(p) : null }}
        locked={locked} onSetAside={turned.setAside} onSetAsideTilt={tilted.setAside}
        onMove={setViewing} onClose={() => setViewing(null)} />}
    </Card>
  )
}
