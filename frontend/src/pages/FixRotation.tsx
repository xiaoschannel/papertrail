import { useState, type ReactNode } from 'react'
import { useQueries, useQuery } from '@tanstack/react-query'
import { Link } from 'react-router-dom'
import { api, inputThumbUrl } from '../api/client.ts'
import { useShortcutKeys } from '../api/config.ts'
import type { Batch, GroupingPage, RotationItem } from '../api/types.ts'
import { CappedImage } from '../components/DocumentCard.tsx'
import { FinalizeBar, useFinalizeStatus } from '../components/finalize.tsx'
import { useHolderOf } from '../components/jobs.tsx'
import { RecentRotations, RotationDecider } from '../components/rotation.tsx'
import { batchHint, batchTitle, scanHeightLeaving } from '../components/scans.tsx'
import { Card, Empty, ErrorState, Loading } from '../components/ui.tsx'
import { keyLabel, useShortcuts } from '../components/useShortcuts.ts'
import './ingest.css'

/**
 * Fix Rotation: the one place a scan is turned, before a sheet is cut or pages are grouped. Like Review, it
 * is a queue: every scan in the unarchived batches that looks sideways, upside down or crooked (its tilt
 * measured on it turned upright), or that Review sent back, one at a time, to Fix (turn, then straighten,
 * in one step) or Leave as is. Every decision is kept, as training data for the detectors, and a fix can be
 * undone. "All scans" shows every scan, to fix one the detectors didn't flag. Finalize commits the step.
 */
export default function FixRotation() {
  const status = useFinalizeStatus('rotation')
  return (
    <div className="ingest-page ingest-page--wide">
      <h1>Fix Rotation</h1>
      <p className="page-sub">
        Turn scans that were fed in sideways or upside down upright, and level the ones fed in crooked, before a
        sheet is <Link to="/slice">sliced</Link> or the pages are <Link to="/group">grouped</Link>: what is cut or
        read from a scan is taken the way it faces. Every batch not archived yet is here.
      </p>
      {status.isPending ? <Loading what="batches" />
        : status.error ? <ErrorState error={status.error} />
          : status.data.batches.length === 0
            ? <Card title="Scans"><Empty>No unarchived batches. Add batches on File Index first.</Empty></Card>
            : <>
              <Rotation batches={status.data.batches} />
              <FinalizeBar step="rotation" status={status.data} what="the rotation fixes" />
            </>}
    </div>
  )
}

/** Which view the page shows, kept while the app is open. */
let shownView: 'queue' | 'all' = 'queue'

function Rotation({ batches }: { batches: Batch[] }) {
  const [view, setViewState] = useState(shownView)
  const setView = (v: 'queue' | 'all') => {
    shownView = v
    setViewState(v)
  }
  const rotation = useQuery({ queryKey: ['ingest', 'rotation'], queryFn: api.ingest.rotation })
  if (rotation.isPending) return <Loading what="the scans (each one is checked the first time)" />
  if (rotation.error) return <ErrorState error={rotation.error} />
  const { items, recent, fixed, left, checked } = rotation.data
  const toggle = (
    <span className="segmented">
      <button className={view === 'queue' ? 'on' : undefined} onClick={() => setView('queue')}>
        To decide ({items.length})
      </button>
      <button className={view === 'all' ? 'on' : undefined} onClick={() => setView('all')}>All scans</button>
    </span>
  )
  return (
    <>
      <Card title="Scans" className="card--full"
        hint={`${items.length} to decide · ${fixed} fixed · ${left} left as is`}>
        {!checked && (
          <p className="ingest-note ingest-warning">
            The orientation model couldn’t be had, so only tilts are found; sideways and upside-down scans aren’t.
          </p>
        )}
        {view === 'queue' ? <Queue items={items} toggle={toggle} />
          : <><div className="controls">{toggle}</div><AllScans batches={batches} items={items} /></>}
      </Card>
      {recent.length > 0 && (
        <Card title="Decided" className="card--full" hint="newest first; a fix can be undone until the batch is archived">
          <RecentRotations recent={recent} />
        </Card>
      )}
    </>
  )
}

const batchOf = (key: string) => Number(key.split(':')[0])

/** One scan at a time, in batch then scan order; the next one comes up as each is decided. */
function Queue({ items, toggle }: { items: RotationItem[]; toggle: ReactNode }) {
  const keys = useShortcutKeys()
  const holderOf = useHolderOf()
  // Kept on the scan's key, so a queue that changes under it (a decision, a refetch) keeps it in view; the
  // index is where to stand when that scan has gone.
  const [cursor, setCursor] = useState<{ key: string | null; index: number }>({ key: null, index: 0 })
  const found = cursor.key === null ? -1 : items.findIndex((i) => i.key === cursor.key)
  const at = found >= 0 ? found : Math.min(cursor.index, Math.max(0, items.length - 1))
  const current = items[at]
  const move = (by: number) => {
    const to = Math.min(Math.max(at + by, 0), items.length - 1)
    setCursor({ key: items[to]?.key ?? null, index: to })
  }
  useShortcuts(keys ? { [keys.prev]: () => move(-1), [keys.next]: () => move(1) } : {}, Boolean(keys),
    keys ? [keys.prev, keys.next] : [])
  if (!current) {
    return <><div className="controls">{toggle}</div>
      <Empty>Nothing to decide: every scan looks upright and level, or was decided on.</Empty></>
  }
  const holder = holderOf(batchOf(current.key))
  const next = items[at + 1]?.key ?? items[at - 1]?.key ?? null
  return (
    <RotationDecider key={`${current.key}@${current.image_version}`} scan={current} source="queue" layout="inline"
      maxHeight={scanHeightLeaving(34)}
      locked={holder === null ? null : `Waiting for ${holder.title}: it is using batch ${batchOf(current.key)}.`}
      onDecided={() => setCursor({ key: next, index: at })}
      header={(
        <div className="rotation-nav">
          {toggle}
          <button disabled={at === 0} onClick={() => move(-1)}>← Prev{keys && <> <kbd>{keyLabel(keys.prev)}</kbd></>}</button>
          <span><strong>{current.key}</strong> — {at + 1} of {items.length} · {current.filename}</span>
          <button disabled={at >= items.length - 1} onClick={() => move(1)}>
            Next →{keys && <> <kbd>{keyLabel(keys.next)}</kbd></>}
          </button>
        </div>
      )} />
  )
}

/** Every scan Fix Rotation can turn, batch by batch (crops are cut the way their sheet is): any one opens to be
 *  fixed, flagged or not. */
function AllScans({ batches, items }: { batches: Batch[]; items: RotationItem[] }) {
  const holderOf = useHolderOf()
  const [opened, setOpened] = useState<GroupingPage | null>(null)
  const groupings = useQueries({
    queries: batches.map((b) => ({
      queryKey: ['ingest', 'grouping', b.batch_id],
      queryFn: () => api.ingest.grouping(b.batch_id),
    })),
  })
  const error = groupings.find((q) => q.error)?.error
  if (error) return <ErrorState error={error} />
  if (groupings.some((q) => q.data === undefined)) return <Loading what="scans" />
  const queued = new Set(items.map((i) => i.key))
  return (
    <>
      {batches.map((batch, n) => {
        const scans = (groupings[n]?.data?.batch_id === batch.batch_id ? groupings[n]?.data?.pages ?? [] : [])
          .filter((p) => !p.crop_of)
        return (
          <section key={batch.batch_id} className="rotation-batch">
            <h3 className="ingest-subhead">{batchTitle(batch)} <span className="ingest-note">{batchHint(batch)}</span></h3>
            <div className="page-grid">
              {scans.map((scan) => {
                const refusal = scan.sliced ? 'Unslice the sheet on Slice to turn it' : null
                return (
                  <div className="page-cell" key={scan.key}>
                    <figure className={`page-tile${scan.tossed ? ' tossed' : ''}`}>
                      <div className="page-tile__scan">
                        {scan.image_available
                          ? (
                            <button className="page-tile__zoom" disabled={refusal !== null || scan.tossed}
                              onClick={() => setOpened(scan)} title={refusal ?? 'Show this scan full size, to fix it'}>
                              <CappedImage src={inputThumbUrl(scan.filename, scan.image_version)} alt={`Scan ${scan.key}`} trim={scan.trim} />
                            </button>
                          )
                          : <span className="ingest-note">Scan not in the scan folder</span>}
                      </div>
                      <figcaption>
                        <strong>{scan.key}</strong>{' '}
                        {queued.has(scan.key) && <span className="page-badge">to decide</span>}
                        {scan.sliced && <span className="page-badge">sliced</span>}
                        {scan.tossed && !scan.sliced && <span className="page-badge">tossed</span>}{' '}
                        {scan.filename}
                      </figcaption>
                    </figure>
                  </div>
                )
              })}
            </div>
          </section>
        )
      })}
      {opened && <OpenScan page={opened} items={items} onClose={() => setOpened(null)}
        locked={(() => {
          const holder = holderOf(batchOf(opened.key))
          return holder === null ? null : `Waiting for ${holder.title}: it is using batch ${batchOf(opened.key)}.`
        })()} />}
    </>
  )
}

/** A scan opened from All scans: the queue's say if it is in it, else what the detectors say of it now. */
function OpenScan({ page, items, locked, onClose }: {
  page: GroupingPage
  items: RotationItem[]
  locked: string | null
  onClose: () => void
}) {
  const inQueue = items.find((i) => i.key === page.key && i.image_version === page.image_version)
  const prediction = useQuery({
    queryKey: ['ingest', 'rotation-prediction', page.key, page.image_version],
    queryFn: () => api.ingest.rotationPrediction(page.key),
    enabled: inQueue === undefined,
  })
  const scan = inQueue ?? (prediction.data
    ? { key: page.key, filename: page.filename, image_version: page.image_version, prediction: prediction.data, sent_back: false }
    : null)
  if (prediction.error) return <div className="error-banner" role="alert">{prediction.error.message}</div>
  if (scan === null) return null
  return <RotationDecider key={`${scan.key}@${scan.image_version}`} scan={scan} source="browse" layout="dialog"
    locked={locked} onDecided={onClose} onClose={onClose} />
}
