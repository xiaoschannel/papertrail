import { useEffect, useMemo, useRef, useState, type CSSProperties, type ReactNode } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api, inputThumbUrl, inputUrl } from '../api/client.ts'
import type { Grouping, GroupingPage, IndexStatus, TopPoints } from '../api/types.ts'
import { ConfirmDialog } from '../components/ConfirmDialog.tsx'
import { useCurrentJob } from '../components/jobs.tsx'
import { Card, Empty, ErrorState, Loading, Tile } from '../components/ui.tsx'
import { useGridColumnCount } from '../components/useGridColumnCount.ts'
import './ingest.css'

export default function FileIndex() {
  return (
    <div className="ingest-page">
      <h1>File Index</h1>
      <p className="page-sub">Add newly scanned files as batches, then group pages that belong to one document.</p>
      <Batches />
      <DocumentGrouping />
    </div>
  )
}

/* --- batches ------------------------------------------------------------------------------------ */
function Batches() {
  const queryClient = useQueryClient()
  const [scheme, setScheme] = useState<string | undefined>(undefined)
  const job = useCurrentJob()
  const status = useQuery({
    queryKey: ['ingest', 'index', scheme],
    queryFn: () => api.ingest.index(scheme),
    placeholderData: (previous) => previous,
  })
  const [added, setAdded] = useState(0)
  const confirm = useMutation({
    mutationFn: (s: IndexStatus) => api.ingest.confirmIndex({ scheme: s.scheme, token: s.token }),
    onSuccess: (_result, sent) => {
      setAdded(sent.proposal.length)
      void queryClient.invalidateQueries({ queryKey: ['ingest'] })
    },
  })

  if (status.isPending) return <Loading what="scan folder" />
  if (status.error) return <ErrorState error={status.error} />
  const s = status.data
  if (s.blocker) return <Card title="Batches"><Empty>{s.blocker}</Empty></Card>
  const busy = job?.status === 'running'

  return (
    <Card title="Batches" hint={`${s.existing_batches} batch(es) indexed`}>
      <div className="controls">
        <div className="field">
          <label htmlFor="index-scheme">Scanner naming scheme</label>
          <select id="index-scheme" value={s.scheme} onChange={(e) => setScheme(e.target.value)}>
            {s.schemes.map((name) => <option key={name} value={name}>{name}</option>)}
          </select>
          <span className="config-hint">
            Only files that aren't indexed yet are passed to the scheme; indexed files are never reassigned.
          </span>
        </div>
      </div>
      <div className="tiles">
        <Tile label="Images in folder" value={s.image_count} />
        <Tile label="Indexed" value={s.indexed_count} />
        <Tile label="New" value={s.unindexed_count} />
      </div>

      {s.error && (
        <div className="error-banner" role="alert">
          {s.error}
          {s.offending.length > 0 && (
            <details className="ingest-details">
              <summary>{s.offending.length} file(s) older than the last batch</summary>
              <ul className="file-list">{s.offending.map((f) => <li key={f}>{f}</li>)}</ul>
            </details>
          )}
        </div>
      )}
      {s.warnings.map((w) => <p key={w} className="ingest-note ingest-warning">{w}</p>)}
      {s.skipped.length > 0 && (
        <details className="ingest-details">
          <summary>{s.skipped.length} file(s) skipped (name doesn't match the scheme)</summary>
          <ul className="file-list">{s.skipped.map((f) => <li key={f}>{f}</li>)}</ul>
        </details>
      )}

      {s.proposal.length > 0 && (
        <>
          <h3 className="ingest-subhead">New batches</h3>
          {s.proposal.map((b) => (
            <details key={b.batch_id} className="ingest-details">
              <summary>Batch {b.batch_id} — {b.file_count} files — {b.start_datetime} to {b.end_datetime}</summary>
              <ul className="file-list">
                {b.files.map((f) => <li key={f.serial}><code>{String(f.serial).padStart(3, '0')}</code> {f.filename}</li>)}
              </ul>
            </details>
          ))}
          {confirm.error && <div className="error-banner" role="alert">{confirm.error.message}</div>}
          <div className="start-bar">
            <button className="primary" disabled={busy || confirm.isPending} onClick={() => confirm.mutate(s)}>
              Add {s.proposal.length} batch{s.proposal.length === 1 ? '' : 'es'}
            </button>
            {busy && <span className="ingest-note">Waiting for {job.title} to finish.</span>}
          </div>
        </>
      )}
      {added > 0 && s.proposal.length === 0 && (
        <p className="ingest-note ok">Added {added} batch(es). Group their pages below, then run OCR.</p>
      )}
      {!s.error && s.proposal.length === 0 && added === 0 && <p className="ingest-note">No new files to index.</p>}
    </Card>
  )
}

/* --- document grouping ------------------------------------------------------------------------------ */
/** The grid's rows per page; a page is whole rows at whatever column count fits. */
const ROWS_PER_PAGE = 6

type Draft = {
  /** Pages in display order (tossed pages keep their slot). */
  keys: string[]
  /** links[i]: active page i continues into active page i + 1. */
  links: boolean[]
  /** The server state the draft was built from (see `rebase`). */
  basis: string
  tossed: string[]
  saved: string
}

const tossedKeys = (g: Grouping) => g.pages.filter((p) => p.tossed).map((p) => p.key)
const basisOf = (g: Grouping) => JSON.stringify([g.display_keys, tossedKeys(g), g.saved_groups])

function draftFrom(g: Grouping): Draft {
  return { keys: g.display_keys, links: g.active_links, basis: basisOf(g), tossed: tossedKeys(g), saved: JSON.stringify(g.saved_groups) }
}

/**
 * Follow a server change without losing unsaved edits. A save (or a different page set) starts over from
 * the server; a toss or recover keeps the draft's order and every link whose two pages are still
 * neighbours among the active pages (a group is split where a page in it was tossed).
 */
function rebase(draft: Draft, g: Grouping): Draft {
  if (draft.basis === basisOf(g)) return draft
  const sameKeys = draft.keys.length === g.display_keys.length && g.display_keys.every((k) => draft.keys.includes(k))
  if (!sameKeys || draft.saved !== JSON.stringify(g.saved_groups)) return draftFrom(g)
  const wasTossed = new Set(draft.tossed)
  const oldActive = draft.keys.filter((k) => !wasTossed.has(k))
  const linkedPairs = new Set(oldActive.flatMap((k, i) => (draft.links[i] ? [`${k}|${oldActive[i + 1]}`] : [])))
  const tossed = new Set(tossedKeys(g))
  const active = draft.keys.filter((k) => !tossed.has(k))
  return {
    keys: draft.keys,
    links: active.slice(0, -1).map((k, i) => linkedPairs.has(`${k}|${active[i + 1]}`)),
    basis: basisOf(g),
    tossed: [...tossed],
    saved: draft.saved,
  }
}

/** Unsaved edits per batch, kept while the app is open (switching batches or pages doesn't lose them). */
const unsavedDrafts = new Map<number, Draft>()

/** Consecutive runs of linked keys (mirror of document_grouping.compute_groups). */
function computeGroups(keys: string[], links: boolean[]): string[][] {
  const groups: string[][] = []
  keys.forEach((key, i) => {
    const last = groups[groups.length - 1]
    if (i > 0 && links[i - 1] && last) last.push(key)
    else groups.push([key])
  })
  return groups
}

const documentKey = (group: string[]): string => {
  const serials = group.map((k) => Number(k.split(':')[1]))
  const batch = group[0]?.split(':')[0] ?? ''
  const lo = Math.min(...serials)
  const hi = Math.max(...serials)
  return lo === hi ? `${batch}:${lo}` : `${batch}:${lo}-${hi}`
}

function DocumentGrouping() {
  const [batchId, setBatchId] = useState<number | undefined>(undefined)
  const grouping = useQuery({
    queryKey: ['ingest', 'grouping', batchId],
    queryFn: () => api.ingest.grouping(batchId),
    placeholderData: (previous) => previous,
  })
  if (grouping.isPending) return <Loading what="document grouping" />
  if (grouping.error) return <ErrorState error={grouping.error} />
  const data = grouping.data
  if (data.blocker || data.batch_id === null) {
    return <Card title="Document grouping"><Empty>{data.blocker ?? 'No batches.'}</Empty></Card>
  }
  // Keyed by batch: switching batches starts from that batch's saved grouping on its first page.
  return <GroupingEditor key={data.batch_id} data={data} batchId={data.batch_id} onBatch={setBatchId} />
}

function GroupingEditor({ data, batchId, onBatch }: { data: Grouping; batchId: number; onBatch: (id: number) => void }) {
  const queryClient = useQueryClient()
  const job = useCurrentJob()
  const [draft, setDraftState] = useState<Draft>(() => rebase(unsavedDrafts.get(batchId) ?? draftFrom(data), data))
  const setDraft = (next: Draft) => {
    unsavedDrafts.set(batchId, next)
    setDraftState(next)
  }
  const [page, setPage] = useState(0)
  const [confirmingSave, setConfirmingSave] = useState(false)
  const [saved, setSaved] = useState('')
  const [zoomed, setZoomed] = useState<GroupingPage | null>(null)
  const grid = useRef<HTMLDivElement>(null)
  const columns = useGridColumnCount(grid, 6)

  useEffect(() => {
    setDraftState((current) => {
      const next = rebase(current, data)
      unsavedDrafts.set(batchId, next)
      return next
    })
  }, [data, batchId])

  const pageAction = useMutation({
    mutationFn: ({ action, key, top }: { action: 'toss' | 'recover' | 'rotate'; key: string; top?: TopPoints }) =>
      action === 'rotate' ? api.ingest.rotate(key, top ?? 'down') : api.ingest[action](key),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['ingest'] }),
  })
  const save = useMutation({
    mutationFn: (groups: string[][]) => api.ingest.saveGrouping(batchId, groups),
    onSuccess: async (result) => {
      setConfirmingSave(false)
      setSaved(result.changed ? 'Saved. Re-run Parse for this batch.' : 'Nothing to save — the grouping is unchanged.')
      await queryClient.invalidateQueries({ queryKey: ['ingest'] })
      await queryClient.invalidateQueries({ queryKey: ['review-queue'] })
    },
    onError: () => setConfirmingSave(false),
  })

  const pagesByKey = useMemo(() => new Map(data.pages.map((p) => [p.key, p])), [data])
  const tossed = useMemo(() => new Set(data.pages.filter((p) => p.tossed).map((p) => p.key)), [data])

  const current = rebase(draft, data)  // same as the synced draft; avoids one frame of stale links
  const activeKeys = current.keys.filter((k) => !tossed.has(k))
  const groups = computeGroups(activeKeys, current.links)
  const multi = groups.filter((g) => g.length > 1)
  const changed = JSON.stringify([...multi].map((g) => JSON.stringify(g)).sort())
    !== JSON.stringify([...data.saved_groups].map((g) => JSON.stringify(g)).sort())
  const groupOf = new Map<string, number>()
  multi.forEach((g, i) => g.forEach((k) => groupOf.set(k, i)))

  const perPage = ROWS_PER_PAGE * Math.max(1, columns)
  const pageCount = Math.max(1, Math.ceil(current.keys.length / perPage))
  const shownPage = Math.min(page, pageCount - 1)
  const start = shownPage * perPage
  const busy = job?.status === 'running'
  const archiving = busy && job.kind === 'archive'

  const toggleLink = (activeIndex: number) => {
    setSaved('')
    setDraft({ ...current, links: current.links.map((v, i) => (i === activeIndex ? !v : v)) })
  }
  const swap = (displayIndex: number) => {
    const keys = [...current.keys]
    const a = keys[displayIndex]
    const b = keys[displayIndex + 1]
    if (a === undefined || b === undefined) return
    keys[displayIndex] = b
    keys[displayIndex + 1] = a
    setDraft({ ...current, keys })
  }

  const pager = pageCount > 1 && (
    <div className="pager">
      <button disabled={shownPage === 0} onClick={() => setPage(shownPage - 1)}>← Prev</button>
      <span>
        Page
        <input className="page-jump" type="number" min={1} max={pageCount} value={shownPage + 1}
          aria-label="Skip to page"
          onChange={(e) => setPage(Math.min(pageCount, Math.max(1, Number(e.target.value) || 1)) - 1)} />
        of {pageCount}
      </span>
      <button disabled={shownPage >= pageCount - 1} onClick={() => setPage(shownPage + 1)}>Next →</button>
    </div>
  )

  return (
    <Card title="Document grouping" hint="Link adjacent pages into one document; ⇄ swaps the order of linked pages.">
      <div className="controls">
        <div className="field">
          <label htmlFor="grouping-batch">Batch</label>
          <select id="grouping-batch" value={batchId}
            onChange={(e) => onBatch(Number(e.target.value))}>
            {data.batches.map((b) => (
              <option key={b.batch_id} value={b.batch_id}>
                Batch {b.batch_id} — {b.file_count} files — {b.start_datetime} to {b.end_datetime}
              </option>
            ))}
          </select>
        </div>
      </div>
      {pageAction.error && <div className="error-banner" role="alert">{pageAction.error.message}</div>}
      {pager}
      <div className="page-grid" ref={grid}>
        {current.keys.slice(start, start + perPage).map((key, offset) => {
          const index = start + offset
          const pageInfo = pagesByKey.get(key)
          if (!pageInfo) return null
          const next = current.keys[index + 1]
          const linkable = next !== undefined && !tossed.has(key) && !tossed.has(next)
          const activeIndex = activeKeys.indexOf(key)
          const linked = linkable && Boolean(current.links[activeIndex])
          const group = groupOf.get(key)
          return (
            <PageTile key={key} page={pageInfo} group={group} onZoom={() => setZoomed(pageInfo)}
              busy={pageAction.isPending} rotateLocked={busy} tossLocked={archiving}
              onRotate={(top) => pageAction.mutate({ action: 'rotate', key, top })}
              onToss={() => pageAction.mutate({ action: pageInfo.tossed ? 'recover' : 'toss', key })}
              link={next === undefined ? null : (
                <div className={`page-link${linked ? ' on' : ''}`}>
                  <button className="page-link__toggle" disabled={!linkable} aria-pressed={linked}
                    title={linkable ? (linked ? 'Unlink from the next page' : 'Link to the next page') : 'Tossed pages can’t be linked'}
                    onClick={() => toggleLink(activeIndex)}>🔗</button>
                  {linked && <button className="page-link__swap" title="Swap with the next page" onClick={() => swap(index)}>⇄</button>}
                </div>
              )} />
          )
        })}
      </div>
      {pager}

      <p className="ingest-note">
        {groups.length} document(s): {groups.length - multi.length} single-page
        {multi.length > 0 && `; multi-page: ${multi.map(documentKey).join(', ')}`}
      </p>
      {save.error && <div className="error-banner" role="alert">{save.error.message}</div>}
      <div className="start-bar">
        <button className="primary" disabled={!changed || busy || save.isPending} onClick={() => setConfirmingSave(true)}>
          Save document groups
        </button>
        {changed && <button disabled={save.isPending} onClick={() => setDraft(draftFrom(data))}>Discard changes</button>}
        {changed && busy && <span className="ingest-note">Waiting for {job.title} to finish.</span>}
        {!changed && <span className={`ingest-note${saved ? ' ok' : ''}`}>{saved || 'No unsaved changes.'}</span>}
      </div>

      {zoomed && <ScanViewer page={zoomed} onClose={() => setZoomed(null)} />}

      {confirmingSave && (
        <ConfirmDialog title="Save document groups?" confirmLabel="Save" danger busy={save.isPending}
          onConfirm={() => save.mutate(groups)} onCancel={() => setConfirmingSave(false)}>
          Changing how batch {batchId}’s pages are grouped clears the batch’s parse results and review
          decisions, so its documents need Parse and Review again.
        </ConfirmDialog>
      )}
    </Card>
  )
}

const GROUP_HUES = [210, 32, 150, 280, 350, 90]

function PageTile({ page, group, link, busy, rotateLocked, tossLocked, onRotate, onToss, onZoom }: {
  page: GroupingPage
  group: number | undefined
  link: ReactNode
  busy: boolean
  rotateLocked: boolean
  tossLocked: boolean
  onRotate: (top: TopPoints) => void
  onToss: () => void
  onZoom: () => void
}) {
  const style = group === undefined ? undefined : ({ '--group-hue': GROUP_HUES[group % GROUP_HUES.length] } as CSSProperties)
  return (
    <div className="page-cell">
      <figure className={`page-tile${page.tossed ? ' tossed' : ''}${group !== undefined ? ' grouped' : ''}`} style={style}>
        <div className="page-tile__tools">
          <span className="page-tile__rotate" title="Which way the top of the page points now">
            <button disabled={busy || rotateLocked || !page.image_available} title="Top points left" onClick={() => onRotate('left')}>←</button>
            <button disabled={busy || rotateLocked || !page.image_available} title="Top points right" onClick={() => onRotate('right')}>→</button>
            <button disabled={busy || rotateLocked || !page.image_available} title="Upside down" onClick={() => onRotate('down')}>↓</button>
          </span>
          <button className={page.tossed ? '' : 'danger-outline'} disabled={busy || tossLocked} onClick={onToss}
            title={page.tossed ? 'Recover this page' : 'Toss this page'}>
            {page.tossed ? '↩' : '✕'}
          </button>
        </div>
        <div className="page-tile__scan">
          {page.image_available
            ? (
              <button className="page-tile__zoom" onClick={onZoom} title="Show this scan full size">
                <img src={inputThumbUrl(page.filename, page.image_version)} alt={`Scan ${page.key}`} loading="lazy" />
              </button>
            )
            : <span className="ingest-note">Scan not in the input folder</span>}
        </div>
        <figcaption><strong>{page.key}</strong> {page.filename}</figcaption>
      </figure>
      {link}
    </div>
  )
}


/** The full scan, for deciding whether a page continues the previous document or is upright. */
function ScanViewer({ page, onClose }: { page: GroupingPage; onClose: () => void }) {
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') onClose()
    }
    document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
  }, [onClose])

  return (
    <div className="modal-backdrop scan-viewer" data-modal-open onClick={onClose} role="dialog" aria-modal="true">
      <figure onClick={(e) => e.stopPropagation()}>
        <img src={inputUrl(page.filename)} alt={`Scan ${page.key}`} />
        <figcaption>
          <strong>{page.key}</strong> {page.filename}
          <button onClick={onClose}>Close <kbd>Esc</kbd></button>
        </figcaption>
      </figure>
    </div>
  )
}
