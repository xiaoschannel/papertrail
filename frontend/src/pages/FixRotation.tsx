import { useMemo, useState } from 'react'
import { useMutation, useQueries, useQueryClient } from '@tanstack/react-query'
import { Link } from 'react-router-dom'
import { api, inputThumbUrl } from '../api/client.ts'
import type { Batch, GroupingPage, TopPoints } from '../api/types.ts'
import { CappedImage } from '../components/DocumentCard.tsx'
import { FinalizeBar, useFinalizeStatus } from '../components/finalize.tsx'
import { useHolderOf } from '../components/jobs.tsx'
import { RotateButtons, batchHint, batchTitle } from '../components/scans.tsx'
import { TiltBadge, useTiltedPages } from '../components/tilts.tsx'
import { PageViewer, describeTurned, useTurnedPages, type Viewing } from '../components/turns.tsx'
import { Card, Empty, ErrorState, Loading } from '../components/ui.tsx'
import './ingest.css'

/**
 * Fix Rotation: the one place a scan is turned. Before a sheet is cut or pages are grouped, each scan fed in
 * sideways or upside down is turned upright, and each fed in crooked is levelled. Every unarchived batch is
 * on the page at once, and the grid shows only the scans that look so, a queue that empties as they are
 * turned or left as they are; "Show all scans" brings the rest back, since any scan can be turned from its
 * full-size view. Each turn is saved as it is made; Finalize commits them all.
 */
export default function FixRotation() {
  const status = useFinalizeStatus('rotation')
  return (
    <div className="ingest-page ingest-page--wide">
      <h1>Fix Rotation</h1>
      <p className="page-sub">
        Turn scans that were fed in sideways or upside down upright, and level the ones fed in slightly crooked,
        before a sheet is <Link to="/slice">sliced</Link> or the pages are <Link to="/group">grouped</Link>: what is
        cut or read from a scan is taken the way it faces. Every batch not archived yet is here.
      </p>
      {status.isPending ? <Loading what="batches" />
        : status.error ? <ErrorState error={status.error} />
          : status.data.batches.length === 0
            ? <Card title="Scans"><Empty>No unarchived batches. Add batches on File Index first.</Empty></Card>
            : <>
              <AllBatches batches={status.data.batches} />
              <FinalizeBar step="rotation" status={status.data} what="the rotation fixes" />
            </>}
    </div>
  )
}

/** Why a scan can't be turned (rotated or straightened): a sliced sheet stays as it was cut. */
const turnRefusal = (page: GroupingPage): string | null =>
  page.sliced ? 'Unslice the sheet on Slice to turn it' : null

/** Whether the grid shows every scan, not just those that look turned or tilted: kept while the app is open. */
let showingAll = false

const batchOf = (key: string) => Number(key.split(':')[0])

/** What the checks found, in a line: how many scans look turned and tilted, or that they are still being looked at. */
function Findings({ turned, tilted }: { turned: ReturnType<typeof useTurnedPages>; tilted: ReturnType<typeof useTiltedPages> }) {
  const found = [
    turned.turns.size > 0 && `${turned.turns.size === 1 ? '1 scan looks' : `${turned.turns.size} scans look`} turned (${describeTurned([...turned.turns.values()])})`,
    tilted.tilts.size > 0 && `${tilted.tilts.size === 1 ? '1 looks' : `${tilted.tilts.size} look`} slightly tilted`,
  ].filter(Boolean).join('; ')
  const checking = turned.query.isPending || tilted.query.isPending
  return (
    <>
      {turned.query.error && <p className="ingest-note ingest-warning">Couldn’t check which way up the scans are: {turned.query.error.message}</p>}
      {tilted.query.error && <p className="ingest-note ingest-warning">Couldn’t check the scans for tilt: {tilted.query.error.message}</p>}
      <p className="ingest-note">
        {found ? `${found}.` : checking ? '' : 'No scan looks turned or tilted.'}
        {checking && `${found ? ' ' : ''}Still checking the scans…`}
      </p>
    </>
  )
}

/** Every unarchived batch's scans (crops are left out: they are cut the way their sheet is). */
function AllBatches({ batches }: { batches: Batch[] }) {
  const groupings = useQueries({
    queries: batches.map((b) => ({
      queryKey: ['ingest', 'grouping', b.batch_id],
      queryFn: () => api.ingest.grouping(b.batch_id),
      placeholderData: (previous: Awaited<ReturnType<typeof api.ingest.grouping>> | undefined) => previous,
    })),
  })
  const error = groupings.find((q) => q.error)?.error
  if (error) return <ErrorState error={error} />
  if (groupings.some((q) => q.data === undefined)) return <Loading what="scans" />
  // a batch archived since the page asked falls back to another one: it is simply left out
  const scans = groupings.flatMap((q, i) => (q.data?.batch_id === batches[i]?.batch_id ? q.data?.pages ?? [] : []))
    .filter((p) => !p.crop_of)
  return <Scans batches={batches} scans={scans} />
}

function Scans({ batches, scans }: { batches: Batch[]; scans: GroupingPage[] }) {
  const queryClient = useQueryClient()
  const holderOf = useHolderOf()
  const [viewing, setViewing] = useState<Viewing | null>(null)
  const [all, setAllState] = useState(showingAll)
  const setAll = (on: boolean) => {
    showingAll = on
    setAllState(on)
  }

  const ids = batches.map((b) => b.batch_id)
  const scansByKey = useMemo(() => new Map(scans.map((p) => [p.key, p])), [scans])
  const turned = useTurnedPages(ids, scansByKey)
  const tilted = useTiltedPages(ids, scansByKey)

  const rotate = useMutation({
    mutationFn: ({ key, top }: { key: string; top: TopPoints }) => api.ingest.rotate(key, top),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ['ingest'] })
      for (const root of ['review-queue', 'review-doc']) void queryClient.invalidateQueries({ queryKey: [root] })
    },
  })

  const lockOf = (key: string) => {
    const holder = holderOf(batchOf(key))
    return holder === null ? null : `Waiting for ${holder.title}: it is using batch ${batchOf(key)}.`
  }
  // one queue across the batches, in batch order, so the viewer steps from one batch into the next
  const flagged = scans.filter((s) => turned.turns.has(s.key) || tilted.tilts.has(s.key))
  const shown = all ? scans : flagged

  return (
    <>
      <Card title="Scans" className="card--full"
        hint="The arrows say where a scan's top points now; open a scan to preview a turn before it is saved.">
        <div className="controls">
          <label className="scan-viewer__check">
            <input type="checkbox" checked={all} onChange={(e) => setAll(e.target.checked)} /> Show all scans
          </label>
        </div>
        {rotate.error && <div className="error-banner" role="alert">{rotate.error.message}</div>}
        <Findings turned={turned} tilted={tilted} />
      </Card>
      {batches.map((batch) => {
        const inBatch = shown.filter((s) => batchOf(s.key) === batch.batch_id)
        if (inBatch.length === 0) return null      // nothing to fix in it: the queue skips it
        const holder = holderOf(batch.batch_id)
        return (
          <Card key={batch.batch_id} title={batchTitle(batch)} className="card--full"
            hint={`${batchHint(batch)}${all ? '' : ` · ${inBatch.length} to check`}`}>
            <div className="page-grid">
              {inBatch.map((scan) => {
                const refusal = turnRefusal(scan)
                // A scan that looks turned or tilted opens with the rest of them after it, to step through.
                const at = flagged.indexOf(scan)
                const open = () => setViewing(at >= 0
                  ? { keys: flagged.map((s) => s.key), at, review: true }
                  : { keys: [scan.key], at: 0, review: false })
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
          </Card>
        )
      })}
      {viewing && <PageViewer viewing={viewing} pages={scansByKey} turns={turned.turns} tilts={tilted.tilts}
        cannotTurn={(key) => { const p = scansByKey.get(key); return p ? turnRefusal(p) : null }}
        locked={lockOf} onSetAside={turned.setAside} onSetAsideTilt={tilted.setAside}
        onMove={setViewing} onClose={() => setViewing(null)} />}
    </>
  )
}
