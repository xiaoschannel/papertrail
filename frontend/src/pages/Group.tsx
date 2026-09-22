import { useEffect, useMemo, useRef, useState, type CSSProperties, type ReactNode } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api, inputThumbUrl, inputUrl } from '../api/client.ts'
import type { Grouping, GroupingPage, TopPoints, Trim } from '../api/types.ts'
import { ConfirmDialog } from '../components/ConfirmDialog.tsx'
import { useBatchHolder, useEverythingHolder } from '../components/jobs.tsx'
import { BatchSelect, Pager, RotateButtons } from '../components/scans.tsx'
import { TiltBadge, TiltNotice, useTiltedPages } from '../components/tilts.tsx'
import { TrimEditor } from '../components/TrimEditor.tsx'
import { TrimmedImage } from '../components/TrimmedImage.tsx'
import { PageViewer, TurnNotice, useTurnedPages, type Viewing } from '../components/turns.tsx'
import { Card, Empty, ErrorState, Loading } from '../components/ui.tsx'
import { useGridColumnCount } from '../components/useGridColumnCount.ts'
import './ingest.css'

export default function Group() {
  return (
    <div className="ingest-page ingest-page--wide">
      <h1>Group</h1>
      <p className="page-sub">Link pages that belong to one document, and toss the ones that don’t belong in the archive.</p>
      <DocumentGrouping />
    </div>
  )
}

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
  // Only a job working on this batch (OCR reading it, Parse extracting it, or Archive) locks its pages.
  const holder = useBatchHolder(batchId)
  const archiving = useEverythingHolder()
  const [draft, setDraftState] = useState<Draft>(() => rebase(unsavedDrafts.get(batchId) ?? draftFrom(data), data))
  const setDraft = (next: Draft) => {
    unsavedDrafts.set(batchId, next)
    setDraftState(next)
  }
  const [page, setPage] = useState(0)
  const [confirmingSave, setConfirmingSave] = useState(false)
  const [saved, setSaved] = useState('')
  const [viewing, setViewing] = useState<Viewing | null>(null)
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
  const turned = useTurnedPages(batchId, pagesByKey)
  const tilted = useTiltedPages(batchId, pagesByKey)

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

  const pager = <Pager page={shownPage} pageCount={pageCount} onPage={setPage} />

  return (
    <Card title="Document grouping" className="card--full"
      hint="Link adjacent pages into one document; ⇄ swaps the order of linked pages.">
      <div className="controls">
        <BatchSelect id="grouping-batch" batches={data.batches} value={batchId} onChange={onBatch} />
      </div>
      {pageAction.error && <div className="error-banner" role="alert">{pageAction.error.message}</div>}
      <TurnNotice query={turned.query} turns={turned.turns}
        onReview={() => setViewing({ keys: current.keys.filter((k) => turned.turns.has(k)), at: 0, review: 'turned' })} />
      <TiltNotice query={tilted.query} tilts={tilted.tilts}
        onReview={() => setViewing({ keys: current.keys.filter((k) => tilted.tilts.has(k)), at: 0, review: 'tilted' })} />
      {pager}
      <div className="page-grid" ref={grid}>
        {current.keys.slice(start, start + perPage).map((key, offset) => {
          const index = start + offset
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
            <PageTile key={key} page={pageInfo} group={group} turn={turned.turns.get(key)} tilt={tilted.tilts.get(key)}
              onZoom={() => {
                trim.reset()
                setViewing({ keys: [key], at: 0, review: false })
              }}
              busy={pageAction.isPending} rotateLocked={holder !== null} tossLocked={archiving !== null}
              onRotate={(top) => pageAction.mutate({ action: 'rotate', key, top })}
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
      {pager}

      <p className="ingest-note">
        {groups.length} document(s): {groups.length - multi.length} single-page
        {multi.length > 0 && `; multi-page: ${multi.map(documentKey).join(', ')}`}
      </p>
      {save.error && <div className="error-banner" role="alert">{save.error.message}</div>}
      <div className="start-bar">
        <button className="primary" disabled={!changed || holder !== null || save.isPending} onClick={() => setConfirmingSave(true)}>
          Save document groups
        </button>
        {changed && <button disabled={save.isPending} onClick={() => setDraft(draftFrom(data))}>Discard changes</button>}
        {changed && holder && <span className="ingest-note">Waiting for {holder.title}: it is using batch {batchId}.</span>}
        {!changed && <span className={`ingest-note${saved ? ' ok' : ''}`}>{saved || 'No unsaved changes.'}</span>}
      </div>

      {viewing && <PageViewer viewing={viewing} pages={pagesByKey} turns={turned.turns} tilts={tilted.tilts}
        cannotTurn={(key) => { const p = pagesByKey.get(key); return p ? turnRefusal(p, 'straighten') : null }}
        onSetAsideTilt={tilted.setAside}
        locked={holder === null ? null : `Waiting for ${holder.title}: it is using batch ${batchId}.`}
        onSetAside={turned.setAside} onMove={setViewing} onClose={() => setViewing(null)}
        pageView={(key) => {
          const shown = pagesByKey.get(key)
          if (!shown) return {}
          if (shown.sliced) return { caption: <span className="ingest-note">Sliced into crops, which are what OCR reads: trim a crop instead.</span> }
          return {
            scan: (
              <TrimEditor src={inputUrl(shown.filename, shown.image_version)} alt={`Scan ${shown.key}`}
                value={shown.trim ?? null} saving={trim.isPending} error={trim.error?.message ?? null}
                blockedBy={holder !== null ? holder.title : null}
                onSave={(band) => trim.mutate({ key: shown.key, band })}
                note={<>
                  Trim off what OCR shouldn’t read — a coupon, a survey, a header. OCR reads only the band between
                  the rulers, and every view shows only that part; the scan itself is never changed. A page OCR has
                  read already is read again by its next run.
                </>} />
            ),
          }
        }} />}

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

/** Why a page's scan can't be turned (rotated or straightened): the Slice page owns crops and sliced sheets. */
function turnRefusal(page: GroupingPage, verb: 'rotate' | 'straighten'): string | null {
  // Crops are cut upright from their sheet, and a sliced sheet stays as it was cut.
  return page.crop_of ? 'A crop is turned the way its sheet is' : page.sliced ? `Unslice the sheet to ${verb} it` : null
}

function PageTile({ page, group, turn, tilt, link, busy, rotateLocked, tossLocked, onRotate, onToss, onZoom }: {
  page: GroupingPage
  group: number | undefined
  /** Where the scan's top seems to point, when it looks turned. */
  turn: TopPoints | undefined
  /** The turn that would level the scan, when it looks tilted. */
  tilt: number | undefined
  link: ReactNode
  busy: boolean
  rotateLocked: boolean
  tossLocked: boolean
  onRotate: (top: TopPoints) => void
  onToss: () => void
  onZoom: () => void
}) {
  const style = group === undefined ? undefined : ({ '--group-hue': GROUP_HUES[group % GROUP_HUES.length] } as CSSProperties)
  // Crops are cut upright from their sheet, and a sliced sheet stays as it was cut: the Slice page owns both.
  const cut = turnRefusal(page, 'rotate') ?? undefined
  return (
    <div className="page-cell">
      <figure className={`page-tile${page.tossed ? ' tossed' : ''}${group !== undefined ? ' grouped' : ''}`} style={style}>
        <div className="page-tile__tools">
          <RotateButtons disabled={busy || rotateLocked || !page.image_available || cut !== undefined}
            {...(cut ? { title: cut } : {})} suggested={turn} onRotate={onRotate} />
          {tilt !== undefined && <TiltBadge degrees={tilt} onOpen={onZoom} />}
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
                <TrimmedImage fit="contain" src={inputThumbUrl(page.filename, page.image_version)} alt={`Scan ${page.key}`}
                  trim={page.trim ?? null} />
              </button>
            )
            : <span className="ingest-note">Scan not in the input folder</span>}
        </div>
        <figcaption>
          <strong>{page.key}</strong>{' '}
          {page.crop_of && <span className="page-badge">from {page.crop_of} · r{page.cell?.[0]}c{page.cell?.[1]}</span>}
          {page.sliced && <span className="page-badge">sliced</span>}{' '}
          {page.filename}
        </figcaption>
      </figure>
      {link}
    </div>
  )
}

