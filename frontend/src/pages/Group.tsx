import { useCallback, useEffect, useMemo, useState, type CSSProperties, type ReactNode } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api, inputThumbUrl, inputUrl } from '../api/client.ts'
import type { Batch, Grouping, GroupingPage, Trim } from '../api/types.ts'
import { CappedImage } from '../components/DocumentCard.tsx'
import { FinalizeBar, useFinalizeStatus } from '../components/finalize.tsx'
import { useBatchHolder, useEverythingHolder } from '../components/jobs.tsx'
import { ScanViewer, batchHint, batchTitle } from '../components/scans.tsx'
import { TrimEditor } from '../components/TrimEditor.tsx'
import { Card, Empty, ErrorState, Loading } from '../components/ui.tsx'
import './ingest.css'

/**
 * Group: every unarchived batch at once, one section each. Tosses and trims are saved as they are made;
 * links are drafted on the page and saved with Finalize, which then commits the step for every batch.
 */
export default function Group() {
  const status = useFinalizeStatus('group')
  // Each batch whose links differ from its saved grouping, with the groups it would save.
  const [changed, setChanged] = useState<Map<number, string[][]>>(new Map())
  const report = useCallback((batchId: number, groups: string[][] | null) => {
    setChanged((current) => {
      if (JSON.stringify(current.get(batchId) ?? null) === JSON.stringify(groups)) return current
      const next = new Map(current)
      if (groups) next.set(batchId, groups)
      else next.delete(batchId)
      return next
    })
  }, [])
  const regrouped = [...changed.keys()].sort((a, b) => a - b)
  const which = regrouped.length === 1 ? `batch ${regrouped[0]}’s` : `batches ${regrouped.join(', ')}’`
  return (
    <div className="ingest-page ingest-page--wide">
      <h1>Group</h1>
      <p className="page-sub">
        Link pages that belong to one document, and toss the ones that don’t belong in the archive. Every batch
        not archived yet is here; Finalize saves the links and commits the step.
      </p>
      {status.isPending ? <Loading what="batches" />
        : status.error ? <ErrorState error={status.error} />
          : status.data.batches.length === 0
            ? <Card title="Document grouping"><Empty>No unarchived batches. Add batches on File Index first.</Empty></Card>
            : <>
              {status.data.batches.map((b) => <BatchGrouping key={b.batch_id} batch={b} onChange={report} />)}
              <FinalizeBar step="group" status={status.data} what="the grouping"
                groups={regrouped.map((id) => ({ batch_id: id, groups: changed.get(id) ?? [] }))}
                confirm={regrouped.length === 0 ? null : <>
                  Changing how {which} pages are grouped clears {regrouped.length === 1 ? 'its' : 'their'} parse
                  results and review decisions, so those documents need Parse and Review again.
                </>} />
            </>}
    </div>
  )
}

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

/** Unsaved edits per batch, kept while the app is open (leaving the page doesn't lose them). */
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

type Report = (batchId: number, groups: string[][] | null) => void

/** One batch's pages, in its own section. */
function BatchGrouping({ batch, onChange }: { batch: Batch; onChange: Report }) {
  const grouping = useQuery({
    queryKey: ['ingest', 'grouping', batch.batch_id],
    queryFn: () => api.ingest.grouping(batch.batch_id),
    placeholderData: (previous) => previous,
  })
  if (grouping.isPending) return <Loading what={`batch ${batch.batch_id}'s pages`} />
  if (grouping.error) return <ErrorState error={grouping.error} />
  if (grouping.data.batch_id !== batch.batch_id) return null     // archived since the page asked
  return <GroupingEditor data={grouping.data} batch={batch} onChange={onChange} />
}

function GroupingEditor({ data, batch, onChange }: { data: Grouping; batch: Batch; onChange: Report }) {
  const batchId = batch.batch_id
  const queryClient = useQueryClient()
  // Only a job working on this batch (OCR reading it, Parse extracting it, or Archive) locks its pages.
  const holder = useBatchHolder(batchId)
  const archiving = useEverythingHolder()
  const [draft, setDraftState] = useState<Draft>(() => rebase(unsavedDrafts.get(batchId) ?? draftFrom(data), data))
  const setDraft = (next: Draft) => {
    unsavedDrafts.set(batchId, next)
    setDraftState(next)
  }
  const [viewing, setViewing] = useState<string | null>(null)   // the key of the page open full size

  useEffect(() => {
    setDraftState((current) => {
      const next = rebase(current, data)
      unsavedDrafts.set(batchId, next)
      return next
    })
  }, [data, batchId])

  const pageAction = useMutation({
    mutationFn: ({ action, key }: { action: 'toss' | 'recover'; key: string }) => api.ingest[action](key),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['ingest'] })
      // tossing or recovering a page also decides it (or takes the decision back) for Review
      for (const root of ['review-queue', 'review-doc']) void queryClient.invalidateQueries({ queryKey: [root] })
    },
  })
  const trim = useMutation({
    mutationFn: ({ key, band }: { key: string; band: Trim | null }) => api.ingest.trim(key, band),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['ingest'] })     // the tiles, and OCR's queue
      void queryClient.invalidateQueries({ queryKey: ['review-doc'] })
    },
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

  // the page's Finalize saves what differs from the saved grouping
  const pending = changed ? JSON.stringify(groups) : ''
  useEffect(() => { onChange(batchId, pending ? JSON.parse(pending) as string[][] : null) }, [onChange, batchId, pending])
  useEffect(() => () => onChange(batchId, null), [onChange, batchId])   // archived meanwhile: nothing to save

  const toggleLink = (activeIndex: number) => {
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

  return (
    <Card title={batchTitle(batch)} className="card--full"
      hint={`${batchHint(batch)} · link adjacent pages into one document; ⇄ swaps the order of linked pages`}>
      {pageAction.error && <div className="error-banner" role="alert">{pageAction.error.message}</div>}
      <div className="page-grid">
        {current.keys.map((key, index) => {
          const pageInfo = pagesByKey.get(key)
          if (!pageInfo) return null
          const next = current.keys[index + 1]
          // a crop is a document of its own, so it links to nothing
          const crops = Boolean(pageInfo.crop_of) || (next !== undefined && Boolean(pagesByKey.get(next)?.crop_of))
          const linkable = next !== undefined && !tossed.has(key) && !tossed.has(next) && !crops
          const activeIndex = activeKeys.indexOf(key)
          const linked = linkable && Boolean(current.links[activeIndex])
          const group = groupOf.get(key)
          return (
            <PageTile key={key} page={pageInfo} group={group}
              onZoom={() => {
                trim.reset()
                setViewing(key)
              }}
              busy={pageAction.isPending} tossLocked={archiving !== null}
              onToss={() => pageAction.mutate({ action: pageInfo.tossed ? 'recover' : 'toss', key })}
              link={next === undefined ? null : (
                <div className={`page-link${linked ? ' on' : ''}`}>
                  <button className="page-link__toggle" disabled={!linkable} aria-pressed={linked}
                    title={linkable ? (linked ? 'Unlink from the next page' : 'Link to the next page')
                      : crops ? 'A crop is a document of its own' : 'Tossed pages can’t be linked'}
                    onClick={() => toggleLink(activeIndex)}>🔗</button>
                  {linked && <button className="page-link__swap" title="Swap with the next page" onClick={() => swap(index)}>⇄</button>}
                </div>
              )} />
          )
        })}
      </div>

      <p className="ingest-note">
        {groups.length} document(s): {groups.length - multi.length} single-page
        {multi.length > 0 && `; multi-page: ${multi.map(documentKey).join(', ')}`}
      </p>
      {changed && (
        <div className="start-bar">
          <span className="ingest-note">Links changed: saved with Finalize.</span>
          <button onClick={() => setDraft(draftFrom(data))}>Discard changes</button>
          {holder && <span className="ingest-note">Waiting for {holder.title}: it is using batch {batchId}.</span>}
        </div>
      )}

      {viewing && (() => {
        // The page full size, with the rulers that trim it; a sliced sheet is never read, so it is just shown.
        const shown = pagesByKey.get(viewing)
        if (!shown) return null
        return (
          <ScanViewer label={shown.key} filename={shown.filename} version={shown.image_version} onClose={() => setViewing(null)}
            scan={shown.sliced ? undefined : (
              <TrimEditor src={inputUrl(shown.filename, shown.image_version)} alt={`Scan ${shown.key}`}
                value={shown.trim ?? null} saving={trim.isPending} error={trim.error?.message ?? null}
                blockedBy={holder !== null ? holder.title : null}
                onSave={(band) => trim.mutate({ key: shown.key, band })}
                note={<>
                  Trim off what OCR shouldn’t read — a coupon, a survey, a header. OCR reads only the band between
                  the rulers, and every view shows only that part; the scan itself is never changed. A page OCR has
                  read already is read again by its next run.
                </>} />
            )}>
            {shown.sliced && <span className="ingest-note">Sliced into crops, which are what OCR reads: trim a crop instead.</span>}
          </ScanViewer>
        )
      })()}

    </Card>
  )
}

const GROUP_HUES = [210, 32, 150, 280, 350, 90]

function PageTile({ page, group, link, busy, tossLocked, onToss, onZoom }: {
  page: GroupingPage
  group: number | undefined
  link: ReactNode
  busy: boolean
  tossLocked: boolean
  onToss: () => void
  onZoom: () => void
}) {
  const style = group === undefined ? undefined : ({ '--group-hue': GROUP_HUES[group % GROUP_HUES.length] } as CSSProperties)
  return (
    <div className="page-cell">
      <figure className={`page-tile${page.tossed ? ' tossed' : ''}${group !== undefined ? ' grouped' : ''}`} style={style}>
        <div className="page-tile__tools">
          <span className="page-tile__key"><strong>{page.key}</strong></span>
          <button className={page.tossed ? '' : 'danger-outline'} disabled={busy || tossLocked || page.sliced} onClick={onToss}
            title={page.sliced ? 'A sliced sheet stays tossed; unslice it on the Slice page'
              : page.tossed ? 'Recover this page' : 'Toss this page'}>
            {page.tossed ? '↩' : '✕'}
          </button>
        </div>
        <div className="page-tile__scan">
          {page.image_available
            ? (
              <button className="page-tile__zoom" onClick={onZoom}
                title={page.sliced ? 'Show this scan full size'
                  : page.trim ? 'Show this scan full size (it is trimmed) and move its cuts'
                  : 'Show this scan full size, and trim it'}>
                <CappedImage src={inputThumbUrl(page.filename, page.image_version)} alt={`Scan ${page.key}`}
                  trim={page.trim ?? null} />
              </button>
            )
            : <span className="ingest-note">Scan not in the input folder</span>}
        </div>
        <figcaption>
          {page.crop_of && <span className="page-badge">from {page.crop_of} · r{page.cell?.[0]}c{page.cell?.[1]}</span>}
          {page.sliced && <span className="page-badge">sliced</span>}{' '}
          {page.filename}
        </figcaption>
      </figure>
      {link}
    </div>
  )
}

