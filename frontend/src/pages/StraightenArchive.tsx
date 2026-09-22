import { useEffect, useState, type ReactNode } from 'react'
import { Link } from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api } from '../api/client.ts'
import { useShortcutKeys } from '../api/config.ts'
import type { StraightenArchive as StraightenArchiveState } from '../api/types.ts'
import { ConfirmDialog } from '../components/ConfirmDialog.tsx'
import { DocumentCard } from '../components/DocumentCard.tsx'
import { StraightenedScan } from '../components/scans.tsx'
import { Card, Empty, ErrorState, Loading } from '../components/ui.tsx'
import { keyLabel, keyOf } from '../components/useShortcuts.ts'
import './dev.css'
import './ingest.css'

/** Suggestions set aside with "Leave as is" while the app is open. */
const leftAsIs = new Set<string>()

/** How a turn reads to a person: which way, and how far. */
const describeTurn = (degrees: number) =>
  `${Math.abs(degrees).toFixed(1)}° ${degrees > 0 ? 'counter-clockwise' : 'clockwise'}`

/**
 * One-off migration: archived scans that look tilted, each straightened only when confirmed (at the
 * detected turn or one adjusted in the full-size view), with a backup that Undo puts back and Finalize
 * deletes. Temporary: kept as a git tag, not shipped.
 */
export default function StraightenArchive() {
  const queryClient = useQueryClient()
  // bumped after every change, so a rewritten scan isn't shown from the browser's cache
  const [stamp, setStamp] = useState(0)
  const [, setLeft] = useState(0)
  const [milder, setMilder] = useState(false)
  // Batches whose tilts were fixed by hand in the scanner software still read as tilted (the shadow the
  // fix leaves), so the list can be limited to a range of batches; empty ends are open.
  const [fromBatch, setFromBatch] = useState('')
  const [toBatch, setToBatch] = useState('')
  const [viewing, setViewing] = useState<string | null>(null)
  const [comparing, setComparing] = useState<string | null>(null)
  const [finalizing, setFinalizing] = useState(false)
  const [finalized, setFinalized] = useState<number | null>(null)
  const state = useQuery({
    queryKey: ['dev', 'straighten-archive'],
    queryFn: api.dev.straightenArchive,
    refetchInterval: (query) => (query.state.data?.running ? 1000 : false),
  })
  const done = (result: StraightenArchiveState) => {
    queryClient.setQueryData(['dev', 'straighten-archive'], result)
    void queryClient.invalidateQueries({ queryKey: ['viz'] })
    setStamp((n) => n + 1)
  }
  const scan = useMutation({ mutationFn: api.dev.scanStraightenArchive, onSuccess: done })
  const straighten = useMutation({ mutationFn: api.dev.straightenArchived, onSuccess: done })
  const undo = useMutation({ mutationFn: api.dev.undoStraightenArchived, onSuccess: done })
  const redo = useMutation({
    mutationFn: api.dev.redoStraightenArchive,
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['dev', 'straighten-archive'] })
      void queryClient.invalidateQueries({ queryKey: ['viz'] })
      setStamp((n) => n + 1)
    },
  })
  const finalize = useMutation({
    mutationFn: api.dev.finalizeStraightenArchive,
    onSuccess: (result) => {
      setFinalizing(false)
      setFinalized(result.removed)
      void queryClient.invalidateQueries({ queryKey: ['dev', 'straighten-archive'] })
    },
    onError: () => setFinalizing(false),
  })

  if (state.isPending) return <Loading what="the archive check" />
  if (state.error) return <ErrorState error={state.error} />
  const s = state.data
  const busy = straighten.isPending || undo.isPending || redo.isPending || finalize.isPending
  const lo = fromBatch.trim() === '' ? null : Number(fromBatch)
  const hi = toBatch.trim() === '' ? null : Number(toBatch)
  const inRange = (batch: number | null) => (lo === null && hi === null)
    || (batch !== null && (lo === null || batch >= lo) && (hi === null || batch <= hi))
  const listed = s.found.filter((f) => !leftAsIs.has(f.rel_path) && inRange(f.batch_id))
  const found = listed.filter((f) => milder || Math.abs(f.degrees) >= s.flag_degrees)
  const hiddenMilder = listed.length - found.length
  // how many look tilted in each batch (whatever the range), to pick the range by
  const perBatch = new Map<string, number>()
  for (const f of s.found) {
    if (leftAsIs.has(f.rel_path) || (!milder && Math.abs(f.degrees) < s.flag_degrees)) continue
    const label = f.batch_id === null ? 'no batch' : String(f.batch_id)
    perBatch.set(label, (perBatch.get(label) ?? 0) + 1)
  }
  const batchCounts = [...perBatch].sort(([a], [b]) => (Number(a) || 1e9) - (Number(b) || 1e9))
  const thumb = (rel: string) => api.dev.straightenArchiveThumb(rel, stamp)
  const error = (scan.error ?? straighten.error ?? undo.error ?? redo.error ?? finalize.error)?.message
  const leave = (rel: string) => {
    leftAsIs.add(rel)
    setLeft((n) => n + 1)
  }
  /** After one is dealt with, the viewer moves on to the next scan in the list (or closes). */
  const after = (rel: string) => {
    const at = found.findIndex((f) => f.rel_path === rel)
    const next = found.slice(at + 1).find((f) => !leftAsIs.has(f.rel_path))
    setViewing(next?.rel_path ?? null)
  }
  const shown = viewing === null ? undefined : s.found.find((f) => f.rel_path === viewing)

  return (
    <div className="dev-page">
      <h1>Straighten Archive</h1>
      <p className="page-sub">
        A one-off pass over scans filed before Slice and Group pointed out tilted pages: every scan in the archive
        (filed, marked and tossed) is checked, and each one that looks crooked is straightened only when you say so,
        at the detected turn or one you adjust. Its OCR boxes move with it, and a backup is kept, so Undo puts it
        back exactly — until you Finalize, which deletes the backups. Batches not archived yet are checked on{' '}
        <Link className="rowlink" to="/slice">Slice</Link> and <Link className="rowlink" to="/group">Group</Link>.
      </p>
      {error && <div className="error-banner" role="alert">{error}</div>}

      <Card title="Check the archive" hint={s.total ? `${s.done} of ${s.total} scans looked at` : undefined}>
        <div className="start-bar">
          <button className="primary" disabled={s.running || scan.isPending} onClick={() => scan.mutate()}>
            {s.running ? 'Checking…' : s.total ? 'Check again' : 'Check every archived scan'}
          </button>
          {s.running && <progress max={s.total || 1} value={s.done} />}
          {!s.running && s.total > 0 && (
            <span className="ingest-note">
              Looked at {s.total} scans{s.unreadable ? `; ${s.unreadable} couldn’t be read` : ''}.
            </span>
          )}
        </div>
        {s.error && <p className="ingest-note ingest-warning">The check stopped: {s.error}</p>}
      </Card>

      <Card title="Look tilted" className="card--full"
        hint={found.length ? `${found.length} scan${found.length === 1 ? '' : 's'}, most crooked first` : undefined}>
        <div className="controls">
          <div className="field">
            <label htmlFor="from-batch">From batch</label>
            <input id="from-batch" type="number" min={0} value={fromBatch} placeholder="first"
              onChange={(e) => setFromBatch(e.target.value)} />
          </div>
          <div className="field">
            <label htmlFor="to-batch">To batch</label>
            <input id="to-batch" type="number" min={0} value={toBatch} placeholder="last"
              onChange={(e) => setToBatch(e.target.value)} />
          </div>
        </div>
        {batchCounts.length > 0 && (
          <p className="ingest-note">
            Look tilted, by batch: {batchCounts.map(([batch, n]) => `${batch}: ${n}`).join(' · ')}
            {(lo !== null || hi !== null) && ' (a range leaves out scans of no known batch)'}
          </p>
        )}
        <label className="scan-viewer__check">
          <input type="checkbox" checked={milder} onChange={(e) => setMilder(e.target.checked)} />
          Show milder tilts too (under {s.flag_degrees}°, which the detector wouldn’t flag)
          {!milder && hiddenMilder > 0 && ` — ${hiddenMilder} more`}
        </label>
        {found.length === 0 ? (
          <Empty>{s.running ? 'Nothing yet.' : s.total ? 'No archived scan looks tilted.' : 'Not checked yet.'}</Empty>
        ) : (
          <div className="receipt-gallery">
            {found.map((f) => (
              <DocumentCard key={f.rel_path} src={thumb(f.rel_path)} name={f.rel_path.split('/').pop()}
                title={f.rel_path}
                caption={`${f.batch_id === null ? 'no batch' : `batch ${f.batch_id}`} · looks tilted ${describeTurn(f.degrees)}`}>
                <span className="straighten-archive-actions">
                  <button disabled={busy} onClick={() => leave(f.rel_path)}>Leave as is</button>
                  <button disabled={busy} onClick={() => setViewing(f.rel_path)}>Adjust…</button>
                  <button className="primary" disabled={busy}
                    title="Straighten it at the detected turn"
                    onClick={() => straighten.mutate({ rel_path: f.rel_path, degrees: f.degrees })}>
                    Straighten
                  </button>
                </span>
              </DocumentCard>
            ))}
          </div>
        )}
      </Card>

      <Card title="Straightened"
        hint={s.straightened.length ? `${s.straightened.length} scan${s.straightened.length === 1 ? '' : 's'}, backed up` : undefined}>
        {s.straightened.length === 0
          ? <Empty>{finalized !== null ? `Finalized: ${finalized} backup(s) deleted.` : 'None straightened yet.'}</Empty>
          : (
            <>
              <div className="receipt-gallery">
                {s.straightened.map((rel) => (
                  <DocumentCard key={rel} src={thumb(rel)} name={rel.split('/').pop()} title={rel}
                    caption={<a className="rowlink" href={api.dev.archivedUrl(rel, stamp)} target="_blank" rel="noreferrer">
                      Open full size</a>}>
                    <span className="straighten-archive-actions">
                      <button onClick={() => setComparing(rel)}>Compare…</button>
                      <button disabled={busy} onClick={() => undo.mutate({ rel_path: rel })}>Undo</button>
                    </span>
                  </DocumentCard>
                ))}
              </div>
              <div className="start-bar">
                <button disabled={busy} onClick={() => redo.mutate()}>
                  {redo.isPending ? 'Redoing…' : 'Redo all from the backups'}
                </button>
                <span className="ingest-note">
                  {redo.data
                    ? `Redone ${redo.data.redone.length}${redo.data.skipped.length
                      ? `; ${redo.data.skipped.length} couldn’t be (their turn can’t be told — Undo and straighten them again): ${redo.data.skipped.join(', ')}`
                      : ''}.`
                    : 'Straightens each again from its original, the way scans are straightened now (cropped to the page).'}
                </span>
              </div>
              <div className="start-bar">
                <button className="danger" disabled={busy || s.running} onClick={() => setFinalizing(true)}>
                  Finalize: delete the backups
                </button>
                <span className="ingest-note">
                  Once you’ve checked them (Receipt Detail draws the boxes over the scan). Undo is gone after.
                </span>
              </div>
            </>
          )}
      </Card>

      {shown && (
        <ArchivedViewer key={`${shown.rel_path}@${stamp}`} relPath={shown.rel_path} suggested={shown.degrees}
          stamp={stamp} busy={busy}
          onStraighten={(degrees) => straighten.mutate({ rel_path: shown.rel_path, degrees },
            { onSuccess: () => after(shown.rel_path) })}
          onLeave={() => {
            leave(shown.rel_path)
            after(shown.rel_path)
          }}
          onClose={() => setViewing(null)} />
      )}
      {comparing !== null && (
        <CompareViewer key={`${comparing}@${stamp}`} relPath={comparing} stamp={stamp} busy={busy}
          onUndo={() => undo.mutate({ rel_path: comparing }, { onSuccess: () => setComparing(null) })}
          onClose={() => setComparing(null)} />
      )}
      {finalizing && (
        <ConfirmDialog title="Delete the backups?" confirmLabel="Finalize" danger busy={finalize.isPending}
          onConfirm={() => finalize.mutate()} onCancel={() => setFinalizing(false)}>
          The {s.straightened.length} straightened scan(s) stay as they are, and can’t be put back any more:
          their backups in <code>.straighten-backup</code> are deleted.
        </ConfirmDialog>
      )}
    </div>
  )
}

/** The full-size view's shell: the scan (or scans) over the page, the path, Close, and what goes under. */
function ArchivedModal({ relPath, guides, onClose, children, footer }: {
  relPath: string
  guides: boolean
  onClose: () => void
  children: ReactNode
  footer: ReactNode
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
      <figure className={guides ? 'scan-viewer--guides' : undefined} onClick={(e) => e.stopPropagation()}>
        {children}
        <figcaption>
          <span>{relPath}</span>
          <button onClick={onClose}>Close{close && <> <kbd>{keyLabel(close)}</kbd></>}</button>
        </figcaption>
        {footer}
      </figure>
    </div>
  )
}

function GuidesSwitch({ guides, setGuides }: { guides: boolean; setGuides: (on: boolean) => void }) {
  return (
    <label className="scan-viewer__check">
      <input type="checkbox" checked={guides} onChange={(e) => setGuides(e.target.checked)} /> Level guides
    </label>
  )
}

/** A straightened scan beside its backup, as it was, to check the result before Finalize deletes it. */
function CompareViewer({ relPath, stamp, busy, onUndo, onClose }: {
  relPath: string
  stamp: number
  busy: boolean
  onUndo: () => void
  onClose: () => void
}) {
  const [guides, setGuides] = useState(true)
  return (
    <ArchivedModal relPath={relPath} guides={guides} onClose={onClose}
      footer={(
        <div className="scan-viewer__suggest">
          <GuidesSwitch guides={guides} setGuides={setGuides} />
          <span className="scan-viewer__actions">
            <button disabled={busy} onClick={onUndo}>Undo</button>
          </span>
        </div>
      )}>
      <div className="scan-viewer__compare">
        <div>
          <span>As scanned (the backup)</span>
          <div className="scan-viewer__frame"><img src={api.dev.straightenArchiveBackupUrl(relPath)} alt={`${relPath}, as scanned`} /></div>
        </div>
        <div>
          <span>Straightened</span>
          <div className="scan-viewer__frame"><img src={api.dev.archivedUrl(relPath, stamp)} alt={`${relPath}, straightened`} /></div>
        </div>
      </div>
    </ArchivedModal>
  )
}

/** An archived scan full size, with the turn slider over level guides, as Group's viewer has it. */
function ArchivedViewer({ relPath, suggested, stamp, busy, onStraighten, onLeave, onClose }: {
  relPath: string
  suggested: number
  stamp: number
  busy: boolean
  onStraighten: (degrees: number) => void
  onLeave: () => void
  onClose: () => void
}) {
  const [degrees, setDegrees] = useState(suggested)
  const [guides, setGuides] = useState(true)
  // where the ink is, so the preview crops as straightening will
  const outline = useQuery({
    queryKey: ['dev', 'straighten-archive', 'ink-outline', relPath, stamp],
    queryFn: () => api.dev.straightenArchiveInkOutline(relPath),
    staleTime: Infinity,
  })
  return (
    <ArchivedModal relPath={relPath} guides={guides} onClose={onClose}
      footer={(
        <div className="scan-viewer__suggest">
          <span className="scan-viewer__suggestion scan-viewer__line">
            Looks tilted: turn {describeTurn(suggested)} to level it.
          </span>
          <span className="scan-viewer__dial">
            <label htmlFor="archive-degrees">Straighten</label>
            <input id="archive-degrees" type="range" min={-15} max={15} step={0.1} value={-degrees}
              onChange={(e) => setDegrees(Math.round(-Number(e.target.value) * 10) / 10 || 0)} />
            <span className="scan-viewer__turn">{degrees === 0 ? 'no turn' : describeTurn(degrees)}</span>
          </span>
          <button className={degrees === suggested ? 'scan-viewer__unused' : undefined} disabled={degrees === suggested}
            onClick={() => setDegrees(suggested)}>Back to suggested</button>
          <GuidesSwitch guides={guides} setGuides={setGuides} />
          <span className="scan-viewer__actions">
            <button disabled={busy} onClick={onLeave}>Leave as is</button>
            <button className="primary" disabled={busy || degrees === 0} onClick={() => onStraighten(degrees)}>
              Straighten
            </button>
          </span>
        </div>
      )}>
      {degrees ? (
        <div className="scan-viewer__compare">
          <div>
            <span>As scanned</span>
            <div className="scan-viewer__frame"><img src={api.dev.archivedUrl(relPath, stamp)} alt={relPath} /></div>
          </div>
          <div>
            <span>Straightened</span>
            <StraightenedScan src={api.dev.archivedUrl(relPath, stamp)} alt={`${relPath}, straightened`} tilt={degrees}
              outline={outline.data?.points} across={2} />
          </div>
        </div>
      ) : <div className="scan-viewer__frame"><img src={api.dev.archivedUrl(relPath, stamp)} alt={relPath} /></div>}
    </ArchivedModal>
  )
}
